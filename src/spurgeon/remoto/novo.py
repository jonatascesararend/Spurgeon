"""Começar pelo celular (Fase R5): um vídeo do canal vira projeto; o PC baixa e processa depois.

Em dois tempos:

* **Criar** (`NovosProjetos.criar`, na hora, sem baixar nada): a pasta do projeto com o **título** e
  a **data de publicação** do vídeo no canal (não a data do arquivo: Remoto.md, S4), as opções
  salvas da janela (`config.padroes`) e a origem em `_work/origem_youtube.json` (id, título, data e
  duração; a postagem usa o título para vincular o sermão). O `estado.json` já aponta para onde o
  vídeo vai ser baixado, com tamanho 0 e sem hash (`EntradaVideo` de um vídeo que ainda não veio).
  Na tela do projeto a pessoa ajusta as opções (`remoto/configuracao.py`).
* **Começar** (`NovosProjetos.comecar`, uma tarefa por vez numa thread): baixa o vídeo pelo YouTube
  Studio (`canal/baixar.py`, 720p, R15) se ainda não está no PC, grava a entrada de verdade
  (tamanho e hash), confere o ambiente como a janela confere (`executar_preflight`, com as opções
  do projeto) e começa o processamento pela fila do celular (um por vez no PC).

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
from spurgeon.core.erros import ErroConfiguracao
from spurgeon.core.estado import EntradaVideo, Estado, carregar_estado, salvar_estado
from spurgeon.core.projeto import PastasProjeto, nome_pasta_projeto

log = logging.getLogger(__name__)

ARQUIVO_DA_ORIGEM = "origem_youtube.json"


class NovoInvalido(ValueError):
    pass


def gravar_origem(
    pastas: PastasProjeto,
    video_id: str,
    titulo: str,
    publicado: date | None,
    duracao_s: int = 0,
) -> None:
    pastas.work.mkdir(parents=True, exist_ok=True)
    dados: dict[str, Any] = {
        "video_id": video_id,
        "titulo": titulo,
        "publicado": publicado.isoformat() if publicado else "",
    }
    if duracao_s:
        dados["duracao_s"] = int(duracao_s)
    escrever_json_atomico(pastas.work / ARQUIVO_DA_ORIGEM, dados)


def video_baixado(pastas: PastasProjeto) -> bool:
    """O vídeo de entrada do projeto já está no PC (um projeto criado do canal nasce sem ele)."""
    estado = carregar_estado(pastas.raiz)
    return (
        estado is not None and estado.entrada.tamanho > 0 and Path(estado.entrada.caminho).is_file()
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
    processar: bool = True  # False = só baixar o vídeo (para a prévia do tratamento de imagem)
    _pronta: threading.Event = field(default_factory=threading.Event, repr=False)

    def para_json(self) -> dict[str, Any]:
        return {"video_id": self.video_id, "titulo": self.titulo, "texto": self.texto,
                "rodando": self.rodando, "projeto": self.projeto, "erro": self.erro,
                "processar": self.processar}  # fmt: skip


def _baixar_de_verdade(video: dict[str, Any], pasta: Path, andamento) -> Path:
    from spurgeon.canal.baixar import baixar_video
    from spurgeon.canal.navegador import abrir_studio

    with abrir_studio() as studio:
        return baixar_video(studio, video["id"], video["titulo"], pasta, andamento=andamento)


def _conferir_o_ambiente(config: ConfigApp, video: Path, opcoes=None) -> None:
    from spurgeon.core.preflight import Nivel, executar_preflight

    problemas = [
        f"{r.nome}: {r.detalhe}"
        for r in executar_preflight(config, video=video, opcoes=opcoes or config.padroes)
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
        conferir: Callable[..., None] = _conferir_o_ambiente,
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

    def criar(self, video: dict[str, Any]) -> PastasProjeto:
        """Cria o projeto do vídeo do canal na hora, sem baixar (ou devolve o que já existe)."""
        if not video.get("id") or not video.get("titulo"):
            raise NovoInvalido("Vídeo inválido.")
        config = self._carregar()
        base = config.pastas.projetos
        if existente := projeto_do_video(base, video, projetos_por_video(base)):
            return PastasProjeto(base / existente)
        publicado = date.fromisoformat(video["publicado"]) if video.get("publicado") else None
        nome = nome_pasta_projeto(publicado or date.today(), video["titulo"])
        pastas = PastasProjeto(base / nome)
        if len(str(pastas.raiz)) > 180:
            raise NovoInvalido(
                "O caminho do projeto ficou longo demais para o Windows (título muito comprido)."
            )
        if pastas.raiz.exists() and any(pastas.raiz.iterdir()):
            raise NovoInvalido(f"Já existe uma pasta “{pastas.raiz.name}” que não é deste vídeo.")
        from spurgeon.canal.baixar import nome_do_arquivo

        destino = config.pastas.entrada / nome_do_arquivo("", video["titulo"])
        try:
            pastas.criar()
        except (OSError, ErroConfiguracao) as erro:
            raise NovoInvalido(f"Não deu para criar a pasta do projeto: {erro}") from erro
        entrada = EntradaVideo(caminho=str(destino), tamanho=0, hash_rapido="")
        salvar_estado(pastas.raiz, Estado(entrada=entrada, opcoes=config.padroes))
        gravar_origem(pastas, video["id"], video["titulo"], publicado,
                      int(video.get("duracao_s") or 0))  # fmt: skip
        log.info("Projeto criado pelo celular (sem baixar ainda): %s", pastas.raiz.name)
        return pastas

    def comecar(self, pastas: PastasProjeto, *, processar: bool = True) -> Tarefa:
        """Baixa o vídeo (se ainda não veio) e começa o processamento, numa thread. Com
        `processar=False` só baixa (a prévia do tratamento de imagem precisa do vídeo)."""
        origem = ler_origem(pastas)
        if not processar and video_baixado(pastas):
            raise NovoInvalido("O vídeo deste projeto já está no PC.")
        if not video_baixado(pastas) and not origem.get("video_id"):
            raise NovoInvalido("O vídeo deste projeto não está no PC e não se sabe de onde baixar.")
        with self._trava:
            if self.rodando():
                raise NovoInvalido("O PC já está baixando um vídeo do canal. Espere terminar.")
            motivo = self._ocupada()
            if motivo:
                raise NovoInvalido(motivo)
            tarefa = Tarefa(str(origem.get("video_id") or ""), str(origem.get("titulo") or ""),
                            projeto=pastas.raiz.name, processar=processar)  # fmt: skip
            self.atual = tarefa
        threading.Thread(target=self._rodar, args=(tarefa, pastas, origem), daemon=True).start()
        return tarefa

    def _rodar(self, tarefa: Tarefa, pastas: PastasProjeto, origem: dict[str, Any]) -> None:
        def andamento(texto: str) -> None:
            tarefa.texto = texto

        try:
            config = self._carregar()
            estado = carregar_estado(pastas.raiz)
            if estado is None:
                raise NovoInvalido("O projeto não tem estado.json.")
            arquivo = Path(estado.entrada.caminho)
            if not video_baixado(pastas):
                andamento("Baixando o vídeo pelo YouTube Studio…")
                video = {"id": origem["video_id"], "titulo": origem.get("titulo") or tarefa.titulo}
                arquivo = self._baixar(video, config.pastas.entrada, andamento)
                estado = carregar_estado(pastas.raiz) or estado  # as opções podem ter mudado
                estado.entrada = EntradaVideo.de_arquivo(arquivo)
                salvar_estado(pastas.raiz, estado)
            if not tarefa.processar:
                tarefa.texto = "Vídeo baixado: a prévia do tratamento de imagem já aparece."
                log.info("Vídeo baixado pelo celular (sem processar): %s", pastas.raiz.name)
                return
            andamento("Conferindo o ambiente…")
            self._conferir(config, arquivo, estado.opcoes)
            self._iniciar(pastas)
            tarefa.texto = "Processamento começou no PC."
            log.info("Projeto começado pelo celular: %s", pastas.raiz.name)
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
