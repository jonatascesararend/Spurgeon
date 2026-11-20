# Controle remoto pelo celular (branch `controle-remoto`)

> **Estado: planejado, nada implementado (2026-11-20).** Projeto **particular** do autor: vive só no branch
> `controle-remoto`, roda no PC dele e só ele usa. Não vai para a `main` nem para quem instala o Studio Spurgeon.
> Este arquivo é a fonte das decisões do branch; o [Projeto.md](Projeto.md) continua valendo para todo o resto.

## 1. Objetivo

Fazer a **curadoria pelo celular** (Android) enquanto o **PC faz o trabalho pesado**. O autor escolhe e ajusta os
Shorts e agenda a postagem; a automação do PC baixa o sermão, processa, renderiza e posta.

O fluxo de ponta a ponta:

1. O celular lista os vídeos longos do canal da igreja e o autor escolhe um.
2. O PC baixa o vídeo pelo YouTube Studio, cria o projeto e processa tudo.
3. Os cortes chegam ao celular, com aviso.
4. O autor ajusta a legenda e as pontas de cada corte, cria Shorts livres e vê a prévia rápida.
5. "Enviar" manda o PC renderizar os Shorts, com aviso quando terminar.
6. O autor agenda no YouTube, e o PC posta o Short já vinculado ao sermão original (vídeo relacionado).

## 2. Decisões do autor

| # | Decisão | Data |
|---|---|---|
| R1 | Projeto particular, num branch; o autor hospeda e só ele usa | 2026-11-20 |
| R2 | Celular **Android**; pode instalar o que precisar (app de terceiros ou nosso) | 2026-11-20 |
| R3 | **Custo zero absoluto.** Os dados não são confidenciais; a restrição é só de custo | 2026-11-20 |
| R4 | **Sem Firebase** (o armazenamento de arquivos exige o plano pago, e a prévia precisa de vídeo) e **sem Telegram** (o autor não usa) | 2026-11-20 |
| R5 | Acesso pelo **Tailscale** (rede privada, atravessa o CGNAT da fibra, sem abrir porta) a um **servidor web no PC**; o app do celular é uma **PWA** instalada pelo Chrome, com HTTPS do `tailscale serve` | 2026-11-20 |
| R6 | **Avisos pelo próprio app (Web Push)** em dois momentos: (a) o processamento terminou e há Shorts para revisar; (b) os Shorts enviados terminaram de renderizar | 2026-11-20 |
| R7 | O **servidor fica sempre ligado** (abre com o Windows, sem console) e pode ser **desligado pelo celular** | 2026-11-20 |
| R8 | **O celular tem prioridade.** Abriu o projeto no celular, o Studio do PC fica só leitura; se o PC tinha alterações não salvas, **o PC salva sozinho** antes de travar, e o celular avisa | 2026-11-20 |
| R9 | No celular: **legenda inteira** editada de uma vez e mandada com "Enviar", **início e fim** do corte e **Shorts livre**. Marca-texto, borracha e saltos ficam para depois | 2026-11-20 |
| R10 | **Só a prévia rápida** no celular (sem a prévia fiel do FFmpeg); o resultado de verdade é a **renderização final** do Short | 2026-11-20 |
| R11 | **O vídeo de um projeto novo vem do YouTube.** Listar os vídeos longos do canal e **baixar pelo YouTube Studio**, por automação de navegador (Python + **Playwright**) numa **sessão já configurada** | 2026-11-20 |
| R12 | **Postar por automação de navegador** para vincular o sermão original ao Short (vídeo relacionado), o que a API do YouTube não faz | 2026-11-20 |
| R13 | O PC fica disponível o dia todo | 2026-11-20 |

## 3. Escolhas técnicas (sugeridas, confirmar)

* **S1. Desligar pelo celular tem dois níveis** (consequência de R7). **"Dormir"**: o servidor recusa tudo, menos
  "acordar", e não processa. **"Encerrar"**: encerra de verdade e avisa que só volta no PC (ou no próximo login). Um
  servidor encerrado não ouve o pedido de religar.
* **S2. Prévia rápida (R10).** O PC grava, uma vez por projeto, um **proxy** do vídeo original: 480p, H.264, AAC, com um
  quadro-chave por segundo (`-g` = fps) e `+faststart`, servido com *range requests*. O celular toca o proxy na linha do
  tempo ORIGINAL: pula os saltos e as pausas do Short (`shorts/pausas.trechos_mantidos`), mostra só a janela 9:16
  (`render/filtros.regiao_do_recorte`, com o zoom do Short) recortando por CSS, e desenha a legenda por cima a partir das
  palavras com tempo. Não mostra o tratamento de cor nem a marca. Como o original vem do YouTube (H.264, SDR), decodificar é
  barato e o proxy existe **antes** do vídeo longo, ou seja, já na revisão dos cortes. Fica em `_work/proxy_celular.mp4` e
  **não** é apagado ao concluir, assim como os `.json`.
* **S3. Postar (R12).** Proposta **híbrida**: a **API** (que já existe e agenda: `publicacao/youtube.py`) envia e agenda o
  Short; depois o **Playwright** abre a página do Short no YouTube Studio e preenche **só o "vídeo relacionado"** com o sermão
  de origem. O navegador faz o mínimo, e o envio (a parte longa e que precisa ser retomável) continua pela API. Se o autor
  preferir postar tudo pelo navegador, troca-se só essa peça.
* **S4. O projeto guarda de onde veio**: id do vídeo no YouTube, título e data de publicação. A data do projeto é a do
  sermão no canal, não a do arquivo baixado (`core/projeto.data_gravacao` leria a data do download). É assim que o Short
  sabe qual sermão vincular (S3).
* **S5. Sessão do navegador (R11, R12).** Um **perfil persistente** do Playwright
  (`launch_persistent_context`) em `%LOCALAPPDATA%\Spurgeon\navegador\`, fora do repositório. O autor faz o login na conta
  da igreja **uma vez, à mão**, por um comando (`spurgeon remoto navegador`); o Google costuma barrar login feito por
  automação. Se a sessão cair, o celular avisa "entre de novo no PC".
* **S6. Listar os vídeos (R11)** pela API, com a conta que a aba Integrações já liga (escopo `youtube`, que já permite
  leitura), filtrando os longos. O navegador só baixa. Se a API não servir para algo, a lista também pode sair do Studio.
* **S7. Uma fila de processamento só.** O servidor é quem dispara o trabalhador quando o pedido vem do celular, e o Studio do
  PC respeita uma trava global (arquivo em `%LOCALAPPDATA%\Spurgeon\`) para não haver dois trabalhadores no mesmo projeto.
* **S8. Segurança.** Só responde pelo endereço do Tailscale (nunca em `0.0.0.0`). Confere a identidade que o
  `tailscale serve` passa (cabeçalho `Tailscale-User-Login`) e um token do aparelho, guardado no `Cofre`. As chaves do Web
  Push (VAPID) também ficam no `Cofre`. Nada no `config.toml`, nada em log.
* **S9. App do celular sem Node**: HTML, CSS e JavaScript em módulos, servidos pelo próprio PC (`remoto/web/`). Não há etapa
  de build nem conta em loja.
* **S10. Revisão dos cortes no celular.** Com "Pausar para aprovar os cortes" ligado, o pipeline espera a revisão
  (`aguardando_revisao`). O celular também precisa aprovar os cortes (marcar e desmarcar), senão o processamento fica parado
  esperando o PC. **Confirmar com o autor.**

## 4. Arquitetura

```
 Celular Android                              PC (Windows)
┌─────────────────────┐                ┌───────────────────────────────────────────┐
│ PWA (Chrome)        │   Tailscale    │ spurgeon remoto (sempre ligado, pythonw)  │
│  • canal: escolher  │◄──────────────►│  ├─ remoto/servidor.py   (API + web/)     │
│    o sermão         │  HTTPS do      │  ├─ remoto/sessao.py     (trava, R8)      │
│  • andamento        │  tailscale     │  ├─ remoto/fila.py       (um por vez)     │
│  • aprovar cortes   │  serve         │  ├─ remoto/avisos.py     (Web Push)       │
│  • legenda, pontas, │                │  ├─ render/proxy.py      (prévia rápida)  │
│    Shorts livre     │                │  └─ canal/               (Playwright+API) │
│  • prévia rápida    │◄── Web Push ───┤                                           │
│  • enviar, agendar  │                │ Studio (Tk): lê a trava e a fila          │
└─────────────────────┘                └───────────────────────────────────────────┘
```

O que é **reaproveitado sem mudar**: `core/executor` + `core/trabalhador` (eventos JSON, pausar/retomar/cancelar),
`core/projeto` (listar e abrir), `shorts/edicao.py`, `shorts/livre.py`, `shorts/busca.py`, `shorts/marcas.py`,
`shorts/pausas.py`, `shorts/titulos.py`, `shorts/llm.py` (o Ollama continua no PC), `core/etapas.gravar_campos_do_corte` /
`gravar_postado`, `render/shorts.sincronizar_arquivos`, `publicacao/` e o `Cofre`.

O que **muda** no código que já existe (o mínimo, para o branch acompanhar a `main` sem conflito):

* `__main__.py`: o comando `spurgeon remoto` (`iniciar`, `navegador`, `instalar-na-inicializacao`).
* `ui/app.py`: ler a trava do celular (S7, R8). Quando o celular toma o projeto: salvar sozinho, travar o Studio com um
  aviso e reler o editor quando a trava sair.
* `core/config.py`: a seção `remoto` (porta, ligado, dormindo). Mudou o formato? Suba `VERSAO_CONFIG`.
* `core/projeto.py`: a origem do YouTube no projeto (S4).
* `pyproject.toml`: um extra `remoto` (servidor web, Web Push, Playwright), escolhido na Fase 1.

## 5. Estrutura do branch

```
Remoto.md                          este documento
src/spurgeon/
  remoto/
    __init__.py
    servidor.py                    app web: rotas + arquivos de web/
    seguranca.py                   S8: só Tailscale, token do aparelho
    sessao.py                      R8: trava do celular com batimento; o PC salva e trava
    fila.py                        S7: um processamento por vez, servidor e Studio
    avisos.py                      R6: Web Push (VAPID no Cofre)
    rotas/
      __init__.py
      projetos.py                  lista, andamento ao vivo, iniciar/pausar/cancelar, aprovar cortes
      cortes.py                    legenda, início/fim, enviar (salva + renderiza)
      livre.py                     busca, categorias, criar Short livre
      previa.py                    proxy (range requests) e dados da prévia rápida
      postagem.py                  agendar, já postado, refazer título com a IA
      canal.py                     vídeos do canal, baixar e criar projeto
      sistema.py                   dormir, acordar, encerrar, estado do PC
    web/                           o app do celular (PWA, sem build)
  canal/
    __init__.py
    navegador.py                   S5: perfil persistente do Playwright
    listar.py                      S6: vídeos longos do canal (API)
    baixar.py                      R11: baixar pelo YouTube Studio
    vincular.py                    S3: vídeo relacionado no Short
  render/proxy.py                  S2: proxy 480p para a prévia rápida
tests/                             test_remoto_*.py e test_canal_*.py (sem internet, sem navegador)
```

Os módulos já existem no branch só com a descrição; o código entra fase por fase.

## 6. Fases

Cada fase vale sozinha e é testada antes da próxima.

**Fase R0: provas de conceito** (protótipos descartáveis na pasta de rascunho, fora do projeto). Estes são os riscos que
podem mudar o desenho:

1. Tailscale no PC e no celular; `tailscale serve` com uma página de teste; abrir no Android **fora do Wi-Fi de casa**,
   instalar como app e receber **uma notificação push** com o app fechado.
2. Playwright com perfil persistente: login à mão uma vez e, depois, sem login, **listar e baixar um vídeo** do YouTube
   Studio. Conferir a **qualidade e o formato** do arquivo (resolução, codec, áudio).
3. Playwright: preencher o **vídeo relacionado** de um Short já enviado e **agendado (privado)**.
4. Medir o tempo do proxy 480p de um sermão inteiro nesta máquina, e o tamanho do arquivo.
5. Conferir que um vídeo baixado do YouTube passa pelo pipeline completo (SDR, sem HDR).

**Fase R1: só ver.** Servidor, segurança, `spurgeon remoto` na inicialização do Windows, PWA com a lista de projetos e o
andamento ao vivo, Shorts já renderizados tocando no celular, Web Push (R6), dormir e encerrar (S1).

**Fase R2: editar.** Trava e "o PC salva sozinho" (R8), aprovar cortes (S10), legenda inteira com "Enviar", início e
fim por toque na palavra (±0,1 s), proxy e prévia rápida (S2), renderizar os Shorts enviados.

**Fase R3: Shorts livre** pelo celular: busca, categorias, criar o Short.

**Fase R4: postar.** Agendar no YouTube pela API, vídeo relacionado pelo navegador (S3), já postado, refazer o título com
a IA.

**Fase R5: começar pelo celular.** Listar o canal, baixar pelo Studio, criar o projeto (S4) e processar com as opções
salvas.

## 7. Armadilhas conhecidas

* **O atualizador do Studio olha a `main`.** A pasta de desenvolvimento é um clone com `.git`, então não se atualiza
  sozinha, e é por isso que o branch pode viver nela. **Nunca** instale o branch por ZIP: o atualizador o trocaria pela
  `main`.
* **O repositório é público**: o branch publicado também é. Nenhum segredo, id de canal, e-mail ou token nos arquivos.
* **Automação do YouTube Studio quebra quando a página muda.** Os seletores ficam num lugar só (`canal/`). Cada falha
  grava uma foto da tela no log e avisa o celular em vez de travar.
* **Sem console** (CLAUDE.md): o servidor roda no `pythonw`, todo `subprocess` usa `CREATE_NO_WINDOW` e a saída vai para
  `%LOCALAPPDATA%\Spurgeon\Logs\remoto.log`.
* **Testes nunca tocam a internet nem o navegador de verdade.** HTTP pelo `TransporteFalso`; o Playwright é trocado por um
  falso; nenhum teste escreve fora de `tmp_path`.
* **Duas pontas escrevendo o mesmo `cortes_shorts.json`.** Tudo passa pelas mesmas funções do Studio
  (`gravar_campos_do_corte`, `salvar`), e só quem tem a trava escreve.
* **O PC dormindo mata o acesso.** O servidor pede ao Windows para não suspender enquanto há trabalho
  (`core/energia.impedir_suspensao`). O plano de energia do PC deve estar em "nunca suspender".

## 8. Em aberto

* S1 a S10 ainda não foram confirmados pelo autor, principalmente S3 (postar pela API e só vincular pelo navegador) e S10
  (aprovar os cortes pelo celular).
* Qualidade do download do YouTube Studio (R0.2): se for baixa, o vídeo longo e os Shorts perdem nitidez em relação ao
  arquivo da câmera.
