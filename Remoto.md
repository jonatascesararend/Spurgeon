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
| R12 | **Postar no YouTube só por automação de navegador, SEM a API do YouTube**: envio, detalhes, agendamento e o vídeo relacionado (o sermão original), que a API não faz | 2026-11-20 |
| R13 | O PC fica disponível o dia todo | 2026-11-20 |
| R14 | Tailscale instalado no PC (`souza-arend`) e no celular (`jonatas-arend`), mesma conta, MagicDNS ligado | 2026-11-20 |
| R15 | O vídeo de um projeto novo vem do **"Baixar" do YouTube Studio (720p)**, não do yt-dlp a 1080p | 2026-11-20 |
| R16 | Para conferir a legenda basta **ouvir**: opção de montar **só o áudio** do Short, com a legenda acompanhando, sem renderizar o vídeo | 2026-11-20 |
| R17 | A **aprovação dos cortes** também é feita pelo celular (S10) | 2026-11-20 |

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
  continua na `main` e não é usada no branch. Riscos: o envio pelo navegador **não retoma** se cair no meio (recomeça), e
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
    rotas/
      __init__.py
      projetos.py                  [R1/R2] lista, detalhe, andamento, Shorts, vídeo (Range), aprovar, renderizar
      sistema.py                   [R1] estado, dormir, acordar, encerrar e os avisos
      cortes.py                    [R2] trava (tomar/soltar), legenda, pontas, marcar
      livre.py                     busca, categorias, criar Short livre
      previa.py                    [R1] legenda e áudio do Short (ouvir, R16); depois o proxy da prévia rápida
      postagem.py                  agendar, já postado, refazer título com a IA
      canal.py                     vídeos do canal, baixar e criar projeto
    web/                           [R1] o app do celular (index.html, app.js, estilo.css, sw.js, manifest)
  canal/
    __init__.py
    navegador.py                   S5: perfil persistente do Playwright
    listar.py                      S6: vídeos longos do canal (página Conteúdo do Studio)
    baixar.py                      R11: baixar pelo YouTube Studio
    postar.py                      S3: postar o Short pelo YouTube Studio (Plataforma, sem API)
  render/proxy.py                  S2: proxy 480p para a prévia rápida
tests/test_remoto.py               [R1] servidor, segurança, dados, avisos e vigia (sem internet, sem Tailscale)
tests/test_remoto_audicao.py       [R1] ouvir com a legenda (um teste roda o FFmpeg de verdade)
tests/test_remoto_edicao.py        [R2] trava, edição, fila, aprovar e renderizar
tests/test_janela_remoto.py        [R2] a janela do PC: salva e trava, revisão aprovada, um por vez (-m gui)
```

`[R1]`, `[R2]` = já tem código (fase em que entrou). Os outros módulos só têm a descrição; o código entra fase por fase. Dependências do branch: o
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
  `https://souza-arend.tailf58721.ts.net/`, só dentro da rede privada. Os certificados HTTPS foram ligados pelo autor.
  **Funcionou no Android fora do Wi-Fi:** a página abriu, o service worker registrou, o celular se inscreveu no push (FCM
  do Chrome, chaves VAPID geradas no PC, `pywebpush`) e recebeu os avisos (resposta 201). O `tailscale serve` passa o
  cabeçalho `Tailscale-User-Login` com a conta do autor: a segurança (S8) pode confiar nele, junto com o servidor ligado
  só em `127.0.0.1`.
* **R0.2, canal:** o perfil da automação entra logado. O autor é **editor** do canal **Revendo Meus Conceitos**, que
  não é o canal padrão da conta: foi preciso trocar de conta no Studio uma vez, e o Studio lembra a escolha. O nome do canal
  fica em `#entity-name`; a automação confere esse nome (e o id) antes de agir (S5).
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

* **R0.3 (postar um Short de teste):** adiada pelo autor em 2026-11-20; fica para a Fase R4.

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

**Fase R3: Shorts livre** pelo celular: busca, categorias, criar o Short.

**Fase R4: postar.** Agendar no YouTube pelo navegador, com o vídeo relacionado (S3), já postado, refazer o título com a
IA.

**Fase R5: começar pelo celular.** Listar o canal, baixar pelo Studio, criar o projeto (S4) e processar com as opções
salvas.

## 7. Armadilhas conhecidas

* **O atualizador do Studio olha a `main`.** A pasta de desenvolvimento é um clone com `.git`, então não se atualiza
  sozinha, e é por isso que o branch pode viver nela. **Nunca** instale o branch por ZIP: o atualizador o trocaria pela
  `main`.
* **O repositório é público**: o branch publicado também é. Nenhum segredo, id de canal, e-mail ou token nos arquivos.
* **Automação do YouTube Studio quebra quando a página muda.** Os seletores ficam num lugar só (`canal/`). Cada falha
  grava uma foto da tela no log e avisa o celular em vez de travar.
* **A porta do servidor tem que ser exclusiva.** No Windows, o `http.server` do Python (`allow_reuse_address = True`)
  liga numa porta que outro programa já usa, **sem erro**, e quem responde é o outro. Na R0.1 o `tailscale serve`
  chegou a expor outro aplicativo por isso. O servidor liga com reuso desligado (ou `SO_EXCLUSIVEADDRUSE`), numa porta
  incomum, e confere a resposta antes de chamar o `tailscale serve`.
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
