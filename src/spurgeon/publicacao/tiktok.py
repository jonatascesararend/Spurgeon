"""TikTok: o fluxo assistido (sempre disponível) e o envio para os rascunhos pela API (opcional).

O que a documentação do TikTok (consultada em 02/11/2026) permite a uma igreja com conta PRÓPRIA:

- Publicação direta (`video.publish`): exige a auditoria do TikTok, que recusa "ferramenta para
  subir
  conteúdo para as contas que você ou sua equipe gerencia". Sem auditoria só publica com
  visibilidade
  privada, e o sandbox nunca publica em público. Por isso o Spurgeon NÃO usa a publicação direta.
- Rascunho (`video.upload`): o vídeo vai para a caixa de entrada do TikTok e a pessoa toca na
  notificação, no celular, para concluir e postar. Esse caminho exige as chaves de um app (modo
  sandbox, com a conta da igreja como usuário de teste) e NÃO foi confirmado com conta pública.
- Agendar: a API não tem. O agendamento nativo existe no TikTok Studio (web), à mão.

Então o padrão é o fluxo ASSISTIDO: o Spurgeon deixa a legenda pronta, abre a pasta do arquivo e o
TikTok Studio, e a pessoa envia e agenda lá. Quem tiver as chaves também pode mandar para os
rascunhos.
"""

from __future__ import annotations

import time
import urllib.parse
from datetime import datetime
from math import ceil
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
    ASSISTIDO,
    RASCUNHO,
    Campo,
    Pacote,
    PedidoDeCancelar,
    Plataforma,
    Progresso,
    Resultado,
)
from spurgeon.publicacao.rede import (
    TEMPO_LIMITE_DO_ENVIO_S,
    codificar_formulario,
    com_tentativas,
    exigir_ok,
    ler_pedaco,
    pedir_json,
)

URL_AUTORIZACAO = "https://www.tiktok.com/v2/auth/authorize/"
URL_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
URL_API = "https://open.tiktokapis.com/v2"
URL_DO_ESTUDIO = "https://www.tiktok.com/tiktokstudio/upload"
ESCOPOS = "user.info.basic,video.upload"
#: o TikTok exige o endereço de retorno cadastrado, com porta e barra final, sem parâmetros
PORTA_DE_RETORNO = 53682
CAMINHO_DE_RETORNO = "/callback/"
ENDERECO_DE_RETORNO = f"http://127.0.0.1:{PORTA_DE_RETORNO}{CAMINHO_DE_RETORNO}"
LIMITE_DA_LEGENDA = 2200
MAIOR_PEDACO = 64 * 1024 * 1024
DURACAO_MAXIMA_S = 600.0  # 10 min pela API (o criador pode ter 3 ou 5)
ESPERA_DO_STATUS_S = 4.0
TEMPO_DO_STATUS_S = 5 * 60.0


def plano_dos_pedacos(tamanho: int) -> tuple[int, int]:
    """`(chunk_size, total_chunk_count)` do TikTok: pedaços de até 64 MB, o último pode ser maior
    (até 128 MB) e `total = tamanho // chunk_size`. Até 64 MB vai tudo num pedaço só."""
    quantos = max(1, ceil(tamanho / MAIOR_PEDACO))
    return tamanho // quantos, quantos


class TikTok(Plataforma):
    chave = "tiktok"
    nome = "TikTok"
    agenda_na_rede = False
    publica_pela_api = False
    campos = (
        Campo(
            "client_key", "Chave do cliente (Client key)", opcional=True,
            ajuda="Só para enviar aos rascunhos pela API. Sem isto vale o fluxo assistido.",
        ),
        Campo("client_secret", "Chave secreta (Client secret)", segredo=True, opcional=True),
    )  # fmt: skip
    como_conectar = (
        "Sem configurar nada: o Spurgeon copia a legenda, abre a pasta do vídeo e o TikTok "
        "Studio, e "
        "você envia o arquivo e escolhe “Agendar” lá (o TikTok não deixa programas agendarem).\n\n"
        "Para enviar o vídeo direto aos rascunhos do TikTok (opcional, avançado):\n"
        "1. Em developers.tiktok.com crie um app com os produtos “Login Kit” e "
        "“Content Posting API”, "
        "plataforma “Desktop”.\n"
        f"2. Em “Redirect URI” cadastre exatamente {ENDERECO_DE_RETORNO}\n"
        "3. Crie um “Sandbox” e adicione a conta do TikTok da igreja como usuário de teste.\n"
        "4. Cole aqui a chave e a chave secreta do sandbox e clique em Conectar. Depois de enviar, "
        "abra o TikTok no celular e toque na notificação para concluir a postagem.\n"
        "A publicação direta (sem tocar no celular) exige a auditoria do TikTok, que não "
        "aceita apps "
        "só para a própria conta."
    )

    @property
    def conectada(self) -> bool:
        return self.cofre.tem(self.conta, "refresh_token")

    @property
    def disponivel(self) -> bool:
        return True  # o fluxo assistido não precisa de conta

    @property
    def resumo_da_conta(self) -> str:
        return self.guardado("conta_nome") or "fluxo assistido (sem chaves)"

    # --- conectar --------------------------------------------------------------------------------
    def conectar(self, *, abrir=None, cancelado: PedidoDeCancelar | None = None) -> str:
        chave, segredo = self.guardado("client_key"), self.guardado("client_secret")
        if not chave or not segredo:
            raise FaltaConfigurar("Preencha a chave e a chave secreta do app antes de conectar.")
        verificador, desafio = oauth.gerar_pkce("hex")

        def montar_url(_redirect_uri: str, estado: str) -> str:
            return URL_AUTORIZACAO + "?" + urllib.parse.urlencode(
                {
                    "client_key": chave, "scope": ESCOPOS, "response_type": "code",
                    "redirect_uri": ENDERECO_DE_RETORNO, "state": estado,
                    "code_challenge": desafio, "code_challenge_method": "S256",
                }
            )  # fmt: skip

        pedido = {"abrir": abrir} if abrir is not None else {}
        auth = oauth.aguardar_codigo(
            montar_url, porta=PORTA_DE_RETORNO, caminho=CAMINHO_DE_RETORNO, cancelado=cancelado,
            **pedido,
        )  # fmt: skip
        dados = pedir_json(
            self.transporte, "POST", URL_TOKEN, "Trocar o código do TikTok por um acesso",
            formulario={
                "client_key": chave, "client_secret": segredo, "code": auth.codigo,
                "grant_type": "authorization_code", "redirect_uri": ENDERECO_DE_RETORNO,
                "code_verifier": verificador,
            },
        )  # fmt: skip
        self._guardar_tokens(dados)
        nome = self._nome_do_usuario()
        self.cofre.definir(self.conta, "conta_nome", nome)
        return nome

    def _guardar_tokens(self, dados: dict[str, Any]) -> None:
        if not dados.get("access_token"):
            raise ErroDePublicacao(
                f"O TikTok não devolveu o acesso: {dados.get('error_description') or dados}"
            )
        self.cofre.definir(self.conta, "access_token", dados["access_token"], segredo=True)
        if dados.get("refresh_token"):  # o TikTok troca o refresh token a cada renovação
            self.cofre.definir(self.conta, "refresh_token", dados["refresh_token"], segredo=True)
        validade = float(dados.get("expires_in", 7200))
        self.cofre.definir(self.conta, "access_vence", str(time.time() + validade - 60))

    def _nome_do_usuario(self) -> str:
        dados = self._api("GET", "/user/info/?fields=display_name", "Ler o perfil do TikTok")
        usuario = (dados.get("data") or {}).get("user") or {}
        return str(usuario.get("display_name") or "conta do TikTok")

    # --- acesso ----------------------------------------------------------------------------------
    def _token(self) -> str:
        vence = float(self.guardado("access_vence") or 0)
        if self.guardado("access_token") and time.time() < vence:
            return self.guardado("access_token")
        refresh = self.guardado("refresh_token")
        if not refresh:
            raise FaltaConfigurar("O TikTok ainda não foi conectado (aba Integrações).")
        resposta = self.transporte.pedir(
            "POST", URL_TOKEN,
            cabecalhos={"Content-Type": "application/x-www-form-urlencoded"},
            corpo=codificar_formulario({
                "client_key": self.guardado("client_key"),
                "client_secret": self.guardado("client_secret"),
                "grant_type": "refresh_token", "refresh_token": refresh,
            }),
        )  # fmt: skip
        if "invalid_grant" in resposta.texto or "refresh_token_expired" in resposta.texto:
            raise ReconectarNecessario(
                "O TikTok não aceita mais o acesso guardado. Conecte de novo na aba Integrações."
            )
        self._guardar_tokens(exigir_ok(resposta, "Renovar o acesso ao TikTok").json())
        return self.guardado("access_token")

    def _api(self, metodo: str, caminho: str, contexto: str, *, json_corpo: Any = None) -> Any:
        dados = com_tentativas(
            lambda: pedir_json(
                self.transporte, metodo, URL_API + caminho, contexto, json_corpo=json_corpo,
                cabecalhos={"Authorization": f"Bearer {self._token()}"},
            )
        )  # fmt: skip
        erro = dados.get("error") or {}
        if isinstance(erro, dict) and erro.get("code") not in (None, "", "ok"):
            raise ErroDeRede(
                f"{contexto}: {erro.get('message') or erro['code']} ({erro['code']}).", codigo=400,
                corpo=str(dados)[:500],
            )  # fmt: skip
        return dados

    # --- publicar ---------------------------------------------------------------------------------
    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        achados = []
        if not pacote.video.is_file():
            achados.append(f"O arquivo do Short não existe: {pacote.video.name}.")
        if self.conectada and pacote.duracao_s > DURACAO_MAXIMA_S:
            achados.append("O TikTok aceita vídeos de até 10 minutos pela API.")
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
        legenda = pacote.legenda(limite=LIMITE_DA_LEGENDA)
        hora = quando.astimezone().strftime("%d/%m/%Y às %H:%M") if quando else ""
        if not self.conectada:
            return Resultado(
                self.chave, ASSISTIDO, url=URL_DO_ESTUDIO,
                detalhe="A legenda foi copiada. No TikTok Studio, envie o arquivo cuja pasta foi "
                "aberta" + (f" e escolha “Agendar” para {hora}." if hora else " e publique."),
                dados={"legenda": legenda, "arquivo": str(pacote.video), "url": URL_DO_ESTUDIO},
            )  # fmt: skip
        identificador = self._enviar_para_rascunhos(pacote, ao_progresso, cancelado)
        return Resultado(
            self.chave, RASCUNHO, identificador,
            detalhe="O vídeo está nos rascunhos do TikTok. Abra o app no celular, toque na "
            "notificação e conclua a postagem" + (f" (marcada para {hora})." if hora else "."),
            dados={"legenda": legenda},
        )  # fmt: skip

    def _enviar_para_rascunhos(
        self, pacote: Pacote, ao_progresso: Progresso | None, cancelado: PedidoDeCancelar | None
    ) -> str:
        tamanho = pacote.tamanho
        pedaco, total = plano_dos_pedacos(tamanho)
        iniciado = self._api(
            "POST", "/post/publish/inbox/video/init/", "Começar o envio ao TikTok",
            json_corpo={"source_info": {
                "source": "FILE_UPLOAD", "video_size": tamanho,
                "chunk_size": pedaco, "total_chunk_count": total,
            }},
        )  # fmt: skip
        dados = iniciado.get("data") or {}
        publicacao, destino = str(dados.get("publish_id", "")), dados.get("upload_url", "")
        if not publicacao or not destino:
            raise ErroDePublicacao("O TikTok não devolveu o endereço de envio.")
        for numero in range(total):
            if cancelado is not None and cancelado():
                raise CanceladoErro("Envio cancelado.")
            inicio = numero * pedaco
            fim = tamanho if numero == total - 1 else inicio + pedaco  # o último leva o resto
            corpo = ler_pedaco(pacote.video, inicio, fim - inicio)
            exigir_ok(
                com_tentativas(
                    lambda corpo=corpo, inicio=inicio, fim=fim: self.transporte.pedir(
                        "PUT",
                        destino,
                        cabecalhos={
                            "Content-Type": "video/mp4",
                            "Content-Range": f"bytes {inicio}-{fim - 1}/{tamanho}",
                        },
                        corpo=corpo,
                        timeout=TEMPO_LIMITE_DO_ENVIO_S,
                    )
                ),
                "Enviar o vídeo ao TikTok",
            )
            if ao_progresso:
                ao_progresso((numero + 1) / total * 0.9)
        self._esperar_chegar(publicacao, cancelado)
        if ao_progresso:
            ao_progresso(1.0)
        return publicacao

    def _esperar_chegar(self, publicacao: str, cancelado: PedidoDeCancelar | None) -> None:
        limite = time.monotonic() + TEMPO_DO_STATUS_S
        while True:
            if cancelado is not None and cancelado():
                raise CanceladoErro("Envio cancelado.")
            dados = (
                self._api(
                    "POST", "/post/publish/status/fetch/", "Conferir o envio ao TikTok",
                    json_corpo={"publish_id": publicacao},
                ).get("data")
                or {}
            )  # fmt: skip
            situacao = dados.get("status", "")
            if situacao in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                return
            if situacao == "FAILED":
                raise ErroDePublicacao(
                    f"O TikTok recusou o vídeo ({dados.get('fail_reason') or 'sem motivo'})."
                )
            if time.monotonic() > limite:
                raise ErroDePublicacao(
                    "O TikTok demorou demais para receber o vídeo. Se a conta é pública, o modo "
                    "sandbox pode travar o envio: use o fluxo assistido."
                )
            time.sleep(ESPERA_DO_STATUS_S)
