"""Começar pelo celular (Fase R5): um vídeo do canal; o PC baixa, cria o projeto e processa.

Uma tarefa por vez, numa thread do servidor:

1. baixa o vídeo pelo YouTube Studio (`canal/baixar.py`, 720p, R15) para a pasta de entrada;
2. cria o projeto (`criar_ou_retomar_projeto`) com o **título** e a **data de publicação** do vídeo
   no canal (não a data do arquivo baixado: Remoto.md, S4) e as opções salvas da janela
   (`config.padroes`), e guarda de onde ele veio em `_work/origem_youtube.json` (o id e o título:
   a postagem usa o título para vincular o sermão como vídeo relacionado);
3. confere o ambiente como a janela confere antes de processar (`executar_preflight`);
4. começa o processamento pela fila do celular (um por vez no PC).

Daí em diante é o de sempre: o vigia avisa quando o PC parar para a aprovação dos cortes e quando
terminar.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from spurgeon.core.arquivos import escrever_json_atomico, ler_json
from spurgeon.core.config import ConfigApp, carregar_config
from spurgeon.core.projeto import PastasProjeto, criar_ou_retomar_projeto, nome_pasta_projeto

log = logging.getLogger(__name__)

ARQUIVO_DA_ORIGEM = "origem_youtube.json"


class NovoInvalido(ValueError):
    pass


def gravar_origem(
    pastas: PastasProjeto, video_id: str, titulo: str, publicado: date | None
) -> None:
    pastas.work.mkdir(parents=True, exist_ok=True)
    escrever_json_atomico(
        pastas.work / ARQUIVO_DA_ORIGEM,
        {
            "video_id": video_id,
            "titulo": titulo,
            "publicado": publicado.isoformat() if publicado else "",
        },
    )


def ler_origem(pastas: PastasProjeto) -> dict[str, Any]:
    try:
        dados = ler_json(pastas.work / ARQUIVO_DA_ORIGEM)
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def projetos_por_video(pasta_projetos: Path) -> dict[str, str]:
    """`id do vídeo no YouTube` → nome da pasta do projeto que veio dele."""
    achados: dict[str, str] = {}
    try:
        pastas = [p for p in Path(pasta_projetos).iterdir() if p.is_dir()]
    except OSError:
        return achados
    for pasta in pastas:
        video = ler_origem(PastasProjeto(pasta)).get("video_id")
        if video:
            achados[str(video)] = pasta.name
    return achados


def projeto_do_video(pasta_projetos: Path, video: dict[str, Any], por_video: dict[str, str]) -> str:
    """O projeto que já existe para este vídeo do canal ("" se nenhum): pela origem gravada ou,
    nos projetos de antes da R5, pelo nome da pasta (data de publicação + título)."""
    if video["id"] in por_video:
        return por_video[video["id"]]
    if video.get("publicado"):
        nome = nome_pasta_projeto(date.fromisoformat(video["publicado"]), video["titulo"])
        if (Path(pasta_projetos) / nome / "estado.json").is_file():
            return nome
    return ""


def resumo_das_opcoes(config: ConfigApp) -> list[str]:
    """As opções salvas que o processamento vai usar, em frases curtas para o celular."""
    o = config.padroes
    itens = ["Vídeo longo" if o.gerar_video_longo else "Sem vídeo longo"]
    if o.gerar_shorts:
        itens.append("Shorts" + (" com legenda dinâmica" if o.legendas_dinamicas else ""))
    qualidade = {"maxima": "máxima", "equilibrado": "equilibrada", "rapido": "rápida"}
    itens.append(f"Qualidade {qualidade.get(o.qualidade, o.qualidade)}")
    if o.zoom_pct > 100:
        itens.append(f"Zoom {o.zoom_pct:g}%")
    if o.tratamento_ativo:
        itens.append("Tratamento de imagem")
    if o.limpar_audio:
        itens.append("Limpeza do áudio")
    if o.normalizar_volume:
        itens.append("Volume normalizado")
    if o.introducao or o.vinheta_final:
        itens.append("Introdução/vinheta")
    if o.marca_dagua:
        itens.append("Marca d'água")
    if o.pausar_aprovar_cortes:
        itens.append("Para para você aprovar os cortes")
    if o.pausar_revisar_transcricao:
        itens.append("Para para você revisar a transcrição")
    return itens


@dataclass
class Tarefa:
    video_id: str
    titulo: str
    texto: str = "Preparando…"
    rodando: bool = True
    projeto: str = ""
    erro: str = ""
    _pronta: threading.Event = field(default_factory=threading.Event, repr=False)

    def para_json(self) -> dict[str, Any]:
        return {"video_id": self.video_id, "titulo": self.titulo, "texto": self.texto,
                "rodando": self.rodando, "projeto": self.projeto, "erro": self.erro}  # fmt: skip


def _baixar_de_verdade(video: dict[str, Any], pasta: Path, andamento) -> Path:
    from spurgeon.canal.baixar import baixar_video
    from spurgeon.canal.navegador import abrir_studio

    with abrir_studio() as studio:
        return baixar_video(studio, video["id"], video["titulo"], pasta, andamento=andamento)


def _conferir_o_ambiente(config: ConfigApp, video: Path) -> None:
    from spurgeon.core.preflight import Nivel, executar_preflight

    problemas = [
        f"{r.nome}: {r.detalhe}"
        for r in executar_preflight(config, video=video, opcoes=config.padroes)
        if r.nivel == Nivel.ERRO
    ]
    if problemas:
        raise NovoInvalido("O PC não está pronto para processar: " + "; ".join(problemas))


class NovosProjetos:
    """Uma tarefa por vez (o navegador e a CPU são um só)."""

    def __init__(
        self,
        *,
        iniciar_processamento: Callable[[PastasProjeto], None],
        ocupada: Callable[[], str] = lambda: "",
        baixar: Callable[..., Path] = _baixar_de_verdade,
        conferir: Callable[[ConfigApp, Path], None] = _conferir_o_ambiente,
        carregar: Callable[[], ConfigApp] = carregar_config,
        ao_terminar: Callable[[Tarefa], None] = lambda _t: None,
    ):
        self._iniciar = iniciar_processamento
        self._ocupada = ocupada
        self._baixar = baixar
        self._conferir = conferir
        self._carregar = carregar
        self._ao_terminar = ao_terminar
        self._trava = threading.Lock()
        self.atual: Tarefa | None = None

    def rodando(self) -> bool:
        return self.atual is not None and self.atual.rodando

    def esperar(self, timeout: float | None = None) -> bool:
        return self.atual is None or self.atual._pronta.wait(timeout)

    def iniciar(self, video: dict[str, Any]) -> Tarefa:
        if not video.get("id") or not video.get("titulo"):
            raise NovoInvalido("Vídeo inválido.")
        with self._trava:
            if self.rodando():
                raise NovoInvalido("O PC já está baixando um vídeo do canal. Espere terminar.")
            motivo = self._ocupada()
            if motivo:
                raise NovoInvalido(motivo)
            tarefa = Tarefa(video["id"], video["titulo"])
            self.atual = tarefa
        threading.Thread(target=self._rodar, args=(tarefa, video), daemon=True).start()
        return tarefa

    def _rodar(self, tarefa: Tarefa, video: dict[str, Any]) -> None:
        def andamento(texto: str) -> None:
            tarefa.texto = texto

        try:
            config = self._carregar()
            arquivo = self._baixar(video, config.pastas.entrada, andamento)
            andamento("Criando o projeto…")
            publicado = date.fromisoformat(video["publicado"]) if video.get("publicado") else None
            criado = criar_ou_retomar_projeto(
                config.pastas.projetos, arquivo, video["titulo"], publicado or date.today(),
                config.padroes,
            )  # fmt: skip
            gravar_origem(criado.pastas, video["id"], video["titulo"], publicado)
            tarefa.projeto = criado.pastas.raiz.name
            andamento("Conferindo o ambiente…")
            self._conferir(config, arquivo)
            self._iniciar(criado.pastas)
            tarefa.texto = "Processamento começou no PC."
            log.info("Projeto criado pelo celular: %s", criado.pastas.raiz.name)
        except Exception as erro:
            log.exception("Falha ao começar o projeto pelo celular.")
            tarefa.erro = str(erro) or erro.__class__.__name__
            tarefa.texto = "Não deu para começar o projeto."
        finally:
            tarefa.rodando = False
            tarefa._pronta.set()
            try:
                self._ao_terminar(tarefa)
            except Exception:
                log.exception("Falha ao avisar do projeto novo.")
