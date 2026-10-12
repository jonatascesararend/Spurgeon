# Studio Spurgeon

Aplicativo desktop (Windows) que transforma sermões e aulas longos em um vídeo limpo,
com legendas, e em Shorts verticais prontos para revisar e publicar. Tudo roda
localmente (offline), em pt-BR.

O plano completo, as decisões e o roadmap estão em [Projeto.md](Projeto.md).

## Estado atual

- Fase 0 (fundação) e Fase 1 (interface) concluídas.
- A interface já cria o projeto, verifica o ambiente, pausa, cancela, retoma e mostra o
  progresso. **As etapas de processamento (áudio, transcrição, Shorts, render) ainda não
  existem**; chegam nas Fases 2 a 5. Para experimentar a tela: `SPURGEON_DEMO=1` antes de abrir.

## Requisitos

- Windows 10/11
- Python 3.11 e [uv](https://docs.astral.sh/uv/)
- [FFmpeg](https://ffmpeg.org/) (com `libass`, `loudnorm` e `libx264`)
- [Ollama](https://ollama.com/) (a partir da Fase 4)

Instalação rápida com o `winget`:

```
winget install Python.Python.3.11 astral-sh.uv Gyan.FFmpeg Ollama.Ollama
```

## Como usar

```
instalar.bat          cria o ambiente e confere as dependências
iniciar.bat           abre o aplicativo
```

Linha de comando (para desenvolvimento):

```
uv run spurgeon doctor        verifica o ambiente (pré-verificação)
uv run spurgeon hardware      mostra o hardware detectado e a recomendação
uv run pytest                 roda os testes
uv run ruff check .           análise estática
```

## Pastas

| Pasta | Função | Versionada |
|---|---|---|
| `Downloads/` | entrada: vídeos brutos | não |
| `recursos/` | introdução, vinheta, vocabulário e fontes | só fontes e vocabulário |
| `modelos/` | modelos baixados (Whisper, spaCy) | não |
| `Projetos/` | saída: uma pasta por sermão | não |

## Licenças

Somente fontes com licença livre (OFL) são empacotadas. Os modelos baixados
(Whisper, Qwen, etc.) seguem as licenças de cada fornecedor.
