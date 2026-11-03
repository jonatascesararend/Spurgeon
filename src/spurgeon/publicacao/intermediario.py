"""Upload-Post: um serviço que publica e agenda nas quatro redes com UMA chave.

É o segundo caminho, opcional: custa dinheiro (plano Basic, cerca de US$ 24 por mês em 02/11/2026),
o vídeo passa pelos servidores deles e os acessos às redes ficam com eles. Em troca, resolve o que
as redes não deixam um programa de desktop fazer sozinho (publicar em público no TikTok, agendar no
Instagram). O TikTok "auditado" é alegação do fornecedor: confirme com um Short de teste.

NÃO foi testado com uma conta real: os campos seguem a documentação de 02/11/2026. A legenda vai nos
campos `*_title` de cada rede porque a documentação diz que `description` só vale para algumas.
Os termos do serviço proíbem repartir a chave: cada igreja usa a sua.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from spurgeon.publicacao.erros import ErroDePublicacao, FaltaConfigurar
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
    com_tentativas,
    exigir_ok,
    mensagem_da_resposta,
    montar_multipart,
)
from spurgeon.publicacao.youtube import descricao_do_youtube, titulo_do_youtube

URL_BASE = "https://api.upload-post.com"
REDES = ("youtube", "instagram", "facebook", "tiktok")


class UploadPost(Plataforma):
    chave = "upload_post"
    nome = "Upload-Post (todas as redes de uma vez)"
    agenda_na_rede = True
    campos = (
        Campo("chave_da_api", "Chave da API", segredo=True, ajuda="upload-post.com → API Keys."),
        Campo(
            "usuario", "Perfil",
            ajuda="O nome do perfil que você criou no Upload-Post, com as redes já ligadas.",
        ),
        Campo(
            "redes", "Redes (separadas por vírgula)", opcional=True,
            ajuda="youtube, instagram, facebook, tiktok. Vazio = as quatro.",
        ),
        Campo("pagina_facebook", "Número da Página do Facebook", opcional=True),
    )  # fmt: skip
    como_conectar = (
        "1. Crie uma conta em upload-post.com (plano Basic ou superior: o gratuito não inclui "
        "TikTok) e ligue o YouTube, o Instagram, o Facebook e o TikTok da igreja a um perfil.\n"
        "2. Em “API Keys”, crie uma chave e cole aqui, com o nome do perfil.\n"
        "Atenção: o vídeo passa pelos servidores do serviço, que é pago e de terceiros."
    )

    @property
    def conectada(self) -> bool:
        return self.cofre.tem(self.conta, "chave_da_api") and bool(self.guardado("usuario"))

    def _cabecalhos(self) -> dict[str, str]:
        return {"Authorization": f"Apikey {self.guardado('chave_da_api')}"}

    def redes_ativas(self) -> list[str]:
        pedidas = [r.strip().lower() for r in self.guardado("redes").split(",") if r.strip()]
        return [r for r in pedidas if r in REDES] or list(REDES)

    def conectar(self, *, abrir=None, cancelado: PedidoDeCancelar | None = None) -> str:
        if not self.cofre.tem(self.conta, "chave_da_api") or not self.guardado("usuario"):
            raise FaltaConfigurar("Preencha a chave da API e o nome do perfil antes de conectar.")
        resposta = exigir_ok(
            com_tentativas(
                lambda: self.transporte.pedir(
                    "GET", f"{URL_BASE}/api/uploadposts/me", cabecalhos=self._cabecalhos()
                )
            ),
            "Validar a chave do Upload-Post",
        )
        try:
            dados = resposta.json()
        except ValueError:
            dados = {}
        nome = str(dados.get("email") or dados.get("user") or self.guardado("usuario"))
        resumo = f"perfil {self.guardado('usuario')} ({nome})"
        self.cofre.definir(self.conta, "conta_nome", resumo)
        return resumo

    def problemas(self, pacote: Pacote, quando: datetime | None) -> list[str]:
        achados = []
        if not self.conectada:
            achados.append("O Upload-Post não está conectado (aba Integrações).")
        if not pacote.video.is_file():
            achados.append(f"O arquivo do Short não existe: {pacote.video.name}.")
        if quando is not None and quando.astimezone() <= self.relogio().astimezone():
            achados.append("O horário já passou.")
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
        redes = self.redes_ativas()
        legenda = pacote.legenda(limite=2200)
        campos: dict[str, Any] = {
            "user": self.guardado("usuario"),
            "platform[]": redes,
            "title": titulo_do_youtube(pacote.titulo),
            "description": descricao_do_youtube(pacote),
            "instagram_title": legenda,
            "tiktok_title": legenda,
            "facebook_title": legenda,
            "privacyStatus": "public",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "post_mode": "DIRECT_POST",
            "media_type": "REELS",
            "facebook_media_type": "REELS",
            "facebook_page_id": self.guardado("pagina_facebook") or None,
            "async_upload": True,
        }
        if quando is not None:
            campos["scheduled_date"] = iso_utc(quando)
            campos["timezone"] = "UTC"
        corpo, tipo = montar_multipart(campos, {"video": pacote.video})
        if ao_progresso:
            ao_progresso(0.05)
        resposta = self.transporte.pedir(
            "POST", f"{URL_BASE}/api/upload",
            cabecalhos={**self._cabecalhos(), "Content-Type": tipo}, corpo=corpo,
            timeout=TEMPO_LIMITE_DO_ENVIO_S,
        )  # fmt: skip
        if not resposta.ok:
            raise ErroDePublicacao(
                f"O Upload-Post recusou o envio: {mensagem_da_resposta(resposta)} "
                f"(código {resposta.codigo})."
            )
        try:
            dados = resposta.json()
        except ValueError:
            dados = {}
        if ao_progresso:
            ao_progresso(1.0)
        identificador = str(dados.get("job_id") or dados.get("request_id") or "")
        return Resultado(
            self.chave, ENVIADO if quando is None else AGENDADO, identificador,
            quando=iso_utc(quando) if quando else "",
            detalhe="Redes: " + ", ".join(redes) + ". O serviço cuida do resto.",
        )  # fmt: skip

    def consultar(self, resultado: Resultado) -> Resultado:
        if not resultado.id_remoto:
            return resultado
        resposta = self.transporte.pedir(
            "GET", f"{URL_BASE}/api/uploadposts/status?request_id={resultado.id_remoto}",
            cabecalhos=self._cabecalhos(),
        )  # fmt: skip
        if resposta.ok:
            try:
                situacao = str(resposta.json().get("status", "")).lower()
            except ValueError:
                situacao = ""
            if situacao in ("completed", "success", "published") and resultado.estado == AGENDADO:
                resultado.estado = ENVIADO
        return resultado
