# Studio Spurgeon

Aplicativo desktop para Windows que transforma sermões e aulas longos em um **vídeo limpo, com
legendas**, e em **Shorts verticais** prontos para publicar, e ainda posta e agenda esses Shorts nas
redes da igreja. Tudo roda no seu computador, em português do Brasil: sem conta, sem assinatura e
sem enviar o vídeo para ninguém (as redes só entram quando você manda postar).

Foi pensado para gravações de 45 min a 1 h 30, só com fala, câmera fixa em 16:9 e o pregador no
centro do quadro. O vocabulário do transcritor e as regras de corte seguem a teologia reformada;
você pode acrescentar os seus termos pelo botão **Dicionário…** do Studio Shorts.

- Instalar sem ler nada técnico: [COMO_INSTALAR.txt](COMO_INSTALAR.txt)
- Arquitetura, decisões, resultados medidos e roadmap: [Projeto.md](Projeto.md)
- Postar nas redes (o que cada uma permite, com as fontes): [Integracoes.md](Integracoes.md)

## O que faz

### Do vídeo bruto ao vídeo limpo

1. **Áudio:** extrai, limpa o ruído (DeepFilterNet) e normaliza o volume (−16 LUFS).
2. **Transcrição:** faster-whisper com vocabulário teológico, palavra por palavra, e uma tela para
   revisar o texto.
3. **Português:** verificador offline (dicionário pt_BR) na revisão da transcrição e nas legendas
   dos Shorts: palavras erradas, repetidas, maiúsculas e pontuação, com correção em um clique.
4. **Vídeo longo:** corta as pausas maiores que o limite escolhido, converte o HDR do iPhone para
   SDR, junta introdução e vinheta e gera `video_longo_otimizado.mp4` e o `.srt`.
5. **Começo e fim do vídeo:** corta o que sobra antes e depois (`0:07`, ou `-2:04` para tirar do
   final), com o quadro exato de cada corte e uma prévia de 15 s de cada ponta.
6. **Zoom e tratamento de imagem:** aproxima a câmera do pregador (área cortada sombreada no quadro)
   e ajusta brilho, contraste, cor, brancos e sombras com o antes e o depois lado a lado, sugestão
   automática e presets que guardam o estilo dos vídeos.
7. **Introdução e vinheta:** arquivo à escolha e fade de imagem e som entre elas e o vídeo, que
   pode **mesclar** (as duas pontas se dissolvem uma na outra, sem trecho escuro nem silêncio; o
   vídeo fica a duração do fade mais curto) ou **passar pelo preto**. Em "Só vinhetas" o fade
   sempre passa pelo preto, porque o vídeo pronto não é recodificado.
8. **Marca d'água:** logotipo (PNG transparente, ou outra imagem com o fundo removido) no canto e no
   tamanho escolhidos, no vídeo longo e/ou nos Shorts.
9. **Conferência final:** confere os arquivos e registra o resumo no log.

### Shorts

- **Escolha por IA:** uma IA local (Ollama) escolhe os melhores trechos (até 180 s, com preferência
  por ~60 s; 9:16, 1080×1920), dá uma nota de qualidade a cada um e cria título, descrição e
  hashtags. Os Shorts saem com legenda dinâmica (palavra falada em amarelo) e volume de −14 LUFS.
  A prévia mostra os elementos do aplicativo do YouTube (botões, canal, título) por cima, para
  conferir que a legenda e o pregador não ficam atrás deles.
- **Studio Shorts:** uma aba lista os Shorts sugeridos (trecho da legenda, nota calculada e título)
  para escolher o que renderizar. Depois é só renderizar o que falta, sem refazer o resto. Em cada
  Short você pode:
  - ajustar o início e o fim por palavra ou frase e editar a legenda (a prévia do corte, em
    miniatura, sempre vem com a legenda);
  - usar a tabela de frases com três ferramentas do mouse: a **seta**, o **marca-texto** (pinta o
    que entra no Short) e a **borracha** (tira uma palavra ou frase do meio, para cortar um erro de
    pronúncia e continuar logo depois);
  - definir o seu próprio corte de pausas (a partir de 0,1 s), **desfazer e refazer** (Ctrl+Z,
    Ctrl+Y) e **descartar alterações**;
  - refazer título, descrição e hashtags com a IA, um campo de cada vez;
  - ver a **categoria** que a IA achou (17, cada uma com uma cor) e se o corte foi "gerado
    automaticamente" ou "gerado manualmente";
  - marcar como **já postado** (o Short vai para a pasta `postados/`) ou **agendar** a postagem (vai
    para `agendados/`), e acertar o **dicionário personalizado** (os termos que você guardou como
    certos).
  Quando você muda o título, a lista e o nome do arquivo acompanham.
- **Shorts livre:** uma aba para escolher a dedo um trecho do texto do sermão (com busca por
  palavras parecidas ou por tema), ou montar um Short de pedaços separados ("frase 1 + frase 3"). A
  IA dá a nota e escreve título e hashtags, e o corte entra na lista como "gerado manualmente". O
  texto mostra, com cores, o que a IA detectou em cada trecho e onde já existem Shorts.

### Postar e agendar nas redes

A aba **Integrações** liga as contas da igreja (YouTube, Instagram, Facebook e TikTok); as chaves
ficam criptografadas neste computador e nunca voltam para a tela. No Studio Shorts, cada Short ganha
**Agendado para** e **Postar nas redes…**, que envia para as redes marcadas, agora ou no horário.
A data se escolhe num **calendário** (📅) e a hora num **relógio de ponteiros** (🕒: primeiro a hora,
depois os minutos de 15 em 15), ou se digita. Ao agendar, o arquivo do Short vai para a pasta
`agendados/`; quando a hora passa, ele vira "já postado" sozinho e segue para `postados/`, o que
ajuda a organizar o que já foi e o que ainda vai. O que cada rede permite:

| Rede | O que o Spurgeon faz |
|---|---|
| YouTube | envia e agenda pela API; o YouTube publica sozinho, mesmo com o computador desligado |
| Facebook (Página) | envia e agenda pela API; o Reel tem de 3 a 90 s |
| Instagram | a API não agenda: o Spurgeon guarda o horário e publica na hora, **com o aplicativo aberto** (ao fechar com postagens na fila, ele avisa) |
| TikTok | fluxo assistido: copia a legenda e abre a pasta e o TikTok Studio, onde você agenda |

Cada igreja usa as **próprias chaves** (cria o seu aplicativo em cada rede; o cartão de cada conta
explica o passo a passo). Isso foi feito seguindo a documentação oficial e testado só com respostas
simuladas: **ainda não foi provado com contas reais**; os testes que faltam estão no fim do
[Integracoes.md](Integracoes.md).

### No dia a dia

Três modos: **Tudo**, **Só Shorts** (aproveita o que o projeto já tem) e **Só vinhetas** (junta a
introdução e a vinheta a um vídeo longo pronto, em minutos). **Abrir projeto…** reabre qualquer
projeto e continua de onde ele está, e o programa já abre no último projeto que você usou.

Se o processamento cair, você cancelar ou faltar luz, ele **retoma de onde parou**. Dá para
**pausar** e **cancelar** durante o processamento (vale no próximo ponto seguro da etapa).

Na primeira abertura o programa oferece um **passo a passo** (o botão **Como usar**, no canto de
baixo, repete quando quiser); todo botão tem uma dica ao passar o mouse e cada etapa tem um "?".

## A janela

No alto ficam as abas **Processamento**, **Studio Shorts** e **Integrações**.

- **Processamento:** a coluna da esquerda tem três páginas, escolhidas nos botões do topo:
  **1 · Vídeo e geração** (escolher o vídeo e o que gerar; só aparece o que vale para o modo
  escolhido), **2 · Corte e imagem** (começo e fim, zoom, tratamento de imagem e marca d'água) e
  **3 · Revisão e opções** (pontos de revisão, processamento e opções avançadas). À direita ficam
  os botões, o progresso e um log detalhado.
- **Studio Shorts** e **Integrações** ocupam a janela inteira.

## Requisitos

- Windows 10 (1809 ou mais novo) ou 11, de 64 bits.
- **Memória:** 16 GB de RAM é o recomendado. Com menos, o programa escolhe um modelo de IA menor
  (`uv run spurgeon hardware` mostra a recomendação para o seu computador).
- **Placa de vídeo:** não é necessária; o caminho principal usa só o processador. Uma placa NVIDIA
  com CUDA 12 e cuDNN 9 acelera a transcrição.
- **Disco:** para um sermão de 65 min, ~6 GB livres além do vídeo original, mais ~20 GB para o
  ambiente e os modelos (Whisper ~1,6 GB, Ollama e seu modelo ~7 GB).
- **Internet:** na instalação (depois o programa roda offline). As conexões que ele faz sozinho são
  a consulta de atualização ao abrir (veja abaixo), que dá para desligar, e a fila de postagens do
  Instagram (só se você agendou algo); as redes só são usadas quando você pede.

**Não é preciso instalar nada antes:** o `instalar.bat` confere e instala o que faltar (Visual C++,
uv, Python 3.11 com as bibliotecas, FFmpeg, Ollama e os modelos de IA); veja os passos em
[COMO_INSTALAR.txt](COMO_INSTALAR.txt). Tudo, menos o Visual C++ e o Ollama (que têm instalador
próprio), fica dentro da pasta do programa.

## Como usar

```
instalar.bat          instala o que faltar e confere o ambiente (rode uma vez; pode repetir)
iniciar.bat           abre o aplicativo (sem janela preta; o instalador também cria um atalho)
```

Opções do instalador (`.\instalar.bat -SemModelos -SemOllama -SemAtalhos -Silencioso`): veja o
[COMO_INSTALAR.txt](COMO_INSTALAR.txt).

Coloque o vídeo em `Downloads/` (ou escolha outro arquivo), marque o que quer gerar e clique em
**Iniciar**. A janela mostra o tempo estimado para o seu computador antes de começar. Na qualidade
padrão (Máxima), um sermão de 65 min leva cerca de 8 h num processador de 12 threads sem placa de
vídeo utilizável (transcrição, IA, 20 Shorts e vídeo longo); as qualidades menores (Alta,
Equilibrado, Rápido) encurtam isso. Deixe rodando de madrugada: o computador não bloqueia sozinho
enquanto processa.

**Atualizações:** ao abrir, o programa compara a sua versão com a da branch `main` no GitHub. Se
houver uma mais nova, uma faixa amarela pisca no alto da janela; um clique baixa o ZIP da `main`,
troca só o código, os scripts e os documentos (projetos, modelos, marca d'água e vocabulário ficam
como estão), roda o instalador se as bibliotecas mudaram e reabre. Numa pasta com `.git` (clone) ele
não troca nada: use `git pull`. A consulta se desliga no rodapé ("Procurar atualizações ao abrir").

## Se algo não anda

- O `processamento.log` (na pasta do projeto, e no painel da janela) diz a cada ~15 s o que cada
  passo demorado está fazendo; se um passo passar do esperado, ele mostra onde está parado.
- **Pausar** vale no próximo ponto seguro da etapa (o log confirma na hora e diz quando); **Cancelar**
  também. O projeto sempre pode ser retomado.
- `uv run spurgeon doctor` confere o ambiente (FFmpeg, Ollama, modelos, espaço em disco).
- A janela abre sem console: se o programa não abrir, o motivo aparece numa caixa e fica em
  `%LOCALAPPDATA%\Spurgeon\Logs\interface.log`; a instalação grava o `instalacao.log` na pasta do
  programa.
- As preferências da janela ficam em `%LOCALAPPDATA%\Spurgeon\config.toml` e as chaves das redes,
  criptografadas, em `integracoes.json` na mesma pasta. Nenhum dos dois entra no git.

## Pastas

| Pasta | Função | Versionada |
|---|---|---|
| `Downloads/` | entrada: vídeos brutos | não |
| `recursos/` | introdução, vinheta, marca d'água, vocabulário, fontes e dicionário pt_BR | só fontes, dicionário, ícone e vocabulário |
| `modelos/` | modelos e ferramentas baixados (Whisper, spaCy, FFmpeg, uv) | não |
| `Projetos/` | saída: uma pasta por sermão (`AAAA-MM-DD_Nome`) | não |

Dentro de cada projeto: `video_longo_otimizado.mp4`, `legendas_video_longo.srt`,
`transcricao.json`, `processamento.log`, `estado.json` e `shorts_gerados/` (um `.mp4` e um `.txt`
com título, descrição e hashtags por Short; os agendados ficam em `agendados/`, os já postados em
`postados/` e os desmarcados em `desmarcados/`). `_work/` guarda os intermediários: os pesados (áudios, partes do vídeo) somem quando
tudo termina bem, e os `.json` da análise ficam para o projeto poder ser reaberto.

## Para quem desenvolve

```
uv sync --extra ui --extra transcricao --extra shorts   prepara o ambiente de desenvolvimento
uv run spurgeon gui                 abre a interface (iniciar.bat faz o mesmo)
uv run pytest                       roda os testes (~30 min; parte deles abre a janela)
uv run ruff check . && uv run ruff format .
uv run spurgeon doctor              verifica o ambiente (pré-verificação)
uv run spurgeon hardware            mostra o hardware detectado e a recomendação
uv run spurgeon baixar-modelos      baixa o Whisper, o filtro de áudio e o modelo do Ollama
uv run spurgeon acordado            mantém o PC acordado e sem bloquear por algumas horas
uv run spurgeon benchmark "Downloads\video.MOV"   mede a velocidade do Whisper nesta máquina
uv run spurgeon processar "Downloads\video.MOV" --limpar-audio --normalizar --shorts --legendas
                              processa sem a janela (mesmo caminho da interface); --qualidade
                              maxima|alta|equilibrado|rapido; sem janela, as revisões ficam desligadas
                              --modo so_shorts|so_vinhetas  --introducao  --vinheta-final
                              --marca logo.png --marca-canto inf_dir --marca-tamanho 15
                              --marca-remover-fundo  --marca-em longo|shorts|ambos
                              --inicio 0:07 --fim 1:04:56   (ou --fim=-2:04, tira do final)
                              --zoom 120 --zoom-x 54 --zoom-y 50  --preset-zoom NOME
                              --preset-tratamento NOME  --fade 0,5  --fade-estilo mesclar|preto
                              --introducao-arquivo A.mp4  --vinheta-arquivo B.mp4
```

Para experimentar a tela sem processar de verdade: `SPURGEON_DEMO=1` antes de abrir.

Os testes não mexem na sua configuração, no vocabulário nem nas chaves das redes; os de atualização
e de postagem usam respostas simuladas, sem internet. **Para uma versão chegar às pessoas pelo aviso de atualização, suba o
`__version__` (em `src/spurgeon/__init__.py`) e rode `uv lock` antes do push.** As convenções do
código ficam no [CLAUDE.md](CLAUDE.md).

## Licenças

Somente fontes com licença livre (OFL) são empacotadas: Montserrat ExtraBold, em
`recursos/fontes/`. Os modelos baixados (Whisper, Qwen, spaCy…) seguem as licenças de cada
fornecedor.

O dicionário ortográfico `recursos/dicionarios/pt_BR/` é o pt_BR do projeto VERO/LibreOffice
(LGPLv3 e MPL; veja o `LEIA-ME_pt_BR.txt` ao lado dele).
