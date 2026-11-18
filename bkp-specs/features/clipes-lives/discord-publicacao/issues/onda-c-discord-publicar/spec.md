# Onda C: bot do Discord e comando "publicar" para TikTok e YouTube Shorts

## Problem Statement

Os clipes ficam em pastas no PC que os gerou; para revisar é preciso abrir a pasta, e para publicar é preciso subir à mão no TikTok e no YouTube. O usuário quer ver a prévia de cada clipe num canal do Discord (servidor `1572417049932599479`, canal `1572417050771595386`) e publicar com um comando, `publicar <id>`. Esta onda depende do `clip_id` e da descrição longa da Onda A e da coordenação da Onda B. Isso reverte a AD-006 (publicação fora de escopo). Os dois apps (TikTok e Google) ficam em modo privado até passarem na auditoria.

## Goals

- [ ] Todo job concluído gera 1 thread no canal com 1 mensagem por clipe (arquivo <= limite de upload, título, `id`, descrição).
- [ ] `publicar <id>` sobe o clipe como PRIVADO no YouTube e no TikTok e responde na thread com os links/IDs, sem publicar duas vezes o mesmo clipe na mesma plataforma.
- [ ] Nenhum token (bot, Google, TikTok) em arquivo versionado, log ou mensagem do Discord.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Publicar como público | Bloqueado até a auditoria dos apps; mudar exige nova decisão AD |
| Agendar publicação, publicar em lote | Um clipe por comando basta agora |
| Instagram Reels, Kwai | Não pedidos |
| Editar o clipe pelo Discord (cortar, trocar título) | Fora do pedido |
| Criar os apps nos portais (Discord, Google Cloud, TikTok for Developers) | Passo manual do usuário via Chrome, com checklist no README |
| Publicar durante o desenvolvimento | Restrição dura: testes só com HTTP falso; publicação real só pelo usuário |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| De onde vem o comando | Mensagem `publicar <id>` no canal ou nas threads dele (exige o intent privilegiado Message Content) e slash `/publicar id:<id>`; `/listar` mostra os 10 últimos `clip_id` | Pedido literal + relatório A seção 5.3; o slash funciona mesmo sem o intent | n |
| Quem pode publicar | Só IDs em `DISCORD_ALLOWED_USER_IDS`; vazio = ninguém publica (falha segura) | Pergunta do relatório A 8.3 com default restritivo | n |
| Biblioteca do bot | `discord.py` (gateway) para comandos; prévia enviada por REST (`urllib`) pelo PC dono do arquivo | Comando precisa de gateway; envio de arquivo não | n |
| Bot com vários PCs | Um único processo `clipes bot` (no PC escolhido em `CLIPES_BOT_HOST`) recebe comandos e grava o pedido na coleção `publish_requests`; o worker do PC dono do clipe executa | O arquivo só existe no PC que renderizou (Onda B) | n |
| Sem Mongo (1 PC) | Bot executa o pedido direto no mesmo processo | Funciona antes da Onda B estar ligada | n |
| Limite de upload do Discord | `CLIPES_DISCORD_MAX_MB=10` e alvo de 90% disso | Relatório A: limite gratuito mudou (10 MB em 2024, possivelmente 20 MiB depois); 10 é o piso seguro, ajustável | n |
| Recompressão | Só se o original passar do alvo: `720x1280`, `libx264 -preset veryfast`, vídeo = `clamp(limite_bytes*0,90*8/1000/duração - 96, 400, 2500)` kbps, `-maxrate 1,2x`, áudio AAC 96k, em `cache/discord_<stem>.mp4` | Relatório A seção 5.2; 1 passada basta com folga de 10% | n |
| ID do clipe | `clip_id = <video_id>_c<NN>` gravado pela Onda A | Default do PEDIDO ("ids estáveis") | y |
| YouTube | Data API v3, upload resumível por REST (`urllib`, sem SDK), `privacyStatus=private`, `selfDeclaredMadeForKids=false`, `categoryId=20`, título `[:90] + " #Shorts"`, descrição longa `[:5000]`; tela de consentimento OAuth em "In production" (em "Testing" o refresh token expira em 7 dias) | Projeto não auditado fica travado em privado; Shorts = vertical <= 180 s | n |
| TikTok | Content Posting API v2: `creator_info/query` antes de postar, Direct Post `FILE_UPLOAD` em 1 chunk (<= 64 MB), `privacy_level=SELF_ONLY`, título = legenda curta `[:2200]`; a conta do TikTok precisa estar como conta privada | App não auditado só posta `SELF_ONLY` e só em conta privada; até 5 usuários/24 h | n |
| Cotas | Contador diário local por plataforma (`CLIPES_YT_MAX_DIA=6`, `CLIPES_TIKTOK_MAX_DIA=5`) | YouTube: 10.000 unidades/dia, `videos.insert` historicamente 1.600 → 6/dia (piso conservador até o usuário ver o painel de cotas); TikTok: limite do criador vem do `creator_info` | n |
| Tokens OAuth | Obtidos 1x pelo usuário com `clipes auth youtube` / `clipes auth tiktok` (porta local) e gravados em `~/.config/clipes-lives/tokens.json` (modo 600, montado em `/config` no contêiner); refresh token novo devolvido pelo TikTok é regravado a cada renovação | TikTok rotaciona o refresh token; segredo fora do git e da imagem | n |
| Plataformas habilitadas | Publica em cada plataforma que tiver token; a outra é reportada `desligada` | Relatório A 8.4 | n |
| Política de canal | `publicar` respeita `policy.permits(canal)` e `ready_at` (`delay_h`, ex.: 24 h) | Restrição dura: só canais autorizados | n |

**Open questions:** none - all resolved or logged above (required before the spec is confirmed).

---

## User Stories

### P1: Prévia dos clipes no Discord ⭐ MVP

**User Story**: Como dono do canal, quero ver cada clipe novo numa thread do Discord para decidir o que publicar pelo celular.

**Why P1**: É onde o usuário revisa e de onde sai o comando de publicar.

**Acceptance Criteria**:

1. WHEN um job terminar com `status=done` e `DISCORD_BOT_TOKEN` estiver definido THEN o sistema SHALL postar no canal `DISCORD_CHANNEL_ID` uma mensagem-cabeçalho (canal, título da live, número de clipes, URL) e criar a partir dela uma thread pública `<canal> · <título[:70]>` com arquivamento em 1440 min.
2. WHEN a thread existir THEN o sistema SHALL postar 1 mensagem por clipe com o arquivo, `clip_id`, título, gancho de topo, nota e fonte da IA, legenda curta, descrição longa, situação na `policy.json` e a instrução `publicar <clip_id>`.
3. IF o `.mp4` passar de 90% de `CLIPES_DISCORD_MAX_MB` THEN o sistema SHALL anexar uma prévia recomprimida para 720x1280 com tamanho <= 90% do limite, sem alterar o original 1080x1920.
4. IF a prévia não couber no limite mesmo com 400 kbps de vídeo THEN o sistema SHALL postar a mensagem sem anexo, com o texto `arquivo grande demais para o Discord` e o caminho local.
5. IF a API do Discord responder 429 THEN o sistema SHALL esperar o `retry_after` informado e tentar de novo até 3 vezes; depois disso SHALL registrar a falha no job (`discord_error`) sem derrubar o worker.
6. The system SHALL gravar no `job`/`clips.json` o `thread_id` e o `message_id` de cada clipe, e não repostar um clipe que já tenha `message_id`.
7. WHERE `DISCORD_BOT_TOKEN` não estiver definido the system SHALL pular o envio sem erro.

**Independent Test**: Com HTTP falso, um job com 2 clipes (um de 25 MB) gera 1 criação de thread e 2 uploads, o segundo com arquivo recomprimido de tamanho <= 9,5 MB.

---

### P1: Comando publicar ⭐ MVP

**User Story**: Como dono do canal, quero digitar `/publicar id:<id>` e ter o clipe enviado ao TikTok e ao YouTube Shorts como privado.

**Why P1**: Pedido item 3.

**Acceptance Criteria**:

1. WHEN `/publicar` ou a mensagem `publicar <id>` vier de um usuário fora de `DISCORD_ALLOWED_USER_IDS` (ou com a lista vazia) THEN o sistema SHALL responder `sem permissao` (efêmero no slash) e não registrar pedido.
2. IF o `clip_id` não existir em nenhum `clips.json`/`jobs` conhecido THEN o sistema SHALL responder `clipe <id> nao encontrado`.
3. IF o canal do clipe não for permitido por `policy.json` ou `agora < ready_at` THEN o sistema SHALL responder `bloqueado pela politica` ou `aguardar ate <data/hora>` e não registrar pedido.
4. WHEN o pedido for válido THEN o sistema SHALL registrar `publish_requests` com chave única `(clip_id, plataforma)` e responder `publicacao enfileirada` na thread do clipe.
5. IF já existir publicação `ok` para `(clip_id, plataforma)` THEN o sistema SHALL responder com o link existente e não chamar a API de novo.
6. WHEN o worker dono do clipe executar o pedido do YouTube THEN o sistema SHALL fazer upload resumível com `privacyStatus=private`, título <= 100 caracteres, descrição longa + `#Shorts`, e gravar `video_id` e `status=ok`.
7. WHEN o worker executar o pedido do TikTok THEN o sistema SHALL consultar `creator_info/query`, iniciar Direct Post com `privacy_level=SELF_ONLY` e `source=FILE_UPLOAD`, enviar o arquivo com `Content-Range`, consultar o status até `PUBLISH_COMPLETE` ou `FAILED` (no máximo 60 consultas, 5 s entre elas) e gravar `publish_id` e o resultado.
8. IF o `creator_info` não listar `SELF_ONLY` ou a API responder `unaudited_client_can_only_post_to_private_accounts` THEN o sistema SHALL marcar `status=erro_conta` e responder `deixe a conta do TikTok como privada`.
9. The system SHALL usar `privacyStatus=private` (YouTube) e `SELF_ONLY` (TikTok) como valores fixos no código desta onda, sem variável que os troque.
10. IF a cota diária local da plataforma tiver sido atingida THEN o sistema SHALL responder `cota diaria atingida (<plataforma>)` e marcar o pedido `status=adiado` sem chamar a API.
11. IF a API responder erro de token (401) THEN o sistema SHALL tentar renovar o access token com o refresh token uma vez, regravando em `tokens.json` o refresh token novo quando vier um; se falhar SHALL marcar `status=erro_auth` e responder `refaca clipes auth <plataforma>`.
12. WHEN a publicação terminar (ok ou erro) THEN o sistema SHALL responder na thread do clipe com plataforma, status e ID/link, sem incluir token ou URL de upload.

**Independent Test**: Com servidores HTTP falsos, `/publicar id:abc-01` por usuário autorizado gera 1 upload privado em cada plataforma; repetir o comando não gera novo upload; usuário não autorizado recebe `sem permissao`.

---

### P2: Autorização OAuth guiada

**User Story**: Como dono dos apps, quero um comando que abra o login do Google/TikTok e salve o refresh token fora do repositório.

**Why P2**: Necessário uma vez por conta; dá para fazer à mão no início.

**Acceptance Criteria**:

1. WHEN `clipes auth youtube` ou `clipes auth tiktok` rodar THEN o sistema SHALL imprimir a URL de autorização com `state` aleatório e escutar o retorno em `127.0.0.1:<porta>`.
2. IF o `state` do retorno não casar THEN o sistema SHALL abortar sem trocar o código por token.
3. WHEN a troca do código der certo THEN o sistema SHALL gravar o refresh token em `tokens.json` (`/config`, modo `600`, escrita atômica) e imprimir só `token salvo`.

**Independent Test**: Com servidor OAuth falso, `state` errado aborta; certo grava o arquivo com modo 600.

---

## Edge Cases

- IF o PC dono do clipe estiver offline THEN o sistema SHALL manter o pedido `pending` e o bot SHALL responder `PC <host> offline; pedido fica na fila`.
- IF o `.mp4` tiver sido apagado THEN o sistema SHALL marcar o pedido `status=erro_arquivo` e avisar na thread.
- WHEN o clipe tiver mais de 180 s THEN o sistema SHALL recusar a publicação no YouTube Shorts com `clipe acima de 3 min`.
- IF o upload do YouTube cair no meio THEN o sistema SHALL retomar pela URL de sessão resumível até 3 vezes antes de marcar erro.
- IF o título contiver `<` ou `>` THEN o sistema SHALL removê-los antes do YouTube (a API rejeita).

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| DISC-01 | P1: Prévia (AC 1, 2, 6, 7) thread e mensagens | Tasks | Pending |
| DISC-02 | P1: Prévia (AC 3, 4) recompressão | Tasks | Pending |
| DISC-03 | P1: Prévia (AC 5) rate limit | Tasks | Pending |
| PUB-01 | P1: Publicar (AC 1 a 5, 10) comando, permissão, política, idempotência e cota | Tasks | Pending |
| PUB-02 | P1: Publicar (AC 6, 9) YouTube privado | Tasks | Pending |
| PUB-03 | P1: Publicar (AC 7, 8, 9) TikTok SELF_ONLY | Tasks | Pending |
| PUB-04 | P1: Publicar (AC 11, 12) renovação de token e resposta | Tasks | Pending |
| AUTH-01 | P2: OAuth guiado | Tasks | Pending |

**Coverage:** 8 total, 8 mapped to tasks, 0 unmapped.

---

## Success Criteria

- [ ] Suite com HTTP falso cobre 100% dos ACs acima; nenhum teste chama rede real.
- [ ] Primeiro teste real feito pelo usuário (não por agente): 1 clipe publicado privado em cada plataforma, visível só na conta dele.
