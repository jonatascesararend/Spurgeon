"""YouTube Shorts pela YouTube Data API v3.

Cada igreja cria o PRÓPRIO projeto no Google Cloud (credencial "Aplicativo para computador") e
cola o
ID e a chave dele na aba Integrações. O agendamento é da própria rede: o vídeo sobe como privado com
`status.publishAt` e o YouTube o publica sozinho, mesmo com o computador desligado.

Atenção (conferida na documentação em 02/11/2026, mas contraditória): projetos de API novos e não
verificados podem ter os envios travados como privados. Por isso, depois de enviar, o Spurgeon lê o
status de volta e avisa se a rede não fez o que foi pedido.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from spurgeon.core.erros import Cancelado as CanceladoErro
from spurgeon.publicacao import oauth
from spurgeon.publicacao.erros import (
    ErroDePublicacao,
    ErroDeRede,
    FaltaConfigurar,
    ReconectarNecessario,
)
from spurgeon.publicacao.modelo import (
    AGENDADO,
    ENVIADO,
    Campo,
    Pacote,
    PedidoDeCancelar,
    Plataforma,
    Progresso,
    Resultado,
    iso_utc,
)
from spurgeon.publicacao.rede import (
    TEMPO_LIMITE_DO_ENVIO_S,
    codificar_formulario,
    com_tentativas,
    exigir_ok,
    ler_pedaco,
    pedir_json,
)

URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_API = "https://www.googleapis.com/youtube/v3"
URL_ENVIO = "https://www.googleapis.com/upload/youtube/v3/videos"
#: `youtube` (e não só `youtube.upload`): reagendar e apagar (`videos.update`/`delete`) o exigem
ESCOPO = "https://www.googleapis.com/auth/youtube"
PEDACO_DO_ENVIO = 8 * 1024 * 1024  # múltiplo de 256 KB, como o YouTube pede
CATEGORIA_PADRAO = "22"  # "Pessoas e blogs": o YouTube não tem categoria de religião
LIMITE_DO_TITULO = 100
LIMITE_DA_DESCRICAO_BYTES = 5000
LIMITE_DAS_TAGS = 500
DURACAO_MAXIMA_DO_SHORT_S = 180.5  # 3 minutos, com a folga de arredondamento do último quadro
ANTECEDENCIA_MINIMA = timedelta(minutes=1)
_FALHAS_POR_PEDACO = 3
_ESPERA_ENTRE_FALHAS_S = 3.0


def titulo_do_youtube(titulo: str) -> str:
    """Até 100 caracteres e sem `<` nem `>` (o YouTube recusa)."""
    limpo = " ".join(titulo.replace("<", "").replace(">", "").split())
    if len(limpo) <= LIMITE_DO_TITULO:
        return limpo
    corte = limpo[: LIMITE_DO_TITULO - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return (corte or limpo[: LIMITE_DO_TITULO - 1]) + "…"


def descricao_do_youtube(pacote: Pacote) -> str:
    """A descrição com as hashtags, dentro dos 5.000 BYTES (acento em pt-BR conta 2)."""
    texto = pacote.legenda(com_titulo=False).replace("<", "").replace(">", "")
    limite = LIMITE_DA_DESCRICAO_BYTES - 100
    while len(texto.encode("utf-8")) > limite:
        texto = texto[: int(len(texto) * 0.9)]
    return texto


def tags_do_youtube(pacote: Pacote) -> list[str]:
    """As hashtags sem `#`, até 500 caracteres no total (a vírgula de cada uma também conta)."""
    tags: list[str] = []
    total = 0
    for etiqueta in pacote.etiquetas:
        nome = etiqueta.lstrip("#")
        custo = len(nome) + 1
        if total + custo > LIMITE_DAS_TAGS:
            break
        tags.append(nome)
        total += custo
    return tags


class YouTube(Plataforma):
    chave = "youtube"
    nome = "YouTube Shorts"
    agenda_na_rede = True
    campos = (
        Campo(
            "client_id", "ID do cliente",
            ajuda="Google Cloud → APIs e serviços → Credenciais → ID do cliente OAuth (tipo "
            "“Aplicativo para computador”).",
        ),
        Campo("client_secret", "Chave secreta do cliente", segredo=True),
    )  # fmt: skip
    como_conectar = (
        "1. Em console.cloud.google.com crie um projeto e ative a “YouTube Data API v3”.\n"
        "2. Em “Tela de consentimento OAuth”, escolha “Externo”, preencha o nome e o e-mail e "
        "adicione o escopo …/auth/youtube. Clique em “Publicar app” (no modo “Teste” o Google "
        "derruba o login a cada 7 dias).\n"
        "3. Em “Credenciais”, crie um “ID do cliente OAuth” do tipo “Aplicativo para computador”.\n"
        "4. Cole o ID e a chave secreta aqui, clique em Conectar e entre com a conta dona do "
        "canal. "
        "O Google avisará que o app “não foi verificado”: use “Avançado → Acessar”."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._acesso = ""
        self._acesso_vence = 0.0

    @property
    def conectada(self) -> bool:
        return self.cofre.tem(self.conta, "refresh_token")

    # --- conectar --------------------------------------------------------------------------------
    def conectar(self, *, abrir=None, cancelado: PedidoDeCancelar | None = None) -> str:
        cliente = self.guardado("client_id")
        segredo = self.guardado("client_secret")
        if not cliente or not segredo:
            raise FaltaConfigurar("Preencha o ID do cliente e a chave secreta antes de conectar.")
        verificador, desafio = oauth.gerar_pkce()

        def montar_url(redirect_uri: str, estado: str) -> str:
            return URL_AUTORIZACAO + "?" + urllib.parse.urlencode(
                {
                    "client_id": cliente, "redirect_uri": redirect_uri, "response_type": "code",
                    "scope": ESCOPO, "code_challenge": desafio, "code_challenge_method": "S256",
                    "access_type": "offline", "prompt": "consent", "state": estado,
                }
            )  # fmt: skip

        pedido = {"abrir": abrir} if abrir is not None else {}
        auth = oauth.aguardar_codigo(montar_url, cancelado=cancelado, **pedido)
        dados = pedir_json(
            self.transporte, "POST", URL_TOKEN, "Trocar o código do Google por um acesso",
            formulario={
                "client_id": cliente, "client_secret": segredo, "code": auth.codigo,
                "code_verifier": verificador, "redirect_uri": auth.redirect_uri,
                "grant_type": "authorization_code",
            },
        )  # fmt: skip
        if not dados.get("refresh_token"):
            raise ErroDePublicacao(
                "O Google não devolveu o acesso permanente. Abra myaccount.google.com/permissions, "
                "remova o “Studio Spurgeon” e conecte de novo."
            )
        self.cofre.definir(self.conta, "refresh_token", dados["refresh_token"], segredo=True)
        self._guardar_acesso(dados)
        canal = self._canal()
        self.cofre.definir(self.conta, "conta_nome", canal)
        return canal

    def _canal(self) -> str:
        dados = self._api("GET", "/channels?part=snippet&mine=true", "Ler o canal")
        itens = dados.get("items") or []
        if not itens:
            raise ErroDePublicacao(
                "Esta conta do Google não tem um canal do YouTube. Entre com a conta do canal."
            )
        return str(itens[0].get("snippet", {}).get("title") or "canal sem nome")

    # --- acesso ----------------------------------------------------------------------------------
    def _guardar_acesso(self, dados: dict[str, Any]) -> None:
        self._acesso = str(dados.get("access_token", ""))
        self._acesso_vence = time.monotonic() + max(60.0, float(dados.get("expires_in", 3600)) - 60)

    def _token(self, *, renovar: bool = False) -> str:
        if self._acesso and not renovar and time.monotonic() < self._acesso_vence:
            return self._acesso
        refresh = self.guardado("refresh_token")
        if not refresh:
            raise FaltaConfigurar("O YouTube ainda não foi conectado (aba Integrações).")
        resposta = self.transporte.pedir(
            "POST", URL_TOKEN,
            cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
            corpo=codificar_formulario({
                "client_id": self.guardado("client_id"),
                "client_secret": self.guardado("client_secret"),
                "refresh_token": refresh, "grant_type": "refresh_token",
            }),
        )  # fmt: skip
        if resposta.codigo in (400, 401) and "invalid_grant" in resposta.texto:
            raise ReconectarNecessario(
                "O Google não aceita mais o acesso guardado (no modo “Teste” ele vale 7 dias, e "
                "também cai se foi revogado). Conecte o YouTube de novo na aba Integrações."
            )
        self._guardar_acesso(exigir_ok(resposta, "Renovar o acesso ao YouTube").json())
        return self._acesso

    def _api(self, metodo: str, caminho: str, contexto: str, *, json_corpo: Any = None) -> Any:
        def pedir() -> Any:
            return pedir_json(
                self.transporte, metodo, URL_API + caminho, contexto, json_corpo=json_corpo,
                cabecalhos={"Authorization": f"Bearer {self._token()}"},
            )  # fmt: skip

        try:
            return com_tentativas(pedir)
        except ErroDeRede as erro:
            if erro.codigo != 401:
                raise
            self._token(renovar=True)
            return com_tentativas(pedir)

    # --- conferir --------------------------------------------------------------------------------
    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        achados = []
        if not self.conectada:
            achados.append("O YouTube não está conectado (aba Integrações).")
        if not pacote.video.is_file():
            achados.append(f"O arquivo do Short não existe: {pacote.video.name}.")
        if not titulo_do_youtube(pacote.titulo):
            achados.append("O Short está sem título.")
        if pacote.duracao_s > DURACAO_MAXIMA_DO_SHORT_S:
            achados.append("Com mais de 3 minutos o YouTube não trata o vídeo como Short.")
        if quando is not None and em_breve(quando, self.relogio()):
            achados.append("O horário do YouTube precisa estar pelo menos 1 minuto à frente.")
        return achados

    # --- publicar --------------------------------------------------------------------------------
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
        corpo = {
            "snippet": {
                "title": titulo_do_youtube(pacote.titulo),
                "description": descricao_do_youtube(pacote),
                "tags": tags_do_youtube(pacote),
                "categoryId": self.guardado("categoria") or CATEGORIA_PADRAO,
            },
            "status": self._status(quando),
        }
        sessao = self._abrir_sessao(corpo, pacote)
        video = self._enviar(sessao, pacote.video, ao_progresso, cancelado)
        id_do_video = str(video.get("id", ""))
        if not id_do_video:
            raise ErroDePublicacao("O YouTube recebeu o vídeo mas não devolveu o número dele.")
        return self._conferir(id_do_video, quando)

    @staticmethod
    def _status(quando: datetime | None) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": "public" if quando is None else "private",
            "selfDeclaredMadeForKids": False,  # sermão: não é conteúdo infantil
        }
        if quando is not None:
            status["publishAt"] = iso_utc(quando)
        return status

    def _abrir_sessao(self, corpo: dict[str, Any], pacote: Pacote) -> str:
        url = URL_ENVIO + "?uploadType=resumable&part=snippet,status"

        def abrir() -> str:
            resposta = exigir_ok(
                self.transporte.pedir(
                    "POST", url,
                    cabecalhos={
                        "Authorization": f"Bearer {self._token()}",
                        "Content-Type": "application/json; charset=UTF-8",
                        "X-Upload-Content-Length": str(pacote.tamanho),
                        "X-Upload-Content-Type": "video/mp4",
                    },
                    corpo=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
                ),
                "Abrir o envio no YouTube",
            )  # fmt: skip
            local = resposta.cabecalhos.get("location", "")
            if not local:
                raise ErroDePublicacao("O YouTube não devolveu o endereço do envio.")
            return local

        return com_tentativas(abrir)

    def _enviar(
        self,
        sessao: str,
        caminho: Path,
        ao_progresso: Progresso | None,
        cancelado: PedidoDeCancelar | None,
    ) -> dict[str, Any]:
        tamanho = caminho.stat().st_size
        inicio = 0
        falhas = 0
        while True:
            if cancelado is not None and cancelado():
                raise CanceladoErro("Envio cancelado.")
            fim = min(inicio + PEDACO_DO_ENVIO, tamanho) - 1
            try:
                resposta = self.transporte.pedir(
                    "PUT", sessao,
                    cabecalhos={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {inicio}-{fim}/{tamanho}",
                    },
                    corpo=ler_pedaco(caminho, inicio, fim - inicio + 1),
                    timeout=TEMPO_LIMITE_DO_ENVIO_S,
                )  # fmt: skip
                if resposta.codigo in (200, 201):
                    if ao_progresso:
                        ao_progresso(1.0)
                    return resposta.json()
                if resposta.codigo != 308:
                    raise ErroDeRede(
                        f"Enviar o vídeo ao YouTube: código {resposta.codigo}.",
                        codigo=resposta.codigo, corpo=resposta.texto[:500],
                    )  # fmt: skip
                inicio = _proximo_byte(resposta.cabecalhos.get("range", ""))
                falhas = 0
            except ErroDeRede as erro:
                falhas += 1
                if not erro.passageiro or falhas > _FALHAS_POR_PEDACO:
                    raise
                time.sleep(_ESPERA_ENTRE_FALHAS_S)
                achado = self._onde_parou(sessao, tamanho)
                if isinstance(achado, dict):
                    return achado
                inicio = achado
            if ao_progresso:
                ao_progresso(min(0.99, inicio / tamanho))

    def _onde_parou(self, sessao: str, tamanho: int) -> int | dict[str, Any]:
        """Pergunta ao YouTube até onde o envio chegou (ou devolve o vídeo, se já terminou)."""
        resposta = self.transporte.pedir(
            "PUT", sessao, cabecalhos={"Content-Range": f"bytes */{tamanho}"}, corpo=b""
        )
        if resposta.codigo in (200, 201):
            return resposta.json()
        if resposta.codigo == 308:
            return _proximo_byte(resposta.cabecalhos.get("range", ""))
        raise ErroDeRede(
            f"Retomar o envio ao YouTube: código {resposta.codigo}.", codigo=resposta.codigo
        )

    def _conferir(self, id_do_video: str, quando: datetime | None) -> Resultado:
        """Lê o status de volta: o YouTube pode ter deixado o vídeo privado (projeto novo)."""
        resultado = Resultado(
            self.chave, ENVIADO if quando is None else AGENDADO, id_do_video,
            f"https://www.youtube.com/shorts/{id_do_video}",
            iso_utc(quando) if quando else "",
        )  # fmt: skip
        try:
            dados = self._api(
                "GET",
                f"/videos?part=status&id={urllib.parse.quote(id_do_video)}",
                "Conferir o vídeo",
            )
            status = (dados.get("items") or [{}])[0].get("status", {})
        except ErroDePublicacao:
            return resultado  # o vídeo subiu; só não deu para conferir agora
        privacidade = status.get("privacyStatus", "")
        if quando is None and privacidade and privacidade != "public":
            resultado.aviso = (
                f"O vídeo subiu, mas o YouTube o deixou {_privacidade(privacidade)}. "
                "Projetos de API "
                "novos e sem verificação podem ter os envios travados como privados: veja "
                "“Integrações → YouTube”. Publique-o pelo YouTube Studio."
            )
        if quando is not None and not status.get("publishAt"):
            resultado.aviso = (
                "O vídeo subiu, mas o YouTube não registrou o horário marcado. Confira no YouTube "
                "Studio (a trava de projetos novos pode impedir a publicação automática)."
            )
        return resultado

    # --- depois de enviado ------------------------------------------------------------------------
    def consultar(self, resultado: Resultado) -> Resultado:
        """Agendado que já foi ao ar vira publicado."""
        if not resultado.id_remoto:
            return resultado
        dados = self._api(
            "GET", f"/videos?part=status&id={urllib.parse.quote(resultado.id_remoto)}",
            "Conferir o vídeo",
        )  # fmt: skip
        itens = dados.get("items") or []
        if not itens:
            resultado.aviso = "O vídeo não existe mais no canal."
            return resultado
        if (
            itens[0].get("status", {}).get("privacyStatus") == "public"
            and resultado.estado == AGENDADO
        ):
            resultado.estado = ENVIADO
            resultado.aviso = ""
        return resultado

    def reagendar(self, resultado: Resultado, quando: datetime) -> Resultado:
        """Muda o horário de um vídeo ainda privado (`videos.update`: reenvia o status inteiro)."""
        if em_breve(quando, self.relogio()):
            raise ErroDePublicacao("O horário precisa estar pelo menos 1 minuto à frente.")
        self._api(
            "PUT", "/videos?part=status", "Reagendar o vídeo",
            json_corpo={"id": resultado.id_remoto, "status": self._status(quando)},
        )  # fmt: skip
        resultado.estado = AGENDADO
        resultado.quando = iso_utc(quando)
        return resultado


def em_breve(quando: datetime, agora: datetime) -> bool:
    """O horário já passou ou está a menos de 1 minuto (o YouTube publicaria na hora)."""
    return quando.astimezone() < agora.astimezone() + ANTECEDENCIA_MINIMA


def _proximo_byte(faixa: str) -> int:
    """`bytes=0-8388607` → 8388608 (a partir de onde reenviar); sem faixa, do começo."""
    if "-" not in faixa:
        return 0
    return int(faixa.rsplit("-", 1)[1]) + 1


def _privacidade(valor: str) -> str:
    return {"private": "privado", "unlisted": "não listado"}.get(valor, valor)
