# Studio Spurgeon

Aplicativo desktop (Windows) que transforma sermões e aulas longos em um vídeo limpo, com
legendas, e em Shorts verticais prontos para publicar. Tudo roda localmente (offline), em pt-BR.

O plano completo, as decisões, os resultados medidos e o roadmap estão em [Projeto.md](Projeto.md).
Para instalar sem ler nada técnico, veja o [COMO_INSTALAR.txt](COMO_INSTALAR.txt).

## O que faz

1. **Áudio:** extrai, limpa o ruído (DeepFilterNet) e normaliza o volume (−16 LUFS).
2. **Transcrição:** faster-whisper com vocabulário teológico, palavra por palavra, e uma tela para
   revisar o texto.
3. **Vídeo longo:** corta as pausas maiores que o limite escolhido, converte o HDR do iPhone para
   SDR, junta introdução e vinheta e gera `video_longo_otimizado.mp4` e o `.srt`.
4. **Shorts (até 180 s, com preferência por ~60 s; 9:16, 1080×1920):** uma IA local (Ollama)
   escolhe os melhores trechos, dá uma nota de qualidade a cada um e cria título, descrição e
   hashtags. Uma janela deixa aprovar, desmarcar, corrigir os textos e ajustar o início e o fim de
   cada corte. Os Shorts saem com legenda dinâmica (palavra falada em amarelo) e volume de −14 LUFS.
   A prévia do Short mostra os elementos do aplicativo do YouTube (botões, canal, título) por cima,
   para conferir que a legenda e o pregador não ficam atrás deles.
5. **Studio Shorts:** uma aba lista os Shorts sugeridos (trecho da legenda, nota calculada e título)
   para escolher o que renderizar, com o início e o fim ajustáveis por palavra ou frase e a legenda
   editável (a prévia do corte sempre vem com a legenda). Depois é só renderizar o que falta, sem
   refazer o resto. Cada corte diz se foi "gerado automaticamente" ou "gerado manualmente" e leva a
   **categoria** que a IA achou (17, cada uma com uma cor). A tabela de frases tem três ferramentas
   do mouse: a **seta**, o **marca-texto** (pinta o que entra no Short) e a **borracha** (tira uma
   palavra ou frase do meio, para cortar um erro de pronúncia e continuar logo depois). Cada Short
   tem o seu corte de pausas (a partir de 0,1 s), e há **desfazer/refazer** (Ctrl+Z, Ctrl+Y),
   **descartar alterações**, refazer título, descrição e hashtags com a IA e o **dicionário
   personalizado** (os termos que você guardou como certos). A aba **Shorts livre** deixa
   escolher a dedo um trecho do texto do sermão (com busca por palavras parecidas ou por tema), ou
   montar um Short de pedaços separados ("frase 1 + frase 3"): a IA dá a nota e escreve título e
   hashtags, e o corte entra na lista como "gerado manualmente". O texto mostra, com cores, o que a
   IA detectou em cada trecho e onde já existem Shorts.
6. **Marca d'água:** logotipo (PNG transparente, ou outra imagem com o fundo removido) no canto e no
   tamanho escolhidos, no vídeo longo e/ou nos Shorts.
7. **Português:** verificador offline (dicionário pt_BR) na revisão da transcrição e nas legendas
   dos Shorts: palavras erradas, repetidas, maiúsculas e pontuação, com correção em um clique.
8. **Começo e fim do vídeo:** corta o que sobra antes e depois (`0:07`, ou `-2:04` para tirar do final), com
   o quadro exato de cada corte e uma prévia de 15 s de cada ponta.
9. **Zoom e tratamento de imagem:** aproxima a câmera do pregador (área cortada sombreada no quadro)
   e ajusta brilho, contraste, cor, brancos e sombras com o antes e o depois lado a lado, sugestão
   automática e presets que guardam o estilo dos vídeos.
10. **Introdução e vinheta:** arquivo à escolha e fade de imagem e som entre elas e o vídeo, que
    pode **mesclar** (as duas pontas se dissolvem uma na outra, sem trecho escuro nem silêncio; o
    vídeo fica a duração do fade mais curto) ou **passar pelo preto**. Em "Só vinhetas" o fade
    sempre passa pelo preto, porque o vídeo pronto não é recodificado.
11. **Conferência final:** confere os arquivos e registra o resumo no log.

Três modos: **Tudo**, **Só Shorts** (aproveita o que o projeto já tem) e **Só vinhetas** (junta a
introdução e a vinheta a um vídeo longo pronto, em minutos). **Abrir projeto…** reabre qualquer
projeto e continua de onde ele está, e o programa já abre no último projeto que você usou.

Na primeira abertura o programa oferece um **passo a passo** (o botão **Como usar**, no canto de
baixo, repete quando quiser); todo botão tem uma dica ao passar o mouse e cada etapa tem um "?".

Se o processamento cair, cancelar ou faltar luz, ele **retoma de onde parou**.

## A janela

A coluna da esquerda tem três páginas, escolhidas nos botões do topo: **1 · Vídeo e geração**
(escolher o vídeo e o que gerar; só aparece o que vale para o modo escolhido), **2 · Corte e
imagem** (começo e fim, zoom, tratamento de imagem e marca d'água) e **3 · Revisão e opções**
(pontos de revisão, processamento e opções avançadas). À direita, as abas **Processamento** (botões,
progresso e um log detalhado) e **Studio Shorts**.

## Requisitos

- Windows 10/11
- Python 3.11 e [uv](https://docs.astral.sh/uv/)
- [FFmpeg](https://ffmpeg.org/) (com `libass`, `libzimg`, `loudnorm` e `libx264`)
- [Ollama](https://ollama.com/) para os Shorts (o aplicativo baixa o modelo `qwen3:8b`, ~5 GB,
  na primeira vez)
- Espaço em disco: para um sermão de 65 min, ~6 GB livres além do vídeo original, mais ~20 GB
  para o ambiente e os modelos (Whisper ~1,6 GB, Ollama ~5 GB)
- Internet na instalação e na primeira vez de cada recurso (os modelos são baixados sozinhos)

Instalação rápida com o `winget` (passo a passo em [COMO_INSTALAR.txt](COMO_INSTALAR.txt)):

```
winget install Python.Python.3.11 astral-sh.uv Gyan.FFmpeg Ollama.Ollama
```

## Como usar

```
instalar.bat          cria o ambiente e confere as dependências (rode uma vez)
iniciar.bat           abre o aplicativo
```

Coloque o vídeo em `Downloads/` (ou escolha outro arquivo), marque o que quer gerar e clique em
**Iniciar**. A janela mostra o tempo estimado antes de começar. Na qualidade padrão (Máxima),
um sermão de 65 min leva cerca de 8 h neste PC (transcrição, IA, 20 Shorts e vídeo longo; veja o Projeto.md): deixe rodando
de madrugada; o PC não bloqueia sozinho enquanto processa.

Linha de comando (para desenvolvimento):

```
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
uv run spurgeon doctor        verifica o ambiente (pré-verificação)
uv run spurgeon hardware      mostra o hardware detectado e a recomendação
uv run spurgeon acordado      mantém o PC acordado e sem bloquear por algumas horas
uv run pytest                 roda os testes
uv run ruff check .           análise estática
```

Para experimentar a tela sem processar de verdade: `SPURGEON_DEMO=1` antes de abrir.

## Se algo não anda

- O `processamento.log` (na pasta do projeto, e no painel da janela) diz a cada ~15 s o que cada
  passo demorado está fazendo; se um passo passar do esperado, ele mostra onde está parado.
- **Pausar** vale no próximo ponto seguro da etapa (o log confirma na hora e diz quando); **Cancelar**
  também. O projeto sempre pode ser retomado.
- `uv run spurgeon doctor` confere o ambiente (FFmpeg, Ollama, modelos, espaço em disco).
- As preferências da janela ficam em `%LOCALAPPDATA%\Spurgeon\config.toml`.
- Os testes (`uv run pytest`, ~30 min, parte deles abre a janela) não mexem nesse arquivo.

## Pastas

| Pasta | Função | Versionada |
|---|---|---|
| `Downloads/` | entrada: vídeos brutos | não |
| `recursos/` | introdução, vinheta, marca d'água, vocabulário, fontes e dicionário pt_BR | só fontes, vocabulário e dicionário |
| `modelos/` | modelos baixados (Whisper, spaCy) | não |
| `Projetos/` | saída: uma pasta por sermão (`AAAA-MM-DD_Nome`) | não |

Dentro de cada projeto: `video_longo_otimizado.mp4`, `legendas_video_longo.srt`,
`transcricao.json`, `processamento.log`, `estado.json` e `shorts_gerados/` (um `.mp4` e um `.txt`
com título, descrição e hashtags por Short). `_work/` guarda os intermediários: os pesados
(áudios, partes do vídeo) somem quando tudo termina bem, e os `.json` da análise ficam para o projeto
poder ser reaberto.

## Licenças

Somente fontes com licença livre (OFL) são empacotadas: Montserrat ExtraBold, em
`recursos/fontes/`. Os modelos baixados (Whisper, Qwen, spaCy…) seguem as licenças de cada
fornecedor.

O dicionário ortográfico `recursos/dicionarios/pt_BR/` é o pt_BR do projeto VERO/LibreOffice
(LGPLv3 e MPL; veja o `LEIA-ME_pt_BR.txt` ao lado dele).
