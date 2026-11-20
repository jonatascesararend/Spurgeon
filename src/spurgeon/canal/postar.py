"""Postar um Short pelo YouTube Studio, sem a API (Remoto.md, R12 e S3).

O caminho é o que a pessoa faria à mão no Studio: "Enviar vídeos" → o arquivo → título, descrição
(com as hashtags numa linha própria, como o autor posta), "não é conteúdo para crianças" →
"Próximo" → no passo **"Elementos do vídeo"**, o **vídeo relacionado** (o sermão completo,
procurado pelo título no seletor do próprio Studio) → "Próximo" até a visibilidade → "Programar"
com data e hora (ou "Público" agora) → concluir. O id do Short sai do "Link do vídeo" da janela.
Se o passo "Elementos do vídeo" não mostrar o campo, o vínculo é feito depois na página de edição
do Short (plano B, `vincular_relacionado`). Um envio que parou no meio fica no canal como
rascunho privado: `postar(..., rascunho=True)` o retoma em vez de mandar o arquivo de novo.

Os seletores ficam todos aqui. A página do Studio muda de tempos em tempos: cada passo tira uma
foto (`fotos`) e, se algo não aparece, a falha diz qual passo foi. `pausa(nome)` deixa quem chama
conferir antes de seguir (o primeiro envio de verdade foi feito assim).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from spurgeon.canal.navegador import STUDIO, Studio

MESES = ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.",
         "dez.")  # fmt: skip
LIMITE_DO_TITULO = 100
LIMITE_DA_DESCRICAO = 5000


class FalhaNaPostagem(RuntimeError):
    """`enviado`: o arquivo já chegou ao Studio (fica lá como rascunho privado: a próxima tentativa
    o retoma em vez de mandar de novo). `video_id`: o do rascunho, se já se sabe."""

    def __init__(self, mensagem: str, *, enviado: bool = False, video_id: str = ""):
        super().__init__(mensagem)
        self.enviado = enviado
        self.video_id = video_id


@dataclass(frozen=True)
class PedidoDePostagem:
    arquivo: Path
    titulo: str
    descricao: str
    hashtags: list[str] = field(default_factory=list)
    #: o título (ou parte dele) do vídeo do canal a vincular como "Vídeo relacionado"
    relacionado: str = ""
    #: None = publicar agora; senão, programar para este horário (hora do computador)
    quando: datetime | None = None


@dataclass(frozen=True)
class ResultadoDaPostagem:
    video_id: str
    link: str
    relacionado: str  # o título do vídeo vinculado ("" se nenhum)
    programado_para: str  # "AAAA-MM-DDTHH:MM" ou "" (publicado agora)


def texto_da_descricao(pedido: PedidoDePostagem) -> str:
    """A descrição e, numa linha própria, as hashtags (o formato que o autor usa no canal)."""
    partes = [pedido.descricao.strip()]
    if pedido.hashtags:
        partes.append(" ".join(h if h.startswith("#") else f"#{h}" for h in pedido.hashtags))
    return "\n\n".join(p for p in partes if p)[:LIMITE_DA_DESCRICAO]


def data_do_studio(quando: datetime) -> str:
    """`20 de nov. de 2026`, como o seletor de data do Studio em português mostra."""
    return f"{quando.day} de {MESES[quando.month - 1]} de {quando.year}"


def id_do_link(link: str) -> str:
    achado = re.search(r"(?:shorts/|v=|youtu\.be/)([\w-]{11})", link)
    return achado.group(1) if achado else ""


def _escrever(campo, texto: str) -> None:
    """Troca o texto de uma caixa do Studio (div contenteditable)."""
    campo.click()
    campo.press("Control+A")
    campo.press("Delete")
    campo.type(texto, delay=5)


def _foto(pg, fotos: Path | None, nome: str) -> None:
    if fotos is None:
        return
    try:  # a foto é para conferir: nunca esconde o erro de verdade
        fotos.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(fotos / f"{nome}.png"))
    except Exception:
        pass


def _abrir_envio(studio: Studio, pedido: PedidoDePostagem, rascunho: bool) -> None:
    """Abre a janela de envio com o arquivo: um envio novo ou o rascunho que já está no canal
    (um envio que parou no meio: o Studio o guarda como privado, e mandar de novo duplicaria)."""
    pg = studio.pagina
    if rascunho:
        pg.goto(f"{STUDIO}/channel/{studio.canal}/videos/short", wait_until="domcontentloaded")
        linha = pg.locator("ytcp-video-row").filter(has_text=pedido.titulo).first
        linha.wait_for(timeout=60_000)
        linha.get_by_text("Editar rascunho").click()
    else:
        pg.goto(
            f"{STUDIO}/channel/{studio.canal}/videos/upload?d=ud", wait_until="domcontentloaded"
        )
        seletor = pg.locator("ytcp-uploads-file-picker input[type=file]")
        seletor.wait_for(state="attached", timeout=60_000)
        seletor.set_input_files(str(pedido.arquivo))
    pg.locator("ytcp-uploads-dialog #title-textarea #textbox").wait_for(timeout=120_000)
    pg.wait_for_timeout(2500)


def _id_do_envio(pg) -> str:
    """O id do Short pelo "Link do vídeo" da janela de envio (o texto tem o link inteiro, mesmo
    quando a tela o mostra cortado com reticências)."""
    janela = pg.locator("ytcp-uploads-dialog")
    return id_do_link(janela.inner_text()) if janela.count() else ""


def _concluir(pg) -> None:
    """Clica em "Programar"/"Publicar" e confirma o que o Studio perguntar depois: o aviso "Ainda
    estamos verificando seu conteúdo" precisa de "Ok" (sem ele o Short continua rascunho), e a
    janela final ("Vídeo programado") é fechada."""
    pg.locator("ytcp-uploads-dialog #done-button").click()
    for _ in range(60):
        pg.wait_for_timeout(1000)
        ok = pg.get_by_role("button", name="Ok", exact=True)
        if ok.count() and ok.first.is_visible():
            ok.first.click()
            continue
        fechar = pg.locator(
            "ytcp-video-share-dialog #close-button, "
            "ytcp-uploads-still-processing-dialog #close-button"
        )
        if fechar.count() and fechar.first.is_visible():
            fechar.first.click()
            pg.wait_for_timeout(1000)
            return
        if not pg.locator("ytcp-uploads-dialog #done-button").is_visible():
            return
    raise FalhaNaPostagem("O Studio não fechou o envio depois de “Programar”.")


def _escolher_no_seletor(pg, busca: str) -> str:
    """No seletor de vídeos do Studio (já aberto): busca `busca` nos vídeos do canal e escolhe o
    primeiro. Devolve o título escolhido."""
    campo = pg.locator("ytcp-video-pick-dialog input#search-yours")
    campo.wait_for(timeout=30_000)
    campo.fill(busca)
    campo.press("Enter")
    pg.wait_for_timeout(3000)
    cartoes = pg.locator("ytcp-video-pick-dialog ytcp-entity-card")
    if cartoes.count() == 0:
        raise FalhaNaPostagem(f"O vídeo “{busca}” não foi achado no canal.")
    titulo = cartoes.first.inner_text().strip().split("\n")[0]
    cartoes.first.click()
    pg.wait_for_timeout(1500)
    return titulo


def relacionar_nos_elementos(pg, busca: str) -> str:
    """No passo "Elementos do vídeo" da janela de envio (antes de programar; foi o autor quem
    indicou que o vídeo relacionado fica ali): abre o "Vídeo relacionado", escolhe o sermão e
    confere. Devolve o título, ou "" se o passo não tem o campo (aí vale a página de edição)."""
    janela = pg.locator("ytcp-uploads-dialog")
    gatilho = janela.locator("#linked-video-editor-link")
    if not gatilho.count():
        gatilho = janela.get_by_text("Vídeo relacionado", exact=False)
    if not gatilho.count() or not gatilho.first.is_visible():
        return ""
    gatilho.first.scroll_into_view_if_needed()
    gatilho.first.click()
    titulo = _escolher_no_seletor(pg, busca)
    if titulo not in janela.inner_text():
        raise FalhaNaPostagem("O Studio não mostrou o sermão como vídeo relacionado.")
    return titulo


def vincular_relacionado(
    studio: Studio, video_id: str, busca: str, *, fotos: Path | None = None
) -> str:
    """Põe o vídeo do canal que casa com `busca` como "Vídeo relacionado" do Short `video_id` na
    página de edição e salva: o plano B, quando o passo "Elementos do vídeo" não mostrou o campo.
    Devolve o título vinculado."""
    pg = studio.pagina
    pg.goto(f"{STUDIO}/video/{video_id}/edit", wait_until="domcontentloaded", timeout=60_000)
    gatilho = pg.locator("#linked-video-editor-link")
    gatilho.wait_for(timeout=60_000)
    gatilho.scroll_into_view_if_needed()
    gatilho.click()
    titulo = _escolher_no_seletor(pg, busca)
    if titulo not in gatilho.inner_text():
        raise FalhaNaPostagem("O Studio não mostrou o sermão como vídeo relacionado.")
    salvar = pg.locator("ytcp-button#save")
    salvar.click()
    pg.wait_for_timeout(4000)
    _foto(pg, fotos, "4_relacionado")
    if salvar.get_attribute("disabled") is None and "salv" not in pg.content().casefold():
        raise FalhaNaPostagem("O Studio não confirmou que salvou o vídeo relacionado.")
    return titulo


def visibilidade_na_edicao(studio: Studio, video_id: str) -> str:
    """A visibilidade na página de edição do Short ("Programado", "Público", "Privado"…)."""
    pg = studio.pagina
    if f"/video/{video_id}/edit" not in pg.url:
        pg.goto(f"{STUDIO}/video/{video_id}/edit", wait_until="domcontentloaded", timeout=60_000)
    caixa = pg.locator("ytcp-video-metadata-visibility").first
    caixa.wait_for(timeout=60_000)
    return " ".join(caixa.inner_text().split())


def postar(
    studio: Studio,
    pedido: PedidoDePostagem,
    *,
    rascunho: bool = False,
    fotos: Path | None = None,
    pausa: Callable[[str], None] = lambda _n: None,
    andamento: Callable[[str], None] = lambda _t: None,
) -> ResultadoDaPostagem:
    """Envia (ou retoma o rascunho), programa ou publica, e vincula o vídeo relacionado."""
    pg = studio.pagina
    passo = "abrir o envio"
    enviado, video_id = rascunho, ""
    try:
        andamento("Abrindo o envio no YouTube Studio…")
        _abrir_envio(studio, pedido, rascunho)
        enviado = True

        passo = "título e descrição"
        _escrever(pg.locator("ytcp-uploads-dialog #title-textarea #textbox"),
                  pedido.titulo[:LIMITE_DO_TITULO])  # fmt: skip
        _escrever(pg.locator("ytcp-uploads-dialog #description-textarea #textbox"),
                  texto_da_descricao(pedido))  # fmt: skip
        video_id = _id_do_envio(pg)

        passo = "não é para crianças"
        pg.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").click()
        _foto(pg, fotos, "1_detalhes")
        pausa("detalhes")

        passo = "elementos do vídeo"
        pg.locator("ytcp-uploads-dialog #next-button").click()
        pg.wait_for_timeout(2000)
        relacionado = ""
        if pedido.relacionado:
            andamento("Vinculando o sermão como vídeo relacionado…")
            relacionado = relacionar_nos_elementos(pg, pedido.relacionado)
            _foto(pg, fotos, "2_elementos")
            pausa("elementos")

        passo = "próximos passos"
        for _ in range(2):
            pg.locator("ytcp-uploads-dialog #next-button").click()
            pg.wait_for_timeout(1500)

        passo = "visibilidade"
        if pedido.quando is None:
            pg.locator("tp-yt-paper-radio-button[name='PUBLIC']").click()
            programado = ""
        else:
            andamento("Programando a data e a hora…")
            pg.locator("ytcp-uploads-dialog #second-container-expand-button").click()
            pg.wait_for_timeout(800)
            pg.locator("ytcp-uploads-dialog #datepicker-trigger").click()
            data = pg.locator("ytcp-date-picker tp-yt-paper-input input").first
            data.wait_for(timeout=15_000)
            data.fill(data_do_studio(pedido.quando))
            data.press("Enter")
            pg.wait_for_timeout(800)
            hora = pg.locator("ytcp-uploads-dialog #time-of-day-container input").first
            hora.click()
            hora.fill(f"{pedido.quando:%H:%M}")
            hora.press("Enter")
            pg.wait_for_timeout(800)
            programado = f"{pedido.quando:%Y-%m-%dT%H:%M}"
        _foto(pg, fotos, "2_visibilidade")
        pausa("visibilidade")

        passo = "esperar o arquivo chegar"
        andamento("Esperando o YouTube receber o arquivo…")
        for _ in range(360):  # até 30 min; a verificação de direitos não precisa terminar
            progresso = pg.locator("ytcp-uploads-dialog ytcp-video-upload-progress").inner_text()
            if not re.search(r"enviando|uploading|upload\s+\d", progresso, re.IGNORECASE):
                break
            pg.wait_for_timeout(5000)

        passo = "concluir"
        video_id = video_id or _id_do_envio(pg)
        if not video_id:
            raise FalhaNaPostagem("O Studio não mostrou o link do Short na janela de envio.")
        _concluir(pg)
        _foto(pg, fotos, "3_concluido")

        if pedido.relacionado and not relacionado:  # plano B: a página de edição
            passo = "vídeo relacionado"
            andamento("Vinculando o sermão como vídeo relacionado (pela página de edição)…")
            relacionado = vincular_relacionado(studio, video_id, pedido.relacionado, fotos=fotos)
        passo = "conferir a visibilidade"
        visibilidade = visibilidade_na_edicao(studio, video_id)
        esperada = "programado" if pedido.quando is not None else "públic"
        if esperada not in visibilidade.casefold():
            raise FalhaNaPostagem(f"O Short ficou “{visibilidade}”, não {esperada}.")
        return ResultadoDaPostagem(
            video_id, f"https://youtube.com/shorts/{video_id}", relacionado, programado
        )
    except FalhaNaPostagem as erro:
        _foto(pg, fotos, "erro")
        erro.enviado, erro.video_id = erro.enviado or enviado, erro.video_id or video_id
        raise
    except Exception as erro:
        _foto(pg, fotos, "erro")
        raise FalhaNaPostagem(
            f"A postagem parou no passo “{passo}”: {erro}", enviado=enviado, video_id=video_id
        ) from erro
