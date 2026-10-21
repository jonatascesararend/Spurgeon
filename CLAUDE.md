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
- Etapas que geram o vídeo final devem ter `entrega = True`; sem nenhuma, `_work/` é mantida. Ao
  concluir, só os arquivos pesados de `_work/` somem: os `.json` da análise ficam (permitem reabrir o
  projeto sem refazer a IA; nunca volte a apagá-los).
- Modos (`OpcoesProcessamento.modo`): `completo`, `so_shorts`, `so_vinhetas`. Use `processa_video_longo`
  e `processa_shorts`, não `gerar_*`, para saber o que roda; etapas fora do modo ficam em
  `estado.etapas_guardadas`. Só vinhetas = etapa `adicionar_vinhetas` (emenda sem recodificar).
- Shorts já renderizados: `_work/shorts_renderizados.json` (por id do corte, com assinatura). O corte,
  a legenda corrigida (`Corte.legenda`) e a marca d'água entram na assinatura; nada é apagado (os
  desmarcados vão para `shorts_gerados/desmarcados/`).
- Marca d'água em `render/marca.py` (overlay numa entrada extra de imagem); PNG com transparência
  vale como está, o resto só perde o fundo se a pessoa pedir.
- Interface: `ui/studio_shorts.py` (`EditorDeCortes`) serve à aba "Studio Shorts" e à janela de
  aprovação; `ui/verificacao.py` (`PainelDePortugues`) serve à revisão da transcrição e à legenda.
  O verificador (`transcricao/verificador.py`) é offline: Hunspell pt_BR via `spylls` diz o que
  existe; `pyspellchecker` só sugere. Tk: ao trocar a imagem de um rótulo, ponha a nova antes de
  soltar a antiga (senão dá "image pyimageN doesn't exist"); `CTkLabel.configure(image=None)` não limpa.
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
- Começo e fim do vídeo (`video_inicio_s`/`video_fim_s`, 0 = até o fim): vale em `calcular_cortes`
  (`limitar_trechos`), na análise dos Shorts e no Studio (`palavras_da_janela`), na estimativa e no
  pré-voo. A **transcrição continua sendo do áudio inteiro** (mudar o corte não refaz o Whisper); o
  começo e o fim não viram preferência salva. Use `opcoes.janela_do_video(duracao)`.
- Estilo da imagem: `core/aparencia.py` (`Enquadramento` = zoom, `TratamentoImagem`), filtros em
  `render/imagem.py`. Ordem no vídeo longo: crop do zoom → HDR→SDR → ampliação → tratamento (em
  yuv420p limitado) → fades → marca. Nos Shorts o 9:16 sai de dentro do zoom. A prévia da tela usa o
  MESMO filtro pelo FFmpeg sobre um quadro `.nut` (`render/previa.py`); nunca reimplemente o filtro em
  PIL. Zoom, tratamento, fade e arquivos das vinhetas entram em `opcoes_relevantes` e nas assinaturas.
- Zoom e legenda dos Shorts: `ui/painel_shorts.py` (`PainelShorts`, abaixo do zoom do vídeo). O zoom
  do Short (`shorts_zoom_*`, `OpcoesProcessamento.enquadramento_do_short`) é aplicado DENTRO do 9:16
  em `filtros.recorte_vertical(..., zoom_do_short)`; só entra nas assinaturas quando ativo. A altura
  da legenda (`legenda_posicao_vertical`) vive só nesse painel (não existe mais nas opções
  avançadas). A prévia é `render/previa.previa_do_short` (FFmpeg + `montar_ass` com texto de
  exemplo); o painel pega as demais opções por `JanelaPrincipal._opcoes_da_tela`.
- Prévias na interface: quadros por `ui/quadros.FonteDeQuadros` (uma extração por vez, com trava) e
  tarefas por `ui/assincrono.Tarefas` (a thread só calcula; o resultado volta pela fila da janela).
  Cada prévia grava num arquivo próprio; dois FFmpeg no mesmo arquivo se atrapalham no Windows.
- Rolagem: use `ui/rolagem.QuadroRolavel`, nunca `CTkScrollableFrame` direto (a roda do padrão é lenta e deixa rastros na imagem). Prévia do Short em miniatura: `render/previa.previa_do_corte` (reduz logo depois do recorte, antes do HDR→SDR) + `ui/miniplayer.MiniPlayer` (quadros por FFmpeg→RGB, som por `winsound`); o `EditorDeCortes` recebe `tarefas` e `origem_da_previa` e guarda uma prévia por corte, com assinatura. O início e o fim do corte são uma caixa só (`janela_do_corte`); `PainelDePortugues` tem o modo `compacto`.
- Shorts: o limite é `DURACAO_MAXIMA_SHORT_S` (180 s, em `core/config.py`), nunca o número solto.
  As avaliações da IA são guardadas por **hash do trecho** (`marca_do_candidato`), não pelo id, e
  `migrar_avaliacoes` roda antes de `candidatos.json` ser trocado. Mudou algo em `config.toml` que
  precisa migrar? Suba `VERSAO_CONFIG` e trate em `ConfigApp._migrar`.
- Ao editar com `perl -0pi` e heredocs, `\n` e `\S` nos textos de substituição se perdem (viram
  quebra de linha e `S`): para código com barras invertidas use a ferramenta de edição.
- Passo que não dá para interromper no meio (carregar modelo, decodificar áudio) roda em
  `ctx.executar_vigiado`; trabalho da própria etapa, em `ctx.batimento`: o log avisa a cada ~15 s, cancelar
  vale na hora e a pausa é confirmada. Nunca deixe um passo longo sem log (a transcrição já ficou 21 min
  "parada" sem dizer nada). Medidas da máquina para o log em `core/diagnostico.py`.
- **Nunca deixe uma leitura bloqueante pendente no stdin do trabalhador** (`for linha in sys.stdin` numa
  thread): no Windows isso trava os `import` pesados (spaCy, faster-whisper…) até chegar um comando, e foi
  a causa do "só anda se eu pausar e retomar" (62 s em vez de 2 s). Use `trabalhador.linhas_do_stdin`
  (`PeekNamedPipe`). O trabalhador roda em prioridade abaixo do normal.
- A coluna esquerda tem 3 páginas (`PainelOpcoes.paginas`, botões no topo): 1 Vídeo e geração, 2 Corte e
  imagem, 3 Revisão e opções. Corte, zoom e legenda dos Shorts são `SecaoRecolhivel`.
- Ordem dos cartões da coluna esquerda: 1 Vídeo, 2 O que gerar, 3 Corte e enquadramento, 4 Tratamento,
  5 Marca, 6 Revisão, 7 Processamento. O corte, o zoom e a legenda dos Shorts nascem em
  `PainelEntrada.montar_ajustes(PainelOpcoes.cartao_ajustes)`. O "O que gerar" esconde grupos por modo
  (`_GRUPOS_POR_MODO`); widgets escondidos continuam existindo (só saem da tela).
- Elementos do YouTube sobre a prévia do Short: `ui/youtube_shorts.py` (desenhados por PIL, não pelo
  FFmpeg; `REGIOES` e `conflitos_da_legenda` dizem o que a legenda encobre).
- Shorts livre: `shorts/livre.py` (cria o corte de um trecho escolhido), `shorts/busca.py` (palavras
  parecidas e tema) e `ui/shorts_livre.py`. `Corte.origem` é `automatico` ou `manual`; manuais têm id
  >= `ID_BASE_DOS_MANUAIS` e `SelecionarShorts` os mantém ao refazer a seleção. A prévia do Studio leva
  SEMPRE a legenda (`com_legenda=True`), mesmo com "Legendas dinâmicas" desligado.
- Fade da introdução e da vinheta: `fade_estilo` = `mesclar` (padrão; sobreposição com `xfade` e `acrossfade`,
  dentro da primeira e da última parte do vídeo; o vídeo fica mais curto pela sobreposição) ou `preto`.
  Quem soma tempos (legenda, conferência) usa `render/mescla.py`. "Só vinhetas" nunca mescla.
- Testes nunca tocam o `config.toml` do usuário: o `conftest.py` isola `platformdirs.user_config_path`.
  Não crie fixture que escreva fora de `tmp_path`.
- Studio Shorts é uma aba que ocupa a janela toda (`JanelaPrincipal._trocar_aba`): a faixa das abas é a linha 0
  da janela; a coluna de opções (`_coluna`) e o andamento (`_direita`) saem de cena e `studio` é filho da
  janela (não da coluna da direita). A altura da legenda dos Shorts é opção GLOBAL que o Studio acerta por
  `definir_ajustes(legenda=…)` (`JanelaPrincipal._definir_ajustes_do_short`); o valor atual vem de
  `origem_da_previa().opcoes`. O corte das pausas NÃO é opção global: é de cada Short (`Corte.pausa_s`).
- Tabela de frases (`ui/tabela_de_frases.py`, `EditorDeCortes._janela`): uma frase por linha, com a hora. O widget
  troca o comando Tcl do `Text` por um filtro: só o texto das linhas do corte aceita inserir e apagar, e tudo o
  que o código escreve passa por `mostrar()`/`mostrar_aviso()` (flag `_interno`). Nunca escreva na tabela por
  fora disso. A legenda tem DOIS lugares de edição (a tabela e a caixa "Legenda do Short"): a caixa é a que
  `_guardar_detalhe` lê, e `_levar_a_tabela_para_a_legenda` / `_levar_a_legenda_para_a_tabela` os mantêm iguais.
- Mexer nas pontas (`EditorDeCortes._ajustar`) NÃO refaz a lista: use `_atualizar_linha` e `_reposicionar`
  (`_reordenar` destrói e recria todas as linhas; só para ordem trocada, corte novo ou projeto recarregado). A
  legenda corrigida acompanha as pontas por `shorts/edicao.legenda_preservada`.
- Refazer título, descrição e hashtags: `ClienteLLM.refazer(campo, …)` (um pedido por campo, a partir do texto
  da legenda do Short, não do trecho original); o modelo fica na memória 3 min (`manter_na_memoria`).
- Pausas e saltos do Short (`shorts/pausas.py`): cada Short tem `Corte.pausa_s` (0 = não corta; de 0,1 a 10 s)
  e `Corte.saltos` (`[[de, ate], …]` em segundos do vídeo: pedaços tirados de dentro dele). `trechos_mantidos`
  junta os dois; vídeo e som usam os MESMOS trechos (`recortes_de_quadros` + `atrim`) e a legenda é remapeada por
  `remapear_palavras`. `Corte.duracao` é a duração LÍQUIDA (sem os saltos); a janela de leitura é `janela_s`.
  Assinaturas e registro só levam `saltos` e `pausa_s` quando existem (Shorts já renderizados continuam valendo).
- Modelo de edição por conjunto de palavras (`shorts/edicao.py`): `indices_incluidos`, `faixas_de`,
  `corte_com_indices`, `incluir_palavras` (marca-texto), `tirar_palavras` (borracha). O que fica entre duas faixas
  vira salto, e cada ponta de faixa leva o respiro de costume (`respiro_nas_pontas`).
- Desfazer e refazer (Ctrl+Z/Ctrl+Y e os botões) vivem no `EditorDeCortes`: uma história por id de corte
  (`_registrar_no_historico`, fotos de `_CAMPOS_DO_HISTORICO`); as caixas de texto têm o `undo` do Tk desligado
  para haver uma história só. Mudança nova apaga o "refazer". "Descartar alterações" (`descartar_alteracoes`)
  relê o arquivo e esquece a história.
- Marca-texto e borracha: `ui/pincel.Pincel` (o arrasto sobre as palavras de um `Text`, usado pela tabela e pelo
  Shorts livre; `ui/ferramentas_de_marca.FerramentasDeMarca` são os três botões seta/marca-texto/borracha).
  A seta é o gesto antigo; com as outras duas o Pincel devolve "break" nos eventos do mouse. O cursor é SEMPRE o
  de texto (`xterm`; `cursor=""` herdaria a seta do sistema). A palavra sob o mouse acende (amarelo no
  marca-texto, vermelho na borracha) e só acendem as palavras que a ferramenta muda (`vale`: o marca-texto, as de
  fora; a borracha, as do Short). O arrasto é um elástico do ponto apertado até a palavra sob o mouse.
- A tabela não tem marcador de começo nem de fim (só `corta`/`volta`, do pedaço cortado): o Short é o texto em
  branco. `TabelaDeFrases._marcas["inicio"|"fim"]` apontam a primeira e a última linha em branco (para `ir_a`).
  Depois de pintar ou apagar a vista da tabela NÃO se mexe; o duplo clique numa palavra cinza leva a ponta até ela
  e deixa a palavra selecionada (`selecionar_palavra`); escolher marca-texto ou borracha rola o detalhe para a
  tabela caber na tela (`_trazer_a_tabela_para_a_vista`).
- Categorias dos Shorts: 17 chaves em `shorts/dados.CATEGORIAS` (rótulo e cor únicos por categoria; a IA só pode
  responder uma delas, o esquema leva o enum). Mudar a lista exige mexer em `ROTULOS_CATEGORIA`,
  `COR_DA_CATEGORIA` e `DEFINICOES_DAS_CATEGORIAS` juntos (`tests/test_categorias.py` confere).
- Shorts livre "vivo" (`ui/shorts_livre.py`): a margem de 3 caracteres de cada linha (`_PREFIXO`) leva a barra
  da categoria detectada (`shorts/marcas.trechos_detectados` lê `candidatos.json` + `avaliacoes.json`) e o losango
  dos Shorts que já existem; `Trecho.fora` guarda as palavras puladas (pedaços separados) e `gerar_corte_livre`
  as transforma em saltos. `atualizar_marcas()` roda ao abrir o painel e depois de criar um Short.
- Dicionário personalizado (`ui/dicionario.py`, botão "Dicionário…" do Studio): edita
  `recursos/vocabulario_teologico.txt`; remover usa `transcricao/vocabulario.remover_do_vocabulario` e avisa o
  verificador (`atualizar_vocabulario_compartilhado`). Testes NUNCA tocam esse arquivo: o `conftest.py` o
  restaura e reprova o teste que o mexer; `Pastas(raiz=…)` não existe (o pydantic ignora o argumento e usa as
  pastas de verdade): use `Pastas(recursos=tmp_path / "recursos", …)`.
- Dicas de ferramenta e "?": `ui/dicas.dica(widget, texto)` em TODO botão, caixa e campo; `CTkSegmentedButton`
  não aceita `bind`, use `ui/ajuda.dica_do_seletor`. O título de cada cartão é um `ui/ajuda.CabecalhoDeEtapa`
  (com o "?"). Os testes (`tests/test_tutorial_e_ajuda.py`, `test_studio_ferramentas.py`) falham se um controle
  novo ficar sem dica.
- Passo a passo (`ui/tutorial.py`: `PASSOS`, `Tutorial`, `Destaque`, `OfertaDoTutorial`): a janela o liga em
  `JanelaPrincipal.abrir_tutorial`. `executar()` reabre `ConfigApp.ultimo_projeto` e oferece o passo a passo se
  `tutorial_ao_abrir`; `JanelaPrincipal(config)` sozinha não faz nenhum dos dois (os testes dependem disso).
  Mudou um recurso que o passo a passo explica? Ajuste o texto em `PASSOS`.
- Menos "piscar": `ui/estabilidade.py` troca o `configure` das classes do CustomTkinter por um que não faz nada se
  nada mudou (compara com `cget`); `RotuloQueSome` não ocupa linha enquanto está vazio (um `CTkLabel` vazio ainda
  pede 28 px) e `CTkScrollbar`/`CTkFrame` pedem 200 px por padrão (passe `height` pequeno em quadros que devem
  encolher). O painel de português do Studio é `compacto=True`.
- Scripts Python escritos por heredoc: `\n` e `\t` dentro de aspas viram caracteres de verdade no arquivo gerado.
  Escreva o script com a ferramenta de escrita (ou use `\\n`) e rode com `uv run python -I -X utf8 script.py`.
- Testes de render usam vídeos sintéticos; o de sincronia usa quadros numerados em binário e ruído
  branco. `tests/test_render.py` leva ~2 min; a suíte inteira ~25 min (os testes de janela são lentos).
- Commits: o hook do pre-commit chama `uv`; no Git Bash ponha a pasta do winget do uv no PATH.
  No Git Bash não há `python`, e heredocs com apóstrofos às vezes quebram: use a ferramenta de
  edição ou um script para textos assim.

## Hardware desta máquina (alvo da v1)

i7-6800K (12 threads), 15,8 GB de RAM, GTX 760 de 2 GB sem CUDA utilizável e sem NVENC.
O caminho só com CPU é o principal; cuidado com tempos e memória.
