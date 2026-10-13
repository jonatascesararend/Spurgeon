# Guia de Projeto: Studio Spurgeon de Cortes e Otimização de Vídeos (Sermões, Aulas e Shorts)

> **Versão 2 — 2026-10-12.** Atualizado com as decisões da pré-entrevista.
> As interpretações da pré-entrevista foram confirmadas pelo autor; a seção 6 registra as decisões e as pendências.

---

## 1. Visão Geral da Arquitetura

A aplicação é um aplicativo desktop nativo para Windows (Python + CustomTkinter, tema escuro, somente pt-BR). Ela detecta as especificações do PC, recomenda o tamanho do faster-whisper compatível com a máquina e usa os recursos disponíveis (CPU, RAM, GPU) conforme o limite escolhido pelo usuário.

### 1.1 Escopo e premissas

* **Público:** v1 para o PC do autor (por enquanto, o mesmo PC de desenvolvimento); depois, instalação em outros PCs. Por isso a detecção de hardware é requisito real, não enfeite.
* **Material de entrada:** vídeos de 45 min a 1h30, somente fala (sem música), câmera fixa em 16:9, pastor sempre no centro do quadro e sem se mover.
* **Um vídeo por vez** (sem fila na v1).
* **100% offline** depois de instalar as dependências e baixar os modelos.
* **Sem preview de vídeo** na interface. A revisão é feita sobre texto e tempos.
* **Idioma:** somente pt-BR (Whisper `language="pt"`, spaCy em português, prompts do LLM em português).
* **Teologia:** o conteúdo é de teologia reformada calvinista; isso orienta o vocabulário do Whisper e as regras de contexto dos cortes.

### 1.2 Uso de recursos (escolha na interface)

Seletor "Uso de processamento":

* ( ) **75% (recomendado)**: deixa o PC utilizável durante o processo.
* ( ) **100% (máximo)**: usa tudo; o PC fica praticamente inutilizável até terminar.

Aplicação do limite: `cpu_threads` do faster-whisper, `-threads` do FFmpeg, `num_thread` do Ollama e variáveis `OMP_NUM_THREADS` (o `deep-filter` não tem opção de threads). GPU é usada quando existir e for compatível. As etapas rodam **em sequência**, nunca em paralelo, e cada modelo é descarregado da memória antes de carregar o próximo (Whisper → LLM).

### 1.3 Fluxo geral do usuário

1. Selecionar o vídeo longo (a janela abre em `Downloads/`) ou colar o caminho.
2. Conferir nome do sermão e data da gravação (preenchidos automaticamente, editáveis).
3. Escolher as opções de processamento e clicar em **Iniciar Processamento**.
4. O app cria a pasta do projeto, **abre-a no Explorer** e inicia o log `processamento.log`.
5. O pipeline roda em segundo plano (subprocesso) com barra de progresso, tempo restante estimado e painel de logs em tempo real.
6. Em dois pontos o processo **pausa para revisão humana** (transcrição e cortes dos Shorts). Esses pontos podem ser desligados.
7. Ao terminar, os intermediários são apagados e o resultado fica pronto para revisar e subir.

### 1.4 Ordem do pipeline

```
 Vídeo bruto (Downloads/)
   │
   ▼
[0] Pré-verificação ──► cria Projetos/AAAA-MM-DD_Nome/, abre a pasta, inicia o log e o estado
   ▼
[1] Extrair áudio (48 kHz, sem perdas) ─► _work/audio_original.wav
   ▼
[2] Limpar áudio (DeepFilterNet) ─► [3] Normalizar volume (loudnorm)      (cada um é opcional)
   ▼
[4] Transcrever UMA vez (faster-whisper, palavra a palavra)
       na linha do tempo ORIGINAL ─► transcricao.json
   ▼
 🛑 Revisão da transcrição (opcional)
   ▼
[5] Detectar pausas ≥ limiar ─► lista de trechos mantidos ─► mapa de tempo original→editado
   ▼
[6] Gerar .srt (tempos remapeados; +duração da introdução, se marcada)
   ▼
[7] Renderizar vídeo longo (cortes + áudio limpo + introdução/vinheta) ─► video_longo_otimizado.mp4
   ▼
[8] Shorts: spaCy → candidatos (pandas) → LLM (Ollama) → ajuste em pausas
   ▼
 🛑 Aprovação dos cortes (opcional)
   ▼
[9] Renderizar Shorts (9:16 + legenda ASS) ─► shorts_gerados/
   ▼
[10] Finalizar: apagar _work/, registrar resumo no log
```

**Por que transcrever uma vez antes de cortar silêncio:** o .srt sempre bate com o vídeo final, os Shorts saem do material original, e não é preciso retranscrever depois de cortar. O vínculo entre as duas linhas do tempo é um **mapa de tempo** (lista de trechos mantidos → função `t_original → t_editado`).

### 1.5 Stack

| Função | Biblioteca / ferramenta | Observação |
|---|---|---|
| Interface | `customtkinter` | Tema escuro. Pipeline em subprocesso; a interface recebe eventos por fila. |
| Áudio e vídeo | **FFmpeg** (binário externo) | Extração, cortes, concatenação, loudnorm, encode e queima de legendas ASS. |
| Limpeza de áudio | **`deep-filter`** (binário oficial do DeepFilterNet 0.5.6) | Sem Python nem torch. Baixado do GitHub oficial para `modelos/ferramentas/`, com SHA-256 conferido (`75E11FA16445F560CB6B021521DDB89E89270D13B83089705D98776F58FD7915`). Não tem assinatura digital. |
| Transcrição | `faster-whisper` | GPU só NVIDIA com CUDA 12 + cuDNN 9; senão CPU `int8`. |
| Análise de texto | `spacy` + `pt_core_news_md` | `sm` como alternativa leve. |
| Seleção por IA | **Ollama** + cliente `ollama` | Saída em JSON estruturado (schema validado com `pydantic`). |
| Dados e janelas | `pandas` + `numpy` | Substitui o DuckDB do plano original (ver Fase 4). |
| Pós-correção de termos | `rapidfuzz` | Compara a transcrição com o vocabulário teológico. |
| Hardware | `psutil`, `nvidia-ml-py` (opcional), `ctranslate2` | Só recomenda; nunca bloqueia. |
| Config / pastas | `pydantic` + TOML, `platformdirs` | Config do usuário fora do repositório. |
| Qualidade | `pytest`, `ruff`, `pre-commit` | Fixture: vídeo de 20 s gerado pelo FFmpeg (não versionado). |

Gerenciamento: **Python 3.11**, `uv` com lockfile, `pyproject.toml` com extras opcionais (`[ui]`, `[ai]`) e o grupo `dev` (instalado por padrão; o `instalar.bat` usa `--no-dev`).

### 1.6 Estrutura do repositório

```
Spurgeon/
├── Projeto.md
├── pyproject.toml
├── iniciar.bat                 ← abre o app (v1: sem .exe)
├── instalar.bat                ← cria o ambiente e baixa os modelos
├── src/spurgeon/
│   ├── core/                   ← config, log, estado, hardware, pipeline, ffmpeg, mapa de tempo
│   ├── audio/                  ← extrair, limpar, normalizar
│   ├── transcricao/            ← whisper, vocabulário, srt
│   ├── shorts/                 ← nlp, llm, candidatos, metadados
│   ├── render/                 ← video_longo, shorts, ass
│   └── ui/                     ← janela principal, painéis de revisão
├── tests/
├── Downloads/                  ← ENTRADA: vídeos brutos          (ignorado no git)
├── recursos/                   ← introdução, vinheta, vocabulário, fontes
├── modelos/                    ← modelos baixados (Whisper, spaCy)  (ignorado no git)
└── Projetos/                   ← SAÍDA, criada pelo app           (ignorado no git)
```

### 1.7 Boas práticas adotadas desde o início

* **Núcleo separado da interface:** o pipeline roda por linha de comando, sem janela (testável e reaproveitável).
* **Cada etapa é uma classe com a mesma interface** (`executar`, `retomar`, `pode_pular`) e é **idempotente**.
* **Subprocessos** (`multiprocessing` com `spawn`) em vez de threads: liberam RAM entre etapas e permitem cancelar de verdade.
* **Escrita atômica** (grava em arquivo temporário e renomeia) para `estado.json` e `transcricao.json`.
* **FFmpeg** sempre com lista de argumentos (sem `shell=True`), stderr capturado e progresso por `-progress pipe:1`.
* **Windows:** `pathlib`, UTF-8 forçado, nomes de arquivo sanitizados, atenção ao limite de 260 caracteres do caminho. Em filtros do FFmpeg, o caminho precisa de escape (`C\:`); por isso o FFmpeg roda com `cwd` na pasta de trabalho e caminhos relativos.
* **`logging`** do Python (nunca `print`), com handler que alimenta o painel da interface.
* **Pré-verificação** ao abrir: Python, FFmpeg, Ollama ativo, modelos baixados, RAM e espaço livre (pelo menos 3× o tamanho do vídeo).
* **Licenças:** somente fontes OFL (livres); avisos de licença dos modelos no README.

---

## 2. Roadmap de Desenvolvimento (Passo a Passo)

### Fase 0: Fundação do projeto (nova) — concluída em 2026-10-12

Instalado: Python 3.11.9, uv 0.13.0, FFmpeg 9.0.2 e Ollama 0.40.3. Núcleo em `src/spurgeon/core/` com 106 testes (`uv run pytest`). O que cada item virou:

* Instalar Python 3.11, `uv`, FFmpeg e Ollama (via `winget`); `git init` e commit inicial.
* Criar a estrutura da seção 1.6, `pyproject.toml`, `ruff`, `pytest` e `pre-commit`.
* Implementar o **núcleo**: configuração (pydantic + TOML), log por projeto, `estado.json`, mapa de tempo, wrapper do FFmpeg, detecção de hardware e a pré-verificação.
* Criar `instalar.bat` e `iniciar.bat` (opção mais leve para a v1).
* Testar as opções do DeepFilterNet no Windows e escolher uma (**feito**). Resultado:
  * `deepfilternet` (PyPI) **não importa** com o torch/torchaudio atual (`torchaudio.backend` foi removido) e exigiria um torch antigo.
  * `deepfilternet-slim` traz só a DLL e o modelo, sem API de áudio; é Alpha e de terceiro.
  * **Escolhido: o binário oficial `deep-filter`.** Na amostra de teste (pt-BR, ruído forte), o ruído de banda larga caiu 19 dB e a faixa da voz mudou ~1 dB. Em 20 min de áudio: 0,12× do tempo real e pico de 482 MB de RAM.
* Gerar a fixture de teste de 20 s.

### Fase 1: Interface Gráfica com CustomTkinter — concluída em 2026-10-12

Tema escuro, sem preview de vídeo, um vídeo por vez. Abre com `iniciar.bat` ou `uv run spurgeon gui`.

**Como ficou (e o que diverge do plano original):**

* **Processamento em subprocesso.** A interface não processa nada: ela inicia `python -m spurgeon.core.trabalhador` e conversa por linhas JSON (stdout para eventos, stdin para `pausar`, `retomar` e `cancelar`). Se a interface cair, o stdin fecha e o trabalho é cancelado com o projeto salvo.
* **Etapas ainda não existem.** O registro (`core/etapas.py`) está vazio até as Fases 2 a 5: "Iniciar" verifica o ambiente, cria o projeto, abre a pasta e avisa que não há etapas. Com `SPURGEON_DEMO=1` rodam etapas de demonstração (pausa, cancelamento e revisão funcionam de verdade) para experimentar a tela.
* **Painéis de revisão ainda não existem.** O aviso de revisão, o botão "Aprovar e continuar" e o ponto de pausa funcionam; o painel de edição de cada etapa chega com ela (transcrição na Fase 3, cortes na Fase 4) e se registra em `ui/revisao.py`.
* **Estimativa de tempo:** o micro-benchmark do Whisper depende do faster-whisper, então fica para a Fase 3. Na Fase 1 há o **tempo restante** calculado pela velocidade observada (sem contar o tempo em pausa). Os pesos das etapas (`Etapa.peso`) deixam a estimativa realista.
* **Cuidados:** reprocessar um projeto já concluído pergunta antes (os intermediários foram apagados); fechar uma pergunta no "X" vale "não"; sair com processamento ativo pede confirmação; as preferências ficam salvas ao iniciar.
* **Testes:** a lógica do formulário e do estimador é pura e testada à parte; `tests/test_janela.py` opera a janela de verdade (escondida) com as etapas de demonstração.

* **Entrada:**
  * Campo para o arquivo de vídeo (.mp4, .mkv, etc.; o seletor abre em `Downloads/`).
  * Nome do sermão (sugerido pelo nome do arquivo) e **data da gravação** (lida do metadado `creation_time` via ffprobe; se faltar, usa a data de modificação do arquivo; editável).
  * Seletor da pasta de projetos (padrão: `Projetos/`).
* **Processamento:**
  * Uso de processamento: 75% (recomendado) ou 100%.
  * Modelo do Whisper (lista abaixo), com recomendação automática pelo hardware.
  * Preset de qualidade de vídeo (Fase 5).
* **Opções (checkboxes):**
  * [ ] Limpar áudio (DeepFilterNet)
  * [ ] Normalizar volume
  * [ ] Gerar vídeo completo sem silêncios + arquivo .srt
    * Campo **Tamanho mínimo da pausa a cortar (s)**: decimal digitado, padrão `10,0`, máximo `30,0`, mínimo `0,5`.
  * [ ] Introdução (habilitado só se o arquivo existir em `recursos/`)
  * [ ] Vinheta final (idem)
  * [ ] Gerar Shorts automáticos (vertical 9:16)
  * [ ] Legendas dinâmicas: só habilitado quando "Shorts" estiver marcado
* **Pontos de revisão (padrão ligado):**
  * [x] Pausar para revisar a transcrição (.srt) antes de continuar
  * [x] Pausar para aprovar os cortes dos Shorts antes de renderizar
* **Avançado:** pausa residual mantida ao cortar, duração mínima e máxima dos Shorts, modelo do Ollama, vocabulário teológico, deslocamento horizontal do recorte, posição vertical da legenda, limite de segurança de Shorts, manter intermediários.
* **Execução:** botões **Iniciar**, **Pausar** e **Cancelar**; barra de progresso com tempo restante estimado; painel de logs em tempo real com a etapa atual ("Limpando áudio...", "Transcrevendo...", "Renderizando Shorts...").
  * *Pausar* age em pontos seguros (fim do bloco, segmento ou Short em andamento).
  * *Cancelar* encerra o subprocesso e deixa o projeto retomável.
  * A tela só habilita **Iniciar** se houver ao menos um produto marcado (vídeo longo ou Shorts). "Limpar áudio" e "Normalizar" são modificadores aplicados aos produtos.
* **Modelos do Whisper**, com aviso de peso na lista:

  | Modelo | Peso | Observação |
  |---|---|---|
  | tiny | muito leve | só para testes; fraco em pt-BR |
  | base | leve | rascunho |
  | small | médio | aceitável |
  | medium | pesado | boa precisão |
  | **large-v3-turbo** | médio-pesado | **recomendado**; em benchmark de GPU levou cerca de 1/3 do tempo do large-v3 |
  | large-v3 | o mais pesado | precisão máxima; em CPU pode levar horas |

  Micro-benchmark do Whisper (≈30 s de áudio, para estimar o tempo total antes de iniciar): **Fase 3**.

### Fase 2: Pré-processamento e Limpeza de Áudio — concluída em 2026-10-12

Etapas em `src/spurgeon/audio/`, registradas em `core/etapas.py`. Para processar sem a janela: `uv run spurgeon processar "Downloads\video.MOV" --limpar-audio --normalizar` (a interface usa o mesmo caminho).

* **Extração (`extrair_audio`):** o FFmpeg extrai o áudio para `_work/audio_original.wav` (48 kHz, PCM de 16 bits, sem perdas), alinhado ao tempo zero do vídeo. Se os dois canais forem idênticos (**dual-mono**, como no iPhone), extrai em mono: metade do tamanho e do tempo de limpeza. O teste compara o canal "lado" (L−R) com o "centro" (L+R); abaixo de −35 dB é mono.
* **Limpeza (`limpar_audio`, opcional):** roda o binário oficial `deep-filter -D` (baixado na primeira vez para `modelos/ferramentas/`, com SHA-256 conferido; a opção `-D` compensa o atraso do filtro, para o áudio não ficar defasado do vídeo). Não informa progresso, então a barra é estimada pelo tempo. Se o resultado diferir mais de 1 s da duração original, é descartado. Gera `_work/audio_limpo.wav`.
* **Normalização (`normalizar_audio`, opcional):** `loudnorm` do FFmpeg em duas passagens, ganho linear, alvo de −16 LUFS e pico verdadeiro de −1,5 dB; uma terceira medição confere o resultado. Gera `_work/audio_normalizado.wav`. Os −14 LUFS dos Shorts são aplicados na Fase 5, clipe a clipe. Áudio praticamente mudo é mantido como está, com aviso.
* **Pausas (`detectar_pausas` e `calcular_cortes`, só com o vídeo longo):**
  * Pausa = intervalo em que o nível **RMS de janelas de 50 ms** fica abaixo de um limiar adaptado à gravação (ruído de fundo medido no percentil 10, mais 25% do caminho até a fala). Cortar fala é o erro caro, então o critério é conservador.
  * O `silencedetect` do FFmpeg **não serve** para isso: ele compara amostras instantâneas, cujos picos ficam 10 a 15 dB acima do RMS do ruído, e deixava de achar pausas em áudio com ruído de fundo.
  * `detectar_pausas` guarda todas as pausas de 0,5 s ou mais em `_work/pausas.json` e registra no log **quanto cada tamanho mínimo cortaria**; `calcular_cortes` aplica o tamanho escolhido (padrão 10 s) e a pausa residual (padrão 1,0 s) e grava `_work/cortes.json` com os trechos mantidos. Mudar só o tamanho mínimo refaz apenas essa segunda etapa.
  * Não remove "é...", "hum" nem outras muletas (deixaria o vídeo picotado).
  * A Fase 3 refinará as pausas com as lacunas entre as palavras do Whisper.
* **Cuidados com processos longos:** pausar suspende o processo de verdade (a CPU é liberada na hora) e cancelar o encerra; o Windows é impedido de suspender o PC durante o processamento; e, enquanto nenhuma etapa gera o vídeo final, `_work/` é mantida (senão o app apagaria o único resultado).

**Resultado no sermão real** (`Miqueias 2. 6-11.MOV`, 65 min), em 11 min 6 s no total:

| Etapa | Tempo | Resultado |
|---|---|---|
| Extração | 5 s | 374 MB em mono (dual-mono detectado) |
| Limpeza | 8 min 28 s | 0,13× a duração, como estimado |
| Normalização | 2 min 9 s | −13,9 → **−16,0 LUFS**, pico verdadeiro −2,0 dB |
| Pausas | 16 s | ruído de fundo −70,5 dB; 445 pausas de 0,5 s ou mais |

**O que as pausas revelaram:** este pregador fala quase sem parar. A maior pausa do sermão tem **5,3 s**, e o padrão de 10 s não cortaria nada:

| Tamanho mínimo | Pausas cortadas | Tempo removido |
|---|---|---|
| 1 s | 123 | 57 s |
| 2 s | 10 | 17 s |
| 3 s | 2 | 7 s |
| 5 s | 1 | 4 s |
| 10 s (padrão) | 0 | nada |

Mesmo no limite de 1 s o vídeo encurta menos de 1 minuto em 65. A remoção de silêncios agrega pouco para este material; o valor está na limpeza do áudio, na legenda e nos Shorts.

### Fase 3: Transcrição e Alinhamento Temporal (faster-whisper) — concluída em 2026-10-12

Etapas em `src/spurgeon/transcricao/`: `transcrever` → `revisar_transcricao` (opcional) → `detectar_pausas` → `refinar_pausas` → `calcular_cortes` → `gerar_srt`. A medição de nível virou a etapa `medir_nivel`, que serve tanto aos blocos da transcrição quanto às pausas. Modelos ficam em `modelos/whisper/<nome>/`; `spurgeon benchmark "Downloads\video.MOV" --modelo large-v3-turbo` mede a velocidade nesta máquina e projeta o tempo de um sermão.

* **Hardware (a pendência da GTX 760 está resolvida):** o CTranslate2 informa *"driver CUDA insuficiente para o runtime"*. Atualizar o driver não resolve: a série R470 é a última que a NVIDIA mantém para a Kepler, e o CTranslate2 4.5+ exige CUDA 12.3+ e cuDNN 9. Tudo roda na **CPU, em `int8`, com 9 threads** (75%); 12 threads não ganham velocidade.
* **Velocidade medida** (3 min do sermão real, CPU):

  | Configuração | Tempo | Para 65 min |
  |---|---|---|
  | large-v3-turbo, beam 5 (padrão) | 0,71× o tempo real | ~46 min |
  | large-v3-turbo, beam 1 | 0,62× | ~41 min |
  | large-v3-turbo, 12 threads | 0,72× | ~47 min |
  | tiny | 0,07× | ~5 min (qualidade bem pior) |

* **Parâmetros do Whisper** (`transcricao/motor.py`; mudar qualquer um invalida os blocos gravados):
  * `language="pt"`, `beam_size=5`, `word_timestamps=True`, `vad_filter=True`, `hallucination_silence_threshold=2.0`.
  * `condition_on_previous_text=False`: evita repetições em laço em gravações longas.
  * O vocabulário vai em **`hotwords`**, que entra no prompt de **toda** janela de 30 s. O `initial_prompt` não serve: com `condition_on_previous_text=False` ele só vale para a primeira janela.
  * O filtro de voz é **ignorado** quando se passa `clip_timestamps`; por isso o áudio é fatiado em blocos pelo próprio código e os tempos recebem o deslocamento do bloco.
* **Blocos de ~10 min cortados em pausas:** cada fronteira fica no meio da maior pausa dentro de 90 s do ponto ideal (sem pausa por perto, no ponto ideal). Cada bloco concluído é gravado em `transcricao.json`; se o processo cair, retoma do bloco seguinte. A transcrição gravada só é reaproveitada se as opções que a afetam, o vocabulário e os parâmetros forem os mesmos.
* **Vocabulário teológico** (`recursos/vocabulario_teologico.txt`, editável, ~270 termos de teologia reformada, nomes, lugares e livros da Bíblia):
  * As **primeiras linhas** (até ~300 caracteres) viram as dicas do Whisper, que tem espaço para só ~224 tokens: o topo da lista deve ter o que ele mais erra.
  * A lista inteira alimenta a **pós-correção** (`rapidfuzz`, sem diferenciar maiúsculas): só troca trechos de até 4 palavras com **confiança baixa** (média < 0,75), com 5+ letras, 85% ou mais parecidos com um termo e com a mesma quantidade de palavras. Cada troca fica registrada no log e em `Palavra.original`.
* **Revisão (🛑 opcional, `ui/revisao_transcricao.py`):** uma linha por trecho (`[2:05] texto`), com as palavras de baixa confiança em **laranja**, busca (Ctrl+F, F3), **"trocar em tudo"** para nomes recorrentes, desfazer (Ctrl+Z) e aviso antes de descartar. Corrigir uma palavra mantém o tempo dela; trocar por mais ou menos palavras divide o intervalo em proporção ao tamanho. Salvar sem mudar nada não reescreve o arquivo.
* **Pausas conferidas pela fala (`refinar_pausas`):** tira das pausas por energia qualquer trecho (com folga de 0,15 s) em que o Whisper ouviu uma palavra; assim nunca se corta fala baixinha. A detecção original fica em `pausas`, e a refinada em `pausas_refinadas`.
* **Legendas (`gerar_srt` → `legendas_video_longo.srt`):** até 2 linhas de ~42 caracteres, equilibradas e de preferência quebradas depois de vírgula ou ponto; no máximo 7 s e no mínimo 1 s por legenda; nova legenda após uma pausa de 1 s; uma frase só fecha a legenda depois de 1,5 s (para "Sim." não ficar sozinha). Os tempos passam pelo mapa de tempo dos cortes e somam a duração da introdução, se marcada.
* **Verificação inicial:** avisa quando o modelo ainda não foi baixado (tamanho e necessidade de internet) e dá erro, com a instrução, se o pacote `faster-whisper` não estiver instalado.
* **Alucinações do Whisper (`transcricao/alucinacao.py`):** no fim de um bloco o áudio acaba em silêncio e o Whisper "completa" com despedidas de vídeo. Um segmento só é removido se tiver uma frase típica ("Obrigado por assistir", "Inscreva-se no canal"...) **e** um sinal físico de erro (mais de 8 palavras por segundo, ou confiança média abaixo de 0,6), ou se tiver palavras demais para o tempo (acima de 15 por segundo). O que sai fica registrado em `removidos` no `transcricao.json` e no log.
* **Destaque da revisão:** só palavras de 4+ letras com confiança abaixo de 0,5. Palavrinhas ("e", "o", "se") têm confiança baixa o tempo todo sem estarem erradas e poluíam a tela (3,5% das palavras, contra 1,0% agora).

**Resultado no sermão real** (65 min, `large-v3-turbo`, CPU, 9 threads): transcrição de **48 min** (0,74× a duração; 6 blocos cortados em pausas), **303 segmentos, 9.009 palavras (139 por minuto)**, **706 legendas** (632 de 2 linhas e 74 de 1; maior linha de 42 caracteres; nenhuma sobreposta nem abaixo de 0,9 s; leitura média de 13 caracteres por segundo).

**Controle de qualidade feito na transcrição real:** nenhuma repetição em laço, nenhum tempo fora de ordem e ritmo estável (133 a 146 palavras por minuto em todos os blocos). Achados: (1) uma alucinação ("Obrigado por assistir", 3 palavras em 0,14 s, aos 39:03), agora removida automaticamente; (2) "senhor" em minúscula com baixa confiança três vezes onde se quer "Senhor" (Deus), erro real que a revisão destaca; (3) o "Miqueias" saiu certo graças às dicas, mesmo com confiança baixa por ser nome raro. A conferência das pausas com a fala tirou 5 min 09 s do que a energia chamava de pausa (445 → 93 pausas), sem efeito prático aqui porque nenhuma passa de 5,3 s.

**Energia:** `spurgeon acordado --horas 12` (e a opção "Não deixar o PC bloquear nem apagar a tela ao processar", ligada por padrão) pede ao Windows para não suspender, não apagar a tela e não bloquear por inatividade, registrando atividade sem mexer no mouse a cada 45 s. Nada permanente é alterado; tudo volta ao normal quando o programa termina.

### Fase 4: Inteligência de Detecção de Cortes para Shorts (até 60 s) — concluída em 2026-10-13

* **Limite:** Shorts de **no máximo 60 s** (mínimo padrão de 30 s). **Não há número fixo de Shorts**: vale o que passar no critério de qualidade, com um **limite de segurança** (padrão 20) e checagem de espaço em disco antes de cada renderização.
* **Tabela de palavras (`pandas`):** uma linha por palavra (`palavra`, `início`, `fim`, `frase`, `pausa_após`). Substitui o DuckDB do plano original: a lógica de janelas e pausas é simples em DataFrame, sem uma segunda base de dados.
* **Análise estrutural (spaCy):** divide em sentenças reais, marca perguntas (`?`) e detecta citações bíblicas (padrões como "capítulo", "versículo", nomes de livros).
* **Candidatos:** janelas de sentenças inteiras de 30 a 60 s. O início e o fim ficam em **pausas naturais** (folga de ~150 ms antes da primeira palavra e ~200 ms depois da última), sem cortar palavra ao meio. Janelas com pausa interna longa (> 3 s) são descartadas.
* **Seleção por IA local (Ollama):**
  * O LLM recebe cada candidato **com o contexto anterior e o seguinte** e devolve, em JSON validado: categoria, nota de retenção e se o trecho é **autossuficiente**.
  * **Categorias:** pergunta-gancho, citação bíblica, aplicação prática, conclusão.
  * **Regras para não tirar de contexto:** descartar trechos que começam dependendo de algo dito antes ("então", "como eu dizia", pronomes sem referente) ou terminam no meio do argumento; incluir a citação bíblica quando o trecho a comenta; sinalizar com um **alerta de contexto** os trechos que, isolados, possam ser mal entendidos doutrinariamente.
  * **Modelo (a confirmar por mini-benchmark com trechos reais de sermão):** candidato inicial `qwen3:8b` (≈5 GB; indicado como bom em pt-BR por uma única fonte fraca, sem benchmark). O `qwen3:4b` é a alternativa leve. Outras: `llama3.1:8b` e `gemma3:12b` (precisa de mais memória). Para saída estruturada, desligar o modo "thinking" do Qwen3.
  * Os modelos Llama 3 e Phi-3 do plano original foram descartados por serem mais fracos em português.
  * Seleção final: ordenar por nota, **sem sobreposição** entre Shorts, até o limite de segurança.
* **Metadados por Short (LLM):** título, descrição e hashtags em pt-BR, mais um **nome de arquivo** (slug curto, sem acentos). Não é preciso registrar justificativa.
* **Aprovação dos cortes (🛑 opcional):** lista com, para cada Short, ✔ incluir, categoria, início–fim, duração, texto do trecho, alerta de contexto e título/descrição/hashtags editáveis. Dá para ajustar início e fim (encaixando em limites de palavra e pausa).
* **Memória:** o Whisper é descarregado antes de carregar o LLM, e o LLM é descarregado ao fim (`keep_alive=0`).

**Implementação (concluída em 2026-10-13).** Etapas em `src/spurgeon/shorts/`: `analisar_shorts` → `avaliar_shorts` → `selecionar_shorts` → `aprovar_cortes` (🛑). Tudo é gravado em `_work/` (`candidatos.json`, `avaliacoes.json`, `metadados_shorts.json`, `cortes_shorts.json`), então uma queda só custa o trecho em curso.

* **Frases e candidatos:** o spaCy (`pt_core_news_md`, sem NER nem lematizador) recebe as palavras já separadas pelo Whisper. Uma frase termina em `. ? ! …` ou numa pausa de 0,35 s onde o spaCy vê início de oração; `abre_dependente` marca frases que começam com conectivo ("mas", "então"), pronome ("isso", "ele") ou conjunção. As janelas são sempre de frases inteiras, de 30 s até 60 s menos 0,55 s de respiro; pausa interna acima de 3 s descarta a janela. Cada janela recebe uma **pré-nota objetiva** (0 a 100: duração 20, bordas em pausas 30, abertura sem dependência 20, gancho 20, ritmo 10) e as 40 melhores (sobreposição ≤ 60%) vão para a IA.
* **IA (Ollama, `qwen3:8b`):** recebe o trecho com o contexto anterior e o seguinte e devolve **JSON validado** (categoria, nota de 0 a 10, se se sustenta sozinho, alerta de contexto, motivo). `think=False` (o modo de raciocínio gasta minutos sem melhorar a nota), até 3 tentativas com temperatura crescente, e **só CPU** (`num_gpu=0`) quando não há GPU utilizável: o Ollama novo usa Vulkan e a GTX 760 derruba o processo ("device lost"). Se o modelo não estiver instalado, o aplicativo o baixa (o pré-voo só avisa). Ao fim da seleção o modelo sai da memória (~5 GB) para sobrar RAM à renderização.
* **Nota de qualidade (0 a 100), mostrada na aprovação com o detalhe de cada parte:** retenção segundo a IA × 4 (até 40) + se se sustenta sozinho (12 se a IA diz que sim, mais 8 se a abertura não depende do que veio antes; até 20) + começa e termina em pausas (15) + duração (10) + gancho e fecho (10) + ritmo da fala (5), **menos 10** com alerta de contexto.
* **Seleção:** por nota, sem sobreposição maior que 20%, até o limite de segurança (20) marcados, mais 8 **reservas** desmarcadas. Abaixo da nota mínima (45, nas opções avançadas) o corte também vira reserva. O respiro nas pontas é de até 0,20 s antes e 0,35 s depois, nunca mais que metade da pausa vizinha.
* **Metadados por corte:** título, descrição, hashtags e nome de arquivo (`slug`). O pedido lista os **títulos já criados** e, se a IA repetir um, pede outro com mais temperatura. (O primeiro prompt tinha um título de exemplo, e a IA o copiou em 8 dos 9 primeiros cortes: exemplos concretos não vão mais no prompt.) Cada corte pronto é guardado na hora; o que a pessoa editou na aprovação tem prioridade numa nova seleção.
* **Janela de aprovação (`ui/revisao_cortes.py`):** lista com ✔ incluir, **nota colorida**, categoria, início–fim e duração; ao lado, o detalhe da nota (uma barra por parte), o alerta de contexto, o motivo da IA, **título, descrição e hashtags editáveis**, o texto e os botões ◀/▶ que movem o início e o fim **de palavra em palavra** (sempre com respiro, no máximo 60 s e no mínimo 10 s). Todos os cortes marcados vêm aprovados; reservas vêm desmarcadas. Ordena por nota ou por horário, com atalhos (Alt+↑/↓, Ctrl+Espaço, Ctrl+S). Sem título, ou sem nenhum corte marcado, ela pergunta antes de salvar.

**Resultado no sermão real** (65 min): 180 frases, 249 janelas, 40 trechos avaliados pela IA (~50 s cada, **33 min**), 28 títulos (~55 s cada, 26 min), **20 cortes marcados com notas de 75 a 89** (34 a 60 s; 12 de "pergunta-gancho", 7 de "aplicação prática", 1 de "citação bíblica") e 8 reservas (nota 69 a 75). A IA é generosa (quase tudo de 8 a 9 em 10), por isso a nota final pesa também os sinais objetivos. Um corte tem alerta de contexto entre os marcados.

### Fase 5: Renderização e Estilização Visual (FFmpeg + legendas ASS) — concluída em 2026-10-13

O motor é o **FFmpeg direto** (chamado pelo Python) com legendas **ASS**. Motivo: o MoviePy 2.x tem mudanças incompatíveis com a v1 e renderiza frame a frame em Python, o que é lento e pesado com legendas por palavra. O MoviePy fica de fora da v1.

* **Qualidade de saída (preset personalizável):**

  | Preset | Codec | Configuração |
  |---|---|---|
  | **Máxima qualidade (padrão)** | libx264 | `-preset veryslow -crf 16` |
  | Alta | libx264 | `-preset slow -crf 18` |
  | Equilibrado | libx264 | `-preset medium -crf 20` |
  | Rápido | libx264 | `-preset veryfast -crf 23` |
  | Personalizado | à escolha | preset, CRF e NVENC (se a GPU suportar) |

  Comum a todos: `-pix_fmt yuv420p`, `-movflags +faststart`, áudio AAC 256 kbps a 48 kHz, fps da origem. O preset padrão é muito lento em CPU; a interface mostra o aviso de tempo estimado.

  **Tempos medidos nesta máquina** (20 s do sermão real, 1080p com conversão HDR→SDR, 12 threads), projetados para 65 min:

  | Preset | Velocidade | Para 65 min de vídeo |
  |---|---|---|
  | só decodificar + HDR→SDR | 38,7 fps | ~50 min |
  | Rápido (CRF 23) | 33,9 fps | ~58 min |
  | Equilibrado (CRF 20) | 26,8 fps | ~1 h 13 min |
  | Alta (CRF 18) | 20,3 fps | ~1 h 38 min |
  | **Máxima (CRF 16)** | 7,9 fps | **~4 h 06 min** |

* **Material real de entrada (iPhone, `Miqueias 2. 6-11.MOV`):** MOV de 65 min e 4 GB, **HEVC Main 10 em HDR HLG** (BT.2020, 10 bits, com Dolby Vision perfil 8), 1920×1080 a 29,97 fps, áudio AAC estéreo de 48 kHz, mais 5 trilhas de dados. Consequências para a renderização:
  * **HDR → SDR (decidido pelo autor em 2026-10-12):** converter para SDR BT.709 em H.264 de 8 bits, compatível com qualquer tela. Sem converter, o H.264 de 8 bits deixa as cores lavadas. Filtros: `zscale` + `tonemap=hable` (o FFmpeg 9.0.2 tem os dois).
  * **Mapear só o que interessa:** `-map 0:v:0 -map 0:a:0`, para as trilhas de dados do iPhone não entrarem no MP4.
  * **Taxa de quadros constante:** a origem oscila de leve (29,97 fps médios, com quadros a 0,035 s). Forçar `fps=30000/1001` antes de cortar, para o áudio não dessincronizar.
  * **A conversão HDR→SDR é um gargalo:** só decodificar e converter já roda a 38,7 fps, quase a mesma velocidade do preset Rápido (33,9 fps), o que indica que o filtro limita a codificação em vez do x264. A ideia a testar na Fase 5 é renderizar os trechos em **processos paralelos** e juntá-los com `-c copy`; o áudio é codificado à parte, uma vez, e muxado no final.
* **Vídeo longo:**
  * Aplica os cortes de pausa (filtros `trim/atrim + concat` lidos de um arquivo de script de filtro, para não estourar o limite de linha de comando do Windows) e troca a trilha pelo áudio limpo/normalizado.
  * **Introdução e vinheta final** (de `recursos/`), quando marcadas: são padronizadas (resolução, fps, SAR, taxa de áudio) para o vídeo principal e concatenadas na mesma passagem de encode. Valem só para o vídeo longo, nunca para os Shorts.
  * Exporta `video_longo_otimizado.mp4`; o .srt fica separado, sem legenda queimada.
* **Shorts (vertical 9:16, 1080×1920):**
  * **Enquadramento:** o vídeo é 16:9 e o pastor fica no centro, parado. Usa **recorte central fixo** (≈608×1080 de uma origem 1080p) e **amplia** com `lanczos` para 1080×1920. Há um ajuste de deslocamento horizontal no Avançado. Origem de menor resolução é ampliada do mesmo jeito (aceito pelo autor).
  * **Áudio:** do áudio limpo, normalizado a −14 LUFS se marcado, com fade de ~100 ms nas pontas contra estalos.
  * **Legendas dinâmicas (estilo karaokê):**
    * Geradas em `.ass` a partir dos tempos por palavra e queimadas com o filtro `ass`.
    * Texto **branco com contorno preto**; a **palavra falada no momento fica em amarelo**, uma palavra por vez, com a frase sempre visível.
    * **Fonte livre (OFL)** empacotada em `recursos/fontes/` (padrão sugerido: Montserrat ExtraBold; alternativas Anton e Poppins).
    * **Posição:** terço central da tela, na parte inferior dele (cerca de 62% da altura, configurável), longe dos elementos de interface dos Shorts (título e botões na parte baixa).
    * Frases de até 2 linhas, com quebra em limites de palavra.
  * Renderiza **um Short por vez** (sem paralelismo) para não estourar a RAM.
* **Sem logo, marca d'água nem título fixo** na v1.

**Implementação (concluída em 2026-10-13).** Módulos em `src/spurgeon/render/`: `filtros.py` (HDR→SDR, recorte 9:16, codec), `video_longo.py`, `shorts.py`, `legenda.py` (ASS). As etapas `renderizar_shorts` e `renderizar_video_longo` rodam **depois** de toda a análise e das revisões humanas, para a pessoa responder tudo no começo e deixar as horas de renderização rodando sozinha.

* **HDR→SDR:** `zscale` (HLG ou PQ → luz linear → primárias BT.709) + `tonemap=hable:desat=0` + volta à curva BT.709, 8 bits. O `desat` padrão (2,0) apagava a cor da cruz dourada do púlpito; com `desat=0` a cor se mantém, ao custo de uma leve tinta ciano na tela de projeção (muito brilhante). Origem SDR só ganha o formato de pixel e a marcação BT.709.
* **Vídeo longo em partes retomáveis:** o FFmpeg 9 não aceita mais `-filter_complex_script`; os filtros vão por `-/filter_complex arquivo`. O vídeo sai em **partes de ~10 min** (`_work/render_longo/parte_NNN.mp4`), cada uma com um número exato de quadros; o áudio é montado uma vez (`audio.m4a`) e no fim as partes e o áudio são juntados **sem recodificar**. Cair, cancelar ou faltar luz só custa a parte em curso. Uma assinatura (vídeo, cortes, qualidade, introdução…) recomeça do zero se algo mudar.
* **Sincronia (controle de qualidade):** o primeiro corte de áudio usava o demuxer `concat` do FFmpeg, que errava ~65 ms por corte (achado no teste com cortes reais). Agora o áudio usa `atrim` (exato até a amostra) e cada pedaço de vídeo recebe quadros por arredondamento do tempo **acumulado**, sem passar de meio quadro de erro. O teste usa um vídeo em que cada quadro traz o próprio número em binário e um áudio de ruído branco, confere a imagem e correlaciona o som: deslocamento ≤ 3 ms depois de 3 cortes e 3 fronteiras de partes.
* **Introdução e vinheta:** padronizadas (tamanho com barras pretas, taxa de quadros, SAR, SDR, estéreo de 48 kHz; vinheta muda ganha silêncio; com "normalizar volume", passam por `loudnorm` no alvo do vídeo longo) e viram partes próprias. Toda duração é múltiplo exato de um quadro.
* **Espaço em disco:** o estimador usa o bitrate medido (**5,5 Mb/s** em CRF 16 no vídeo horizontal; ~10 Mb/s no vertical (9,4 medidos); +6 de CRF corta pela metade). Se só couber no **modo econômico**, a junção apaga cada parte assim que a lê. O pré-voo estima o espaço pela duração e pelas opções (antes era "3× o tamanho do arquivo").
* **Shorts (1080×1920):** um Short por vez, cada um gravado só quando termina (`.parcial` → nome final) e conferido (duração, áudio); os já prontos são reaproveitados por uma assinatura do corte e das opções, e os que saíram da aprovação são removidos. Ordem: `fps` constante → recorte central (+4% para a direita: a câmera fixa deixa o pregador e a cruz a ~54% da largura) → HDR→SDR (só nos 608×1080 recortados) → `lanczos` para 1080×1920 → legenda ASS → `yuv420p`. Áudio: estéreo, **loudnorm de duas passagens a −14 LUFS** (medido −14,0 LUFS no resultado) e fade de 100 ms nas pontas. Arquivos: `short_NN_<slug>.mp4` e `.txt` com título, descrição, hashtags, trecho de origem, nota e alerta.
* **Legenda dinâmica (`legenda.py`):** a frase fica na tela e **a palavra falada fica amarela**; cada palavra vira um evento ASS com o mesmo texto. Montserrat ExtraBold (OFL, em `recursos/fontes/` com a licença), tamanho 74, contorno de 5 px, espaçamento de 2, posição 62% da altura, até 24 caracteres por linha e 2 linhas, quebra equilibrada. Fica **desligada por padrão** ("Legendas dinâmicas" nas opções); `--legendas` na linha de comando liga.
* **Medidas nesta máquina** (veryslow, CRF 16, HDR→SDR, 40 s do sermão real): **8,6 quadros/s** (65 min ≈ 3 h 55 min) e 5,5 Mb/s. `veryfast` com HDR: ~34 quadros/s.

### Fase 6: Organização e Armazenamento Local — concluída em 2026-10-13

O app cria a estrutura abaixo automaticamente. `Downloads/` é a pasta de entrada, `recursos/` guarda os arquivos fixos e `Projetos/` recebe os resultados.

```
📁 Downloads/                         ← entrada (vídeos brutos)
📁 recursos/
   ├── 🎬 introducao.mp4
   ├── 🎬 vinheta_final.mp4
   ├── 📄 vocabulario_teologico.txt
   └── 📁 fontes/
📁 Projetos/
└── 📁 2026-10-12_Nome_Do_Sermao/      ← data da GRAVAÇÃO + nome
    ├── 🎬 video_longo_otimizado.mp4
    ├── 📄 legendas_video_longo.srt
    ├── 📄 transcricao.json            ← checkpoint palavra a palavra
    ├── 📄 estado.json                 ← etapa atual, para retomar
    ├── 📄 processamento.log           ← o que foi feito e os erros
    ├── 📁 shorts_gerados/
    │   ├── 🎬 short_01_<nome_dado_pelo_llm>.mp4
    │   ├── 📄 short_01_<nome_dado_pelo_llm>.txt   ← título, descrição e hashtags
    │   └── ...
    └── 📁 _work/                      ← intermediários (apagada ao concluir)
```

* **Nome e data:** a pasta usa a data da gravação. Se a pasta já existir e for o **mesmo vídeo** (conferido por hash), o app **retoma o projeto**. Se for outro vídeo com o mesmo nome, ele pergunta antes de misturar os arquivos.
* **Vídeo original:** é **referenciado** (não copiado) pelo caminho.
* **Intermediários:** `_work/` é apagada **só após o sucesso total**. Se algo falhar ou for cancelado, ela permanece para a retomada (opção "manter intermediários" no Avançado).
* **Log (`processamento.log`):** texto legível com horário, nível e etapa; erros com traceback e o que o sistema fez em seguida.

**Implementação (concluída em 2026-10-13).**

* **Estrutura:** `Downloads/`, `Projetos/`, `recursos/` e `recursos/fontes/` são criadas na abertura do aplicativo e na pré-verificação.
* **Conferência final (`finalizar`, `render/finalizar.py`):** depois das renderizações, confere o vídeo longo (imagem e som, duração = cortes + introdução + vinheta, ±1 s), a legenda (a última não passa do fim do vídeo) e cada Short (1080×1920, com áudio, até 60 s, com o `.txt`), e registra no log o **resumo** (arquivos, durações, tamanhos, nota de cada Short e o tempo total sem contar esperas por revisão). Problema grave vira erro e **deixa `_work/` no lugar** para retomar; só um processamento conferido apaga os intermediários (a menos que "manter intermediários" esteja marcado).
* **Tempo estimado antes de começar:** a janela mostra, ao escolher o vídeo e a cada mudança de opção, o tempo estimado total e o da renderização nesta máquina (HDR ou não, preset, Shorts, modelo do Whisper).
* **Progresso:** o peso de cada renderização no progresso total e no tempo restante acompanha a qualidade escolhida (`Etapa.peso_para(opcoes)`).
* **Linha de comando:** `--shorts`, `--legendas` e `--qualidade maxima|alta|equilibrado|rapido`; as revisões ficam desligadas sem janela.

---

## 3. Estado e Retomada (se a operação cair, volta de onde parou)

`estado.json` (escrita atômica) registra a etapa de cada passo do pipeline:

```json
{
  "versao": 1,
  "entrada": { "caminho": "...", "tamanho": 0, "hash_rapido": "..." },
  "opcoes":  { "limpar_audio": true, "limiar_pausa_s": 10.0, "whisper": "large-v3-turbo" },
  "etapas": [
    { "id": "extrair_audio", "status": "concluida", "artefatos": ["_work/audio_original.wav"] },
    { "id": "transcrever",   "status": "em_andamento", "progresso": 0.42 },
    { "id": "revisar_transcricao", "status": "pendente" }
  ]
}
```

* **Status:** `pendente`, `em_andamento`, `aguardando_revisao`, `concluida`, `erro`, `cancelada`.
* **Hash rápido do vídeo:** tamanho + SHA-256 dos primeiros e últimos 4 MB.
* **Regras de retomada:**
  * Etapa `concluida` com artefatos presentes é pulada.
  * Etapa `em_andamento` recomeça do último ponto salvo (último bloco de transcrição, último Short renderizado).
  * Mudar uma opção invalida só as etapas que dependem dela.
  * `aguardando_revisao` sobrevive a fechar o app: ao reabrir, volta ao painel de revisão.

---

## 4. Fora do Escopo da v1

* Preview de vídeo na interface; fila de vários vídeos.
* Enquadramento com rastreamento de rosto ou fundo desfocado (hoje: recorte central fixo).
* Logo, marca d'água, título fixo no vídeo; introdução e vinheta nos Shorts.
* Remoção de muletas de fala ("é...", "hum").
* Diarização (identificar vários falantes).
* Empacotamento em `.exe` (PyInstaller), a fazer depois para distribuir a outros PCs.
* Legendas em outros idiomas.

---

## 5. Hardware: pontos de atenção

* **O PC-alvo da v1 é este PC de desenvolvimento.** Inspecionado em 2026-10-12: i7-6800K (6 núcleos/12 threads físicos), **15,8 GB de RAM**, **GTX 760 de 2 GB** (driver 456.71), **36 GB livres** no C:, placa-mãe Huananzhi X99 e Virtualization-based Security ativo (`HypervisorPresent=True`).
* **Limite de processadores corrigido em 2026-10-12.** Antes o Windows enxergava só 2 processadores lógicos, embora a BIOS já informasse 6 núcleos e 12 threads. Depois de reiniciar o PC, `NUMBER_OF_PROCESSORS=12`. Não sei qual ajuste resolveu (BIOS, `msconfig` ou `bcdedit`). Se o número voltar a cair, reveja essas três.
* A GTX 760 provavelmente não roda o faster-whisper atual (CUDA 12 + cuDNN 9; a compatibilidade ainda precisa de teste na Fase 3). O **NVENC está confirmado como indisponível** (teste real com o FFmpeg 9.0.2, no `spurgeon doctor`): o encode é em libx264 na CPU. O **caminho só com CPU é o principal** desta máquina.
* Mesmo com 12 threads, o caminho só com CPU deixa o encode `veryslow` (medido: ~4 vezes a duração do vídeo), o Whisper e o LLM de 8B lentos (estes dois ainda não medi). Os avisos de tempo da interface continuam sendo requisitos, não extras.
* Recomendação inicial por hardware, para esta e futuras máquinas (a validar com benchmark):

  | Hardware | Whisper | LLM |
  |---|---|---|
  | Sem GPU NVIDIA compatível | large-v3-turbo (`int8`), com aviso de tempo | `qwen3:8b` com ≥16 GB de RAM; `qwen3:4b` com menos |
  | NVIDIA com ≥6 GB de VRAM (CUDA 12) | large-v3 (`float16` ou `int8_float16`) | `qwen3:8b` |
  | NVIDIA com ≥12 GB de VRAM | large-v3 | modelo maior (opcional) |

---

## 6. Decisões Confirmadas e Pendências

### Confirmadas pelo autor (2026-10-12)

* **PC-alvo da v1:** o mesmo PC de desenvolvimento (seção 5).
* **Legenda dos Shorts:** frase visível, com a palavra falada em amarelo, uma por vez.
* **Posição da legenda:** parte baixa do terço central (≈62% da altura).
* **Pausa:** padrão 10 s, faixa de 0,5 a 30 s, com pausa residual de 1,0 s em cada corte.
* **Revisão da transcrição:** o processo pausa para revisar o .srt.
* **Introdução e vinheta final:** só nos vídeos longos.
* **Shorts:** mínimo de 30 s e máximo de 60 s, limite de segurança de 20 por vídeo, descarte de janelas com pausa interna maior que 3 s.
* **Loudness:** −16 LUFS (vídeo longo) e −14 LUFS (Shorts).
* **Qualidade padrão:** máxima (`veryslow`, CRF 16), com aviso de tempo estimado na interface. Mantida depois de medir (~4 h para 65 min de vídeo): o autor roda o PC de madrugada.
* **HDR do iPhone:** convertido para SDR BT.709 em H.264 de 8 bits, compatível com qualquer tela.
* **LLM:** `qwen3:8b` como candidato provisório, com `qwen3:4b` como alternativa leve.

### Pendências técnicas

1. **Mini-benchmark do LLM** em pt-BR, com trechos reais de sermão (`qwen3:4b`, `qwen3:8b`, `llama3.1:8b`).
2. **Comparar visualmente** a qualidade "Alta" (CRF 18) e a "Máxima" (CRF 16) em trechos do sermão, para o autor saber o quanto a diferença de ~2 h 30 min compensa.

### Decisão que depende do autor (descoberta com o sermão real)

3. **Tamanho mínimo da pausa a cortar:** no sermão real o padrão de 10 s não corta nada (a maior pausa tem 5,3 s). Manter 10 s como padrão, ou usar um valor menor (por exemplo 2 s, que corta 10 pausas e 17 s)? A remoção de silêncios encurta menos de 1 minuto em 65 mesmo em 1 s, então vale também decidir se ela continua ligada por padrão.
