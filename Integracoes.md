# Integrações: publicar e agendar os Shorts nas redes da igreja

Pesquisa feita em **02/11/2026** na documentação oficial de cada rede (e em fontes de terceiros quando
a oficial não dizia). As regras das APIs mudam: antes de mexer em `src/spurgeon/publicacao/`, reconfira
a página citada. O que não deu para confirmar está marcado **NÃO CONFIRMADO**.

A ideia do produto: o Spurgeon é o lugar único de onde a igreja posta e agenda cada Short em todas as
redes. Cada igreja usa as PRÓPRIAS chaves (cada uma cria o próprio app em cada rede); o programa não
tem servidor nem chave embutida. Os segredos ficam no `Cofre`, criptografados pelo Windows (DPAPI), em
`integracoes.json` ao lado do `config.toml`.

## Resumo: o que cada rede permite

| Rede | Publicar pela API | Agendar | O que o Spurgeon faz |
|---|---|---|---|
| YouTube Shorts | Sim (envio resumível do arquivo) | Nativo: privado + `publishAt` | API completa; confere o status devolvido |
| Instagram Reels | Sim, com o arquivo local (login do Facebook + `rupload`) | **A API não agenda** | Fila local: envia ~45 min antes e publica na hora (aplicativo aberto) |
| Facebook Reels (Página) | Sim, de 3 a 90 s | Nativo: de 10 min a 29 dias | API completa; Short com mais de 90 s não entra |
| TikTok | Publicação direta **inviável** (auditoria recusa app só para a própria conta) | A API não tem | Fluxo assistido (legenda copiada + pasta + TikTok Studio); rascunho pela API é opcional |
| Upload-Post (intermediário) | Sim, as quatro redes com uma chave | Sim | Segundo caminho opcional, pago, de terceiros |

## YouTube

- **Envio:** `POST upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`; o cabeçalho
  `Location` da resposta é a sessão; `PUT` em pedaços múltiplos de 256 KB (usamos 8 MiB), com
  `Content-Range`; 308 devolve `Range`; para retomar, `PUT` vazio com `Content-Range: bytes */TOTAL`.
- **Escopo:** `https://www.googleapis.com/auth/youtube` (e não só `youtube.upload`): `videos.update` e
  `videos.delete` não aceitam o `upload`.
- **Cota (mudou em 2026):** o `videos.insert` tem balde próprio, de 100 chamadas por dia (1 unidade cada);
  as demais chamadas ficam nos 10.000/dia (`videos.update` 50, `videos.list` 1). Uma igreja cabe folgada.
- **Agendar:** `status.privacyStatus=private` + `status.publishAt` em ISO 8601 UTC (`…Z`). Limites de
  antecedência não são documentados; horário no passado publica na hora. `videos.update part=status`
  reagenda (reenvie o `status` inteiro: o que a parte omite é apagado). Cancelar o agendamento: sem
  método documentado (**NÃO CONFIRMADO** se `update` sem `publishAt` serve).
- **Short:** não há flag. Vídeo vertical ou quadrado de até 3 min é Short. Título até 100 caracteres
  (sem `<` e `>`), descrição até 5.000 **bytes** (acento conta 2), tags até 500 caracteres.
- **A TRAVA (o ponto mais perigoso):** o histórico de revisões de 28/07/2020 diz que vídeos enviados por
  projetos de API NÃO verificados ficam restritos a privado até uma auditoria; a referência atual de
  `videos.insert` diz que "não ficam restritos". **NÃO CONFIRMADO qual vale.** Por isso, depois de
  enviar, o Spurgeon lê `videos.list part=status` e avisa se o YouTube deixou o vídeo privado ou não
  registrou o `publishAt`. Um vídeo já travado não se destrava: precisa ser reenviado.
- **Login (OAuth de desktop):** navegador do sistema, retorno em `http://127.0.0.1:porta` e PKCE;
  credencial do tipo "Aplicativo para computador". No modo **Teste** do Google o refresh token **vence
  em 7 dias**; "Publicar app" (sem verificação) tira esse prazo, mas mostra "app não verificado" e tem
  teto de 100 usuários na vida do projeto (uma igreja usa 1). `invalid_grant` = conectar de novo.
- **Configuração única (cole no app):** projeto no Google Cloud, YouTube Data API v3 ativada, tela de
  consentimento Externa com o escopo `youtube`, ID do cliente + chave secreta.

Fontes: developers.google.com/youtube/v3/docs/videos/insert · …/revision_history ·
…/guides/using_resumable_upload_protocol · …/determine_quota_cost · developers.google.com/identity/
protocols/oauth2/native-app · support.google.com/youtube/answer/15424877 (Shorts).

## Instagram (Reels) e Facebook (Reels de Página): a Meta

- **API do Instagram:** a "Instagram API com Login do Facebook" (host `graph.facebook.com`). Só ela
  aceita o ARQUIVO LOCAL (upload resumável); a "com Login do Instagram" exige `video_url` pública.
- **Fluxo do Instagram:** `POST /{ig-id}/media` (`media_type=REELS`, `upload_type=resumable`, `caption`,
  `share_to_feed`) → `{id, uri}`; `POST` do arquivo na `uri` (`rupload.facebook.com/ig-api-upload/…`)
  com `Authorization: OAuth <token>`, `offset: 0`, `file_size`; consultar `GET /{contêiner}?fields=
  status_code` até `FINISHED` (`ERROR`/`EXPIRED` falham); `POST /{ig-id}/media_publish`.
- **Vídeo (Instagram):** MP4/MOV, H.264/HEVC, AAC, 23–60 fps, até 1920 px de largura, de 3 s a 15 min,
  **até 300 MB**. Legenda até 2.200 caracteres e 30 hashtags. Limite de publicações por dia: o guia diz
  100 e a referência diz 50 (planeje com 50; `GET /{ig-id}/content_publishing_limit` informa).
- **Instagram NÃO agenda pela API** (nenhuma página documenta `scheduled_publish_time`). Por isso o
  Spurgeon guarda o horário (estado `na_fila`), cria o contêiner ~45 min antes (o contêiner expira em
  24 h) e chama `media_publish` na hora. **O aplicativo precisa estar aberto**; se estava fechado, publica
  com atraso e avisa.
- **Facebook Reels:** `POST /{page-id}/video_reels` com `upload_phase=start` → `video_id` e `upload_url`;
  `POST` do arquivo (`rupload.facebook.com/video-upload/{id}`); `upload_phase=finish` com
  `video_state=PUBLISHED|SCHEDULED|DRAFT` e `scheduled_publish_time` (Unix, **mais de 10 min e até 29
  dias**). Duração de **3 a 90 s** (erro 1363128 acima disso). Limite de 30 publicações por 24 h.
  Cancelar/reagendar pela API: **NÃO CONFIRMADO**.
- **Sem App Review:** "se o app só serve a conta profissional que você administra, o Standard Access
  basta" (acesso Standard é automático, vale para quem tem função no app, e dispensa App Review e
  verificação de empresa). Tipo de app: Business, modo Desenvolvimento.
- **ARMADILHA (a conferir com um Reel de teste):** a Meta diz que dados gerados por app em modo
  Desenvolvimento "só podem ser vistos por quem tem função no app". Para Páginas há relato de que os
  posts ficam invisíveis ao público; para o Instagram é **NÃO CONFIRMADO**. Publique um Reel e abra-o
  com outra conta/deslogado. Se estiver invisível, o app precisa virar "Live" (política de privacidade,
  ícone, exclusão de dados); se isso exige App Review para uso próprio é **NÃO CONFIRMADO**.
- **Tokens (caminho mais simples para uma igreja):** a pessoa gera um token no Explorador da Graph API
  (permissões `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`,
  `instagram_content_publish`) e cola no app com o ID e a chave secreta do app. O Spurgeon troca por
  token de longa duração (`fb_exchange_token`, ~60 dias) e então pega o **token da Página, que não
  expira** (`/me/accounts`) e é o único que fica guardado. Cai se a senha mudar, a função for removida
  ou a permissão for revogada (erro 190 → "conectar de novo"). Redirecionamento OAuth em
  `http://localhost` na Meta hoje: **NÃO CONFIRMADO**, por isso não usamos OAuth para a Meta.
- **Versão da Graph API:** fixada em `v23.0` (`VERSAO_DA_API`); a documentação mistura exemplos de v19 a
  v26. O campo `versao_da_api` do cofre permite trocar sem mexer no código.
- **Armadilhas:** conta pessoal ou Página sem Instagram ligado; Página com 2FA/PPA pendente; áudio mono
  no Facebook (a especificação diz "estéreo": **NÃO CONFIRMADO** se rejeita); códigos 2207026
  (formato), 2207042 (limite diário), 2207050 (conta restrita).

Fontes: developers.facebook.com/docs/instagram-platform/content-publishing · …/resumable-uploads ·
…/instagram-graph-api/reference/ig-user/media · /docs/video-api/guides/reels-publishing ·
/docs/graph-api/reference/page/video_reels · /docs/development/build-and-test/app-modes ·
/docs/graph-api/overview/access-levels · /docs/facebook-login/guides/access-tokens/get-long-lived.

## TikTok

- **Publicação direta (`video.publish`) é inviável** para o nosso caso: sem a auditoria só publica com
  visibilidade privada (`SELF_ONLY`, e a conta precisa estar privada), o Sandbox nunca publica em
  público, e as diretrizes de uso dizem que o cliente não pode ser "uma ferramenta para subir conteúdo
  para as contas que você ou sua equipe gerencia" (rejeições reais de 13/10 e 29/10/2026 citadas em
  issues do Postiz). Prazo de revisão: de dias a semanas, sem garantia.
- **Rascunho (`video.upload`):** o vídeo vai à caixa de entrada do TikTok e a pessoa toca na notificação,
  no celular, para concluir. Não há campo de legenda documentado nesse fluxo. Limite de 5 envios
  pendentes por 24 h. **NÃO CONFIRMADO** que funciona com conta pública (relato de que travou em
  `PROCESSING_UPLOAD`); em sandbox, só com a conta da igreja como usuário de teste.
- **Agendar:** a API não tem. O agendamento nativo existe no TikTok Studio (web), à mão.
- **Padrão do Spurgeon:** fluxo ASSISTIDO (sem chaves): copia a legenda (até 2.200 caracteres, com as
  hashtags), abre a pasta do arquivo e o TikTok Studio (`tiktok.com/tiktokstudio/upload`).
- **Rascunho pela API (opcional):** app em developers.tiktok.com com Login Kit + Content Posting API,
  plataforma Desktop, retorno **exatamente** `http://127.0.0.1:53682/callback/` (porta e barra final
  obrigatórias), PKCE com `code_challenge` = SHA-256 em **hexadecimal**; `init` → `PUT` em pedaços de
  5 a 64 MB (o último pode ter até 128 MB; `total_chunk_count = tamanho // chunk_size`) → `status/fetch`
  até `SEND_TO_USER_INBOX`. O refresh token **gira** a cada renovação: grave sempre o novo. Vida dos
  tokens: a página é contraditória (2 h/180 dias no texto, 24 h/365 dias nos exemplos); use `expires_in`.
- Vídeo: MP4/MOV/WebM, H.264/H.265, 23–60 fps, 360–4096 px, até 4 GB e 10 min pela API.

Fontes: developers.tiktok.com/doc/content-posting-api-get-started · …-reference-direct-post ·
…-reference-upload-video · …/content-sharing-guidelines · …/login-kit-desktop · …/add-a-sandbox.

## Intermediários: uma chave só (segundo caminho, opcional)

Serviços que já têm apps auditados e publicam nas quatro redes (preços de 02/11/2026; câmbio ~R$ 5,20):

| Serviço | Arquivo local? | Agenda | Preço (4 contas) | Observação |
|---|---|---|---|---|
| Upload-Post | Sim (multipart) | `scheduled_date` + `timezone` | Basic US$ 24/mês (grátis não tem TikTok) | Escolhido para implementar; diz ser "app auditado" (alegação deles) |
| Zernio (ex-Late) | Sim (`presign` + PUT) | `scheduledFor` + `timezone` | ~US$ 12/mês | Alternativa mais barata; empresa jovem |
| Ayrshare | Sim | `scheduleDate` | US$ 149/mês | Só se a igreja já for cliente |
| Postiz, Buffer, Publer, Metricool | — | — | — | Descartados (Buffer sem upload de arquivo; Postiz sem TikTok confirmado) |

Riscos: o vídeo passa por servidores de terceiros e os acessos às redes ficam com eles (contradiz o
"tudo offline", por isso é opt-in e o aplicativo avisa); os termos do Upload-Post proíbem repartir a
chave (cada igreja usa a própria). `UploadPost` **não foi testado com conta real**: confira com um Short de
teste (qual campo vira a legenda em cada rede, e se o TikTok sai em público).

## O que ainda precisa ser confirmado com contas reais (nada disso dá para testar sem credenciais)

1. YouTube: um envio de teste (`unlisted`) volta `private`? (a trava de projetos novos).
2. YouTube: `publishAt` agendado de fato publica na hora marcada?
3. Meta: um Reel publicado por app em Desenvolvimento aparece para quem não tem função no app?
4. Instagram: o limite diário é 50 ou 100?
5. TikTok: o envio para rascunhos funciona com a conta pública da igreja?
6. Upload-Post: legenda e hashtags saem certas em Instagram e TikTok? O TikTok sai em público?
