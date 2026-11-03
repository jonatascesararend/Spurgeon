# Guia de Projeto: Studio Spurgeon de Cortes e Otimização de Vídeos (Sermões, Aulas e Shorts)

> **Versão 5 — 2026-10-15: versão 1.2 do aplicativo.** A 1.0 (Fases 0 a 6) foi testada com o sermão real de 65 min; a 1.1 (Fase 7) acrescentou a marca d'água, os modos "só Shorts" e "só vinhetas", a abertura de projetos, a aba **Studio Shorts** e o verificador de português; a 1.2 (Fase 8) traz o corte do começo e do fim do vídeo com prévia, o zoom, o tratamento de imagem, o fade e a escolha do arquivo das vinhetas, o modelo do Ollama em lista, Shorts de até 180 s e a legenda do Studio que acompanha os ajustes.
> As decisões da pré-entrevista estão confirmadas pelo autor; a seção 6 registra as decisões, as escolhas feitas sem consulta e as pendências.

---

## 1. Visão Geral da Arquitetura

A aplicação é um aplicativo desktop nativo para Windows (Python + CustomTkinter, tema escuro, somente pt-BR). Ela detecta as especificações do PC, recomenda o tamanho do faster-whisper compatível com a máquina e usa os recursos disponíveis (CPU, RAM, GPU) conforme o limite escolhido pelo usuário.

### 1.1 Escopo e premissas

* **Público:** v1 para o PC do autor (por enquanto, o mesmo PC de desenvolvimento); depois, instalação em outros PCs. Por isso a detecção de hardware é requisito real, não enfeite.
* **Material de entrada:** vídeos de 45 min a 1h30, somente fala (sem música), câmera fixa em 16:9, pastor sempre no centro do quadro e sem se mover.
* **Um vídeo por vez** (sem fila na v1).
* **100% offline** depois de instalar as dependências e baixar os modelos. A única conexão que o programa faz sozinho é a consulta de atualização ao abrir (`core/atualizacao.py`), que a pessoa pode desligar.
* **Sem player de vídeo embutido** na interface. A revisão é feita sobre texto e tempos; as únicas imagens são quadros de exemplo (corte, zoom e tratamento de cor) e uma prévia de 15 s que abre no player do Windows (Fase 8).
* **Idioma:** somente pt-BR (Whisper `language="pt"`, spaCy em português, prompts do LLM em português).
* **Teologia:** o conteúdo é de teologia reformada calvinista; isso orienta o vocabulário do Whisper e as regras de contexto dos cortes.

### 1.2 Uso de recursos (escolha na interface)

Seletor "Uso de processamento":

* ( ) **75% (recomendado)**: deixa o PC utilizável durante o processo.
* ( ) **100% (máximo)**: usa tudo; o PC fica praticamente inutilizável até terminar.

Aplicação do limite: `cpu_threads` do faster-whisper, `-threads` do FFmpeg, `num_thread` do Ollama e variáveis `OMP_NUM_THREADS` (o `deep-filter` não tem opção de threads). GPU é usada quando existir e for compatível. As etapas rodam **em sequência**, nunca em paralelo, e cada modelo é descarregado da memória antes de carregar o próximo (Whisper → LLM).

### 1.3 Fluxo geral do usuário

1. Selecionar o vídeo longo (a janela abre em `Downloads/`) ou colar o caminho — **ou** clicar em **Abrir projeto…** e escolher a pasta de um projeto já criado, para aproveitar tudo o que ele já tem (a tela volta com o nome, a data, o vídeo e as opções do projeto).
2. Conferir nome do sermão e data da gravação (preenchidos automaticamente, editáveis).
3. Escolher o **modo** (Tudo, Só Shorts ou Só vinhetas; a janela mostra só o que vale para ele), cortar o começo e o fim e ajustar o zoom, o tratamento, a marca d'água, os pontos de revisão e o processamento, e clicar em **Iniciar Processamento**.
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
[10] Finalizar: apagar os intermediários pesados de _work/, registrar resumo no log
```

**Modos (v1.1).** O mesmo pipeline roda em três modos (`OpcoesProcessamento.modo`): `completo` (tudo o que estiver marcado); `so_shorts` (os passos do vídeo longo ficam de fora, e a transcrição e a análise já feitas são aproveitadas); `so_vinhetas` (só a etapa `adicionar_vinhetas`: codifica a introdução e a vinheta com o mesmo preset do vídeo, emenda sem recodificar o vídeo principal e desloca o `.srt`; o resultado é `video_longo_com_vinhetas.mp4`). `Pipeline.ativas` filtra as etapas pelo modo, e o registro das etapas fora do modo fica em `estado.etapas_guardadas`, para não se perder o que já foi feito.

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
├── iniciar.bat                 ← abre o app pelo pythonw (sem console; v1: sem .exe)
├── instalar.bat                ← chama o instalar.ps1
├── instalar.ps1                ← instala o que faltar num PC zerado (Visual C++, uv, Python, FFmpeg, Ollama, modelos, atalhos)
├── src/spurgeon/
│   ├── core/                   ← config, log, estado, hardware, pipeline, ffmpeg, mapa de tempo
│   ├── audio/                  ← extrair, limpar, normalizar
│   ├── transcricao/            ← whisper, vocabulário, srt, dicionário e verificador de português
│   ├── shorts/                 ← nlp, llm, candidatos, metadados, edição dos cortes, busca e Shorts livre
│   ├── render/                 ← video_longo, shorts, ass, marca d'água, vinhetas
│   └── ui/                     ← janela principal, abas, painéis de revisão, Studio Shorts
├── tests/
├── Downloads/                  ← ENTRADA: vídeos brutos          (ignorado no git)
├── recursos/                   ← introdução, vinheta, vocabulário, fontes, marca d'água, dicionários
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
* **Avançado:** pausa residual mantida ao cortar, duração mínima e máxima dos Shorts, modelo do Ollama, vocabulário teológico, deslocamento horizontal do recorte, limite de segurança de Shorts, manter intermediários.
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

### Fase 4: Inteligência de Detecção de Cortes para Shorts (até 60 s; desde a 1.2, até 180 s) — concluída em 2026-10-13

> **Desde a 1.2 (Fase 8) o limite é de 180 s**, com preferência por trechos de ~60 s; onde esta seção diz "60 s", leia o limite de 180 s e a regra de peso por duração da Fase 8.

* **Limite:** Shorts de **no máximo 60 s** (mínimo padrão de 30 s). **Não há número fixo de Shorts**: vale o que passar no critério de qualidade, com um **limite de segurança** (padrão 20) e checagem de espaço em disco antes de cada renderização.
* **Tabela de palavras (`pandas`):** uma linha por palavra (`palavra`, `início`, `fim`, `frase`, `pausa_após`). Substitui o DuckDB do plano original: a lógica de janelas e pausas é simples em DataFrame, sem uma segunda base de dados.
* **Análise estrutural (spaCy):** divide em sentenças reais, marca perguntas (`?`) e detecta citações bíblicas (padrões como "capítulo", "versículo", nomes de livros).
* **Candidatos:** janelas de sentenças inteiras de 30 a 60 s. O início e o fim ficam em **pausas naturais** (folga de ~150 ms antes da primeira palavra e ~200 ms depois da última), sem cortar palavra ao meio. Janelas com pausa interna longa (> 3 s) são descartadas.
* **Seleção por IA local (Ollama):**
  * O LLM recebe cada candidato **com o contexto anterior e o seguinte** e devolve, em JSON validado: categoria, nota de retenção e se o trecho é **autossuficiente**.
  * **Categorias:** pergunta-gancho, citação bíblica, aplicação prática, conclusão. (Desde a 1.5 são 17, cada uma com cor própria: veja a rodada 1.5.)
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
* **Seleção:** por nota, sem sobreposição maior que 20%, até o limite de segurança (20) marcados, mais 8 **reservas** desmarcadas. Abaixo da nota mínima (45, nas opções avançadas) o corte também vira reserva. **Início e fim procuram o silêncio de verdade** (`shorts/bordas.py`, usando as janelas de 50 ms de `medir_nivel`): o corte começa 100 ms antes de a voz subir e termina 100 ms depois de ela morrer, sem invadir a palavra vizinha e sem passar de 60 s; os tempos do Whisper erram dezenas de milissegundos, e um respiro fixo cortava o fim de sílabas. Sem a medição, vale um respiro de até 0,20 s antes e 0,35 s depois.
* **Metadados por corte:** título, descrição, hashtags e nome de arquivo (`slug`). O pedido lista os **títulos já criados** e, se a IA repetir um, pede outro com mais temperatura. (O primeiro prompt tinha um título de exemplo, e a IA o copiou em 8 dos 9 primeiros cortes: exemplos concretos não vão mais no prompt.) Cada corte pronto é guardado na hora; o que a pessoa editou na aprovação tem prioridade numa nova seleção.
* **Janela de aprovação (`ui/revisao_cortes.py`):** lista com ✔ incluir, **nota colorida**, categoria, início–fim e duração; ao lado, o detalhe da nota (uma barra por parte), o alerta de contexto, o motivo da IA, **título, descrição e hashtags editáveis**, o texto e os botões ◀/▶ que movem o início e o fim **de palavra em palavra** (sempre com respiro e no mínimo 10 s; passar de 60 s é permitido, com aviso, mas esse corte não pode ser Short). Todos os cortes marcados vêm aprovados; reservas vêm desmarcadas. Ordena por nota ou por horário, com atalhos (Alt+↑/↓, Ctrl+Espaço, Ctrl+S). Sem título, ou sem nenhum corte marcado, ela pergunta antes de salvar.

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
* **Volume do estéreo (achado no controle de qualidade):** o áudio do iPhone é dual-mono e a extração o guarda em mono, mas dois canais iguais medem **3 dB acima** do mono (BS.1770). Três dos 20 Shorts saíram em −11 LUFS (alvo −14) porque o `loudnorm` de duas passagens media o mono e aplicava no estéreo; o vídeo longo, normalizado a −16 LUFS no mono, sairia em −13. Agora a medição dos Shorts passa pelo mesmo estéreo (resultado: −14,0 LUFS) e o vídeo longo compensa −3 dB nos dois canais quando o volume foi normalizado (medido −16,0 LUFS). Sem normalizar, o estéreo duplicado preserva o nível do original.
* **Fades:** 100 ms nas pontas, que encolhem (piso de 20 ms) quando a fala começa ou termina colada no corte, para não apagar a primeira ou a última sílaba.
* **Introdução e vinheta:** padronizadas (tamanho com barras pretas, taxa de quadros, SAR, SDR, estéreo de 48 kHz; vinheta muda ganha silêncio; com "normalizar volume", passam por `loudnorm` no alvo do vídeo longo) e viram partes próprias. Toda duração é múltiplo exato de um quadro.
* **Espaço em disco:** o estimador usa o bitrate medido (**5,5 Mb/s** em CRF 16 no vídeo horizontal; ~10 Mb/s no vertical (9,4 medidos); +6 de CRF corta pela metade). Se só couber no **modo econômico**, a junção apaga cada parte assim que a lê. O pré-voo estima o espaço pela duração e pelas opções (antes era "3× o tamanho do arquivo").
* **Shorts (1080×1920):** um Short por vez, cada um gravado só quando termina (`.parcial` → nome final) e conferido (duração, áudio); os já prontos são reaproveitados por uma assinatura do corte e das opções, e os que saíram da aprovação são removidos. Ordem: `fps` constante → recorte central (+4% para a direita: a câmera fixa deixa o pregador e a cruz a ~54% da largura) → HDR→SDR (só nos 608×1080 recortados) → `lanczos` para 1080×1920 → legenda ASS → `yuv420p`. Áudio: estéreo, **loudnorm de duas passagens a −14 LUFS** (medido −14,0 LUFS no resultado) e fade de 100 ms nas pontas. Arquivos: `short_NN_<slug>.mp4` e `.txt` com título, descrição, hashtags, trecho de origem, nota e alerta.
* **Legenda dinâmica (`legenda.py`):** a frase fica na tela e **a palavra falada fica amarela**; cada palavra vira um evento ASS com o mesmo texto. Montserrat ExtraBold (OFL, em `recursos/fontes/` com a licença), tamanho 74, contorno de 5 px, espaçamento de 2, posição 62% da altura, até 24 caracteres por linha e 2 linhas, quebra equilibrada. Fica **desligada por padrão** ("Legendas dinâmicas" nas opções); `--legendas` na linha de comando liga.
* **Medidas nesta máquina** (veryslow, CRF 16, HDR→SDR, 40 s do sermão real): **8,6 quadros/s** (65 min ≈ 3 h 55 min) e 5,5 Mb/s. `veryfast` com HDR: ~34 quadros/s.

**Resultado no sermão real** (Máxima qualidade, 65 min, HDR HLG → SDR; o vídeo longo e a nova renderização dos Shorts rodaram juntos por ~2,5 h):

* **Vídeo longo:** `video_longo_otimizado.mp4` com **2,87 GB** (5,9 Mb/s), 1920×1080 BT.709, **3.897,66 s e 116.813 quadros**, exatamente a duração do original (nenhuma pausa passou de 10 s, então não houve cortes). Seis partes de ~10 min, ~35 min cada com a CPU livre. Volume **−16,0 LUFS**. Conferido: o áudio coincide com o original em **0,0 ms** em todas as fronteiras de partes e a imagem acompanha o original sem deriva (±1 quadro de ruído em cenas quase paradas).
* **Shorts:** 20 arquivos de 34 a 60 s (16 min 41 s no total, **1,12 GB**, ~9 Mb/s), **−14,2 a −13,8 LUFS**, ~4 min de renderização cada. O início cai em silêncio em 19 dos 20 (o 16 começa colado na fala) e o fim em 16 (os 11, 15, 16 e 17 acabam com a voz ainda audível porque não há pausa ali); a janela de aprovação permite ajustar esses cortes.
* **Tempo:** análise e transcrição ~1 h 50 min (a transcrição sozinha, 48 min); IA dos Shorts ~1 h; Shorts ~1 h 20 min; vídeo longo ~3 h 50 min com a CPU livre. A etapa final conferiu todos os arquivos e apagou `_work/`.

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
    │   ├── 📁 postados/    ← os Shorts marcados como "já postado no YouTube" no Studio (o .mp4 e o .txt)
    │   └── ...
    └── 📁 _work/                      ← intermediários (os pesados são apagados ao concluir; os .json da análise ficam)
```

* **Nome e data:** a pasta usa a data da gravação. Se a pasta já existir e for o **mesmo vídeo** (conferido por hash), o app **retoma o projeto**. Se for outro vídeo com o mesmo nome, ele pergunta antes de misturar os arquivos.
* **Vídeo original:** é **referenciado** (não copiado) pelo caminho.
* **Intermediários:** os arquivos pesados de `_work/` (áudios, partes do vídeo, legendas ASS, fontes) são apagados **só após o sucesso total**. Se algo falhar ou for cancelado, ela permanece para a retomada (opção "manter intermediários" no Avançado). **Desde a 1.1 os `.json` da análise ficam** (candidatos, notas da IA, cortes e títulos dos Shorts, registro do que já foi renderizado, medidas de volume): são poucos MB e permitem reabrir o projeto, escolher outros Shorts e corrigir legendas sem refazer a análise. Os áudios são refeitos sob demanda (~11 min no sermão real) se for preciso renderizar de novo.
  * *Lição (2026-10-13):* antes da 1.1 a limpeza apagava tudo, e a seleção dos Shorts do sermão real só pôde ser recuperada refazendo a avaliação da IA (~35 min). Por isso a análise é preservada.
* **Log (`processamento.log`):** texto legível com horário, nível e etapa; erros com traceback e o que o sistema fez em seguida.

**Implementação (concluída em 2026-10-13).**

* **Estrutura:** `Downloads/`, `Projetos/`, `recursos/` e `recursos/fontes/` são criadas na abertura do aplicativo e na pré-verificação.
* **Conferência final (`finalizar`, `render/finalizar.py`):** depois das renderizações, confere o vídeo longo (imagem e som, duração = cortes + introdução + vinheta, ±1 s), a legenda (a última não passa do fim do vídeo) e cada Short (1080×1920, com áudio, até 60 s, com o `.txt`), e registra no log o **resumo** (arquivos, durações, tamanhos, nota de cada Short e o tempo total sem contar esperas por revisão). Problema grave vira erro e **deixa `_work/` no lugar** para retomar; só um processamento conferido apaga os intermediários (a menos que "manter intermediários" esteja marcado).
* **Tempo estimado antes de começar:** a janela mostra, ao escolher o vídeo e a cada mudança de opção, o tempo estimado total e o da renderização nesta máquina (HDR ou não, preset, Shorts, modelo do Whisper).
* **Progresso:** o peso de cada renderização no progresso total e no tempo restante acompanha a qualidade escolhida (`Etapa.peso_para(opcoes)`).
* **Linha de comando:** `--shorts`, `--legendas` e `--qualidade maxima|alta|equilibrado|rapido`; as revisões ficam desligadas sem janela.

### Fase 7: Studio Shorts, marca d'água, modos e português (versão 1.1) — concluída em 2026-10-13

Pedidos do autor depois da 1.0, todos feitos e testados.

**Marca d'água** (`render/marca.py`, cartão "4. Marca d'água", `ui/painel_marca.py`).

* A imagem é escolhida na janela. **PNG com fundo transparente é usado como está.** Qualquer outra imagem (JPG, WebP… ou PNG sem transparência) ganha a opção **"Remover o fundo da imagem"**: a cor do fundo é a da borda, e só some o que está ligado à borda por pixels dessa cor (o branco *dentro* de um logotipo fica), com a orla suavizada e as margens vazias cortadas. O resultado é um PNG com canal alfa em `_work/`.
* **Canto** (superior/inferior × esquerdo/direito) e **tamanho** (3 a 50% da largura do vídeo, 15% por padrão; margem de 3%), com duas prévias na tela: a imagem sobre um xadrez e um quadro 16:9 com a marca no canto e no tamanho escolhidos. Marcas separadas para o **vídeo longo** e para os **Shorts**.
* No FFmpeg a marca é uma entrada extra de imagem (`overlay`): no vídeo longo, depois da conversão HDR→SDR; nos Shorts, entre a ampliação e a legenda queimada (a legenda fica por cima). A assinatura de cada render inclui o conteúdo da marca, o canto e o tamanho: mudar a marca renderiza de novo só o que ela afeta.

**Modos e projetos abertos** (`ui/painel_opcoes.py`, `ui/painel_entrada.py`, `ui/app.py`).

* Seletor **Tudo / Só Shorts / Só vinhetas** e botão **Abrir projeto…**: lê o `estado.json`, preenche nome, data (a do projeto, não a do arquivo), vídeo e opções, mostra o que o projeto já tem e abre a aba Studio Shorts se os cortes existirem. Se o vídeo original sumiu, ainda dá para ver e editar os cortes; renderizar pede o vídeo.
* "Só vinhetas" exige um projeto com o vídeo longo pronto (a tela avisa) e acrescenta o que faltar sem recodificar o vídeo (vídeo anterior preservado; `.srt` deslocado pelo tamanho da introdução).
* A linha de comando ganhou `--modo`, `--introducao`, `--vinheta-final` e `--marca`, `--marca-canto`, `--marca-tamanho`, `--marca-remover-fundo`, `--marca-em`.

**Aba Studio Shorts** (`ui/studio_shorts.py` = `EditorDeCortes`, `ui/aba_studio.py`; a janela de aprovação dos cortes usa o mesmo editor).

* A aba fica **desativada** até os cortes existirem (áudio normalizado, legenda extraída e Shorts calculados) e **travada** com o motivo enquanto um processamento roda ou espera revisão.
* Cada Short sugerido aparece com **categoria, início–fim, duração, título, trecho da legenda, nota calculada** e o estado: *✔ renderizado*, *⟳ alterado: renderizar de novo* ou nada (novo). O botão **Renderizar N Shorts** grava as alterações e roda o modo "só Shorts" (sem pausas de revisão); o que já está pronto e não mudou não é refeito, os desmarcados vão para `shorts_gerados/desmarcados/` (nada é apagado) e a tela estima o tempo e avisa se o áudio precisar ser refeito.
* **Início e fim:** campos de tempo editáveis (`8:21,3`, caem na palavra mais próxima) e botões para **acrescentar ou tirar uma palavra ou uma frase** em cada ponta. **Passar de 60 s é permitido**: a tela mostra a duração em vermelho com o aviso de que acima de 60 s o vídeo não pode ser Short, deixa salvar o trabalho em andamento, mas recusa renderizar (e a etapa de render também recusa, antes de começar) enquanto o corte marcado estiver longo; o mínimo continua sendo 10 s. Há **uma caixa só que rola**, com o corte inteiro (em destaque) e ~220 palavras antes e depois (cinza, o que fica de fora), uma frase por linha e as marcas "COMEÇA AQUI" e "TERMINA AQUI"; **duplo clique numa palavra** leva até ela o começo ou o fim, o que estiver mais perto. Os botões do começo (acima da caixa) e do fim (abaixo) seguem a mesma ordem: −palavra, −frase, +frase, +palavra. Avisos apontam o que costuma dar errado: começar no meio de uma frase ou com "mas/então/isso…", terminar no meio de uma frase ou numa pergunta (a resposta pode ter ficado de fora). Serve para não cortar uma pergunta retórica ao meio nem abrir com um pedaço de outro argumento.
* **Legenda:** editável por Short (`Corte.legenda`), mostrada **dividida como vai aparecer no vídeo** (blocos de até 2 linhas separadas por Enter, com uma linha em branco entre os blocos: a mesma divisão da legenda queimada, em `render/legenda.py`); as quebras se refazem sozinhas ao sair do campo, sem mexer nas palavras. Os tempos da fala são preservados; "Desfazer correções" volta ao texto da transcrição. A correção entra na assinatura do Short quando a legenda é queimada (opção "Legendas dinâmicas"), então corrigir e renderizar de novo refaz só aquele Short. Mudar o início ou o fim descarta as correções daquele corte (o texto-base mudou) e a tela avisa.

**Verificador de português** (`transcricao/verificador.py`, `transcricao/dicionario.py`, `ui/verificacao.py`).

* Na **revisão da transcrição** (botão "Verificar português" e F8) e na **legenda de cada Short**. Tudo offline: o dicionário **Hunspell pt_BR** do projeto VERO/LibreOffice (`recursos/dicionarios/pt_BR/`, LGPLv3/MPL, lido pelo `spylls`) diz se a palavra existe; o `pyspellchecker` e a distância de edição só sugerem, e cada sugestão passa pelo dicionário brasileiro (nada de "incómodo" ou "aspetos"). "Mais sugestões" chama o Hunspell, melhor mas lento.
* Acha: palavra fora do dicionário (nomes do `vocabulario_teologico.txt` e palavras que se repetem 3 vezes ou mais no texto não contam), **palavra repetida** ("nós nós"), **frase começando em minúscula** e nomes sagrados em minúscula, **pontuação solta** (`palavra ,`), **hífen solto** do Whisper (`chamá -las`) e parênteses sem par. No sermão real: ~60 avisos em 302 trechos, quase todos corrigíveis com um clique ("Corrigir os 27 de pontuação").
* Cada aviso mostra sugestões, **Ignorar** (vale até fechar o aplicativo) e **Está certa: guardar no vocabulário** (grava no `vocabulario_teologico.txt`, que também melhora o Whisper nas próximas transcrições).
* **Não** corrige concordância, crase nem sentido. Para isso seria preciso o LanguageTool (Java, ~300 MB): fica como opção futura.

### Fase 8: corte do vídeo, zoom, tratamento de imagem, vinhetas e Shorts de até 180 s (versão 1.2) — concluída em 2026-10-15

Os sete pedidos da rodada (registrados no fim da seção 6), todos feitos e testados.

**1. Cortar o começo e o fim do vídeo** (`ui/painel_aparar.py`, no cartão "1. Vídeo").

* Dois campos de tempo, **Começar em** e **Terminar em** (`0:07`, `1:04:56,5`; vazios = o vídeo todo). Escrever `-2:04` no fim tira 2 min 04 s do final (o campo vira o tempo absoluto). O texto abaixo diz quanto o vídeo usado terá e o que sai.
* Cada ponta mostra o **quadro exato** onde o corte cai (o primeiro, e o último antes do fim) e um botão **"Ver 15 s"**: gera em segundo plano um trecho de 15 s em baixa resolução (640 px, `ultrafast`, com som; HDR já convertido) e o abre no player do Windows. A prévia do começo são os 15 s que começam no corte; a do fim, os 15 s que terminam nele. Usa o zoom e o tratamento de cor que estiverem na tela. Fica travado no modo "Só vinhetas" e durante o processamento.
* Opções: `video_inicio_s` e `video_fim_s` (0 = até o fim). **Onde vale:** `calcular_cortes` recorta os trechos mantidos à janela (então o vídeo longo, o `.srt` e a conferência final seguem sem mais nada); a análise dos Shorts só enxerga as palavras da janela; o Studio Shorts só mostra a fala dela; a estimativa de tempo e o espaço em disco contam só o trecho usado; o pré-voo recusa uma janela com menos de 10 s.
* **Decisão:** a **transcrição continua sendo feita no áudio inteiro, uma vez**. Assim mudar o começo ou o fim depois (ou reabrir o projeto) refaz só os cortes, as legendas e a renderização, nunca os ~48 min do Whisper. O custo é transcrever (e mostrar na revisão da transcrição) o que depois é cortado. As palavras fora da janela não entram em nada que sai.
* O começo e o fim são de cada vídeo: **não viram preferência** para o próximo (o zoom e o tratamento, sim).
* O `recursos/cortador de vídeo.py` (script avulso do autor) passa a ser desnecessário.

**2. Ajustar o zoom** (`ui/painel_zoom.py`, `core/aparencia.py`, `render/imagem.py`).

* Quadro de exemplo do vídeo (com **"Outro quadro"**) e a **área que será cortada escurecida**, com moldura no que fica. Zoom em % (100 a 200 na tela; o modelo aceita até 300), digitado ou no controle, e **centro** escolhido **clicando no quadro** ou pelos controles de posição. O recorte nunca sai do quadro.
* Render (vídeo longo): `crop` → conversão HDR→SDR → ampliação (`lanczos`) de volta ao tamanho original → tratamento → fades → marca d'água. Cortar antes da conversão poupa trabalho do `zscale`. Acima de 150% a tela avisa que a imagem perde nitidez.
* **Shorts:** com "Aplicar também aos Shorts" (ligado), o recorte 9:16 sai de **dentro** da área do zoom, e o "Deslocamento do recorte" das opções avançadas é uma porcentagem da largura dessa área, somada ao centro do zoom. Sem zoom, nada muda em relação à 1.1.
* **Presets** de zoom (nome → zoom + posição) ficam na configuração do programa (`config.toml`: `presets_enquadramento`). O que está na tela também é lembrado da próxima vez.
* **Zoom e legenda dos Shorts** (`ui/painel_shorts.py`, logo abaixo do zoom do vídeo): prévia **vertical (9:16)** de um quadro do vídeo e, sobre ela, duas linhas de **legenda de exemplo** ("As legendas ficarão / posicionadas aqui"), sem gerar legenda nenhuma. A prévia sai do FFmpeg sobre o quadro `.nut` com o mesmo `recorte_vertical`, o mesmo tratamento e a ampliação para 1080×1920 do render, e a legenda é montada pelo mesmo `montar_ass` dos Shorts de verdade (Montserrat, contorno e altura reais): `render/previa.previa_do_short`. Controles: **zoom do Short** (100 a 200% na tela), **posição horizontal e vertical** desse zoom e **altura da legenda** (30 a 90%, contada de cima; também se arrasta clicando no quadro). A altura da legenda saiu das opções avançadas e vive só aqui (`legenda_posicao_vertical`).
* O zoom do Short é um **segundo zoom, dentro do 9:16** já recortado (depois do zoom do vídeo e do deslocamento do recorte): `shorts_zoom_pct`, `shorts_zoom_centro_x_pct`, `shorts_zoom_centro_y_pct`. É independente do "Aplicar também aos Shorts" do zoom do vídeo. Acima de 125% a tela avisa da perda de nitidez (o 9:16 de um vídeo 1080p já é uma ampliação de ~1,8×). Só entra nas assinaturas quando é maior que 100%: Shorts já renderizados sem zoom continuam valendo.

**3. Tratamento de imagem** (`ui/painel_tratamento.py`, `render/imagem.py`, `render/previa.py`).

* Novo cartão "4. Tratamento de imagem": **brilho, contraste, saturação, temperatura (frio ↔ quente), tom (verde ↔ magenta), brancos e sombras**, de −100 a +100, com a explicação de cada um. Um quadro do vídeo com **divisória que se arrasta**: original à esquerda, tratado à direita. Mexer num ajuste liga o tratamento.
* **O quadro tratado sai do próprio FFmpeg, com os mesmos filtros do render**, a partir de um quadro SDR guardado sem perdas (`.nut`, yuv420p, BT.709 de faixa limitada): o que se vê é o que sai. Filtros (depois do HDR→SDR, em yuv420p limitado): `eq` (brilho, contraste, saturação) e `lutyuv` (brancos e sombras como curva de luma ponderada pela própria luz; temperatura e tom como deslocamento de Cb/Cr). Ajustes em zero não geram filtro.
* **✨ Sugestão automática:** mede 5 quadros espalhados pelo trecho usado (histograma de luz, saturação média, dominante de cor) e propõe um ponto de partida: estica o contraste só se faltar amplitude, leva a luz média para perto do meio (com tolerância), abre sombras muito fechadas, recupera brancos estourados, reforça cor apagada e desfaz uma dominante de cor com a metade da força (madeira e pele são quentes de verdade). Uma imagem equilibrada volta quase neutra; nada passa de ±45.
* **Presets** (nome → os 7 ajustes) em `presets_tratamento` da configuração; **Salvar preset…**, **Excluir** e a lista. "Aplicar também aos Shorts" (ligado). A introdução e a vinheta **não** são tratadas.
* Mudar zoom ou tratamento entra nas assinaturas e refaz o vídeo longo e os Shorts afetados; a aba Studio Shorts mostra "⟳ alterado" nos Shorts que já estavam prontos (o registro guarda uma assinatura da aparência: zoom, cor, recorte e marca d'água).

**4. Introdução e vinheta: arquivo à escolha e fade** (`ui/painel_opcoes.py`, `render/video_longo.py`, `render/vinhetas.py`).

* Cada uma ganhou **"Escolher arquivo…"** e **"Usar o padrão"** (`recursos/introducao.mp4` e `recursos/vinheta_final.mp4`). A caixa de marcar vale se o arquivo escolhido existir, mesmo sem o padrão. Opções `introducao_arquivo` e `vinheta_arquivo`; o pré-voo, a conferência final e o deslocamento do `.srt` usam o arquivo escolhido.
* **Fade (imagem e som) pelo preto, com duração de 0,1 a 3 s (padrão 0,5 s):** a introdução escurece até o preto e o vídeo surge dele; no fim, o vídeo escurece e a vinheta entra do preto. Imagem: `fade` na primeira e na última parte do vídeo e nos clipes (depois da marca d'água, que também some no preto); som: `afade` no áudio montado de uma vez. A duração total não muda (o fade cai dentro dos clipes e do vídeo).
* **Limite no modo "Só vinhetas":** o vídeo pronto é emendado sem recodificar, então ali o fade vale só do lado da introdução (sai para o preto) e da vinheta (entra do preto); o vídeo principal começa e termina seco. Para o fade dos dois lados, renderize o vídeo longo no modo Tudo.

**5. Opções avançadas** (`ui/painel_opcoes.py`).

* **Modelo do Ollama é uma lista**, não um campo de texto: os modelos já instalados (lidos de `/api/tags`, em segundo plano, ao abrir e no botão ↻), mais os recomendados que faltam, marcados "não instalado (será baixado)", e o modelo escolhido mesmo que o Ollama não responda ("não encontrado no Ollama"). O projeto aberto mostra o modelo dele.
* **Uma legendinha cinza logo abaixo de cada opção avançada** explica o que ela faz (pausa residual, duração mínima e máxima, limite de segurança, nota mínima, modelo, deslocamento do recorte, posição da legenda, qualidade personalizada, intermediários e bloqueio do PC).

**6. Shorts de até 180 s, com preferência por ~60 s** (`shorts/analise.py`, `shorts/etapas.py`, `shorts/llm.py`).

* **Limite de 180 s** (o que o YouTube aceita) em tudo: opções (`shorts_duracao_max_s`, padrão e teto 180), candidatos, ajuste de bordas, Studio Shorts (aviso e recusa de renderizar acima de 180 s), renderização e conferência final. A configuração antiga com o 60 s de então migra para 180 s (versão 2 do `config.toml`); quem escolheu outro valor o mantém.
* **Peso por duração** (`pontos_de_duracao`, 0 a 10): de 50 a 75 s vale 10; abaixo, 8 (40 s) e 5 (30 s); acima de 75 s cai em linha reta até **2 pontos aos 180 s**. É pontuação, não corte: um trecho de 150 s precisa ser bem melhor que um de 60 s para passar na frente, mas pode passar. Entra duas vezes (pré-nota, 0 a 20, e nota final, 0 a 10).
* **Cota para os longos:** sem ela, a pré-seleção dos 40 candidatos que vão para a IA seria toda de ~60 s e os longos nunca seriam avaliados. `escolher_para_ia` reserva **25% das vagas** para os melhores trechos de 90 s ou mais (sem sobreposição maior que 60%); se não houver longos, as vagas voltam para os demais. A IA recebe a instrução de que o ideal é ~1 min, mas de que num trecho de mais de 90 s a ideia precisa se sustentar o tempo todo.
* **As avaliações da IA agora valem por trecho e texto, não por número do candidato.** Achado ao mudar o limite: refazer a análise renumera os candidatos, e as avaliações guardadas por número passariam para o trecho errado. Agora cada avaliação é guardada com o hash do trecho (`avaliacoes.json` versão 2); um projeto antigo é **migrado antes** de a análise trocar os candidatos, e as avaliações dos trechos que continuam existindo são reaproveitadas (a IA leva ~1 min por trecho): mudar o limite, o começo ou o fim só avalia o que é novo.

**7. Studio Shorts: a legenda acompanha o que se acrescenta e se tira** (`ui/studio_shorts.py`, `shorts/edicao.py`).

* Ao acrescentar ou tirar uma palavra ou uma frase (ou digitar o tempo, ou dar duplo clique numa palavra), a caixa da legenda mostra **como ficou**: as **palavras acrescentadas ganham fundo verde**, e uma linha diz o que entrou ("➕ acrescentou no fim: …") ou saiu ("➖ tirou do começo: …"), já que o que saiu não está mais na caixa.
* A caixa **rola até o ponto que mudou** (o começo ou o fim), o que importa numa legenda de 3 minutos, que não cabe na tela.
* Acima e abaixo da caixa, em cinza, o **texto que vem antes do começo e depois do fim** (30 palavras) acompanha cada ajuste: dá para ir "correndo" o sermão para trás e para a frente e ver o que ainda há fora do corte. Trocar de corte ou desfazer as correções limpa o destaque.

**8. Studio Shorts: caixa única, português compacto, prévia e rolagem** (`ui/studio_shorts.py`, `ui/miniplayer.py`, `ui/rolagem.py`, `render/previa.py`).

* **Início e fim numa caixa só** (`shorts/edicao.janela_do_corte`): "Começa em" + botões, a caixa, botões + "Termina em" (espelhado). A ordem dos botões é a mesma nas duas pontas; a seta mostra para onde a ponta anda. Dois botões "↥ começo" e "↧ fim" levam a caixa às marcas.
* **Painel "Português" compacto** (`PainelDePortugues(compacto=True)`): a lista tem uma linha por aviso (até 8) e some, com a navegação, quando não há aviso. A revisão da transcrição continua com o painel de altura cheia.
* **Prévia do Short** (final do detalhe): "Gerar prévia" renderiza o corte em miniatura (270×480, 12 quadros/s, x264 ultrafast CRF 34) com o MESMO recorte 9:16, zoom, HDR→SDR, tratamento e legenda (`montar_ass`, com a legenda corrigida) do Short; o vídeo é reduzido logo depois do recorte, antes da conversão de cor, para sair rápido. O som é o do vídeo original (mono, sem limpeza nem normalização) e a marca d'água não entra. Aparece num **mini player** (botão, barra de posição, tempo): quadros por um FFmpeg que decodifica o `.mp4` para RGB e segue o relógio (pula quadros se atrasa); som pelo `winsound` a partir do ponto certo. A prévia é guardada por corte e só vale enquanto o trecho, a legenda e a aparência (zoom, cor, recorte, legenda) não mudam; senão a tela avisa "⟳ versão anterior". Abrir outro corte cancela a geração em andamento. Vive em `%TEMP%/spurgeon_previas/shorts` (apagada ao fechar o app).
* **Rolagem** (`QuadroRolavel`, no lugar do `CTkScrollableFrame`): 80 px por giro da roda (eram 20) com desaceleração, em passos de no máximo 70 px com o Tk desenhando entre um e outro, o que tira os rastros (texto repetido) ao rolar rápido.

**Testes:** a suíte passou de 617 (fora o `test_render`) para 875 testes (arquivos novos: `test_imagem.py`, `test_estilo_e_cortes.py`, `test_paineis_imagem.py`, `test_studio_legenda.py`, `test_janela_rodada8.py`, `test_cli_rodada8.py`, `test_correcoes_da_revisao.py`; a verificação do vídeo longo com fade e zoom roda o FFmpeg de verdade e mede a luz nos quadros da emenda).

### Fase 9: janela reorganizada, elementos do YouTube, log detalhado e Shorts livre (versão 1.3) — concluída em 2026-10-17

Os seis pedidos da rodada (registrados no fim da seção 6), todos feitos e testados.

**1. Nova ordem dos cartões** (`ui/painel_opcoes.py`, `ui/painel_entrada.py`, `ui/app.py`).

* A coluna da esquerda agora é: **1. Vídeo** (arquivo, nome, data, pasta) → **2. O que gerar** → **3. Corte e enquadramento** (cortar começo e fim, zoom do vídeo, zoom e legenda dos Shorts) → **4. Tratamento de imagem** → **5. Marca d'água** → **6. Pontos de revisão** → **7. Processamento** → opções avançadas.
* Para pôr o "O que gerar" entre a escolha do vídeo e o corte, o `PainelEntrada` deixou de criar o corte, o zoom e a legenda dos Shorts: eles nascem em `PainelEntrada.montar_ajustes(pai)`, que a janela chama com o cartão 3 (`PainelOpcoes.cartao_ajustes`). `aparar`, `zoom` e `shorts` continuam sendo atributos do `PainelEntrada` (`valores`, `aplicar_valores`, `habilitar` não mudaram).

**2. "O que gerar" mostra só o que vale para o modo** (`PainelOpcoes._montar_gerar`, `_mostrar_grupos`).

* Quatro grupos com título e fio: **Áudio** (limpar, normalizar), **Vídeo completo** (vídeo sem silêncios + .srt, tamanho mínimo da pausa), **Introdução e vinheta final** (arquivos, fade) e **Shorts (vertical 9:16)** (gerar Shorts, legendas dinâmicas). Cada opção tem uma legendinha cinza.
* **Tudo** mostra os quatro; **Só Shorts** mostra Áudio e Shorts (sem a caixa "Gerar Shorts", que no modo já é o produto); **Só vinhetas** mostra só a introdução e a vinheta. Os grupos escondidos continuam existindo (só saem da tela), então o estado e a validação não mudam.

**3. Elementos do YouTube Shorts sobre a prévia** (`ui/youtube_shorts.py`, `ui/painel_shorts.py`).

* Sobre a prévia vertical do cartão 3 aparecem, desenhados à parte (não passam pelo FFmpeg, então ligar e desligar é imediato): os **botões da direita** (curtir, não gostei, comentar, compartilhar, remixar, disco do som), o **canal com "Inscrever-se"**, o **título e a descrição**, a **música**, os **ícones do topo** e a **barra de progresso**, com o escurecimento da base que o aplicativo faz. O botão **"Esconder/Mostrar elementos do YouTube"** liga e desliga.
* Abaixo do botão, uma **conclusão em palavras**: "⚠ A legenda a 62% pode ficar atrás dos botões da direita…" ou "✔ A legenda a 35% fica livre dos elementos do YouTube" (`conflitos_da_legenda`: a legenda ocupa ~9,4% da altura em torno do centro e, com linhas longas, chega a 93% da largura).
* É uma **referência aproximada** do aplicativo do YouTube (que muda de tempos em tempos), com as áreas medidas em fração da tela (`REGIOES`): ajuste ali se o desenho do YouTube mudar. **Achado:** com a legenda no padrão (62%), duas linhas longas encostam na coluna de botões da direita; a conclusão da tela já avisa disso.

**4. Log mais detalhado e a "pausa" da transcrição** (`core/pipeline.py`, `core/diagnostico.py`, `transcricao/etapas.py`, `core/trabalhador.py`, `shorts/etapas.py`).

* **O que foi achado (a conclusão sobre "algo externo" foi corrigida na rodada 1.3.1: era o stdin do trabalhador).** O log do projeto mostra que a lacuna de 21 min 35 s fica entre `Carregando o modelo…` e `Termos de apoio ao Whisper`, isto é, **dentro de `carregar_modelo` ou da decodificação do áudio**, passos que não escreviam nada. Medido nesta máquina: importar o `faster-whisper` leva ~5 s e carregar o `large-v3-turbo` (1,6 GB) ~8 a 10 s, com a leitura do disco feita em ~4 s; a decodificação de 8 min de áudio é instantânea. Ou seja, **não é o código**: aquele carregamento ficou ~150 vezes mais lento por algo externo (disco ocupado ou lento na leitura a frio, antivírus lendo o arquivo, ou pouca memória livre). **Não deu para provar qual**, porque o log não registrava nada ali (e agora registra). O "pause" do autor **não foi o que destravou**: o carregamento terminou sozinho às 12:08:16, e o "Pausado pelo usuário" (12:08:37) só apareceu depois porque a pausa só vale no próximo segmento transcrito (`ponto_seguro`), e dentro do carregamento ela nem era vista.
* **Passos que não dá para interromper** (`ContextoProjeto.executar_vigiado`): rodam numa thread vigiada; o log diz a cada 15 s que continuam, há quanto tempo e o que se mede (para o modelo: **MB lidos do disco** de `model.bin` pelo processo, **RAM livre** e **uso do processador**). **Cancelar vale na hora**; a **pausa é confirmada** ("Pausa pedida: ela vale quando “Carregando o modelo do Whisper” terminar"). Passando de 90 s, um **aviso** explica as causas prováveis (se os MB lidos não andam, é o disco; se a RAM livre está perto de zero, é memória).
* **Trabalho da própria etapa** (`ContextoProjeto.batimento`): a transcrição diz a cada 20 s "transcrito até 3:12 de 8:43, 24 trechos, 1,3× o tempo real, falta cerca de 5 min"; cada avaliação da IA e cada título dizem "ainda em andamento há 30 s" (a IA leva ~1 min por trecho na CPU).
* **Mais detalhe em geral:** cada etapa diz "(etapa 3 de 12)" ao começar e "em 1 min 02 s" ao terminar; o início da execução lista as etapas por fazer; o FFmpeg informa a cada 30 s (% , minutos de mídia, velocidade e quanto falta); a decodificação do áudio, a verificação do Ollama e o modelo em uso aparecem; o download do modelo Whisper mostra a porcentagem. **Pausar, retomar e cancelar são confirmados na hora no log** (`comando`), antes de a etapa chegar ao ponto seguro.

**5. Studio Shorts: aba "Shorts livre"** (`shorts/busca.py`, `shorts/livre.py`, `ui/shorts_livre.py`, `ui/aba_studio.py`).

* No Studio, o seletor **"Cortes | Shorts livre"**. O Shorts livre mostra o **texto inteiro da transcrição** (uma frase por linha, com a hora), uma **busca** e o **trecho escolhido**.
* **Busca por palavras parecidas** (`rapidfuzz`): tolera erro de digitação, falta de acento e variação ("perdoar" acha "perdoou"); a frase achada fica destacada e as palavras que casaram, em destaque forte. **Busca por tema:** compara o assunto de janelas de 4 frases com o da busca pelos **vetores de palavras do modelo de português do spaCy** (offline; acha "perdão" num trecho que fala em "misericórdia" e "culpa"). É uma **aproximação**: serve para chegar perto, e a pessoa confere lendo; abstrações vagas ("o que é pecado") dão resultados fracos.
* **Marcar o trecho:** arrastando o mouse sobre as palavras, ou clicando numa palavra e usando "↥ Começar aqui" / "↧ Terminar aqui"; "Começar neste achado" e "Usar o achado todo" partem de um resultado da busca. Os quatro botões de cada ponta (−palavra, −frase, +frase, +palavra) afinam. A tela mostra início, fim, duração, número de palavras, com que palavras abre e fecha, e **avisa** se o trecho tem menos de 10 s ou passa de 180 s (já contando o respiro).
* **Criar Short deste trecho:** em segundo plano, a IA **avalia** o trecho (categoria, retenção, risco de contexto), a **nota** sai da mesma conta dos automáticos (`calcular_nota`, com os sinais calculados das palavras escolhidas por `analise.candidato_do_trecho`), as pontas se ajustam ao **silêncio de verdade** (`refinar_bordas`) e a IA cria **título, descrição, hashtags e nome do arquivo** (`etapas.metadados_sem_repetir`, sem repetir título). O corte entra na lista do Studio **marcado**, é **gravado** na hora em `cortes_shorts.json` e a tela vai até ele. Leva ~2 min na CPU. A IA é descarregada da memória ao terminar, para sobrar RAM para a renderização.
* **Anotação de origem:** `Corte.origem` (`automatico` ou `manual`; cortes antigos valem como automáticos). A lista e o detalhe do Studio mostram **"gerado automaticamente"** ou **"gerado manualmente"** (este em azul, no detalhe).
* **Os cortes manuais sobrevivem:** refazer a seleção automática reescreve `cortes_shorts.json`; `SelecionarShorts` agora **mantém os cortes manuais** (`cortes_manuais_guardados`). Seus ids começam em 1.000.000, longe dos ids dos candidatos.
* O Ollama precisa estar de pé e o **modelo já instalado** (aqui não se baixa sozinho: o aviso diz o que fazer). A aba só abre quando o projeto já tem cortes calculados, como o resto do Studio.

**6. A prévia do Short no Studio sempre leva a legenda** (`ui/studio_shorts.py`).

* Antes a prévia só queimava a legenda se as "Legendas dinâmicas" estivessem marcadas nas opções (que vêm desmarcadas), então a prévia saía sem texto. Agora **`com_legenda=True` sempre**: a prévia mostra a legenda com a correção feita no corte, e a legenda deixou de entrar na assinatura da prévia. O texto da tela explica que **no vídeo final a legenda só é queimada com "Legendas dinâmicas" ligado**.

**Testes:** arquivos novos `test_youtube_shorts.py`, `test_shorts_livre.py`, `test_shorts_livre_ui.py`, `test_janela_rodada9.py`, e casos novos em `test_pipeline.py` (passo vigiado, cancelamento, pausa, alerta, batimento, posição da etapa) e `test_painel_shorts.py`.

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

* Player de vídeo embutido na interface (a prévia de 15 s abre no player do Windows); fila de vários vídeos.
* Enquadramento com rastreamento de rosto ou fundo desfocado (hoje: recorte central fixo).
* Título fixo no vídeo; introdução e vinheta nos Shorts. (A marca d'água entrou na 1.1.)
* Remoção de muletas de fala ("é...", "hum").
* Diarização (identificar vários falantes).
* Empacotamento em `.exe` (PyInstaller), a fazer depois para distribuir a outros PCs.
* Legendas em outros idiomas.
* **Trocar o CustomTkinter por outra interface (só anotado em 2026-10-23; nada foi decidido nem alterado).**
  A análise do código mostrou que a dor vem de duas camadas. Do CustomTkinter (desenha cada widget num Canvas):
  piscar a cada `configure`, rastros na rolagem, `CTkLabel` vazio pedindo 28 px e `CTkFrame`/`CTkScrollbar` pedindo
  200 px (remendos em `ui/estabilidade.py` e `ui/rolagem.py`). Do próprio Tk: o `Text` não tem gancho de entrada
  (`ui/tabela_de_frases.py` renomeia o comando Tcl do widget; `ui/pincel.py`), não há mídia (`ui/miniplayer.py` usa
  FFmpeg→RGB + `winsound`) e os testes de janela precisam de tela. Alternativas que ficam no Tk (ttk + sv-ttk)
  só curam a primeira camada.
  * **Candidata: PySide6 (Qt, Widgets, não QML).** `QTextEdit`/`QTextCursor`/`QTextCharFormat` e `eventFilter` para
    o editor de palavras, `QMediaPlayer` para a prévia, `QProcess` + sinais para o trabalhador, `QUndoStack` para o
    desfazer, modo `offscreen` para testes sem tela. O 6.11.x declara suporte a Python 3.11 no Windows; licença
    LGPL; usar `PySide6-Essentials`. Outras opções avaliadas: ttk + sv-ttk (menor risco, mantém os hacks do `Text`),
    web com pywebview/NiceGUI (precisa de JavaScript e gasta RAM que o Whisper e o Ollama disputam), wxPython,
    PyQt6 (GPL); Flet, Dear PyGui, Kivy, Toga e Textual foram descartadas.
  * **Cuidado:** o Qt só ajuda de verdade se a migração refizer o desenho, não se for trocar `CTkButton` por
    `QPushButton`. As armadilhas de desenho do CLAUDE.md (`_guardar_detalhe`, dois lugares de edição da legenda,
    `_reordenar` contra `_atualizar_linha`, estado dentro dos widgets em `EditorDeCortes`, 2.462 linhas) viajariam junto.
    Ideia: `QAbstractListModel` para os cortes e `QTextDocument` como fonte única da tabela e da legenda. O Qt traz
    armadilhas próprias (objeto C++ já apagado, objetos sem referência coletados, widget mexido em thread derruba o
    processo).
  * **Como decidir quando chegar a hora:** prova de 2 a 3 dias num `spurgeon gui-qt` experimental, ao lado da
    interface atual (Tk e Qt não convivem na mesma janela), só com a tabela de frases (marca-texto, borracha,
    sublinhado) e o miniplayer. Se as duas ficarem claramente mais simples e estáveis, portar o resto tela por tela
    (Studio Shorts primeiro), mantendo o CustomTkinter até haver paridade.
  * **Para uma suíte profissional, o toolkit é a menor parte:** faltam instalador e assinatura (PyInstaller/Nuitka,
    `pyside6-deploy`), atualização automática, relatório de falhas e conferir as licenças da distribuição (o
    build do FFmpeg com libx264 é GPL; o LGPL do Qt pede um build em pasta, não arquivo único).

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
* **Shorts:** mínimo de 30 s e máximo de 60 s (desde a 1.2: máximo de 180 s, com preferência por ~60 s), limite de segurança de 20 por vídeo, descarte de janelas com pausa interna maior que 3 s.
* **Loudness:** −16 LUFS (vídeo longo) e −14 LUFS (Shorts).
* **Qualidade padrão:** máxima (`veryslow`, CRF 16), com aviso de tempo estimado na interface. Mantida depois de medir (~4 h para 65 min de vídeo): o autor roda o PC de madrugada.
* **HDR do iPhone:** convertido para SDR BT.709 em H.264 de 8 bits, compatível com qualquer tela.
* **LLM:** `qwen3:8b` como candidato provisório, com `qwen3:4b` como alternativa leve.

### Pendências técnicas

1. **Comparar o `qwen3:8b` com outros modelos** (`qwen3:4b`, `llama3.1:8b`) em trechos reais: o `qwen3:8b` funcionou bem (JSON sempre válido, títulos específicos depois de tirar o exemplo do prompt, ~50 s por trecho na CPU), mas ele é generoso nas notas e só foi comparado a si mesmo.
2. **Comparar visualmente** a qualidade "Alta" (CRF 18) e a "Máxima" (CRF 16) em trechos do sermão, para o autor saber o quanto a diferença de ~2 h 30 min compensa.

### Decisão que depende do autor (descoberta com o sermão real)

3. **Tamanho mínimo da pausa a cortar:** no sermão real o padrão de 10 s não corta nada (a maior pausa tem 5,3 s). Manter 10 s como padrão, ou usar um valor menor (por exemplo 2 s, que corta 10 pausas e 17 s)? A remoção de silêncios encurta menos de 1 minuto em 65 mesmo em 1 s, então vale também decidir se ela continua ligada por padrão.

### Escolhas que fiz sozinho na Fase 4 a 6 (confirmar ou mudar)

4. **Recorte 9:16 deslocado +4% para a direita (padrão):** a câmera fixa deixa o pregador e a cruz a ~54% da largura; com 0% o ombro esquerdo encosta na borda. Opção avançada "Deslocamento do recorte".
5. **Legendas dinâmicas desligadas por padrão** (a resposta "Legendas dinâmicas = Não" foi lida como "não vem marcada"): ligue em "O que gerar" ou com `--legendas`. Os Shorts do sermão real foram gerados com elas.
6. **Shorts numerados pela ordem em que aparecem no sermão** (`short_01_…`), não pela nota.
7. **Todos os cortes sugeridos vêm marcados** (20) e as reservas desmarcadas (8), como pedido; a nota mínima para vir marcado é 45 (opção avançada).
8. **Espaço em disco:** este PC tem pouco espaço no C: (7 a 10 GB livres durante o desenvolvimento). O vídeo longo em Máxima ocupa ~2,7 GB e os 20 Shorts ~1,2 GB; a junção tem um modo econômico, mas o modelo do Ollama (5 GB) e o do Whisper (1,5 GB) pesam. Se for processar vários sermões, vale liberar espaço ou mover `Projetos/` para outro disco.
9. **(1.1) "Se for PNG, remover o fundo":** li o pedido como "PNG com transparência vale como está; se não for PNG, ou o PNG não tiver transparência, ofereça remover o fundo". Remover o fundo só funciona bem com fundo liso; a prévia mostra o resultado antes de renderizar.
10. **(1.1) Português:** o verificador usa o dicionário `pt_BR` do LibreOffice (aceita algumas formas de Portugal, como "facto" ou "aspeto", mas recusa as grafias do acordo europeu como "perceção"). Não é um corretor gramatical completo (seção da Fase 7).
11. **(1.1) Legenda corrigida** só aparece no vídeo com as "Legendas dinâmicas" ligadas; sem elas a correção fica guardada no corte.
12. **(1.1) Modos persistem como "Tudo":** o modo escolhido vale só para aquela execução; o aplicativo sempre abre em "Tudo" e lembra o que for a marca d'água e as demais opções.



13. **(1.2) Transcrição sempre do áudio inteiro**, mesmo com começo e fim cortados: mudar o corte depois não refaz os ~48 min do Whisper. O que fica fora do trecho não entra em nada que sai, mas aparece na revisão da transcrição.
14. **(1.2) Zoom também nos Shorts (ligado, dá para desligar):** o recorte 9:16 sai de dentro da área do zoom, e o "Deslocamento do recorte" se soma ao centro do zoom. Com o zoom em 100% nada muda. O mesmo vale para o tratamento de imagem; a introdução e a vinheta nunca são tratadas.
15. **(1.2, superado em 18/10 pela mescla; veja "Mescla da introdução e da vinheta") Fade passando pelo preto** (e não fusão entre as imagens), porque as partes do vídeo longo são emendadas sem recodificar. No modo "Só vinhetas" o fade vale só do lado da introdução e da vinheta.
16. **(1.2) A prévia de 15 s abre no player do Windows** (o CustomTkinter não tem player); a tela mostra o quadro exato de cada corte.
17. **(1.2) Peso dos Shorts por duração:** 10 pontos de 50 a 75 s, caindo em linha reta até 2 pontos aos 180 s, e 25% das vagas da IA reservadas aos trechos de 90 s ou mais. Os números (75 s, 2 pontos, 25%) são escolhas minhas: se os Shorts longos estiverem sobrando ou faltando, mude `pontos_de_duracao` e `COTA_DE_LONGOS` em `shorts/analise.py`.
18. **(1.2) Configuração antiga:** um `config.toml` com o 60 s de antes passa para 180 s sozinho (uma vez). Projetos já criados mantêm as opções que tinham (60 s) até a pessoa mudar na tela.
19. **(1.2) Sugestão automática de cor é uma heurística** (histograma, saturação e dominante de cor de 5 quadros), com a metade da força na correção de cor e limite de ±45. Vale como ponto de partida; **ainda não foi comparada olho a olho no sermão real** (o zoom e o tratamento só foram testados com vídeos sintéticos e o HDR de teste).

### Pedidos da rodada 1.2 (todos feitos: veja a Fase 8)

1. Ao selecionar o vídeo para iniciar o projeto, dar a opção de cortar o início do vído e o final, para iniciar no segundo correto e terminar antes do "fim absoluto", e se for possível incluir uma preview de 15 segundos de início para ver onde de fato via começar o vídeo depois do corte, fazer o mesmo, uma preview de 15 segundos antes do fim para ver onde o vídeo vai acabar, isso só ficaria habilitado na parte de inclusão do vídeo.

2. Na parte de adicionar o vídeo dar a opção de "ajustar o zoom" (em % para ficar mais fácil) no caso pega um frame do vídeo e mostra em área sombreada o que vai ser cortado para "aproximar" mais a câmera do pregador, também coloque a opção de salvar o preset. 

3. Também se for possível adicionar uma etapa de "tratamento de imagem" com alguns comandos básics de configuração como saturação, correção de cor, brilho, contraste, realçe de brancos o sombras, pode usar isso com um frame de exemplo do vídeo com uma divisão rolável no meio do frame para o origina na esquerda e o editado na direitta, faça um botãozinho de sugestão automática e outro de salvar preset, para ficar salvo no grograma o estilo dos vídeos.

4. Na parte de adicionar a vinheta tem a possibilidade e habilitar "fade in" de vídeo e áudio entre a vinheta e o vídeo? e também a opção de selecionar o arquivo tanto de início quanto de fim.
5. Nas opções avançadas coloque o modelo do Ollama como caixa de seleção suspensa, e não digitado. também coloque uma legendinha abaixo de cada opção avançada para explicar o que ela faz.
6. De modo geral limite o tamanho do shorts para 180 segundos, dando um peso maior para gerações por volta de 60 segundos, mas sem descartar os 180 segundos que ainda geram oportunidade de shorts valiosos.
7. No studio de shorts também quando adicionarmos ou removermos uma frase, também atualiza a caixa da legenda do short para ver como ficaria a frase que adicionamos ou removemos, também que o texto vá "correndo" para frente ou para trás para irmos vendo o que tem para t´ras ainda do texto ou para frente.

### Pedidos da rodada 1.3 (todos feitos: veja a Fase 9)

1. Reorganizar: o **"O que gerar"** vem logo depois da escolha do vídeo ou do projeto, antes de cortar o começo e o fim do vídeo; o **"Processamento"** vai para depois dos **"Pontos de revisão"**.
2. No "O que gerar", a janela mostra **só o que vale para o modo escolhido** (Tudo mostra tudo; Só Shorts, só o que é de Shorts; Só vinhetas, só o que é de vinhetas), com as opções **melhor organizadas**.
3. Na parte de ajuste de zoom e legenda dos Shorts, pôr os **elementos de exemplo da interface de Shorts do YouTube** (com botão de esconder e mostrar) para conferir se nada fica na frente da legenda ou da pessoa do vídeo.
4. Deixar o **terminalzinho (log) mais "verbose"** e **investigar por que "demorou para avançar"**: a transcrição ficou 21 min entre "Carregando o modelo…" (11:46:41) e "Termos de apoio ao Whisper" (12:08:16), parecendo travada, e só "voltou" quando o autor pausou.
5. No Studio Shorts, uma aba **"Shorts livre"**: o texto inteiro da legenda numa caixa, com busca (palavras parecidas ou tema), para o autor escolher o trecho; o sistema faz o Short como os outros (título, legenda, hashtags e nota) e o joga no Studio com a anotação **"gerado manualmente"** (os automáticos ganham **"gerado automaticamente"**).
6. No Studio Shorts, a **prévia tem de vir também com a legenda**.

### Escolhas que fiz sozinho na rodada 1.3 (confirmar ou mudar)

20. **(1.3) A lacuna de 21 min da transcrição teve causa, achada na rodada 1.3.1** (leitura bloqueante do stdin no trabalhador travando os `import` pesados; veja a seção "Rodada 1.3.1"). A explicação "disco ou antivírus" que este item dava antes estava errada.
21. **(1.3) Numeração:** "Corte e enquadramento" virou o cartão 3 (e o Tratamento, a Marca e a Revisão seguem 4, 5 e 6). Os outros cartões não escondem nada por modo (só o "O que gerar"); eles só ficam apagados, como antes.
22. **(1.3) Elementos do YouTube** são um desenho aproximado (medidas em fração da tela em `ui/youtube_shorts.REGIOES`), com os rótulos em português ("Não gostei", "Compart.", "Remixar"). A conclusão em palavras usa a altura da legenda de duas linhas (~9,4%) e a largura total; por isso a legenda a 62% já avisa que pode encostar nos botões da direita.
23. **(1.3) Busca por tema** usa os vetores do `pt_core_news_md` (janelas de 4 frases, passo 2, até 12 resultados sem sobreposição). É barato e offline, mas fraco em assuntos vagos; a alternativa (embeddings do Ollama, como o `nomic-embed-text`) pede baixar outro modelo.
24. **(1.3) Shorts livre:** aceita de 10 s a 180 s menos o respiro (179,45 s); o modelo do Ollama é o das opções avançadas e **não é baixado sozinho** aqui; a IA é descarregada da memória depois de cada Short criado (o próximo recarrega o modelo, ~30 s). O corte é gravado na hora em `cortes_shorts.json`.
25. **(1.3) A prévia do Studio sempre mostra a legenda**, mesmo com "Legendas dinâmicas" desligado (como o autor pediu); o vídeo final só a queima com a opção ligada, e o texto da tela diz isso.

### Rodada 1.3.1 (2026-10-17, à noite): "só anda se eu pausar e retomar" e a coluna de opções

**Causa achada e reproduzida: uma thread lendo o stdin de forma bloqueante.** O log desta execução mostrou o carregamento do modelo do Whisper levando **18 min 33 s com 0 MB lidos do disco** (e o processo vivo, avisando a cada 15 s), terminando 8 s depois de a pessoa clicar em Pausar; a análise dos Shorts levou **17 min 33 s** (15 s numa máquina livre) e também terminou junto com uma pausa. O trabalhador tinha uma thread esperando comandos num `for linha in sys.stdin`. No Windows, uma leitura síncrona pendente num handle **bloqueia qualquer outra operação nele**, e as bibliotecas nativas (spaCy, faster-whisper, ONNX…) consultam os handles padrão ao carregar: o primeiro `import` pesado ficava parado, sem gastar processador nem disco, até chegar um comando pelo stdin. Pausar ou retomar era justamente o comando que o liberava. **Reproduzido isolado:** `import spacy` com a thread bloqueante levou 61,6 s (e só andou quando se escreveu no stdin); sem ela, 2,2 s.

* **Correção** (`core/trabalhador.linhas_do_stdin`): o pipe só é consultado (`PeekNamedPipe`, a cada 0,15 s) e só se lê quando já há bytes; nunca fica uma leitura pendente. Pipe fechado = a interface sumiu (cancela). Fora do Windows, ou se o stdin não é um pipe, volta para a leitura comum. Teste de regressão (`test_stdin_do_trabalhador.py`): um filho com a thread ouvinte importa o spaCy sem receber comando nenhum e ainda recebe pausar, retomar e cancelar e o fechamento.
* **Regra nova:** nunca deixar uma leitura bloqueante pendente no stdin do trabalhador (nem em outro handle padrão que as bibliotecas possam consultar).
* **A análise dos Shorts** ganhou passos vigiados (`executar_vigiado`: carregar o spaCy, separar frases, montar as janelas), com log e pausa/cancelamento que valem, e ficou ~3× mais rápida (`_sem_acento` com cache: 9 s → ~3 s nas 5010 janelas do sermão do Miquéias).
* **Passos vigiados mostram onde estão parados:** passando do alerta, o log escreve a **pilha da thread** (e de novo a cada 5 min), o que teria apontado a causa na hora.
* **Prioridade do trabalho:** o trabalhador roda um degrau abaixo do normal (`BELOW_NORMAL_PRIORITY_CLASS`; o FFmpeg herda), para a janela continuar respondendo com o processador cheio.

**Coluna de opções em três páginas** (`ui/painel_opcoes.py`, `ui/app.py`, `ui/secao.py`).

* Botões no topo da coluna: **1 · Vídeo e geração** (Vídeo, O que gerar), **2 · Corte e imagem** (Corte e enquadramento, Tratamento, Marca d'água) e **3 · Revisão e opções** (Revisão, Processamento, opções avançadas). Só uma página fica na tela, então há bem menos widgets para rolar; cada página abre no topo. A numeração dos cartões não mudou.
* No cartão 3, **cortar começo e fim, zoom do vídeo e zoom e legenda dos Shorts** viraram **seções que recolhem** (`SecaoRecolhivel`; começam recolhidas) e o título repetido dentro de cada painel saiu.
* **Cartão do vídeo:** o campo do arquivo e o nome do sermão usam a largura toda; "Escolher vídeo…" e "Abrir projeto…" ficam numa linha própria (antes o campo ficava com ~150 px e cortava o caminho, e a estimativa de tempo aparecia pela metade).
* **Arrastar a barra de rolagem** agora anda em passos curtos (`QuadroRolavel._arrastar`, até 160 px por quadro, desenhando entre eles), como já era com a roda. **O rastro de texto da imagem não foi reproduzido** aqui (nem com o processador saturado e arrastos agressivos: a imagem final sempre saiu igual à de um redesenho limpo), então não dá para garantir que acabou; as três páginas e o arrasto suave atacam as causas prováveis (muitos widgets movidos de uma vez). Se voltar, mande o print e o que estava rodando.
* Corrigido um erro no console ao reabrir um projeto com o Studio aberto (`_ajustar_quebras_do_detalhe` mexia em rótulos de um editor já destruído).

### Mescla da introdução e da vinheta (2026-10-18)

Pedido: a introdução e a vinheta final **se mesclarem** com o vídeo (imagem e som), sem mudança abrupta. Antes o fade só passava pelo preto e pelo silêncio.

* **Nova opção `fade_estilo`:** `mesclar` (padrão) ou `preto`, ao lado do fade, em "Introdução e vinheta final" (seletor "Como o fade acontece"; `--fade-estilo mesclar|preto` na linha de comando). Só vale com o fade ligado.
* **Como funciona (`render/video_longo.py`, `render/mescla.py`).** Mesclar é **sobrepor**: o fim da introdução e o começo do vídeo passam juntos. A duração da sobreposição é o fade pedido (0,1 a 3 s), nunca mais que metade do clipe nem do vídeo, em quadros inteiros.
  * **Imagem:** a primeira parte do vídeo recebe, como entrada extra do FFmpeg, os últimos quadros da introdução e os funde com `xfade` (`offset=0`); a última recebe os primeiros quadros da vinheta (`xfade` no fim da parte). O arquivo da introdução é codificado só até onde a fusão começa, e o da vinheta, só a partir de onde ela termina (`Clipe.sobreposicao`, `quadros_no_arquivo`, `Mescla`, `cadeia_de_clipe`). As partes continuam emendadas **sem recodificar**.
  * **Som:** `acrossfade` (curva `qsin`, potência constante) no lugar do `concat` nas duas emendas.
  * **O vídeo final fica mais curto** que a soma das partes, pela duração de cada sobreposição (ex.: introdução de 3 s + vídeo de 6 s + vinheta de 2 s com fade de 0,6 s = 9,8 s). A **legenda `.srt`** é antecipada pela sobreposição da introdução (`GerarSrt`), e a **conferência final** (`Finalizar`) desconta as sobreposições da duração esperada.
* **"Só vinhetas" não mescla (limite):** ali o vídeo longo já está pronto e é emendado sem recodificar; mesclar exigiria refazer o começo e o fim dele. O fade continua passando pelo preto, e o seletor fica apagado (`OpcoesProcessamento.fade_mesclado` é falso nesse modo).
* **Renders anteriores:** um vídeo longo feito com o fade antes de existir o estilo passou pelo preto; como o padrão agora é mesclar, a assinatura muda quando há fade ligado e o render é refeito (`fade_estilo` entra na assinatura só com fade ligado).
* **Testes:** unidade (quadros da mescla, scripts de áudio e de parte, assinatura, formulário, CLI, janela, legendas) e um render de verdade com FFmpeg que mede a luz no centro de cada fusão e a duração (9,8 s).

### Achado: os testes da janela sobrescreviam a configuração do usuário (2026-10-18)

O `doctor` mostrou as pastas de entrada e de projetos apontando para `%TEMP%\pytest-of-…`: um teste da janela (o que salva um preset de zoom, entre outros) chamava `salvar_config()` sem caminho e gravava no `config.toml` **de verdade** (`%LOCALAPPDATA%\Spurgeon\config.toml`), com as pastas temporárias e o preset "Meu estilo". **Correção:** `tests/conftest.py` ganhou um fixture automático (`config_do_usuario_isolada`) que redireciona `platformdirs.user_config_path` para uma pasta do teste; conferido que rodar os testes de preset não mexe mais no arquivo. O arquivo do autor teve as pastas e os presets de teste restaurados para o padrão, **mas as preferências que ele tinha salvo antes (marca d'água, modelo, opções e presets próprios) foram perdidas** e precisam ser escolhidas de novo na janela.

### Rodada 1.4 (2026-10-20): ajustes do Studio Shorts

Seis pedidos do autor, todos feitos e testados.

1. **Botões do começo na ordem `◀ +palavra | ◀ +frase | −frase ▶ | −palavra ▶`** (os do fim ficaram como estavam: `◀ −palavra | ◀ −frase | +frase ▶ | +palavra ▶`). As duas pontas deixaram de ter a mesma ordem, de propósito.
2. **A caixa do corte virou uma tabela de frases** (`ui/tabela_de_frases.py`, `shorts/edicao.linhas_da_tabela`).
   * Cada linha é **uma frase** (a que o corte atravessa aparece em duas linhas, uma de cada lado do marcador), com a **hora** numa coluna e o texto na outra (as linhas que quebram se alinham sob o texto). Cinza = fora do Short; branco = o texto do Short.
   * **Mexer nas pontas não refaz o painel:** a linha do corte na lista é atualizada no lugar (`_atualizar_linha`) e só é reposicionada se a ordem mudou (`_reposicionar`); antes a lista inteira era destruída e recriada a cada clique (a "mini travada"). A tabela se redesenha mantendo a vista (a mesma frase no alto) e só garante que o marcador que mudou apareça.
   * **A legenda se edita na própria tabela** (só o texto das linhas do corte; hora, linhas de fora, marcadores e quebras de linha são protegidos, no nível do Tcl: o comando do widget é trocado por um filtro). O que se escreve ali chega à caixa "Legenda do Short" depois de 0,35 s parado (e vice-versa) e vai para `Corte.legenda` ao salvar. **Sem o verificador de português na tabela** (ele continua na caixa da legenda).
   * **As correções da legenda agora sobrevivem a mexer nas pontas** (`shorts/edicao.legenda_preservada`): as palavras que ficam mantêm a correção, as que entram vêm da transcrição, as que saem levam a correção embora (aí aparece o aviso). Antes qualquer ajuste de início ou fim apagava a legenda inteira, o que tornaria a edição na tabela inútil.
3. **Prévia do Short: subir e descer a legenda** (▲ Subir / ▼ Descer, 2% por clique, de 30% a 90% da altura).
4. **Cortar as pausas dos Shorts** (`shorts/pausas.py`, `render/shorts.py`, `render/previa.py`). (**Mudou na 1.5:** deixou de ser opção global e passou a ser de cada Short, `Corte.pausa_s`, com mínimo de 0,1 s; os campos `shorts_cortar_pausas` e `shorts_pausa_s` não existem mais.) Opção `shorts_cortar_pausas` (desligada) e `shorts_pausa_s` (1,0 s; de 0,5 a 10,0 s), na tela de opções (grupo "Shorts") e na prévia do Studio. Pausa entre duas palavras do corte que chega ao tamanho escolhido fica só com um respiro de `min(0,4 s, tamanho/2)` (resíduo fixo, `RESIDUAL_DA_PAUSA_S`); o respiro do começo e do fim do corte não é cortado. Vale no vídeo, no som (`atrim` + emendas com micro-fade de 10 ms) e na legenda (tempos remapeados com o `MapaTempo` do vídeo longo). Quadros exatos (`recortes_de_quadros`); `fps=…:start_time=0` no caminho cortado para não perder o primeiro quadro. Sem a opção, o filtro e as assinaturas são idênticos aos de antes (Shorts já renderizados continuam valendo).
5. **Studio Shorts é uma aba separada que ocupa a janela toda** (`ui/app.py`): a faixa `Processamento | Studio Shorts` foi para uma linha própria no alto da janela (não muda de lugar ao trocar de aba); Processamento mostra as opções à esquerda e o andamento à direita; Studio Shorts esconde os dois e ocupa a janela. A coluna da lista ficou mais estreita e o detalhe, mais largo.
6. **Refazer título, descrição e hashtags com a IA**, um botão ("↻ Refazer com IA") para cada campo, a partir do **texto do Short como está agora** (a legenda, com as correções), não do trecho original (`ClienteLLM.refazer`, um pedido por campo, só o campo pedido volta). Roda em segundo plano (~1 min na CPU), um por vez; o resultado entra no campo e só vai para o arquivo ao salvar.

#### Escolhas que fiz sozinho na rodada 1.4 (confirmar ou mudar)

26. **(1.4, o corte das pausas foi revertido na 1.5: passou a ser de cada Short) A altura da legenda e o corte das pausas são opções de todos os Shorts, não de um Short só.** Mexer na prévia de um muda os outros (e a tela de opções, que guarda o valor); a prévia guardada dos outros cortes passa a "versão anterior". Por Short seria um campo novo em `Corte` e nas assinaturas; fiz global porque a câmera é fixa e a altura da legenda já era global.
27. **(1.4) "Cortar pausas" = encurtar, não sumir com a pausa:** o limiar vem da tela (1,0 s) e o que sobra é 0,4 s (ou metade do limiar, se for menor que 0,8 s). Mudar o resíduo é `RESIDUAL_DA_PAUSA_S`. As pausas são medidas pelos tempos das palavras da transcrição (não pelas janelas RMS do áudio), então uma pausa que o Whisper "estica" na palavra anterior pode ser subestimada.
28. **(1.4) O limite de 180 s continua valendo sobre o corte original**, não sobre o Short já sem as pausas (a tela mostra "o Short passa de 51 s para 47 s"). Se o corte de pausas deve deixar passar de 180 s, é preciso mudar `passa_do_limite`.
29. **(1.4) Duplo clique dentro do corte já não leva a ponta até a palavra:** dentro do corte ele seleciona a palavra (para editar). O duplo clique numa palavra cinza continua estendendo o corte, e o **botão direito** numa palavra (cinza ou do corte) abre "Começar o Short nesta palavra / Terminar o Short nesta palavra".
30. **(1.4) O título refeito pela IA zera o nome do arquivo** (`slug`), que é refeito do título novo ao salvar (um Short já renderizado só é renomeado). A IA fica carregada 3 min depois do último pedido (`manter_na_memoria="3m"`), e não 30 min, para sobrar memória para a renderização.
31. **(1.4) As abas ganharam uma linha só delas** (cerca de 40 px de altura a mais, no alto), para o seletor não mudar de lugar entre Processamento e Studio Shorts.

### Rodada 1.5 (2026-10-20 à noite): Studio Shorts mais fino, Shorts livre "vivo", dicas e passo a passo

Treze pedidos do autor, todos feitos e testados (o autor pediu para trabalhar a noite toda, um passo de cada vez).

1. **Menos "piscar" e menos vãos na interface** (`ui/estabilidade.py`). O `configure` das classes do CustomTkinter passou a comparar o que se pede com o `cget` e a não fazer nada quando nada muda (cada `configure` redesenhava o widget inteiro, e a janela chama muitos a cada segundo). `RotuloQueSome` não ocupa linha enquanto está sem texto (um rótulo vazio ainda pede 28 px, o que deixava vãos grandes no meio do detalhe do Studio). Os botões das pontas ganharam mais espaço entre si.
2. **Cortar uma palavra ou uma frase do meio do Short** (`Corte.saltos`, `shorts/edicao.py`, `shorts/pausas.py`). O Short continua logo depois do pedaço cortado, ótimo para um erro de pronúncia. O modelo de edição passou a ser um conjunto de palavras (`indices_incluidos`, `corte_com_indices`): o que fica entre duas faixas vira salto, cada ponta de faixa leva o respiro de costume, e `Corte.duracao` é a duração líquida. Vídeo, som e legenda usam os mesmos trechos. Na tabela as palavras cortadas aparecem riscadas, entre os marcadores "✂ CORTA AQUI ✂" e "↩ CONTINUA AQUI ↩"; o botão direito numa palavra ganhou "Cortar esta palavra/frase do Short" e "Pôr esta palavra/frase no Short".
3. **Marca-texto e borracha** (`ui/pincel.py`, `ui/ferramentas_de_marca.py`), nos dois estúdios. Três botões escolhem o que o mouse faz no texto: a seta (o gesto de sempre), o marca-texto (pinta palavra por palavra o que entra no Short, até longe dele) e a borracha (apaga o que sai).
4. **Pausas por Short, com mínimo de 0,1 s** (`LIMIAR_MINIMO_S`). A caixa "Cortar as pausas deste Short maiores que … s" vale só para o Short aberto; "Aplicar a todos" copia para os outros. As opções globais saíram da tela principal. A explicação ao lado diz quanto o Short encurta.
5. **Desfazer e refazer** (Ctrl+Z, Ctrl+Y e dois botões à direita das duas linhas de controles). Uma história por Short (pontas, palavras cortadas, pausas e legenda).
6. **"Descartar alterações"** ao lado de "Salvar alterações": pergunta e volta tudo ao que está gravado.
7. **17 categorias de corte**, cada uma com cor única (chip colorido na lista e no detalhe do Short): as quatro antigas mais confronto de pecados, exortação, apontando Cristo, necessidade de arrependimento, doutrina bíblica, doutrina reformada, fato histórico, oração, profecia, promessa, anúncio de salvação e esperança. A IA responde uma delas (o esquema do Ollama lista as 17) e o prompt define cada uma em uma linha. As avaliações que já existiam continuam valendo (a chave é o trecho, não a categoria).
8. **Dicionário personalizado no Studio** (botão "Dicionário…", `ui/dicionario.py`): lista os termos de `recursos/vocabulario_teologico.txt`, acrescenta, procura e remove os que foram guardados errados (`remover_do_vocabulario`); o verificador de português é avisado na hora.
9. **O programa reabre o último projeto** ao abrir (`ConfigApp.ultimo_projeto`; em silêncio, e some se a pasta não existe mais).
10. **Shorts livre "vivo"** (`ui/shorts_livre.py`, `shorts/marcas.py`). A margem esquerda de cada frase leva a barra da cor da categoria que a IA viu no melhor trecho que a cobre; as palavras que já viraram Short ficam sublinhadas, com um losango na margem; uma legenda de cores (um chip por categoria) leva ao próximo trecho de cada uma; passar o mouse numa frase diz a categoria, a nota e os Shorts dela. Dá para montar **"frase 1 + frase 3"** pintando as duas com o marca-texto (`Trecho.fora`); a IA lê só o que o Short fala e o corte sai com saltos.
11. **Dicas de ferramenta em todos os botões e o "?" em cada etapa** (`ui/dicas.py`, `ui/ajuda.py`): o balão aparece depois de uma pausa e some ao sair ou clicar; cada cartão tem um "?" no canto com a explicação da etapa. Os testes falham se um controle novo ficar sem dica.
12. **Passo a passo na primeira abertura** (`ui/tutorial.py`, 28 passos): pergunta "Quer ver o passo a passo?" com a caixinha "não mostrar de novo"; um contorno amarelo marca o que cada passo explica; o rodapé da janela tem a caixinha "Mostrar o passo a passo ao abrir" e o botão "Como usar".
13. **O painel de português do Studio** ocupava 380 a 500 px à toa (a barra de rolagem da lista pede 200 px por padrão); agora tem só o tamanho do que mostra (cerca de 66 px sem avisos).

#### Escolhas que fiz sozinho na rodada 1.5 (confirmar ou mudar)

32. **(1.5) O corte das pausas voltou a ser de cada Short**, como o autor pediu; a decisão 26 da 1.4 ("global") ficou valendo só para a altura da legenda. Config e projetos antigos com `shorts_cortar_pausas` e `shorts_pausa_s` carregam normalmente (o pydantic ignora os campos).
33. **(1.5) A borracha não deixa o Short sem palavras:** se tudo fosse apagado, sobra o que estava (nada acontece). No Shorts livre, apagar tudo limpa a escolha.
34. **(1.5) Desfazer não vale para o texto digitado nas caixas de uma linha** (título, hashtags, tempos): ali o Ctrl+Z é o do próprio campo. Na tabela e na legenda, o Ctrl+Z é o do corte.
35. **(1.5) A cor do trecho detectado no Shorts livre é a do trecho de MAIOR nota que cobre a frase** (empate: o mais curto). Os candidatos que a IA não avaliou não aparecem (só os ~40 avaliados). O sublinhado dos Shorts feitos tem a cor da categoria do Short.
36. **(1.5) Um Short do Shorts livre com pedaços de fora usa para a nota e para a IA a duração líquida** (sem os pedaços), mas as pontas continuam as do silêncio do trecho todo.
37. **(1.5) O vocabulário (`recursos/vocabulario_teologico.txt`) foi mexido por um teste meu e perdeu 3 termos que o autor tinha guardado** (veja o achado abaixo).

#### Achado: um teste novo sobrescreveu o vocabulário do autor (2026-10-20, à noite)

Ao escrever o teste do dicionário usei `Pastas(raiz=tmp_path)`, mas `Pastas` não tem o campo `raiz` (o pydantic o ignora), então o teste escreveu e apagou termos no `recursos/vocabulario_teologico.txt` **de verdade**. O arquivo foi restaurado a partir do último commit (227 linhas), **mas as 3 linhas que o autor tinha acrescentado depois do commit (termos guardados por "Está certa: guardar no vocabulário") foram perdidas**: não há outra cópia. É preciso guardá-las de novo (agora o botão "Dicionário…" do Studio ajuda). **Correção:** `tests/conftest.py` ganhou um fixture automático (`vocabulario_do_usuario_protegido`) que restaura o arquivo e reprova qualquer teste que o altere; os testes do dicionário usam pastas dentro de `tmp_path`.

**Testes:** arquivos novos `test_studio_ferramentas.py`, `test_saltos_do_corte.py`, `test_pausas_do_short.py`, `test_marcas_do_sermao.py`, `test_categorias.py`, `test_tutorial_e_ajuda.py`, `test_estabilidade_e_dicas.py`, e casos novos em `test_shorts_livre.py` e `test_shorts_livre_ui.py`.

### Rodada 1.5.1 (2026-10-21): seta, marca-texto e borracha da tabela

Pedido: tirar "COMEÇA AQUI" e "TERMINA AQUI" da tabela e revisar as três ferramentas (o marca-texto "marcava e o
texto sumia", a borracha "apagava texto que não estava selecionado", a seta "não selecionava com dois cliques").
Reproduzido com cliques e arrastos de mouse simulados numa janela de verdade (`event_generate`):

1. **Sem marcador de começo e de fim.** O Short é o texto em branco, em negrito; só o pedaço cortado do meio tem
   marcadores ("corta aqui" e "continua aqui"). `linhas_da_tabela` não devolve mais as linhas `inicio` e `fim`.
2. **Cursor de texto nas três ferramentas.** A seta trocava o cursor por `""`, que herda a seta do sistema (o
   marca-texto usava `pencil`, que parece espelhado, e a borracha `circle`). Agora é `xterm` nas três. O ícone do
   marca-texto passou a ser o "⌶" (o cursor de texto). Para mostrar o que o clique vai fazer, a palavra sob o
   mouse **acende**: amarelo no marca-texto (só as palavras de fora ou riscadas) e vermelho na borracha (só as do
   Short).
3. **Arrasto em elástico.** Antes, toda palavra que o mouse tocasse no caminho ficava pintada, mesmo que ele
   voltasse (por isso a borracha tirava uma palavra a mais). Agora o pintado é sempre do ponto em que o botão foi
   apertado até a palavra sob o mouse, e as palavras que a ferramenta não muda (de fora, para a borracha; do
   Short, para o marca-texto) não entram na conta.
4. **A vista da tabela não pula.** A tabela ficava com a metade de baixo escondida pela rolagem do detalhe, e o
   texto pintado "sumia" para baixo; escolher o marca-texto ou a borracha agora rola o detalhe até a tabela caber
   na tela. Pintar ou apagar no meio não leva mais a vista da tabela de volta ao começo do Short.
5. **Duplo clique da seta.** Dentro do Short já selecionava a palavra; numa palavra cinza ele leva o começo ou o
   fim até ela e agora também **deixa a palavra selecionada**. A seleção da tabela ganhou cor própria
   (`selectbackground`), que continua visível quando o foco sai.
6. As mesmas regras (cursor, aviso sob o mouse, elástico, `vale`) valem no Shorts livre, pelo mesmo `Pincel`.

**Escolhas que fiz sozinho:** (38) o duplo clique numa palavra cinza continua movendo o começo ou o fim (era o
combinado: "a seta seleciona normal e corta com 2 cliques"); (39) a borracha e o marca-texto têm o mesmo cursor
(`xterm`), e o que os diferencia é o botão aceso e a cor do aviso.

### Rodada 1.5.2 (2026-10-22): o texto que sumia no Studio e a tabela com as cores do Shorts livre

Pedido (com prints do aplicativo aberto): no Studio normal, pintar a frase com o marca-texto fazia a frase **sumir**;
o Shorts livre funcionava. Pediram a mesma experiência nos dois, com a legenda colorida de categorias, e que a
tela do Shorts livre parasse de "dar bugs visuais" ao passar o mouse.

1. **Causa do texto que sumia (um defeito meu, da rodada 1.5).** Em `EditorDeCortes._depois_de_mudar` o aviso à aba
   (`ao_mudar` → `pendentes()` → `_guardar_detalhe()`) vinha ANTES de a caixa da legenda mostrar o corte novo; a
   legenda velha era gravada por cima da nova e as palavras que entraram sumiam do Short e da tabela. Só acontecia
   com a aba (nos testes do editor sozinho, não): valia para o marca-texto, a borracha, os botões de palavra e frase
   e o desfazer, e deixava o Short com "✎ legenda" sem a pessoa ter corrigido nada. **Correção:** o detalhe é refeito
   primeiro e `_tela_desatualizada` impede a cópia nesse meio tempo. Conferi as 18 legendas gravadas dos projetos
   reais: todas são correções de verdade (nenhuma tem o defeito), mas **um aplicativo que estava aberto antes desta
   correção guarda o defeito na memória: feche-o sem salvar (ou use "Descartar alterações") e abra de novo.**
2. **A borracha não achava palavras num Short com a legenda corrigida** (14 dos 31 Shorts do projeto real): as linhas
   do Short saíam do texto da legenda e perdiam a ligação com a transcrição. Agora cada palavra da legenda que não
   mudou de tempo continua sabendo qual é a sua (`-1` para a que foi escrita pela pessoa).
3. **A tabela do Studio ganhou a margem do Shorts livre:** barra colorida da categoria que a IA viu na frase, losango
   (e sublinhado nas palavras) do que já virou OUTRO Short, e a legenda de cores acima da tabela, que leva ao próximo
   trecho de cada categoria. O widget da legenda é o mesmo nos dois estúdios (`ui/legenda_de_categorias.py`).
4. **Bug visual do Shorts livre ao passar o mouse:** o rótulo com o que se sabe da frase (categoria, nota, Shorts)
   mudava de tamanho a cada frase, empurrava a coluna do texto e o painel da direita piscava. Agora o rótulo mora
   num quadro de tamanho fixo (duas linhas, texto cortado em 180 letras). A barra da categoria também perdia a cor
   dentro do trecho escolhido (a etiqueta azul pintava a letra de branco); agora só pinta o fundo.

**Escolha que fiz sozinho:** (40) os marcadores "corta aqui" e "continua aqui" continuam na tabela do Studio (o
Shorts livre risca as palavras no próprio texto, mas ali a tabela é uma frase por linha e é editável).

### Rodada 1.6 (2026-10-30): aviso de contato, abertura sem console e instalador para um PC zerado

Pedido (com print do canto superior direito da janela): (1) mostrar ali "Sugestões ou contribuições (Pix):
jonatascesararend@gmail.com", com o e-mail como link `mailto:`; (2) ao abrir o programa ficava uma janela do prompt
de comando aberta no fundo; (3) conferir e **testar** se o instalar vai mesmo instalar tudo num PC sem nada.

1. **Aviso de contato** (`ui/contato.py`, `AvisoDeContato`): na faixa das abas, encostado à direita, nas duas abas.
   O e-mail é um link azul sublinhado (`mailto:`); o botão direito copia o endereço (a chave Pix, para quem não tem
   programa de e-mail). Tem dica de ferramenta, como todo controle.
2. **Console:** a causa era o `iniciar.bat` rodar `uv run …`, que mantém o `cmd` vivo enquanto o programa roda. Agora
   `iniciar.bat` e o atalho abrem `.venv\Scripts\pythonw.exe -m spurgeon gui` (o `.bat` fecha em ~0,1 s). Sem console,
   `sys.stdout`/`sys.stderr` são `None`; por isso `core/sem_console.py` manda a saída para
   `%LOCALAPPDATA%\Spurgeon\Logs\interface.log` (recomeça acima de 1 MB) e um erro de importação ou uma queda mostra uma
   caixa. O trabalhador continua no `python.exe` (`executor.python_do_trabalhador`), com `CREATE_NO_WINDOW`.
3. **O instalar.bat antigo NÃO instalava tudo:** só fazia `uv sync` e exigia Python, uv, FFmpeg e Ollama já instalados
   (o COMO_INSTALAR mandava rodar um `winget install` antes, que num Windows 10 sem winget nem existe). Agora o
   `instalar.bat` chama o `instalar.ps1`, que confere e instala, sem precisar do winget:
   Visual C++ (instalador oficial, UAC, só se faltar), uv `0.13.0` e FFmpeg `9.0.2` full (zips fixados por SHA-256 em
   `modelos\ferramentas\`, só se o PC não tiver um FFmpeg com `ass`, `loudnorm`, `zscale`, `tonemap`, `libx264` e
   `aac`), Python 3.11 e as bibliotecas (`uv sync`; o uv baixa o Python se não houver), Ollama (instalador oficial,
   assinatura conferida), modelos (`spurgeon baixar-modelos`: Whisper, deep-filter e o modelo do Ollama, com pergunta) e
   atalhos na Área de Trabalho e no Menu Iniciar (`recursos/studio_spurgeon.ico`). Opções: `-SemModelos`, `-SemOllama`,
   `-SemAtalhos`, `-Silencioso`. Rodar de novo é seguro (pula o que existe e recria um `.venv` que veio de outro PC).
4. **Dois defeitos achados pelo teste:** (a) **pasta com acento** (`C:\Users\João\…`, comum no Brasil): o Python 3.11
   lê os `.pth` com a página de código local (cp1252), o `.pth` da instalação editável perdia o caminho e
   `import spurgeon` falhava em silêncio, ou seja, o programa inteiro não abriria. O instalador grava um
   `sitecustomize.py` no `site-packages` que acha `src` pela posição do arquivo. (b) O `ffmpeg -filters` do FFmpeg 9 usa
   2 colunas de flags (antes 3); a minha primeira checagem recusava o FFmpeg bom.
5. **Como foi testado.** *De verdade*, numa cópia isolada do projeto ("PC zerado" simulado: `PATH` só com as pastas do
   Windows, perfil e `TEMP` numa pasta com espaço e acento, sem Python do sistema): download do uv e do FFmpeg com hash
   conferido, Python 3.11.17 gerenciado, 76 pacotes, `spurgeon doctor` sem `[ERRO]`, deep-filter e Whisper tiny
   baixados, programa aberto pelo `iniciar.bat` e pelo atalho **sem console**, executor dentro do `pythonw.exe`,
   processamento ponta a ponta (limpeza de áudio, transcrição, legendas, vídeo longo) com o FFmpeg baixado, nova
   execução (idempotência) e recuperação de um `.venv` de outro PC. *Com simulação* (funções trocadas por mentiras):
   a instalação do Visual C++ (UAC negado, códigos 0/3010/1638/erro, assinatura inválida) e a do Ollama (silenciosa, falha,
   assinatura adulterada), além do download com hash e retomada. **Não foi possível testar de verdade:** a instalação
   real do Visual C++ e do Ollama (este PC já os tem, e o instalador do Ollama mexeria no Ollama de verdade), o UAC,
   um Windows 10 real e um PC sem internet. Para a prova completa: Windows Sandbox ou uma VM com Windows limpo, rodando
   `instalar.bat`.

**Escolhas que fiz sozinho na rodada 1.6 (confirmar ou mudar):** (41) o instalador baixa os programas direto, sem
`winget`, com versões e hashes fixados (o `winget` pode faltar e falha por conta própria); (42) a cópia do FFmpeg da
pasta do programa vale mais que a do PATH, e só é baixada se o PC não tiver um FFmpeg completo; (43) os modelos são
baixados na instalação (com pergunta), os das opções: Whisper `large-v3-turbo` e `qwen3:8b`; (44) o botão direito no
e-mail o copia; (45) o ícone do atalho é um quadrado azul com "play" que gerei (troque `recursos/studio_spurgeon.ico`
se quiser outro); (46) o `iniciar.bat` não usa mais `uv run`, então não atualiza as bibliotecas sozinho: depois de
atualizar o programa, rode o `instalar.bat` de novo.

### Rodada 1.7 (2026-11-02): instalação sem tropeços, aviso de atualização, "já postado" e títulos que não se repetem

Pedido: (1) numa instalação do zero, o instalador mandou instalar à mão o uv e o Ollama (`winget install astral-sh.uv` e
`Ollama.Ollama`), foi preciso rodar o `instalar` três vezes e o Windows bloqueou aplicativos; (2) um aviso de atualização
que sempre puxe a versão corrigida do GitHub, no alto da tela, piscando em amarelo, conferido a cada abertura; (3) no
Studio Shorts, uma caixa "já postado no YouTube" que também marque o arquivo na pasta do projeto (no meio da rodada o
autor pediu: separar fisicamente numa subpasta de Shorts já postados); (4) títulos dos Shorts menos parecidos e menos
genéricos, e nunca iguais ou "muito parecidos" com outro.

1. **Instalação: a causa era o GitHub desatualizado.** `origin/main` ainda estava na v1.1.1; os commits 1.3 a 1.6 (com o
   `instalar.ps1` que baixa tudo) nunca subiram. Quem baixou do GitHub pegou o `instalar.bat` e o `COMO_INSTALAR.txt`
   antigos, que mandavam instalar o uv e o Ollama à mão. **O principal conserto é o `git push`** (as credenciais não
   estão nesta máquina). Além disso, o instalador foi endurecido: (a) **o `-Wait` do `Start-Process` no instalador do
   Ollama foi trocado por `-PassThru` + `WaitForExit(20 min)`:** testado no PowerShell 5.1 com um instalador de mentira que
   sai e deixa um filho rodando, o `-Wait` segurou 12,1 s (o filho inteiro) e o `WaitForExit` voltou em 0,2 s; o
   instalador real do Ollama abre o aplicativo da bandeja no fim, o que explica a instalação "travada" que faz a pessoa
   fechar a janela e rodar de novo (causa provável, **não verificada com o instalador real**); (b) **plano B pelo winget**
   para o uv (`astral-sh.uv`) e o Ollama (`Ollama.Ollama`) quando o download direto falha; (c) **`Unblock-File`** nos
   arquivos do programa e no que é extraído (tira a marca "baixado da internet", o `Zone.Identifier` que o SmartScreen
   usa; testado de verdade); (d) **aviso do Controle Inteligente de Aplicativos** (`VerifiedAndReputablePolicyState` 1 ou
   2) com o caminho para desligar; (e) três tentativas nos downloads; (f) o `instalar.bat` chama o `find.exe` do
   Windows pelo caminho inteiro (o `find` do Git para Windows quebrava o teste de "esperar tecla"); (g) `COMO_INSTALAR.txt`
   explica o SmartScreen do `instalar.bat` ("Mais informações" > "Executar assim mesmo"), "Desbloquear" o ZIP antes de
   extrair e o que fazer com o Controle Inteligente. **Não testado:** o instalador real do Ollama, o `winget` instalando
   de verdade, o SmartScreen e o Controle Inteligente ligados (neste PC o valor é 0), um Windows limpo.

2. **Aviso de atualização** (`core/atualizacao.py`, `ui/aviso_de_atualizacao.py`, `ui/app.py`). Ao abrir (1,5 s depois de a
   janela aparecer, em segundo plano), lê o `__version__` de `src/spurgeon/__init__.py` na `main`
   (`raw.githubusercontent.com`, sem a API) e compara com o da versão em uso. Mais nova: uma **faixa amarela por cima
   das abas** pisca (amarelo vivo ↔ âmbar, a cada 0,6 s) com "Atualizar agora" e ✕ (dispensa até a próxima abertura).
   Sem internet, nada aparece e nada reclama. Clicar: confirma, baixa o ZIP da `main`, troca só `src/`, os scripts e
   documentos da raiz e os recursos que ainda não existem (nunca sobrescreve `recursos/`, projetos, modelos, a marca
   d'água nem o vocabulário), com **cópia do que trocou** em `modelos/ferramentas/atualizacao/anterior/` e **desfazer tudo
   se algo falhar no meio**; o `__init__.py` com a versão é o último a ser trocado. Pede o instalador (`instalar.bat
   -Silencioso -SemModelos -SemOllama -SemAtalhos -AguardarPid N -Abrir`, numa janela que espera o programa fechar e o
   reabre) **só se as bibliotecas mudaram**: compara `pyproject.toml` e `uv.lock` sem a linha de versão do próprio
   programa (que muda a cada release). Num clone com `.git` não troca nada (oferece abrir a página do GitHub). A faixa
   fica fixa durante o download e volta a piscar se falhar. Opção "Procurar atualizações ao abrir" no rodapé
   (`ConfigApp.atualizacao_ao_abrir`, ligada). `JanelaPrincipal(config)` não consulta nada (só `executar()` passa
   `verificar_atualizacao=True`), então nenhum teste toca a internet.

3. **Já postado no YouTube** (`Corte.postado`, `Corte.postado_em`; `render/shorts.aplicar_marcas_de_postado`;
   `ui/studio_shorts.py`). Caixa sob o título, no detalhe do corte: marcar **grava na hora só esse campo**
   (`etapas.gravar_postado`, sem levar junto o que está em edição) e **move o `.mp4` e o `.txt` para
   `shorts_gerados/postados/`**; desmarcar traz de volta (para `shorts_gerados/`, ou `desmarcados/` se o corte está
   desmarcado). O registro `shorts_renderizados.json` acompanha (`arquivo` = `postados/short_01_x.mp4`), então o corte
   continua "renderizado"; o `.txt` ganha a primeira linha "JÁ POSTADO NO YOUTUBE (marcado em dd/mm/aaaa)". Um arquivo
   aberto em outro programa não se move no Windows: vira aviso na tela e a marca fica gravada (a próxima renderização
   acerta). `ShortPlanejado.video`/`.texto` já levam `postados/`, então um Short postado que for renderizado de novo
   nasce na subpasta, e o organizador não manda um postado para `desmarcados/`. A lista mostra "✔ postado no YouTube" e o
   resumo, "N já postado(s)". Refazer a seleção mantém a marca (`_criar_metadados`). Cortes sem arquivo só guardam a marca.

4. **Títulos que não se repetem** (`shorts/titulos.py`, `shorts/llm.py`, `shorts/etapas.py`). **A causa, nos projetos reais:**
   no sermão 07 havia 25 cortes com "Quem deveria fazer justiça pratica o mal" 4 vezes e "Quem deveria julgar pratica o
   mal" 6 vezes; no sermão 01, 20 de 31 títulos abriam com "Por que". O `metadados_sem_repetir` só comparava texto idêntico,
   tentava de novo **só 2 vezes e aceitava o último mesmo repetido**, e o prompt só levava os 12 títulos mais recentes.
   Agora: `semelhanca` mede palavras de conteúdo (sem artigos e verbos de ligação, radical de 5 letras, palavras amplas como
   "Deus", "palavra" e "pecado" pesando 0,5); **igual** (só pontuação, caixa e acento), **parecido** (≥ 0,55: Jaccard ou
   contenção com ≥ 2 palavras em comum), **genérico** (menos de 2 palavras específicas, ou só repete o título do sermão) e
   **abertura saturada** (a mesma abertura de 2 palavras em ≥ 3 títulos e ≥ 30% deles). O limite foi calibrado com os 60
   títulos reais (o do Catecismo, com 4 títulos distintos, não dá nenhum falso alarme). `escolher_titulo` pede **só o
   título** de novo (rápido), até 3 vezes, **dizendo à IA o motivo de cada recusa** e soltando a temperatura (0,5, 0,8, 1,0);
   se nenhum servir, fica o de **menor gravidade** (nunca um igual se houve outro). Vale na geração automática, no Shorts
   livre e no "Refazer com IA" do título; o prompt leva até 30 títulos usados, pede título **específico**, de forma variada
   e sem exemplos. Reaproveitar um título que a IA fez antes e que repete outro já criado o refaz (um título que a pessoa
   escreveu ou editou nunca é trocado). Sob o campo Título, o Studio avisa quando o título é igual, parecido ou genérico
   em relação aos outros Shorts **marcados** (serve também aos duplicados que já existem e aos digitados à mão).

**Escolhas que fiz sozinho na rodada 1.7 (confirmar ou mudar):** (47) a consulta compara só o `__version__` da `main` (não
commits): **quem publica precisa subir a versão**, e o desenvolvedor, com a versão à frente do GitHub, nunca vê o aviso;
(48) o `__version__` foi para **1.7.0** (estava em 1.2.0 enquanto as rodadas já iam em 1.6; daqui a numeração das rodadas e a
do código voltam a coincidir); (49) a atualização troca `src/`, scripts e documentos da raiz e **só acrescenta** recursos
novos; `tests/` não vai e arquivos apagados do repositório não são removidos; (50) clone com `.git` não se atualiza
sozinho; (51) o "já postado" **grava na hora** (diferente dos outros campos do Studio, que esperam "Salvar") e usa a
**subpasta `postados/`** (pedido do autor), sem sufixo no nome; (52) limites dos títulos: semelhança 0,55, 3 tentativas
extras, abertura repetida em ≥ 3 títulos e ≥ 30%; a "abertura" só bloqueia na geração (a tela não avisa dela, é só
estilo); **não** há regra de "o título tem de usar palavras do trecho" (nenhum dos 60 títulos reais a acusaria, e
sinônimos gerariam tentativas de ~1 min à toa); (53) a consulta de atualização é uma **opção ligada** no rodapé, por causa da
promessa de "tudo offline"; (54) o instalador tenta o `winget` como plano B, mas nunca como primeira opção (versões e
hashes fixados são mais previsíveis).

**Pendências do autor (rodada 1.7):** (a) **`git push -u origin main --tags`**: sem isso nada disto chega a outros PCs e o
GitHub continua com o instalador antigo (v1.1.1); (b) quem já instalou a v1.1.1 não tem o atualizador: precisa baixar de
novo **uma vez** (depois, o próprio programa avisa); (c) testar num Windows limpo: SmartScreen, Controle Inteligente de
Aplicativos, o instalador real do Ollama e o `winget`; (d) rodar "Selecionar" de novo nos projetos antigos troca os títulos
repetidos que a IA criou (os que a pessoa editou ficam); (e) o `recursos/cortador de vídeo.py` (arquivo do autor, não
rastreado) tem 2 avisos do `ruff` e não foi tocado.
