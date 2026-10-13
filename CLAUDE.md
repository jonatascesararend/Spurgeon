# Studio Spurgeon

Aplicativo desktop (Windows, Python 3.11, CustomTkinter, só pt-BR) que transforma sermões
longos em vídeo limpo + legendas + Shorts verticais. Tudo local e offline.

**Leia o [Projeto.md](Projeto.md) antes de mexer:** ele tem as decisões, a ordem do pipeline
e o roadmap por fase. Se uma decisão mudar, atualize o Projeto.md junto com o código.

## Comandos

```
uv sync --extra ui --extra transcricao --extra shorts   # ambiente de desenvolvimento (inclui o grupo dev)
uv run pytest                      # testes (precisam do FFmpeg; sem ele os marcados são pulados)
uv run ruff check . && uv run ruff format .
uv run spurgeon doctor             # pré-verificação do ambiente
uv run spurgeon hardware           # hardware detectado e modelos recomendados
uv run spurgeon processar "Downloads\video.MOV" --limpar-audio --normalizar --shorts --legendas   # sem janela
uv run spurgeon gui                # abre a interface (iniciar.bat faz o mesmo)
SPURGEON_DEMO=1 uv run spurgeon gui   # com etapas de mentira, para experimentar a tela
```

Testes da janela (`-m gui`) abrem o app escondido e precisam de ambiente gráfico; sem ele são pulados.

## Convenções

- Nomes de módulos, funções e domínio em **português sem acento** (`estado`, `transcricao`,
  `calcular_trechos_mantidos`); textos para o usuário em pt-BR com acentos.
- O núcleo (`src/spurgeon/core`) não importa a interface; o pipeline roda sem janela.
- A interface só controla: o processamento roda em subprocesso (`core/trabalhador.py`, eventos em
  JSON por linha; comandos `pausar`/`retomar`/`cancelar` pelo stdin). Etapas chamam
  `ctx.ponto_seguro()` entre blocos de trabalho para aceitar pausa e cancelamento.
- Widgets: nunca use `self._w`, `self.master` etc. como nome de atributo (colide com o Tk).
  Threads de trabalho não mexem em widgets; agendam com `JanelaPrincipal._na_interface`.
- Etapas reais se registram em `core/etapas.py`; painéis de revisão em `ui/revisao.py`.
- Processos longos (FFmpeg, deep-filter) passam por `core/processos.vigiar_processo`: cancelar
  encerra, pausar suspende o processo de verdade. Dentro de uma etapa use `ctx.ffmpeg(...)`.
- Medidas de silêncio: use janelas RMS (`audio/pausas.py`), nunca o `silencedetect` do FFmpeg
  (compara picos instantâneos, não o RMS do ruído de fundo).
- Etapas que geram o vídeo final devem ter `entrega = True`; sem nenhuma, `_work/` é mantida.
- O áudio do sermão real é dual-mono (extraído em mono); o vídeo é HEVC 10 bits HDR (HLG).
- Cada etapa do pipeline é uma `Etapa` (`core/pipeline.py`): idempotente, declara `depende_de`,
  `opcoes_relevantes` e `artefatos`. O estado fica em `estado.json` (escrita atômica).
- Toda a transcrição acontece **uma vez, na linha do tempo original**; o `MapaTempo`
  (`core/tempo.py`) converte para o vídeo sem as pausas longas.
- FFmpeg sempre por `core/ffmpeg.py` (lista de argumentos, sem shell). Saída em nome
  temporário, renomeada só no fim.
- Pastas `Downloads/`, `Projetos/`, `modelos/` e vídeos de `recursos/` não vão para o git.
- FFmpeg 9 não tem mais `-filter_complex_script`: use `-/filter_complex arquivo` (roteiros grandes).
  Cortes de áudio sempre com `atrim` (o demuxer `concat` com inpoint/outpoint erra dezenas de ms).
- Renderização (`render/`): vídeo longo em partes de ~10 min com quadros exatos (retomável),
  Shorts um por vez com assinatura; ambas gravam em `.parcial` e renomeiam. Pouco disco → a junção
  apaga cada parte ao lê-la (modo econômico). HDR→SDR em `render/filtros.py` (`desat=0` de propósito).
- IA dos Shorts (`shorts/`): nunca ponha exemplos concretos de título nos prompts (a IA os copia);
  o Ollama roda só em CPU quando não há GPU utilizável (`num_gpu=0`).
- Testes de render usam vídeos sintéticos; o de sincronia usa quadros numerados em binário e ruído
  branco. `tests/test_render.py` leva ~2 min; a suíte inteira ~7 min.
- Commits: o hook do pre-commit chama `uv`; no Git Bash ponha a pasta do winget do uv no PATH.

## Hardware desta máquina (alvo da v1)

i7-6800K (12 threads), 15,8 GB de RAM, GTX 760 de 2 GB sem CUDA utilizável e sem NVENC.
O caminho só com CPU é o principal; cuidado com tempos e memória.
