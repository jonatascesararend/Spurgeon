# Controle remoto pelo celular

> **Estado: feito (fases R0 a R5) e na `main` desde a v2.0.0 (2026-11-20).** Nasceu como projeto particular do autor
> no branch `controle-remoto` e foi trazido para a `main` como recurso **opcional**: o servidor só existe com o extra
> `remoto` instalado (`uv sync --extra remoto`) e configurado (`spurgeon remoto configurar`); para quem não usa, nada
> muda. Este arquivo é a fonte das decisões do controle remoto; o [Projeto.md](Projeto.md) continua valendo para o resto.

## 1. Objetivo

Fazer a **curadoria pelo celular** (Android) enquanto o **PC faz o trabalho pesado**. O autor escolhe e ajusta os
Shorts e agenda a postagem; a automação do PC baixa o sermão, processa, renderiza e posta.

O fluxo de ponta a ponta:

1. O celular lista os vídeos longos do canal da igreja e o autor escolhe um.
2. O PC baixa o vídeo pelo YouTube Studio, cria o projeto e processa tudo.
3. Os cortes chegam ao celular, com aviso.
4. O autor ajusta a legenda e as pontas de cada corte, cria Shorts livres e vê a prévia rápida.
5. "Enviar" manda o PC renderizar os Shorts, com aviso quando terminar.
6. O autor agenda no YouTube, e o PC posta o Short pelo YouTube Studio (sem a API) já vinculado ao sermão original (vídeo relacionado).

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
| R12 | **Postar no YouTube só por automação de navegador, SEM a API do YouTube** (feito na R4): envio, detalhes, agendamento e o vídeo relacionado (o sermão original), que a API não faz | 2026-11-20 |
| R13 | O PC fica disponível o dia todo | 2026-11-20 |
| R14 | Tailscale instalado no PC e no celular, mesma conta, MagicDNS ligado | 2026-11-20 |
| R15 | O vídeo de um projeto novo vem do **"Baixar" do YouTube Studio (720p)**, não do yt-dlp a 1080p | 2026-11-20 |
| R16 | Para conferir a legenda basta **ouvir**: opção de montar **só o áudio** do Short, com a legenda acompanhando, sem renderizar o vídeo | 2026-11-20 |
| R17 | A **aprovação dos cortes** também é feita pelo celular (S10) | 2026-11-20 |
| R18 | Corrigir a legenda **na própria audição** (a legenda que acompanha o áudio), não numa caixa à parte | 2026-11-20 |
| R19 | **Borracha** no início e fim (cortar palavras no meio do Short), e as mudanças de início, fim e borracha **só vão ao PC ao salvar** (botão, troca de aba ou de ferramenta): o áudio não é refeito a cada toque | 2026-11-20 |

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
* **S3. Postar (R12).** Uma `Plataforma` nova, **"YouTube pelo navegador"** (`canal/postar.py`), com a mesma interface
  das outras (`problemas`, `publicar`, `consultar`). Assim a agenda, o `postagens.json`, as pastas `agendados/`/`postados/`
  e a janela de postagem continuam iguais, e só quem fala com o YouTube muda. No upload do YouTube Studio ela preenche
  título, descrição, tags, "não é para crianças", **vídeo relacionado** (o sermão de origem, S4) e "Programar"
  (data/hora) ou "Publicar". O id do Short sai da página no fim e vai para o `postagens.json`. A `publicacao/youtube.py` (API)
  continua no código e não é usada pelo controle remoto. Riscos: o envio pelo navegador **não retoma** se cair no meio (recomeça), e
  cada mudança da página do Studio pode quebrar um passo.
* **S4. O projeto guarda de onde veio**: id do vídeo no YouTube, título e data de publicação. A data do projeto é a do
  sermão no canal, não a do arquivo baixado (`core/projeto.data_gravacao` leria a data do download). É assim que o Short
  sabe qual sermão vincular (S3).
* **S5. Sessão do navegador (R11, R12).** **Não dá para usar o Chrome do dia a dia:** desde o Chrome 136, a depuração
  remota é ignorada no perfil padrão, e os cookies dele são cifrados para o próprio Chrome (copiar o perfil não leva a
  sessão). Por isso o Chrome instalado é aberto com um **perfil só da automação**, em `%LOCALAPPDATA%\Spurgeon\navegador\`
  (fora do repositório), onde o autor faz o login na conta da igreja **uma vez, à mão**. Nesse login é um Chrome comum,
  sem automação, porque o Google costuma barrar login de navegador automatizado. Depois, a automação abre esse perfil
  (`launch_persistent_context` com `channel="chrome"`, ou Chrome + `--remote-debugging-port` só em `127.0.0.1` e
  `connect_over_cdp`). Esse perfil não pode estar aberto à mão enquanto a automação roda. Se a sessão cair, o celular avisa
  "entre de novo no PC".
  **Provado em 2026-11-20 (R0.2a):** `launch_persistent_context(perfil, channel="chrome")` com o Chrome 157 abre o Studio
  já logado e lê a lista "Conteúdo → Vídeos" (`ytcp-video-row`, id em `a[href*="/video/"]`, `#video-title`). **Uma conta
  Google pode ter vários canais**, e o Studio abriu num canal que não era o da igreja. Por isso a automação tem que
  **conferir o canal** antes de qualquer ação (id do canal guardado na configuração, comparado com a URL
  `studio.youtube.com/channel/<id>`) e, se for outro, trocar ou parar com aviso. Nunca agir no canal errado.
* **S6. Listar os vídeos (R11)** pela página "Conteúdo → Vídeos" do YouTube Studio, no mesmo perfil, para não depender da
  API em nada do canal (R12). Os sermões são **vídeos enviados** (não transmissões): a aba é "Vídeos".
* **S7. Uma fila de processamento só.** O servidor é quem dispara o trabalhador quando o pedido vem do celular, e o Studio do
  PC respeita uma trava global (arquivo em `%LOCALAPPDATA%\Spurgeon\`) para não haver dois trabalhadores no mesmo projeto.
* **S8. Segurança.** Só responde pelo endereço do Tailscale (nunca em `0.0.0.0`). Confere a identidade que o
  `tailscale serve` passa (cabeçalho `Tailscale-User-Login`). Na R1 isso bastou: o servidor só escuta em `127.0.0.1`, e o
  token do aparelho ficou de fora (um programa hostil rodando no próprio PC passaria pelos dois). A chave do Web Push
  (VAPID) e as inscrições ficam no `Cofre`. Nada no `config.toml`, nada em log.
* **S9. App do celular sem Node**: HTML, CSS e JavaScript em módulos, servidos pelo próprio PC (`remoto/web/`). Não há etapa
  de build nem conta em loja.
* **S10. Revisão dos cortes no celular.** Com "Pausar para aprovar os cortes" ligado, o pipeline espera a revisão
  (`aguardando_revisao`). O celular também precisa aprovar os cortes (marcar e desmarcar), senão o processamento fica parado
  esperando o PC. **Confirmado pelo autor em 2026-11-20 (R17)**; feito na Fase R2.

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

O que **muda** no código que já existe (o mínimo: o controle remoto é opcional):

* `__main__.py`: o comando `spurgeon remoto` (`iniciar`, `navegador`, `instalar-na-inicializacao`).
* `ui/app.py`: ler a trava do celular (S7, R8). Quando o celular toma o projeto: salvar sozinho, travar o Studio com um
  aviso e reler o editor quando a trava sair.
* `core/config.py`: a seção `remoto` (porta, ligado, dormindo). Mudou o formato? Suba `VERSAO_CONFIG`.
* `core/projeto.py`: a origem do YouTube no projeto (S4).
* `pyproject.toml`: um extra `remoto` (servidor web, Web Push, Playwright), escolhido na Fase 1.

## 5. Estrutura

```
Remoto.md                          este documento
src/spurgeon/
  remoto/
    __init__.py
    cli.py                         [R1] `spurgeon remoto iniciar|configurar|inicializacao|aviso-teste`
    config.py                      [R1] `remoto.json` (porta, contas, dormindo, avisos), fora do config.toml
    contexto.py                    [R1] o que as rotas compartilham (config, pasta de projetos, avisos)
    servidor.py                    [R1] Starlette + uvicorn, porta exclusiva, `Porteiro` (conta e dormindo)
    seguranca.py                   [R1] S8: só a conta do Tailscale autorizada (`Tailscale-User-Login`)
    tailscale.py                   [R1] conta, endereço, HTTPS e `tailscale serve` pela linha de comando
    inicializacao.py               [R1] atalho na pasta "Inicializar" do Windows (pythonw, sem console)
    dados.py                       [R1] leitura dos projetos, andamento e Shorts (só leitura)
    avisos.py                      [R1] R6: Web Push (VAPID e inscrições no Cofre)
    vigia.py                       [R1] R6: olha os `estado.json` e avisa o que mudou
    audicao.py                     [R1] R16: só o áudio do Short, com a legenda no tempo dele
    sessao.py                      [R2] R8: trava do celular, recibo do PC e a janela aberta
    fila.py                        [R2] S7: um processamento por vez (psutil), roda o trabalhador
    edicao.py                      [R2] legenda, início e fim, marcar (as regras do Studio)
    postagem.py                    [R4] postar no YouTube: uma por vez, registro, agendado/postado
    novo.py                        [R5] projeto novo de um vídeo do canal: baixa, cria, confere, processa
    livre.py                       [R3] Shorts livre: texto, busca, trecho, criação com a IA em segundo plano
    rotas/
      __init__.py
      projetos.py                  [R1/R2] lista, detalhe, andamento, Shorts, vídeo (Range), aprovar, renderizar
      sistema.py                   [R1] estado, dormir, acordar, encerrar e os avisos
      cortes.py                    [R2] trava (tomar/soltar), legenda, pontas, marcar
      livre.py                     [R3] texto vivo, busca, trecho (ouvir) e criar o Short livre
      previa.py                    [R1] legenda e áudio do Short (ouvir, R16); depois o proxy da prévia rápida
      postagem.py                  [R4] postar (programar ou agora) e o andamento da postagem
      canal.py                     [R5] vídeos do canal, baixar e criar o projeto, a tarefa
    web/                           [R1] o app do celular (index.html, app.js, estilo.css, sw.js, manifest)
  canal/
    __init__.py
    navegador.py                   [R4] S5: o Chrome com o perfil da automação; confere o canal
    listar.py                      [R5] S6: os vídeos da aba Vídeos do Studio (1ª página)
    baixar.py                      [R5] R11: baixar pelo "Opções → Baixar" do Studio (720p)
    postar.py                      [R4] S3: postar pelo YouTube Studio (sem API), vídeo relacionado
  render/proxy.py                  S2: proxy 480p para a prévia rápida
tests/test_remoto.py               [R1] servidor, segurança, dados, avisos e vigia (sem internet, sem Tailscale)
tests/test_remoto_audicao.py       [R1] ouvir com a legenda (um teste roda o FFmpeg de verdade)
tests/test_remoto_edicao.py        [R2] trava, edição, fila, aprovar e renderizar
tests/test_janela_remoto.py        [R2] a janela do PC: salva e trava, revisão aprovada, um por vez (-m gui)
tests/test_remoto_livre.py         [R3] Shorts livre (a IA trocada por uma função de mentira)
tests/test_remoto_postagem.py      [R4] postar pelo celular (o navegador trocado por uma função de mentira)
tests/test_canal_postar.py         [R4] descrição, data do Studio e id pelo link
tests/test_remoto_novo.py          [R5] começar pelo celular (o Studio trocado por funções de mentira)
```

`[R1]` a `[R5]` = já tem código (fase em que entrou). Os outros módulos só têm a descrição; o código entra fase por fase. Dependências do branch: o
extra `remoto` (`starlette`, `uvicorn`, `http-ece`, `cryptography`) e o `httpx` no grupo `dev` (cliente de teste). Ambiente:
`uv sync --extra ui --extra transcricao --extra shorts --extra remoto`.

## 6. Fases

Cada fase vale sozinha e é testada antes da próxima.

**Fase R0: provas de conceito** (protótipos descartáveis na pasta de rascunho, fora do projeto). Estes são os riscos que
podem mudar o desenho:

1. Tailscale no PC e no celular (feito, R14). Falta ligar os **certificados HTTPS** no painel do Tailscale (DNS → HTTPS
   Certificates; hoje `CertDomains` vem vazio). Depois: `tailscale serve` com uma página de teste, abrir no Android **fora do
   Wi-Fi de casa**, instalar como app e receber **uma notificação push** com o app fechado.
2. Perfil da automação (S5): login à mão uma vez e, depois, sem login, **listar e baixar um vídeo** do YouTube Studio.
   Conferir a **qualidade e o formato** do arquivo (resolução, codec, áudio).
3. **Postar um Short de teste pelo navegador** (S3): enviar como **privado e programado**, com título, descrição e o
   **vídeo relacionado**, e ler o id no fim. Apagar o Short de teste depois.
4. Medir o tempo do proxy 480p de um sermão inteiro nesta máquina, e o tamanho do arquivo.
5. Conferir que um vídeo baixado do YouTube passa pelo pipeline completo (SDR, sem HDR).

**Resultados da R0 (2026-11-20):**

* **R0.1:** `tailscale serve --bg --https=443 http://127.0.0.1:<porta>` publica em
  `https://<nome-do-pc>.<rede>.ts.net/`, só dentro da rede privada. Os certificados HTTPS foram ligados pelo autor.
  **Funcionou no Android fora do Wi-Fi:** a página abriu, o service worker registrou, o celular se inscreveu no push (FCM
  do Chrome, chaves VAPID geradas no PC, `pywebpush`) e recebeu os avisos (resposta 201). O `tailscale serve` passa o
  cabeçalho `Tailscale-User-Login` com a conta do autor: a segurança (S8) pode confiar nele, junto com o servidor ligado
  só em `127.0.0.1`.
* **R0.2, canal:** o perfil da automação entra logado. O autor é **editor** do canal da igreja, que
  não é o canal padrão da conta: foi preciso trocar de conta no Studio uma vez, e o Studio lembra a escolha. O nome do canal
  fica em `#entity-name`; a automação confere esse nome antes de agir (S5). O nome fica só no PC (`remoto.json`, campo
  `canal`, gravado por `spurgeon remoto configurar --canal "Nome"`), nunca no repositório.
* **R0.2, download pelo Studio:** com o papel de editor, o menu "Opções → Baixar" da linha funciona
  (`ytcp-video-row` filtrada pelo título → botão `aria-label="Opções"` → item "Baixar" → `expect_download`). Um sermão de
  1h08 deu **503 MB em 54 s**, mas em **720p, 24 qps, H.264 a 0,85 Mbps, AAC 128 kbps estéreo**.
* **R0.2, qualidade:** o mesmo vídeo existe no YouTube em **até 1080p** (H.264 `137` a 1,6 Mbps, HLS `270` a 2,5 Mbps,
  AV1 `399`), mas o "Baixar" do Studio entrega só 720p. No 720p, a faixa 9:16 dos Shorts tem 405 px de largura (ampliação de
  2,7×); no 1080p, 607 px (1,8×). Os formatos têm **duas faixas de som** (`-0`/`-1`: a original e uma dublagem
  automática). **Decisão do autor (R15): a fonte é o "Baixar" do YouTube Studio, em 720p**, mesmo sabendo da ampliação
  nos Shorts (o yt-dlp a 1080p foi descartado).
* **R0.4, proxy:** do arquivo de 720p (1h08), 480p H.264 `veryfast` CRF 28, `-g 24`, AAC 96k, `+faststart`: **211 s e
  190 MB** nesta máquina (cerca de 1/20 da duração). Cabe logo depois do download, antes de a transcrição terminar.

* **R0.3 (postar um Short de teste):** feita na Fase R4, com um Short revisado (veja o estado da R4).

**Fase R1: só ver.** Servidor, segurança, `spurgeon remoto` na inicialização do Windows, PWA com a lista de projetos e o
andamento ao vivo, Shorts já renderizados tocando no celular, Web Push (R6), dormir e encerrar (S1).

**Estado da R1 (2026-11-20): feita e testada com os 3 projetos reais do PC** (31 testes em `tests/test_remoto.py`; teste
de fumaça no servidor de verdade; fotos do app em 393×851, temas escuro e claro). Como ficou:

* `spurgeon remoto configurar` autoriza a conta dona do PC no Tailscale (`remoto.json`) e publica a porta 18765 pelo
  `tailscale serve` (fica guardado no Tailscale). `spurgeon remoto iniciar` (ou só `spurgeon remoto`) sobe o servidor e o
  vigia; `spurgeon remoto inicializacao ligar` cria o atalho do Windows. O log fica em
  `%LOCALAPPDATA%\Spurgeon\Logs\remoto.log`.
* App: lista de projetos (situação, data, Shorts), projeto (etapas com progresso e fim do log, recarregando a cada 4 s
  enquanto processa; Shorts com filtro Marcados/Renderizados/Todos, selos de postado, agendado e renderizado, categoria
  com cor), Short (player com pulo no vídeo, título, descrição, hashtags e texto/legenda) e menu (ativar e testar avisos,
  pausar avisos, dormir/acordar, encerrar).
* Avisos sem `pywebpush` (que traria `aiohttp` e `requests`): o VAPID é assinado com o `cryptography`, a cifra é do
  `http-ece`, e o POST sai pelo `Transporte` da `publicacao/`. Os avisos do vigia são: "esperando a sua revisão",
  "pronto: N de M Shorts renderizados", "N Shorts renderizados" e "parou com erro". Cada projeto tem uma marca própria, e um
  aviso novo substitui o anterior do mesmo projeto no celular.
* Escolhas feitas sem consulta: (a) a segurança confia só no cabeçalho do Tailscale (sem token do aparelho, que S8
  previa; o servidor só escuta em `127.0.0.1`); (b) a lista vazia de contas barra todo mundo, e `configurar`/`iniciar`
  põem a conta dona do PC; (c) "Dormir" para os avisos e a API, mas o app continua abrindo para mostrar "Acordar"; (d)
  o vigia olha a cada 15 s e a primeira olhada não avisa nada.
* O autor confirmou o app no celular e pediu a abertura com o Windows (2026-11-20): o atalho
  `Studio Spurgeon - controle remoto.lnk` está na pasta "Inicializar" (o `pythonw` do `.venv`, sem console). **Falta na
  R1:** receber no celular um aviso do vigia numa execução real.
* **Ouvir com a legenda (R16, `remoto/audicao.py`, `rotas/previa.py`).** Na tela do Short, "🎧 Ouvir com a legenda" é o
  modo padrão (o celular lembra a escolha); "▶ Vídeo pronto" só aparece se o Short já foi renderizado. O PC monta só o
  som do Short, com os MESMOS trechos do render (`trechos_mantidos` + `linhas_do_audio_cortado`: sem os saltos, pausas
  encurtadas), em AAC mono 64 kbps, guardado em `_work/remoto/audio_<id>_<assinatura>.m4a`. A legenda é a do render
  (`palavras_com_a_legenda` → `palavras_do_short` → `remapear_palavras` → `agrupar_em_frases`). No celular: tocar e pausar,
  voltar 3 s, velocidade (1×, 1,25×, 1,5×, 2×), a palavra falada acende, a frase atual fica em destaque e rola para o
  meio, e tocar numa palavra pula o áudio para ela. **De onde vem o som:** o áudio tratado do projeto (se ainda existe),
  senão o vídeo original, senão o Short renderizado (com aviso se o corte mudou depois). Medido nos projetos reais: cerca
  de **1 s por Short** (350 a 750 KB). O sermão 08 não tem mais o original em `Downloads`: só os Shorts renderizados dele
  têm som. Limites: o som do original não leva o tratamento (limpeza e volume) quando o áudio tratado já foi apagado, e um
  Short renderizado do vídeo longo traz as pausas já cortadas pelo longo (a legenda pode adiantar).

**Fase R2: editar.** Trava e "o PC salva sozinho" (R8), aprovar cortes (S10), legenda inteira com "Enviar", início e
fim por toque na palavra (±0,1 s), proxy e prévia rápida (S2), renderizar os Shorts enviados.

**Estado da R2 (2026-11-20): feita e testada** (`tests/test_remoto_edicao.py`, `tests/test_janela_remoto.py`; telas
fotografadas com os projetos reais, sem gravar neles). Como ficou:

* **A trava (R8, `remoto/sessao.py`).** Abrir um projeto no celular (a tela do projeto, de um Short ou da aprovação) toma
  a trava `_work/celular.json`, renovada a cada 30 s; voltar à lista de projetos ou fechar o app (`pagehide` +
  `sendBeacon`) a solta, e sem batimento por 90 s ela vence sozinha. A janela do PC anuncia a cada ~2 s em que projeto
  está (`janela_aberta.json`, ao lado do `config.toml`) e, uma vez por segundo (`JanelaPrincipal._conferir_o_celular`),
  vê a trava do projeto aberto: salva o que o Studio tinha em edição **sem perguntar**
  (`EditorDeCortes.salvar_em_silencio`, o mesmo `salvar` sem as caixas de diálogo), grava o recibo
  `_work/celular_pc.json` e trava o Studio com o motivo "O celular está editando este projeto". O servidor espera esse
  recibo (até 6 s) só se a janela está naquele projeto; o celular avisa "O PC salvou…". Quando a trava sai, o Studio
  se solta e relê o projeto (o que o celular mudou aparece).
* **Editar (R9, `remoto/edicao.py`, `rotas/cortes.py`).** Na tela do Short ("Ouvir e editar"): **✎ Legenda** (a
  legenda inteira numa caixa, em blocos como o vídeo mostra; "Salvar" grava `Corte.legenda` por `legenda_corrigida`;
  "Voltar ao texto da transcrição" apaga a correção), **Início e fim** (as palavras do corte e 40 de cada lado; tocar numa
  palavra → "Começar nesta palavra"/"Terminar nesta palavra"; setas de uma palavra = `mover_limite`; a correção da
  legenda acompanha por `legenda_preservada`; menos de 10 s é recusado, acima de 3 min avisa) e **Marcado/Desmarcado**.
  Cada mudança grava só os campos do corte (`gravar_campos_do_corte`) e exige a trava (sem ela: 409 `sem_trava`, e o app
  toma de novo e repete). O áudio da audição é refeito sozinho (a assinatura muda). Não se edita enquanto um trabalhador
  está numa etapa de análise (ela reescreve o `cortes_shorts.json`).
* **Aprovar (S10/R17).** O projeto parado em "Aprovação dos cortes" mostra "✋ Os cortes esperam a sua aprovação" →
  tela com todos os cortes (quadrado para marcar, título para ouvir e editar) → "Aprovar e continuar (N marcados)". Outra
  revisão (a da transcrição) só pode ser aprovada como está. Aprovar = o servidor roda o trabalhador com `--aprovar`; a
  janela do PC, se esperava essa revisão, percebe e volta ao ocioso.
* **Renderizar e acompanhar (`remoto/fila.py`).** "▶ Renderizar N Shorts" (os marcados novos ou alterados:
  `situacao_do_short`, a mesma do Studio) roda o pipeline em `so_shorts` sem pausas de revisão, com a configuração da
  última execução (`_work/config_execucao.json`), como o "Renderizar" do Studio. Exige o vídeo original no PC. O
  celular mostra a etapa e o progresso, e pausa, retoma e cancela o que ele mesmo pediu. **Um processamento por vez no
  PC:** o servidor procura qualquer `spurgeon.core.trabalhador` rodando (`psutil`) antes de começar, e a janela faz o
  mesmo (`_impedimento_do_controle_remoto`), recusando também se o celular está com o projeto aberto.
* Escolhas feitas sem consulta: (a) abrir o projeto no celular (até só para ver) já trava o PC, como o autor descreveu
  ("abriu o projeto, o PC trava"); (b) cada mudança vai ao PC na hora (o "Enviar" é o botão "Salvar a legenda"; não há
  rascunho no celular); (c) o ajuste fino de ±0,1 s ficou de fora: as pontas caem sempre em fronteira de palavra, com o
  respiro de costume, como no Studio; (d) marca-texto, borracha e saltos não estão no celular.

**Correção na audição (R18, 2026-11-20).** O "✎ Corrigir a legenda" da tela do Short não abre mais uma caixa à parte: liga o
**modo correção** na própria legenda que acompanha o áudio. As frases ficam tracejadas, a palavra falada continua
acendendo, e tocar numa frase a troca ali mesmo por uma caixa com "▶ Ouvir" (toca só aquela frase e para), "✓ Salvar" e
"✕". Salvar manda só aquela frase (`POST …/legenda` com `frase`, `antes` e `texto`; `remoto/edicao.corrigir_frase`): o
servidor refaz as frases pelo mesmo cálculo da audição, confere que a frase ainda é a que o celular via (senão 422, "a
legenda mudou") e troca só aquelas palavras na legenda do corte (`legenda_corrigida`, como o Studio). O áudio não é
refeito (a legenda não muda o som) e a posição continua. "Editar o texto inteiro de uma vez" mantém a caixa antiga para
correções grandes. Achado: uma palavra **acrescentada antes da primeira** do corte ganha duração zero (é do
`aplicar_edicao` do Studio, igual no PC).

**Borracha e rascunho (R19, 2026-11-20).** O "Início e fim" da tela do Short virou **"Início, fim e borracha"**, com três
ferramentas: **↔ Início e fim** (tocar numa palavra → começar/terminar nela), **✂ Borracha** (um toque corta a palavra do
meio; de novo, devolve) e **✂ Trecho** (dois toques: tudo entre as duas palavras sai, ou volta se a primeira já estava
cortada). Tudo é um **rascunho no celular** (o conjunto de palavras do corte, com a duração estimada pelo início e fim de
cada palavra): nada vai ao PC e o áudio não é refeito a cada toque. Vai de uma vez (`POST …/pontas` com `indices`;
`remoto/edicao.definir_palavras` → `corte_com_indices`: o que fica entre dois pedaços vira salto, e `legenda_preservada`)
no **"💾 Salvar alterações"**, ao **trocar de ferramenta ou de aba** (Ouvir/Vídeo) e ao **sair do Short**; "Descartar" volta
ao que estava. Menos de 10 s não salva. As setas de uma palavra da R2 saíram (o toque em "Início e fim" faz o mesmo).

**Volta à última tela (2026-11-20).** O app guarda no próprio celular (`localStorage`, chave `ultimaTela`) a última tela
aberta (projeto, Short, aprovação, Shorts livre ou a lista do canal) e, aberto de novo pelo atalho (que sempre abre a
página inicial), volta a ela (`restaurarUltimaTela`, antes do primeiro `rotear`). Um aviso com link abre a tela do aviso.
Se o projeto ou o Short lembrado não existe mais (404), o app esquece e mostra a lista de projetos.

**Mais texto no início e fim, e exportar a legenda (2026-11-20).** (a) O editor "Início, fim e borracha" recebe **600
palavras de cada lado** do corte (uns 5 minutos; `edicao.PALAVRAS_DE_CONTEXTO`), e "⬆/⬇ Mostrar mais texto" pede mais
1200 ao PC (`…/edicao?antes=&depois=`, até 6000) sem perder o rascunho nem refazer o áudio. (b) **Exportar a legenda
do sermão inteiro** (o autor esclareceu que é do sermão, não do Short): um arquivo de texto com o título, a duração e uma
frase por linha com o tempo em que ela começa **no vídeo original** (o mesmo do YouTube), a partir da transcrição inteira
com as revisões (`shorts/exportar.texto_da_legenda_do_sermao`, frases de `separar_frases`; `legenda_<sermão>.txt`). No
celular: "⬇ Exportar a legenda do sermão" na tela do projeto (`GET /api/projetos/<p>/legenda.txt`). No Studio do PC:
"Exportar a legenda do sermão…" ao lado de "Dicionário…" (pergunta onde salvar, começando na pasta do projeto). O cálculo
da legenda no tempo do Short (a audição) também foi para o núcleo (`legenda_no_tempo_do_short`). O botão do PC chega a
quem instala junto com a v2.0.0. Medido no sermão 09: 183 frases, de 0:07 a 1:08:12.

**Fase R3: Shorts livre** pelo celular: busca, categorias, criar o Short.

**Estado da R3 (2026-11-20): feita e testada** (`tests/test_remoto_livre.py`; tela fotografada com o sermão 09 real, 8668
palavras, sem criar nada nele). Como ficou (`remoto/livre.py`, `rotas/livre.py`, tela `#/p/<projeto>/livre`):

* **O texto vivo.** O botão "✚ Shorts livre" do projeto abre o sermão inteiro, uma linha por frase com a hora, a barra
  da cor da categoria que a IA detectou (`trechos_detectados` + `categoria_de_cada_frase`) e as palavras que já estão
  num Short sublinhadas na cor dele (`short_de_cada_palavra`). São as palavras da janela do vídeo do projeto
  (`palavras_da_janela`), como no PC, e os índices são os dessa lista. O texto fica guardado no servidor enquanto a
  transcrição e o estado não mudam.
* **Escolher o trecho por toques.** Um toque na primeira palavra e outro na última. Depois, tocar numa palavra oferece
  "Começar nesta palavra", "Terminar nesta palavra", "Tirar esta frase do trecho", "Tirar só esta palavra", "Voltar a
  incluir esta frase" e "Começar um trecho novo aqui". O painel de baixo mostra a duração (`duracao_do_trecho`), onde
  começa e termina (`resumo_das_pontas`) e o problema, se houver (`problema_do_trecho`: menos de 10 s ou mais do que
  cabe).
* **Buscar.** "Palavras" (`procurar_palavras`, parecidas) ou "Tema" (`procurar_tema`; o modelo de vetores do spaCy é
  carregado na primeira busca e fica na memória do servidor). Tocar num achado escolhe aquele trecho.
* **Ouvir o trecho antes de criar.** O mesmo áudio da audição (`audicao.audio_do_corte`, generalizado na R3), de um
  corte provisório (`corte_de_previa`: as pontas com o respiro e os pedaços tirados como saltos). Só com o áudio tratado
  ou o vídeo original no PC.
* **Criar.** "✚ Criar Short deste trecho" roda `gerar_corte_livre` (a IA: categoria, nota, título, descrição e hashtags)
  numa thread do servidor, uma criação por vez, com as opções do projeto (modelo do Ollama, threads). O celular mostra
  o andamento e, pronto, abre o Short novo; se a pessoa saiu do app, chega um aviso ("Shorts livre: Short criado…").
  Enquanto a IA trabalha, o servidor renova a trava do celular, e o corte só entra no `cortes_shorts.json` com a trava
  valendo (a janela do PC não regrava a lista por cima).
* **Medido com a IA de verdade** (numa cópia do sermão 09, fora dos projetos): busca por palavras com 9 achados, por
  tema com 12 em 2,8 s (com a carga do modelo), e um Short livre de 45 s criado em **190 s** ("Eles usavam a construção
  para cobrar impostos", nota 76) pelo mesmo caminho do servidor.
* Escolhas feitas sem consulta: (a) o destaque do trecho é palavra a palavra (os espaços não se pintam); (b) não há
  "ouvir com a palavra acendendo" no trecho (só tocar e pausar); (c) a criação pode rodar junto com um processamento
  (como no PC); a CPU fica dividida.

**Fase R4: postar.** Agendar no YouTube pelo navegador, com o vídeo relacionado (S3), já postado, refazer o título com a
IA.

**Estado da R4 (2026-11-20): feita e testada, com uma postagem de verdade** (autorizada pelo autor):
`canal/navegador.py`, `canal/postar.py`, `remoto/postagem.py`, `rotas/postagem.py`, testes em `test_remoto_postagem.py`
e `test_canal_postar.py`.

* **A primeira postagem real.** Um Short de um projeto real estava
  revisado mas não renderizado: foi renderizado pela rota do celular (`/renderizar`, 4 min 54 s) e postado pelo YouTube
  Studio, programado para 20/11/2026 às 18:30 e vinculado ao sermão completo. O projeto ficou com `agendado_para`, o resultado `agendado` em
  `postagens.json` e o arquivo em `shorts_gerados/agendados/`.
* **O caminho no Studio** (`postar`): "Enviar vídeos" (`/videos/upload?d=ud`, `ytcp-uploads-file-picker input[type=file]`)
  → título e descrição (`#title-textarea #textbox`, `#description-textarea #textbox`; a descrição leva as hashtags numa
  linha própria, como o autor posta) → "Não é conteúdo para crianças" (`VIDEO_MADE_FOR_KIDS_NOT_MFK`) → `#next-button`
  ×3 → "Programar" (`#second-container-expand-button`, `#datepicker-trigger` com "20 de nov. de 2026",
  `#time-of-day-container input` com "18:30") ou "Público" → `#done-button` ("Programar"). O id sai do texto da janela
  ("Link do vídeo"). O **vídeo relacionado** vai no passo **"Elementos do vídeo"** da janela de envio, antes de programar
  (indicação do autor, 2026-11-20; `relacionar_nos_elementos`): "Vídeo relacionado" → busca `input#search-yours` com o
  título do sermão → o primeiro `ytcp-entity-card`. Se esse passo não mostrar o campo, o plano B é a página de edição
  (`/video/<id>/edit`, `#linked-video-editor-link` → o mesmo seletor → `ytcp-button#save`), que foi o caminho usado na
  primeira postagem. No fim confere que a visibilidade (`ytcp-video-metadata-visibility`) ficou "Programado" (ou
  "Público").
* **Achados no primeiro envio** (viraram regra no código): (a) na primeira postagem o campo não foi achado no passo
  "Detalhes" e o vínculo foi feito na página de edição; **o autor indicou que ele fica em "Elementos do vídeo"**, e é
  ali que a automação vincula agora (a confirmar na próxima postagem: a pausa `elementos` tira a foto); (b) com a verificação de direitos ainda rodando, "Programar" abre o aviso "Ainda estamos verificando
  seu conteúdo" e **só grava com "Ok"** (sem ele o Short continua rascunho); (c) um envio que para no meio deixa no canal
  um **rascunho privado**: `postar(..., rascunho=True)` o retoma pela lista ("Editar rascunho") em vez de mandar de novo,
  e o registro guarda `rascunho` para a próxima tentativa fazer isso sozinha; (d) não é preciso esperar a verificação
  de direitos terminar para programar.
* **No celular:** "📤 Postar" na tela do Short (só renderizado e em dia): o que já está no YouTube (programado,
  publicado, rascunho ou erro, com o link), "📅 Programar" (dia e hora, pelo menos 15 min à frente) ou "Publicar
  agora", "Vincular ao vídeo do sermão" (o nome do projeto já vem preenchido) e "📤 Postar no YouTube". O PC abre o
  Chrome da automação e faz tudo; o celular mostra o andamento e recebe um aviso no fim. Short já programado ou
  publicado pede confirmação e vira "Postar de novo" (criaria outro vídeo). A lista mostra o selo "YouTube:
  programado/publicado/rascunho/falhou".
* Escolhas feitas sem consulta: (a) sem tags (as hashtags vão na descrição, como o autor já faz); (b) o navegador da
  automação abre visível no PC (o Google costuma estranhar o modo invisível); (c) "refazer o título com a IA" ficou para
  depois (não era o pedido desta rodada).

**Fase R5: começar pelo celular.** Listar o canal, baixar pelo Studio, criar o projeto (S4) e processar com as opções
salvas.

**Estado da R5 (2026-11-20): feita e testada** (`canal/listar.py`, `canal/baixar.py`, `remoto/novo.py`,
`rotas/canal.py`; testes em `test_remoto_novo.py`). Como ficou:

* **No celular:** "✚ Novo projeto de um vídeo do canal" (na lista de projetos) abre `#/canal`: as opções salvas com
  que o PC vai processar (`config.padroes`: vídeo longo, Shorts, qualidade, zoom, tratamento, pausas de revisão…) e os
  vídeos da aba "Vídeos" do Studio (os ~30 mais recentes; título, data, duração, visibilidade e "✔ Já é um projeto").
  Tocar num vídeo → "⬇ Baixar e processar no PC". O celular mostra o andamento e abre o projeto; o aviso chega se a
  pessoa saiu ("Baixado; o processamento começou no PC").
* **No PC** (`NovosProjetos`, uma tarefa por vez, recusada com o PC já processando): baixa pelo "Opções → Baixar" para
  a pasta de entrada (`Spurgeon\Downloads`; se o arquivo já está lá, não baixa de novo) → cria o projeto com o
  **título e a data de publicação do canal** (S4) e as opções salvas → grava `_work/origem_youtube.json` (id, título,
  data) → confere o ambiente (`executar_preflight`, como a janela) → processa pela fila (`--aprovar` não; as pausas de
  revisão são as das opções). A postagem passa a vincular o sermão pelo título gravado na origem.
* **"▶ Processar (continua de onde parou)"** na tela do projeto, para um projeto que não começou, parou com erro ou foi
  cancelado (não aparece concluído nem esperando revisão). Exige o vídeo original no PC.
* **Trava do navegador:** o perfil da automação só abre uma vez; listar, baixar e postar esperam a vez
  (`canal/navegador._NAVEGADOR`).
* **Prova real (sem processar):** listados 30 vídeos do canal (os projetos 09 e 08 reconhecidos pelo nome); o sermão mais
  novo foi baixado pelo Studio (506 MB, 1280×720 H.264,
  1h08min54s) e virou um projeto novo, com a origem gravada. O processamento **não**
  começou (a escolha de gastar as horas de CPU é do autor: um toque em "Processar").
* Escolhas feitas sem consulta: (a) só a primeira página da lista do Studio (o sermão novo está nela); (b) o nome do
  arquivo baixado segue o das pastas de projeto (sublinhados no lugar dos espaços); (c) o começo e o fim do vídeo não
  são pedidos (o processamento usa o vídeo inteiro, como o padrão da janela).

**Depois da v2.0.0:**

* **Vídeo relacionado na janela de envio:** o cartão "Vídeo relacionado" de "Elementos do vídeo" abre pelo botão
  "Adicionar" (`ytcp-shorts-content-links-picker #shorts-content-links-add-button`); clicar no título do cartão não faz
  nada, e a espera pelo seletor estourava os 30 s. Conferido num rascunho de verdade (o cartão mostra o sermão).
* **Título, descrição e hashtags no celular:** cada caixa da tela do Short tem "✎ Editar" (`POST …/shorts/<id>/textos`,
  `edicao.mudar_textos`). Título novo refaz o `slug` e renomeia o Short já renderizado e o `.txt`, como o salvar do PC
  (`sincronizar_arquivos`); com um processamento rodando só o `cortes_shorts.json` muda (o PC acerta os arquivos depois).
* **Cronograma do canal** (aba "📅 Cronograma" da tela inicial; `canal/cronograma.py`, `remoto/cronograma.py`,
  `GET /api/canal/cronograma[?auto=1]`, `POST …/atualizar`): calendário do mês passado em diante com quantos Shorts (S) e
  vídeos longos (V) cada dia tem (cor forte = programado, clara = publicado); tocar no dia mostra as horas; embaixo, os
  próximos agendados. A leitura abre as listas "Vídeos" e "Shorts" do Studio e lê o JSON que a página carrega
  (`list_creator_videos`: `scheduledPublishingDetails` traz a hora, que a tabela não mostra), seguindo as páginas até o
  dia 1º do mês passado (~17 s). Só entra o que está programado ou saiu público (não listados e privados ficam de fora).
  A última leitura fica no `cronograma.json` ao lado do `remoto.json`; abrir a aba com a leitura velha (30 min) ou depois
  de uma postagem lê de novo, em segundo plano. A janela abre FORA DA TELA (`abrir_studio(fora_da_tela=True)`): o
  Chrome `headless` abre o Studio deslogado (o Google não aceita a sessão guardada nele).
* **O sermão de cada Short no cronograma:** o JSON da lista traz o vídeo relacionado de cada Short
  (`videoLinkageShortsAttribution.links[].targetExternalVideoId`). O título sai de qualquer vídeo lido (até os não
  listados e os que não entram no calendário); o de sermões mais antigos que as páginas lidas vem do oEmbed do YouTube
  pelo próprio navegador da automação (até 30 por leitura). Na tela: "🔗 Sermão: …" (link) ou "Sem vídeo relacionado".
  O `cronograma.json` tem `versao` (2): um arquivo de formato antigo é ignorado e o canal é lido de novo.
* **O que o Spurgeon agendou entra no calendário na hora** (`cronograma.itens_dos_projetos` +
  `juntar_com_os_projetos`, a cada pedido da aba): os Shorts com `agendado_para` ou `postado` nos projetos (postados
  pelo celular ou marcados no Studio do PC), com o id do YouTube e o sermão que o `postagens.json` gravou. O canal
  manda: um Short dos projetos só aparece se a leitura do canal não o tem pelo id, pelo título ou (programado) por um
  Short do canal no mesmo minuto (o título pode ter mudado no YouTube). Na tela: "Marcado no Spurgeon (ainda não
  conferido no YouTube)". Postar não marca mais a leitura como velha (não abre o navegador à toa).
* **Liberar espaço: varrer e apagar o projeto** (`remoto/limpeza.py`; `GET /api/projetos/<p>/espaco`,
  `POST …/varrer` e `POST …/apagar`, os dois com `{"confirmar": "<nome da pasta>"}`; na tela do projeto, "🧹 Espaço no
  PC", com folha de confirmação, e apagar pede duas vezes). Varrer apaga os `.mp4` de `shorts_gerados/` (todas as
  subpastas), o vídeo longo pronto, os intermediários de `_work/` (como `limpar_intermediarios`) e o vídeo original;
  ficam os `.json`, os `.txt`, as `.srt` e o `estado.json`, e grava `_work/varrido.json`. Apagar tira a pasta inteira e
  o original. O original (pode estar em `Downloads/`) só sai se for vídeo e se nenhum OUTRO projeto usa o mesmo
  arquivo. Recusa com processamento, postagem deste projeto ou criação da IA em andamento; apagar recusa também com a
  janela do PC no projeto. Varrer toma a trava do celular (o PC salva antes). O app solta a trava ANTES de apagar (a
  renovação, `sessao.tomar`, recriaria `_work/`), e `apagar_projeto` tira de novo uma pasta que voltou só com a trava.
* **Projeto do canal em dois tempos** (`novo.NovosProjetos.criar` / `.comecar`; `POST /api/canal/novo` devolve só
  `{"projeto"}`; `POST /api/projetos/<p>/comecar`): escolher o vídeo cria o projeto NA HORA, sem baixar (o `estado.json`
  aponta para onde o vídeo vai ficar, tamanho 0 e sem hash; `novo.video_baixado` diz se já veio; a origem guarda também
  a duração). Na tela do projeto, enquanto nenhuma etapa saiu de "pendente", vêm as **opções** (`remoto/configuracao.py`,
  `GET/POST /api/projetos/<p>/opcoes`): o que gerar, trecho do vídeo, áudio, imagem, Shorts, revisões e processamento,
  descritas pelo servidor (tipo, limites, ajuda) e validadas por `OpcoesProcessamento` (o mesmo do PC); cada mudança é
  gravada na hora. Introdução, vinheta e marca d'água só ligam se o arquivo já foi escolhido no PC. "▶ Começar" baixa
  (se preciso), grava a entrada de verdade, confere o ambiente com as opções DO PROJETO e põe na fila. Depois de
  começar, as opções ficam só para ver ("⚙ Opções usadas").
* **A lista de vídeos do canal fica guardada** (`rotas/canal.py`, `canal_videos.json` ao lado do `remoto.json`,
  `Contexto.arquivo_da_lista`): a tela "Novo projeto" abre na hora com a última lista e diz quando foi lida; só
  "↻ Atualizar a lista do canal" (ou a primeira vez, sem lista) abre o Studio. Se atualizar falhar, a lista guardada
  continua na tela. Testes: o `NovosProjetos` padrão lê a configuração do app (as pastas DE VERDADE); nos testes passe
  `carregar=` com pastas em `tmp_path`. O `conftest.py` reprova o teste que criar ou apagar algo em `Projetos/` ou
  `Downloads/` (só compara os nomes; não apaga nada).
* **Nunca rode outra automação no perfil do navegador com o servidor podendo postar:** a trava `_NAVEGADOR` é só do
  processo; um script de fora usando o mesmo perfil atrapalhou uma postagem real (o Short foi programado, mas a
  conferência final falhou e o registro ficou como rascunho). Depois de programar, a página pode dizer "Pendente" por um
  tempo: a conferência agora espera (até 1 min).
* **Postagem conferida no canal** (`postagem._postar_de_verdade`, `conferir_no_canal`, `canal/cronograma.procurar_short`):
  depois de um erro com o arquivo já enviado, e antes de retomar um "rascunho", a automação lê a 1ª página da lista de
  Shorts (o mesmo JSON do cronograma; pelo id, ou pelo título se o id não ficou guardado). Se o canal já mostra o Short
  programado ou público, isso vale como sucesso, com a hora que o CANAL diz (`programado_para` do resultado manda no
  `agendado_para`), e nada é enviado de novo. Uma falha ao conferir vale como "não sei" (fica o erro original).
* **Agendados que já saíram viram "já postado" no disco** (`dados.vencer_agendados_de_todos`, a cada olhada do vigia,
  15 s): antes só a janela do PC fazia isso, e só com o projeto aberto; com ela fechada o Short ficava "agendado" para
  sempre na lista do Studio e do celular. Com um processamento rodando o `.mp4` não é movido (o PC o leva depois).
* **Datas em dia/mês/ano:** o dia da postagem é um campo de texto `dd/mm/aaaa` (barras automáticas, 📅 abre o calendário
  do celular), porque o `<input type="date">` segue o idioma do celular e pode mostrar mês/dia/ano.

## 7. Armadilhas conhecidas

* **O controle remoto é opcional.** A interface importa só `remoto/sessao.py` e `remoto/fila.py` (sem dependência do
  extra); o servidor, o Web Push e o Playwright só são importados por `spurgeon remoto`. O instalador não instala o extra.
* **O repositório é público.** Nenhum segredo, nome ou id de canal, nome de máquina do Tailscale, e-mail ou token nos
  arquivos: o nome do canal e as contas autorizadas ficam no `remoto.json` do PC; chaves no `Cofre`.
* **Automação do YouTube Studio quebra quando a página muda.** Os seletores ficam num lugar só (`canal/`). Cada falha
  grava uma foto da tela no log e avisa o celular em vez de travar.
* **A porta do servidor tem que ser exclusiva.** No Windows, o `http.server` do Python (`allow_reuse_address = True`)
  liga numa porta que outro programa já usa, **sem erro**, e quem responde é o outro. Na R0.1 o `tailscale serve`
  chegou a expor outro aplicativo por isso. O servidor liga com reuso desligado (ou `SO_EXCLUSIVEADDRUSE`), numa porta
  incomum, e confere a resposta antes de chamar o `tailscale serve`.
* **No app do celular, `encher(el, ...)` em vez de `el.replaceChildren(...)`.** O `replaceChildren` do navegador escreve
  `null` como texto (apareceu "null" no painel do Shorts livre). Confira a sintaxe do `app.js` com `node --check`.
* **Sem console** (CLAUDE.md): o servidor roda no `pythonw`, todo `subprocess` usa `CREATE_NO_WINDOW` e a saída vai para
  `%LOCALAPPDATA%\Spurgeon\Logs\remoto.log`.
* **Testes nunca tocam a internet nem o navegador de verdade.** HTTP pelo `TransporteFalso`; o Playwright é trocado por um
  falso; nenhum teste escreve fora de `tmp_path`.
* **Duas pontas escrevendo o mesmo `cortes_shorts.json`.** Tudo passa pelas mesmas funções do Studio
  (`gravar_campos_do_corte`, `salvar`), e só quem tem a trava escreve.
* **O PC dormindo mata o acesso.** O servidor pede ao Windows para não suspender enquanto há trabalho
  (`core/energia.impedir_suspensao`). O plano de energia do PC deve estar em "nunca suspender".

## 8. Em aberto

* S1, S2 e S4 a S9 ainda não foram confirmados pelo autor. S3 segue R12 (tudo pelo navegador); S10 virou R17.
* As escolhas sem consulta da R1 e da R2 (veja o "Estado" de cada fase).
* Qualidade do download do YouTube Studio (R0.2): se for baixa, o vídeo longo e os Shorts perdem nitidez em relação ao
  arquivo da câmera.
