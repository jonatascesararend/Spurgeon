"""Instagram Reels e Facebook Reels (Página) pela Graph API da Meta.

Caminho mais simples para uma igreja, com contas e Página PRÓPRIAS: um app da Meta em modo
Desenvolvimento (a pessoa é administradora dele, sem App Review) e um token gerado no Graph API
Explorer. O Spurgeon troca esse token por um de longa duração e pega o token da Página, que não
expira. O vídeo vai direto do arquivo (upload resumável no `rupload.facebook.com`): não precisa de
endereço público.

- Instagram: a API NÃO agenda. O Spurgeon guarda o horário (`NA_FILA`), cria o envio uns 45 minutos
  antes (o contêiner vale 24 h) e publica na hora marcada. O aplicativo precisa estar aberto.
- Facebook: agendamento da própria rede (`video_state=SCHEDULED`, de 10 minutos a 29 dias à frente).
  O Reel de Página aceita de 3 a 90 s.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from spurgeon.core.erros import Cancelado as CanceladoErro
from spurgeon.publicacao.erros import (
    ErroDePublicacao,
    ErroDeRede,
    FaltaConfigurar,
    ReconectarNecessario,
)
from spurgeon.publicacao.modelo import (
    AGENDADO,
    ENVIADO,
    NA_FILA,
    Campo,
    Pacote,
    PedidoDeCancelar,
    Plataforma,
    Progresso,
    Resultado,
)
from spurgeon.publicacao.rede import TEMPO_LIMITE_DO_ENVIO_S, com_tentativas, exigir_ok, pedir_json

VERSAO_DA_API = "v23.0"
URL_GRAPH = "https://graph.facebook.com"
URL_UPLOAD = "https://rupload.facebook.com"

INSTAGRAM_LIMITE_DA_LEGENDA = 2200
INSTAGRAM_LIMITE_DE_HASHTAGS = 30
INSTAGRAM_LIMITE_DO_ARQUIVO = 300 * 1024 * 1024
INSTAGRAM_DURACAO_MIN_S = 3.0
INSTAGRAM_DURACAO_MAX_S = 15 * 60.0
#: com quanta antecedência o Instagram recebe o vídeo (o contêiner expira em 24 h sem publicar)
PREPARO_ANTES_DO_HORARIO = timedelta(minutes=45)
VALIDADE_DO_CONTEINER = timedelta(hours=23)

FACEBOOK_DURACAO_MIN_S = 3.0
FACEBOOK_DURACAO_MAX_S = 90.0
FACEBOOK_ANTECEDENCIA_MIN = timedelta(minutes=10)
FACEBOOK_ANTECEDENCIA_MAX = timedelta(days=29)

ESPERA_DO_PROCESSAMENTO_S = 10.0
TEMPO_DO_PROCESSAMENTO_S = 15 * 60.0

_CAMPOS_DA_META = (
    Campo(
        "app_id", "ID do app",
        ajuda="developers.facebook.com → seu app → Configurações → Básico.",
    ),
    Campo("app_secret", "Chave secreta do app", segredo=True),
    Campo(
        "token", "Token de acesso (do Graph API Explorer)", segredo=True,
        ajuda="Só é usado para conectar: o Spurgeon o troca pelo token da Página, que não expira.",
    ),
    Campo(
        "pagina", "Página (nome ou número)", opcional=True,
        ajuda="Só se o token enxergar mais de uma Página: diga qual é a da igreja.",
    ),
)  # fmt: skip

_COMO_CONECTAR_META = (
    "1. A conta do Instagram deve ser Profissional (Criador ou Comercial) e estar ligada a uma "
    "Página do Facebook da qual você é administrador.\n"
    "2. Em developers.facebook.com crie um app do tipo “Negócios” (deixe em modo Desenvolvimento: "
    "como você é administrador dele, não precisa de revisão da Meta).\n"
    "3. No “Explorador da Graph API”, escolha o app, clique em “Gerar token de acesso” e marque as "
    "permissões: pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, "
    "instagram_content_publish.\n"
    "4. Cole aqui o ID e a chave secreta do app e o token, e clique em Conectar.\n"
    "Teste um Reel e confira, de fora da sua conta, que ele aparece: apps em modo Desenvolvimento "
    "podem esconder as postagens de quem não tem função no app."
)


class _Meta(Plataforma):
    """O que Instagram e Facebook têm em comum: a conta da Meta e as chamadas à Graph API."""

    campos = _CAMPOS_DA_META
    como_conectar = _COMO_CONECTAR_META

    @property
    def conta(self) -> str:
        return "meta"  # uma conta só para as duas redes

    @property
    def conectada(self) -> bool:
        return self.cofre.tem(self.conta, "page_token") and bool(self.guardado("page_id"))

    # --- conectar --------------------------------------------------------------------------------
    def conectar(self, *, abrir=None, cancelado: PedidoDeCancelar | None = None) -> str:
        app_id, segredo = self.guardado("app_id"), self.guardado("app_secret")
        token = self.guardado("token")
        if not (app_id and segredo and token):
            raise FaltaConfigurar(
                "Preencha o ID do app, a chave secreta e o token antes de conectar."
            )
        longo = pedir_json(
            self.transporte, "GET",
            self._url("/oauth/access_token", {
                "grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": segredo, "fb_exchange_token": token,
            }),
            "Trocar o token da Meta por um de longa duração",
        )  # fmt: skip
        acesso = str(longo.get("access_token", ""))
        if not acesso:
            raise ErroDePublicacao("A Meta não devolveu o token de longa duração.")
        contas = pedir_json(
            self.transporte, "GET",
            self._url("/me/accounts", {
                "fields": "id,name,access_token,instagram_business_account{id,username}",
                "limit": 100, "access_token": acesso,
            }),
            "Listar as Páginas da conta",
        )  # fmt: skip
        pagina = _escolher_pagina(contas.get("data") or [], self.guardado("pagina"))
        self.cofre.definir(self.conta, "page_id", str(pagina["id"]))
        self.cofre.definir(self.conta, "page_token", str(pagina["access_token"]), segredo=True)
        self.cofre.definir(self.conta, "pagina_nome", str(pagina.get("name", "")))
        instagram = pagina.get("instagram_business_account") or {}
        self.cofre.definir(self.conta, "ig_id", str(instagram.get("id", "")))
        self.cofre.definir(self.conta, "ig_usuario", str(instagram.get("username", "")))
        self.cofre.remover(self.conta, "token")  # o da Página basta (e não expira)
        resumo = f"Página {pagina.get('name', '')}"
        if instagram.get("username"):
            resumo += f" · Instagram @{instagram['username']}"
        else:
            resumo += " (sem Instagram profissional ligado)"
        self.cofre.definir(self.conta, "conta_nome", resumo)
        return resumo

    # --- chamadas ---------------------------------------------------------------------------------
    def _url(self, caminho: str, parametros: dict[str, Any] | None = None) -> str:
        consulta = f"?{urllib.parse.urlencode(parametros)}" if parametros else ""
        return f"{URL_GRAPH}/{self.guardado('versao_da_api') or VERSAO_DA_API}{caminho}{consulta}"

    def _graph(
        self, metodo: str, caminho: str, contexto: str, *, formulario: dict[str, Any] | None = None,
        parametros: dict[str, Any] | None = None,
    ) -> Any:  # fmt: skip
        token = self.guardado("page_token")
        if not token:
            raise FaltaConfigurar("A Meta ainda não foi conectada (aba Integrações).")
        if metodo == "GET":
            url = self._url(caminho, {**(parametros or {}), "access_token": token})
            formulario = None
        else:
            url = self._url(caminho)
            formulario = {**(formulario or {}), "access_token": token}
        try:
            return com_tentativas(
                lambda: pedir_json(self.transporte, metodo, url, contexto, formulario=formulario)
            )
        except ErroDeRede as erro:
            if '"code":190' in erro.corpo.replace(" ", "") or erro.codigo == 401:
                raise ReconectarNecessario(
                    "A Meta não aceita mais o token da Página (senha trocada, função removida ou "
                    "permissão revogada). Gere um token novo no Explorador da Graph API e conecte "
                    "de novo na aba Integrações."
                ) from erro
            raise

    def _enviar_arquivo(self, url: str, pacote: Pacote) -> None:
        """O upload resumável da Meta: um POST com o arquivo inteiro, `offset` 0."""
        exigir_ok(
            self.transporte.pedir(
                "POST", url,
                cabecalhos={
                    "Authorization": f"OAuth {self.guardado('page_token')}",
                    "offset": "0",
                    "file_size": str(pacote.tamanho),
                    "Content-Type": "application/octet-stream",
                },
                corpo=pacote.video.read_bytes(),
                timeout=TEMPO_LIMITE_DO_ENVIO_S,
            ),
            "Enviar o vídeo à Meta",
        )  # fmt: skip


class Instagram(_Meta):
    chave = "instagram"
    nome = "Instagram Reels"
    agenda_na_rede = False  # a API da Meta não agenda: o Spurgeon guarda o horário

    @property
    def conectada(self) -> bool:
        return super().conectada and bool(self.guardado("ig_id"))

    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        achados = []
        if not super().conectada:
            achados.append("A Meta não está conectada (aba Integrações).")
        elif not self.guardado("ig_id"):
            achados.append(
                "A Página conectada não tem uma conta profissional do Instagram ligada a ela."
            )
        if not pacote.video.is_file():
            achados.append(f"O arquivo do Short não existe: {pacote.video.name}.")
            return achados
        if pacote.tamanho > INSTAGRAM_LIMITE_DO_ARQUIVO:
            achados.append("O vídeo passa de 300 MB, o limite do Instagram.")
        if not INSTAGRAM_DURACAO_MIN_S <= pacote.duracao_s <= INSTAGRAM_DURACAO_MAX_S:
            achados.append("O Reel do Instagram precisa ter de 3 segundos a 15 minutos.")
        if quando is not None and quando.astimezone() <= self.relogio().astimezone():
            achados.append("O horário do Instagram já passou.")
        return achados

    def publicar(
        self,
        pacote: Pacote,
        quando: datetime | None,
        *,
        ao_progresso: Progresso | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> Resultado:
        achados = self.problemas(pacote, quando)
        if achados:
            raise ErroDePublicacao(" ".join(achados))
        if quando is not None:  # fica na fila do Spurgeon até chegar a hora de preparar
            return Resultado(
                self.chave, NA_FILA, quando=quando.astimezone().isoformat(),
                detalhe="O Instagram não agenda pela API: o Spurgeon publica na hora marcada "
                "(deixe o aplicativo aberto).",
                dados={"fase": "aguardando"},
            )  # fmt: skip
        conteiner = self._preparar(pacote, ao_progresso, cancelado)
        return self._publicar_conteiner(conteiner)

    def retomar(
        self,
        resultado: Resultado,
        pacote: Pacote,
        *,
        ao_progresso: Progresso | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> Resultado:
        if resultado.estado != NA_FILA or not resultado.quando:
            return resultado
        agora = self.relogio().astimezone()
        hora = datetime.fromisoformat(resultado.quando).astimezone()
        fase = resultado.dados.get("fase", "aguardando")
        if fase == "pronto":
            preparado = datetime.fromisoformat(resultado.dados["preparado_em"])
            if agora - preparado > VALIDADE_DO_CONTEINER:
                fase = "aguardando"  # o contêiner venceu: refaz tudo
        if fase == "aguardando" and agora >= hora - PREPARO_ANTES_DO_HORARIO:
            conteiner = self._preparar(pacote, ao_progresso, cancelado)
            resultado.dados = {
                "fase": "pronto", "conteiner": conteiner, "preparado_em": agora.isoformat(),
            }  # fmt: skip
            fase = "pronto"
        if fase == "pronto" and agora >= hora:
            publicado = self._publicar_conteiner(resultado.dados["conteiner"])
            if agora - hora > timedelta(minutes=10):
                publicado.aviso = (
                    "Publicado com atraso: o aplicativo estava fechado na hora marcada."
                )
            return publicado
        return resultado

    # --- os passos --------------------------------------------------------------------------------
    def _preparar(
        self, pacote: Pacote, ao_progresso: Progresso | None, cancelado: PedidoDeCancelar | None
    ) -> str:
        """Cria o contêiner, envia o vídeo e espera o Instagram terminar de processá-lo."""
        ig = self.guardado("ig_id")
        criado = self._graph(
            "POST", f"/{ig}/media", "Criar o Reel no Instagram",
            formulario={
                "media_type": "REELS", "upload_type": "resumable",
                "caption": legenda_do_instagram(pacote), "share_to_feed": True,
            },
        )  # fmt: skip
        conteiner = str(criado.get("id", ""))
        if not conteiner:
            raise ErroDePublicacao("O Instagram não devolveu o número do Reel.")
        destino = criado.get("uri") or (
            f"{URL_UPLOAD}/ig-api-upload/"
            f"{self.guardado('versao_da_api') or VERSAO_DA_API}/{conteiner}"
        )
        if ao_progresso:
            ao_progresso(0.05)
        self._enviar_arquivo(destino, pacote)
        if ao_progresso:
            ao_progresso(0.5)
        self._esperar_processar(conteiner, cancelado)
        if ao_progresso:
            ao_progresso(0.9)
        return conteiner

    def _esperar_processar(self, conteiner: str, cancelado: PedidoDeCancelar | None) -> None:
        limite = time.monotonic() + TEMPO_DO_PROCESSAMENTO_S
        while True:
            if cancelado is not None and cancelado():
                raise CanceladoErro("Envio cancelado.")
            dados = self._graph(
                "GET", f"/{conteiner}", "Conferir o processamento no Instagram",
                parametros={"fields": "status_code,status"},
            )  # fmt: skip
            codigo = dados.get("status_code", "")
            if codigo in ("FINISHED", "PUBLISHED"):
                return
            if codigo in ("ERROR", "EXPIRED"):
                raise ErroDePublicacao(
                    f"O Instagram recusou o vídeo ({dados.get('status') or codigo}). Confira a "
                    "duração (3 s a 15 min), o tamanho (até 300 MB) e a taxa de quadros (23 a 60)."
                )
            if time.monotonic() > limite:
                raise ErroDePublicacao("O Instagram demorou demais para processar o vídeo.")
            time.sleep(ESPERA_DO_PROCESSAMENTO_S)

    def _publicar_conteiner(self, conteiner: str) -> Resultado:
        ig = self.guardado("ig_id")
        publicado = self._graph(
            "POST", f"/{ig}/media_publish", "Publicar o Reel no Instagram",
            formulario={"creation_id": conteiner},
        )  # fmt: skip
        id_da_midia = str(publicado.get("id", ""))
        endereco = ""
        try:
            achado = self._graph(
                "GET", f"/{id_da_midia}", "Ler o endereço do Reel",
                parametros={"fields": "permalink"},
            )  # fmt: skip
            endereco = str(achado.get("permalink", ""))
        except ErroDePublicacao:
            pass  # publicou; só não deu para pegar o endereço
        return Resultado(self.chave, ENVIADO, id_da_midia, endereco)


class Facebook(_Meta):
    chave = "facebook"
    nome = "Facebook Reels"
    agenda_na_rede = True

    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        achados = []
        if not super().conectada:
            achados.append("A Meta não está conectada (aba Integrações).")
        if not pacote.video.is_file():
            achados.append(f"O arquivo do Short não existe: {pacote.video.name}.")
        if not FACEBOOK_DURACAO_MIN_S <= pacote.duracao_s <= FACEBOOK_DURACAO_MAX_S:
            achados.append(
                "O Reel de Página do Facebook aceita de 3 a 90 segundos (este Short tem "
                f"{pacote.duracao_s:.0f} s)."
            )
        if quando is not None:
            agora = self.relogio().astimezone()
            hora = quando.astimezone()
            if hora < agora + FACEBOOK_ANTECEDENCIA_MIN:
                achados.append("O Facebook exige o horário a mais de 10 minutos de agora.")
            elif hora > agora + FACEBOOK_ANTECEDENCIA_MAX:
                achados.append("O Facebook agenda no máximo 29 dias à frente.")
        return achados

    def publicar(
        self,
        pacote: Pacote,
        quando: datetime | None,
        *,
        ao_progresso: Progresso | None = None,
        cancelado: PedidoDeCancelar | None = None,
    ) -> Resultado:
        achados = self.problemas(pacote, quando)
        if achados:
            raise ErroDePublicacao(" ".join(achados))
        pagina = self.guardado("page_id")
        inicio = self._graph(
            "POST", f"/{pagina}/video_reels", "Começar o Reel no Facebook",
            formulario={"upload_phase": "start"},
        )  # fmt: skip
        id_do_video = str(inicio.get("video_id", ""))
        destino = inicio.get("upload_url") or f"{URL_UPLOAD}/video-upload/{id_do_video}"
        if not id_do_video:
            raise ErroDePublicacao("O Facebook não devolveu o número do vídeo.")
        if ao_progresso:
            ao_progresso(0.05)
        self._enviar_arquivo(destino, pacote)
        if ao_progresso:
            ao_progresso(0.6)
        self._esperar_envio(id_do_video, cancelado)
        formulario: dict[str, Any] = {
            "upload_phase": "finish", "video_id": id_do_video,
            "video_state": "PUBLISHED" if quando is None else "SCHEDULED",
            "description": pacote.legenda(limite=INSTAGRAM_LIMITE_DA_LEGENDA),
            "title": pacote.titulo,
        }  # fmt: skip
        if quando is not None:
            formulario["scheduled_publish_time"] = int(quando.timestamp())
        self._graph(
            "POST", f"/{pagina}/video_reels", "Concluir o Reel no Facebook", formulario=formulario
        )
        if ao_progresso:
            ao_progresso(1.0)
        return Resultado(
            self.chave, ENVIADO if quando is None else AGENDADO, id_do_video,
            f"https://www.facebook.com/reel/{id_do_video}",
            quando.astimezone().isoformat() if quando else "",
        )  # fmt: skip

    def _esperar_envio(self, id_do_video: str, cancelado: PedidoDeCancelar | None) -> None:
        """Espera a fase de envio ficar completa. Se a Meta não informar, segue (best effort)."""
        limite = time.monotonic() + 5 * 60.0
        while time.monotonic() < limite:
            if cancelado is not None and cancelado():
                raise CanceladoErro("Envio cancelado.")
            dados = self._graph(
                "GET", f"/{id_do_video}", "Conferir o envio no Facebook",
                parametros={"fields": "status"},
            )  # fmt: skip
            fase = (dados.get("status") or {}).get("uploading_phase") or {}
            situacao = fase.get("status", "")
            if situacao in ("complete", "completed", ""):
                return
            if situacao == "error":
                raise ErroDePublicacao("O Facebook recusou o vídeo (envio com erro).")
            time.sleep(5.0)

    def consultar(self, resultado: Resultado) -> Resultado:
        if not resultado.id_remoto:
            return resultado
        dados = self._graph(
            "GET", f"/{resultado.id_remoto}", "Conferir o Reel no Facebook",
            parametros={"fields": "status"},
        )  # fmt: skip
        fase = (dados.get("status") or {}).get("publishing_phase") or {}
        if fase.get("publish_status") == "published" and resultado.estado == AGENDADO:
            resultado.estado = ENVIADO
        return resultado


def legenda_do_instagram(pacote: Pacote) -> str:
    """Título, descrição e até 30 hashtags, dentro de 2.200 caracteres."""
    enxuto = replace(pacote, hashtags=tuple(pacote.etiquetas[:INSTAGRAM_LIMITE_DE_HASHTAGS]))
    return enxuto.legenda(limite=INSTAGRAM_LIMITE_DA_LEGENDA)


def _escolher_pagina(paginas: list[dict[str, Any]], pedida: str) -> dict[str, Any]:
    if not paginas:
        raise ErroDePublicacao(
            "O token não enxerga nenhuma Página. Gere-o de novo no Explorador da Graph API, "
            "marcando pages_show_list, e escolha a Página da igreja na janela de permissões."
        )
    if pedida:
        busca = pedida.strip().lower()
        for pagina in paginas:
            if busca in (str(pagina.get("id", "")), str(pagina.get("name", "")).lower()):
                return pagina
        nomes = ", ".join(str(p.get("name", "")) for p in paginas)
        raise ErroDePublicacao(f"Não achei a Página “{pedida}”. O token enxerga: {nomes}.")
    if len(paginas) > 1:
        nomes = ", ".join(f"“{p.get('name', '')}”" for p in paginas)
        raise ErroDePublicacao(
            f"O token enxerga mais de uma Página ({nomes}). Escreva o nome da Página da igreja no "
            "campo “Página” e conecte de novo."
        )
    return paginas[0]
