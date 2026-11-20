# Studio Spurgeon

Aplicativo desktop (Windows, Python 3.11, CustomTkinter, só pt-BR) que transforma sermões
longos em vídeo limpo + legendas + Shorts verticais. Tudo local e offline (as únicas conexões sozinhas são a
consulta de atualização ao abrir, desligável, e a fila de postagens do Instagram, que só tem o que a pessoa
agendou). As redes (YouTube, Meta, TikTok) só são usadas quando a pessoa pede.

**Leia o [Projeto.md](Projeto.md) antes de mexer:** ele tem as decisões, a ordem do pipeline
e o roadmap por fase. Se uma decisão mudar, atualize o Projeto.md junto com o código.

**Branch `controle-remoto` (particular do autor, não vai para a `main`):** controle pelo celular (Tailscale + PWA servida
pelo PC, Web Push, Playwright no YouTube Studio, que também posta: sem a API do YouTube). As decisões (R1 a R17), as escolhas a confirmar (S1 a S10), a estrutura
(`src/spurgeon/remoto/`, `src/spurgeon/canal/`, `render/proxy.py`) e as fases R0 a R5 estão no [Remoto.md](Remoto.md):
leia antes de mexer nele e atualize-o junto com o código. Código novo fica nesses pacotes; no código que já existe, mexa
o mínimo (o branch precisa acompanhar a `main`). Para trazer a `main`: `git merge main` neste branch, nunca o contrário.
No branch: `uv sync --extra ui --extra transcricao --extra shorts --extra remoto`; `uv run spurgeon remoto` sobe o
servidor (porta 18765, só em `127.0.0.1`, publicada pelo `tailscale serve`). O servidor NÃO importa a interface e só lê os
projetos com as funções do núcleo (`remoto/dados.py`); a porta é ligada com `SO_EXCLUSIVEADDRUSE` (no Windows, sem isso
outro programa divide a porta em silêncio). Testes do servidor pelo `TestClient` do Starlette, com o cabeçalho
`Tailscale-User-Login`; Web Push pelo `TransporteFalso`.
A janela do PC fala com o celular por arquivos (`remoto/sessao.py`): a cada segundo
`JanelaPrincipal._conferir_o_celular` vê a trava `_work/celular.json` do projeto aberto, salva o Studio SEM perguntar
(`EditorDeCortes.salvar_em_silencio`), dá o recibo e trava o Studio; também solta a revisão que o celular aprovou. Antes de
processar, a janela e o servidor procuram outro `spurgeon.core.trabalhador` rodando (um por vez no PC, `remoto/fila.py`).
App do celular (`remoto/web/app.js`, sem build): monte o conteúdo com `encher(el, ...)` (ignora null; o `replaceChildren`
escreveria "null"); confira com `node --check`. Shorts livre no servidor: `remoto/livre.py` (índices = palavras da janela do
vídeo, como no PC; a criação com a IA roda numa thread e segura a trava do celular até gravar o corte).
Postar no YouTube (sem API): `canal/postar.py` pelo Chrome com o perfil da automação (`canal/navegador.py` confere o
canal "Revendo Meus Conceitos" antes de agir); o vídeo relacionado vai no passo "Elementos do vídeo" (plano B: a página de
edição), "Programar" pode pedir "Ok" e um
envio que para vira rascunho a retomar (`rascunho=True`). Nunca poste no canal sem o autor pedir: testes trocam o navegador
por funções de mentira (`remoto/postagem.Postagens(postar=…)`).

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
- Ordem das renderizações: `renderizar_video_longo` ANTES de `renderizar_shorts` (a análise e as revisões humanas vêm
  antes das duas). Os Shorts são cortados do `video_longo_otimizado.mp4` (`render/fonte_do_short.py`): o longo grava no
  `_work/video_longo_info.json` o bloco `fonte_dos_shorts` (mapa de tempo, onde o vídeo principal começa, zonas de
  mescla/fade, zoom e cor queimados, retângulo da marca) e `fonte_do_longo` decide se ele serve (mesmo vídeo, mesmas
  `opcoes_relevantes`, mesma janela, zoom/cor iguais aos dos Shorts); `motivo_de_nao_servir` decide por Short. Tudo do
  Short (`corte`, `saltos`, `pausa_s`, palavras) continua no tempo ORIGINAL; só `leitura_do_corte`/`trechos_na_fonte`/
  `palavras_na_fonte` convertem, na hora de renderizar. No original o caminho é o de sempre (conversor `None`). A
  assinatura do Short só leva `fonte_id` quando sai do longo (`assinatura_do_short(..., fonte_id=)`): não mude as
  `opcoes_relevantes` das duas etapas por causa disso (refaria o que já está pronto). Longo de versão antiga (sem o
  bloco) não serve: os Shorts saem do original. Subiu o formato do bloco? Suba `VERSAO_DA_FONTE`.
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
- ARMADILHA do Studio: `EditorDeCortes` avisa a aba a cada mudança (`ao_mudar`), e a aba chama `pendentes()`, que
  chama `_guardar_detalhe()`, que copia a caixa da legenda para o corte. Se isso rodar antes de a tela mostrar o
  corte novo, a legenda velha cobre a nova e as palavras que entraram somem. Por isso `_depois_de_mudar` refaz o
  detalhe ANTES de chamar `_houve_mudanca` e `_tela_desatualizada` trava `_guardar_detalhe` nesse meio tempo.
  Os testes do editor sem aba NÃO pegam isso: teste mudanças do corte também por `AbaStudioShorts`.
- Linhas da tabela do Short com legenda corrigida (`linhas_da_tabela`) mantêm `indices` (a posição na
  transcrição) das palavras cujo tempo não mudou; `-1` é a palavra que a pessoa escreveu. Sem isso o marca-texto e
  a borracha não achavam palavra nenhuma num Short com legenda corrigida.
- Margem da tabela do Studio (`TabelaDeFrases.mostrar(..., margens=...)`, `MargemDaLinha`): igual à do Shorts livre,
  com a barra da categoria detectada, o losango dos OUTROS Shorts e o sublinhado de suas palavras; a legenda de
  cores é `ui/legenda_de_categorias.LegendaDeCategorias` (a mesma nos dois estúdios; `itens_da_legenda` monta os
  chips). Recalcular o que não muda é caro: `EditorDeCortes` guarda as detecções e as palavras de cada corte
  (`_indices_do_corte`) por carga. Rótulos que mudam ao passar o mouse vão num quadro de tamanho fixo
  (`_quadro_info` do Shorts livre): um rótulo que cresce empurra a grade e o painel da direita pisca.
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
  `JanelaPrincipal.abrir_tutorial`. `executar()` mostra a lista de projetos (`ui/escolha_de_projeto.py`, o último
  `ConfigApp.ultimo_projeto` marcado) e oferece o passo a passo se `tutorial_ao_abrir`; `JanelaPrincipal(config)`
  sozinha não faz nenhum dos dois (`perguntar_projeto=False`; os testes dependem disso). A lista abre o projeto na
  página de Processamento (`_abrir_projeto(..., ir_para_o_studio=False)`); o botão "Abrir projeto…" vai ao Studio.
  `core/projeto.listar_projetos` só lista o que "Abrir projeto…" abriria. Mudou um recurso que o passo a passo
  explica? Ajuste o texto em `PASSOS`.
- Ícone: `ui/icone.py`. `iconbitmap` com `recursos/studio_spurgeon.ico` na janela e em toda `CTkToplevel`
  (`icone.instalar()`, chamado em `ui/__init__.py`); o CustomTkinter só põe o ícone dele depois de 200 ms se ninguém
  chamou `iconbitmap` (por isso é na criação). `definir_identidade_do_app` (AppUserModelID) vem antes da 1ª janela.
  `wm_iconbitmap()` não devolve o caminho no Windows: teste pela bandeira `_iconbitmap_method_called`.
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
- Abertura SEM console: `iniciar.bat` e o atalho abrem `.venv\Scripts\pythonw.exe -m spurgeon gui`. Sob o
  pythonw `sys.stdout`/`sys.stderr` são `None`: `core/sem_console.preparar_sem_console` (chamado por `__main__._gui`)
  manda a saída para `%LOCALAPPDATA%\Spurgeon\Logs\interface.log` e `_avisar_na_tela` mostra erro fatal numa caixa.
  O trabalhador roda no `python.exe` ao lado (`executor.python_do_trabalhador`) e TODO `subprocess` do app usa
  `CREATE_NO_WINDOW` (um filho de console aberto por um processo sem console abre uma janela preta). Nunca avise o
  usuário só por `print`/stderr. Testes que simulam o pythonw põem `sys.stdout = None` DENTRO do teste (o pytest
  recoloca o dele ao começar cada fase).
- Instalador: `instalar.bat` só chama o `instalar.ps1` (PowerShell 5.1 vem em todo Windows). O `.ps1` é UTF-8 COM BOM
  (sem BOM o 5.1 lê os acentos errado) e CRLF. Cada passo (`Garantir-*`) confere antes e é idempotente: Visual C++
  (UAC, só se faltar), uv (zip fixado em `modelos\ferramentas\uv`), `uv sync`, FFmpeg (zip fixado por SHA-256 em
  `modelos\ferramentas\ffmpeg`; `core/ffmpeg.localizar_binario` procura ali ANTES do PATH), Ollama (instalador
  oficial, `/VERYSILENT`), modelos (`spurgeon baixar-modelos`, `core/instalacao.py`) e atalhos (pythonw, sem
  console). Trocou a versão ou a URL do uv/FFmpeg? Refaça o SHA-256 (`tests/test_instalador.py` confere o formato).
  `. .\instalar.ps1` só carrega as funções (não instala), o que dá para testar com `Start-Process`/`Baixar-Arquivo`
  trocados por funções de mentira. A saída do `ffmpeg -filters` já mudou de 3 para 2 colunas de flags: não conte
  colunas.
- Pasta com acento (`C:\Users\João\…`): o Python 3.11 lê os `.pth` com a página de código local (cp1252), então o
  `.pth` da instalação editável perde o caminho e `import spurgeon` falha em silêncio. O instalador grava um
  `sitecustomize.py` no `site-packages` do `.venv` que acha `src` pela posição do arquivo (marcador "Criado pelo
  instalar.ps1"). O código usa `raiz_repositorio()` (posição do pacote), por isso NÃO dá para trocar por
  `--no-editable`, e o `dev-mode-exact` do hatch exige o pacote `editables` em tempo de execução.
- Testar o instalador num "PC zerado" sem VM: cópia do projeto (sem `.venv`/`modelos`) numa pasta com espaço e acento,
  `PATH` só com as pastas do Windows, `USERPROFILE`/`LOCALAPPDATA`/`APPDATA`/`TEMP` numa pasta de teste e
  `UV_PYTHON_PREFERENCE=only-managed`; rode com `-Silencioso -SemOllama -SemAtalhos` (o Ollama e os atalhos mexem no
  PC de verdade). `platformdirs` ignora variáveis de ambiente: para isolar o `config.toml` use um `.pth` no
  `site-packages` do `.venv` de teste que troque `platformdirs.user_config_path`.
- Atualização pelo GitHub (`core/atualizacao.py`, `ui/aviso_de_atualizacao.py`): compara o `__version__` da `main`
  (raw.githubusercontent) com o do código; **suba `__version__` (e rode `uv lock`) a cada push que deve chegar às pessoas**.
  Troca só `src/`, scripts/documentos da raiz e recursos NOVOS (nunca sobrescreve `recursos/`), com desfazer se falhar, e só
  roda o instalador se `pyproject.toml`/`uv.lock` mudaram SEM contar a linha de versão do projeto. Clone com `.git` não
  atualiza sozinho. Só `executar()` consulta (`verificar_atualizacao=True`); testes nunca tocam a internet (troque
  `app_mod.consultar_atualizacao`/`baixar_e_atualizar`/`reabrir_programa`). A faixa fica numa linha própria acima das abas
  (`faixa` → `aviso_de_atualizacao` + `linha_das_abas`). `instalar.ps1` aceita `-AguardarPid N` e `-Abrir`.
- Já postado e agendado, e as pastas deles: `Corte.postado`/`postado_em`; marcar no Studio grava só esse campo na
  hora (`etapas.gravar_postado`) e move o `.mp4` e o `.txt` para `shorts_gerados/postados/` por
  `render/shorts.aplicar_marcas_de_postado`. Um Short com `agendado_para` mora em `shorts_gerados/agendados/`
  (`PASTA_DOS_AGENDADOS`); ao vencer vira postado e segue para `postados/`; tirar a marca devolve: `shorts_gerados/`, ou
  `desmarcados/` se o corte está desmarcado. Postado vale mais que agendado, e agendado mais que desmarcado
  (`pasta_do_estado`). `ShortPlanejado.video`/`.texto` já levam o prefixo (`postados/` ou `agendados/`) e o registro
  guarda `postados/arq.mp4`. O `.txt` abre com "JÁ POSTADO" ou "AGENDADO PARA …". Depois de mover, chame
  `EditorDeCortes.recarregar_registro(reordenar=False)` (senão o corte parece "novo"). A fila das redes não guarda o
  caminho do arquivo (`publicacao/pacote.arquivo_do_short` lê o registro), por isso mover não a quebra.
  Depois de mover, chame `EditorDeCortes.recarregar_registro(reordenar=False)` (senão o corte parece "novo").
- Títulos dos Shorts (`shorts/titulos.py`): igual/parecido/genérico/abertura repetida, sem IA. `ClienteLLM.melhorar_titulo`
  pede só o título de novo (até 3 vezes), dizendo o motivo da recusa; `refazer("titulo")` e `metadados_sem_repetir` o usam.
  Clientes de IA falsos nos testes precisam de `melhorar_titulo` (e títulos realistas e distintos, como
  `TITULOS_DISTINTOS` em `tests/test_shorts.py`: "Título número 1/2" agora seria barrado como parecido). Nunca ponha
  exemplos de título nos prompts (`REGRAS_DO_TITULO`).
- Scripts de edição em Python: `Path.write_text` no Windows troca `\n` por `\r\n` e arquivos inteiros ficam em CRLF (o
  `.gitattributes` quer LF nos `.py`); use `read_bytes().decode()`/`write_bytes()`. `Path.read_text(newline=)` só existe
  no Python 3.13. Confira com `git ls-files --eol` (o `grep $'\r'` do Git Bash não é confiável). No Git Bash o `find` é
  o do Git, não o do Windows (por isso o `instalar.bat` usa `%SystemRoot%\System32\find.exe`).
- Publicar nas redes (`publicacao/`, pesquisa e limites em [Integracoes.md](Integracoes.md)): cada rede é uma
  `Plataforma` (`modelo.py`) com `problemas`, `publicar`, `retomar` (fila local) e `consultar`; `agenda.py` tem
  `criar_plataformas`, `publicar_em` (uma rede de cada vez; uma que falha não impede as outras e todo resultado,
  inclusive o erro, vai para `_work/postagens.json`) e o `Agendador`; `fila.py` varre TODOS os projetos. Os estados
  são `enviado`, `agendado` (a rede guarda o horário), `na_fila` (o Spurgeon guarda: Instagram, com o aplicativo
  aberto), `rascunho`, `assistido` (TikTok) e `erro`. Segredos só pelo `Cofre` (`integracoes.json` ao lado do
  `config.toml`, DPAPI do Windows): NUNCA no `config.toml` nem em log. HTTP só por `Transporte` (`rede.py`); os
  testes usam `tests/falso_de_rede.TransporteFalso` e `sem_dormir` (`com_tentativas` e os laços de espera usam
  `time.sleep`): nenhum teste toca a internet nem tem credencial. Relógio sempre injetável (`relogio=`). Nada
  disso foi provado com contas reais: o que falta confirmar está no fim do Integracoes.md.
- Aba Integrações (`ui/integracoes.py`): um `CartaoDaConta` por conta (a Meta junta Instagram e Facebook); segredo
  guardado nunca volta para a caixa (placeholder "guardado"), campo secreto vazio no Salvar NÃO apaga; Conectar
  roda em `Tarefas` (OAuth de desktop em `oauth.py`: loopback `127.0.0.1` + PKCE; o TikTok exige a porta 53682 e o
  PKCE em hexadecimal). A aba ocupa a janela toda (`_trocar_aba`), como o Studio.
- Postar pelo Studio (`ui/postagem.py`): `LinhaDeAgendamento` (caixa "Agendado para" + data + hora + previsão,
  ao lado do "Já postado") e `JanelaDePostagem` (escolhe redes e horário, envia em segundo plano com o andamento
  lido por `after`, nunca por widget na thread). `Corte.agendado_para` (`AAAA-MM-DDTHH:MM`, hora do computador)
  grava na hora (`gravar_campos_do_corte`, só aquele campo, como o "já postado") e leva o arquivo para `agendados/`
  (`EditorDeCortes._gravar_agendamento`; ao abrir o projeto os agendados fora da pasta são acertados); passada a hora o
  Short vira postado sozinho (`shorts/agendamento.aplicar_vencimento`, conferido ao abrir o projeto e a cada 30 s, sem
  perguntar à rede). Agendar nas redes guarda o horário no Short; publicar agora marca como já postado; rascunho
  e passo assistido não mudam nada. Testes do Studio sem aba/`publicacao` escondem o botão de postar.
- Calendário e relógio (`ui/seletores_de_horario.py`): `BotaoDoCalendario` (📅) e `BotaoDoRelogio` (🕒) ficam ao lado
  dos campos de data e de hora da `LinhaDeAgendamento` e da `JanelaDePostagem` e abrem uma `JanelaDeSeletor` com
  `CalendarioMensal` ou `RelogioDePonteiros` (hora em dois anéis, 00 a 11 fora e 12 a 23 dentro; depois os minutos só
  em blocos de 15). Desenhados em `Canvas`; a geometria (`hora_do_ponto`, `minuto_do_ponto`, `celula_do_ponto`) são
  funções puras: teste por elas, sem Tk. Os botões entregam a escolha por `ao_escolher` e quem os usa escreve nos
  campos (na linha, `_ao_escolher_o_dia`/`_ao_escolher_a_hora` já marcam o agendamento e avisam uma vez só). A
  janelinha segura o foco e o devolve à janela de antes (a de postagem é modal). O texto digitado continua valendo
  (`shorts/agendamento.ler_data`/`ler_hora`).
- Studio durante a renderização (`ui/app.py`): o Studio só fica travado enquanto a etapa atual (`_etapa_atual`, do evento
  `progresso`) NÃO é uma de `_ETAPAS_DE_RENDER` (a `selecionar_shorts` é a única que reescreve o `cortes_shorts.json`); ao se
  soltar, o editor é relido. Com um processamento rodando o `EditorDeCortes` está em `definir_processamento(True)`: "já
  postado", "agendado para" e título gravam na hora no `cortes_shorts.json`, mas NADA move nem renomeia arquivo renderizado
  (o trabalhador regrava o `shorts_renderizados.json` inteiro a cada Short): toda mudança de arquivo passa por
  `_mover_arquivos`/`_sincronizar_arquivos`, que só anotam `_arquivos_adiados`, e `definir_processamento(False)` aplica o que
  está SALVO. Código novo do editor que chame `aplicar_marcas_de_postado`/`sincronizar_arquivos` direto quebra isso. Quem
  confere Shorts depois de renderizar (`render/finalizar.py`) acha o arquivo por `arquivo_do_short_pronto` (registro primeiro):
  o caminho que o corte pede mudou sem o arquivo mudar. No fim, o editor só é refeito se não houver alteração sem salvar.
- Lista do Studio: cada linha tem o selo "✔ Postado dd/mm às hh:mm" ou "📅 Agendado …" (`selo_de_publicacao`; `postado_em`
  guarda `AAAA-MM-DDTHH:MM`, ou só o dia nos projetos antigos: leia por `shorts/agendamento.ler_postado_em`/`rotulo_do_postado`)
  e o filtro "Mostrar" (`passa_no_filtro`) tira linhas de `_ordem`: nunca use `self._ordem.index(i)` sem conferir
  (`_posicao_na_lista`), e `_manter_visivel` segura a linha do corte que a pessoa acabou de mudar até a lista ser refeita.
- "Fechar o programa ao terminar" (`_var_fechar`, rodapé): NÃO vai para o `config.toml` (um programa que se fecha sozinho tem que
  ser pedido a cada vez), só fecha depois de `concluido` e nunca pergunta: com erro, cancelamento, alteração não salva no Studio
  ou fila de postagens, fica aberto. Teste que chame `_fechar_ao_terminar` tem que trocar `destroy` (a janela é compartilhada).
- Título e arquivo: o nome do arquivo vem do `Corte.slug`. O Studio guarda o título que o nome tinha
  (`_titulo_do_nome`): se o título muda, `salvar()` zera o `slug` e ele é refeito a partir do título novo (título
  que a IA deu e a pessoa não mexeu mantém o nome curto da IA). `render/shorts.sincronizar_arquivos` renomeia em
  duas fases o `.mp4` e o `.txt` já renderizados, move para `postados/` ou `agendados/`, reescreve o `.txt` com os textos de agora
  e NUNCA sobrescreve nem perde arquivo (nome ocupado ou arquivo preso viram `falhas`). A lista da esquerda
  acompanha o que se digita (`_espelhar_titulo_na_lista`, só fora de `_carregando`/`_tela_desatualizada`).
- Aviso do canto superior direito: `ui/contato.py` (`AvisoDeContato`, e-mail como `mailto:`; botão direito copia),
  dentro da faixa das abas, visível nas duas abas.

## Hardware de referência (a máquina de desenvolvimento)

i7-6800K (12 threads), 15,8 GB de RAM, GTX 760 de 2 GB sem CUDA utilizável e sem NVENC.
Os tempos do Projeto.md foram medidos nela. O caminho só com CPU é o principal; cuidado com tempos e memória.
