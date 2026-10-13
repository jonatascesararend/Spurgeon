# Studio Spurgeon

Aplicativo desktop (Windows) que transforma sermões e aulas longos em um vídeo limpo, com
legendas, e em Shorts verticais prontos para publicar. Tudo roda localmente (offline), em pt-BR.

O plano completo, as decisões, os resultados medidos e o roadmap estão em [Projeto.md](Projeto.md).

## O que faz

1. **Áudio:** extrai, limpa o ruído (DeepFilterNet) e normaliza o volume (−16 LUFS).
2. **Transcrição:** faster-whisper com vocabulário teológico, palavra por palavra, e uma tela para
   revisar o texto.
3. **Vídeo longo:** corta as pausas maiores que o limite escolhido, converte o HDR do iPhone para
   SDR, junta introdução e vinheta e gera `video_longo_otimizado.mp4` e o `.srt`.
4. **Shorts (até 60 s, 9:16, 1080×1920):** uma IA local (Ollama) escolhe os melhores trechos,
   dá uma nota de qualidade a cada um e cria título, descrição e hashtags. Uma janela deixa
   aprovar, desmarcar, corrigir os textos e ajustar o início e o fim de cada corte. Os Shorts saem
   com legenda dinâmica (palavra falada em amarelo) e volume de −14 LUFS.
5. **Conferência final:** confere os arquivos e registra o resumo no log.

Se o processamento cair, cancelar ou faltar luz, ele **retoma de onde parou**.

## Requisitos

- Windows 10/11
- Python 3.11 e [uv](https://docs.astral.sh/uv/)
- [FFmpeg](https://ffmpeg.org/) (com `libass`, `libzimg`, `loudnorm` e `libx264`)
- [Ollama](https://ollama.com/) para os Shorts (o aplicativo baixa o modelo `qwen3:8b`, ~5 GB,
  na primeira vez)
- Espaço em disco: para um sermão de 65 min, ~6 GB livres além do vídeo original

Instalação rápida com o `winget`:

```
winget install Python.Python.3.11 astral-sh.uv Gyan.FFmpeg Ollama.Ollama
```

## Como usar

```
instalar.bat          cria o ambiente e confere as dependências
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
uv run spurgeon doctor        verifica o ambiente (pré-verificação)
uv run spurgeon hardware      mostra o hardware detectado e a recomendação
uv run spurgeon acordado      mantém o PC acordado e sem bloquear por algumas horas
uv run pytest                 roda os testes
uv run ruff check .           análise estática
```

Para experimentar a tela sem processar de verdade: `SPURGEON_DEMO=1` antes de abrir.

## Pastas

| Pasta | Função | Versionada |
|---|---|---|
| `Downloads/` | entrada: vídeos brutos | não |
| `recursos/` | introdução, vinheta, vocabulário e fontes | só fontes e vocabulário |
| `modelos/` | modelos baixados (Whisper, spaCy) | não |
| `Projetos/` | saída: uma pasta por sermão (`AAAA-MM-DD_Nome`) | não |

Dentro de cada projeto: `video_longo_otimizado.mp4`, `legendas_video_longo.srt`,
`transcricao.json`, `processamento.log`, `estado.json` e `shorts_gerados/` (um `.mp4` e um `.txt`
com título, descrição e hashtags por Short). `_work/` guarda os intermediários e some quando tudo
termina bem.

## Licenças

Somente fontes com licença livre (OFL) são empacotadas: Montserrat ExtraBold, em
`recursos/fontes/`. Os modelos baixados (Whisper, Qwen, spaCy…) seguem as licenças de cada
fornecedor.
