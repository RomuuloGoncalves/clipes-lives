# clipes-lives

Gerador gratuito e 100% CPU-only de clipes verticais (`1080x1920`, H.264/AAC) com legendas queimadas para lives e VODs (YouTube e Twitch), rodando integralmente em Docker.

## Requisitos

- Apenas **Docker** e **Docker Compose** (nada instalado no host, sem GPU, sem chaves de API ou login).

## Uso

Construir a imagem:

```sh
docker compose build
```

Processar um VOD ou live ativa. Use o `./clipar` (cria a pasta de saída como seu usuário; sem ele o Docker a cria como root, com cadeado). Saída padrão em `~/Downloads/clipes-lives/<AAAA-MM-DD - Título [id]>/`, personalizável via `OUT_DIR`:

```sh
# VOD (YouTube / Twitch)
./clipar "https://www.youtube.com/watch?v=UBv07VGeD24"

# Live ativa (grava N minutos do stream e processa o arquivo gravado)
./clipar "https://www.youtube.com/watch?v=cStJTQf9XUk" --live-minutes 4 --max-clips 2
```

O `./clipar` limita o container a 3 CPUs (`CLIPES_CPUS`, `CLIPES_CPU_THREADS`), roda com prioridade baixa e liga o `./vigia-temp`: ele grava a temperatura em `<saída>/.temp.log` a cada 5 s, pausa o container a 85 °C (`CLIPES_TEMP_MAX`) e retoma a 75 °C (`CLIPES_TEMP_MIN`). Para uso contínuo, `./clipar monitor` mantém o ciclo detectar, gravar e processar um job por vez; se parar, retoma da fila.

Opções da CLI:

```sh
docker compose run --rm clipes <url> \
  --max-clips 8 \
  --min-dur 25 \
  --max-dur 55 \
  --live-minutes 20 \
  --channel gaules \
  --reframe \
  --no-llm \
  --mode auto \
  --min-per-hour 3 \
  --asr-budget-ratio 0.30 \
  --chat   # ou --no-chat (lives e VODs sem chat degradam sem erro)
```

Detecção (`--mode`): `peaks` usa só os picos de áudio/chat/heatmap; `quota` transcreve rápido (Whisper small, beam 1) até `--asr-budget-ratio` da duração e escolhe os melhores trechos para garantir `--min-per-hour` clipes por hora; `auto` (padrão) usa picos e só aciona a cota quando os picos fortes não a preenchem. `--no-llm` desliga título/legenda/hashtags por IA local. O `clips.json` registra `detection_mode`, `floor_mode` e `quota_target`.

Variáveis de ambiente de renderização:

- `CLIPES_BLUR_BG` (padrão `0`): por padrão o vídeo 16:9 fica centralizado sobre barras pretas; `1` liga o fundo borrado (`split`/`boxblur`/`overlay`), que consome mais CPU.

Se um vídeo ou live contínua (ex.: só música) não gerar picos/clipes válidos, o pipeline encerra normalmente (código `0`), grava `clips.json` vazio com o motivo e imprime aviso claro.

Rodar a suíte de testes dentro do container:

```sh
docker compose run --rm test
```

## Saída

Em `${OUT_DIR:-~/Downloads/clipes-lives}/<AAAA-MM-DD - Título [id]>/` (reaproveita a pasta antiga só com o id, para não perder cache):

- `clip_01.mp4` ... `clip_NN.mp4` (1080x1920, vídeo centralizado sobre fundo desfocado + legendas `.ass`)
- `clips.json` (score, sinais que dispararam, início/fim, título, transcrição e métricas)
- `README.md` (resumo da execução)

## Bot do Discord e Publicação Privada

O monitor pode enviar prévias dos clipes gerados para um canal do Discord e o serviço `bot` aceita comandos para publicar como **privado** no YouTube Shorts (`privacyStatus="private"`) e no TikTok (`privacy_level="SELF_ONLY"`).

Todos os segredos ficam fora do repositório, em `~/.config/clipes-lives/segredos.env` (permissão `600`, lido automaticamente pelo `./clipar`) e `~/.config/clipes-lives/tokens.json` (montado em `/config` nos serviços `clipes` e `bot`).

### 1. Discord Developer Portal

1. Crie uma aplicação em `https://discord.com/developers/applications`.
2. Em **Bot**:
   - Gere o token e salve em `DISCORD_BOT_TOKEN` no `segredos.env`.
   - Em **Privileged Gateway Intents**, ative **Message Content Intent** e salve (necessário para o comando em texto `publicar <id>`; o slash `/publicar` funciona mesmo sem ele).
3. Em **OAuth2 → URL Generator**:
   - **Scopes**: marque `bot` e `applications.commands`.
   - **Bot Permissions**: marque `View Channels`, `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Attach Files`, `Embed Links`, `Read Message History`, `Add Reactions` e `Use Slash Commands`.
   - Abra a URL gerada e autorize o bot no seu servidor.
4. No `segredos.env` (nunca no repositório), defina os IDs obtidos no Discord (Modo Desenvolvedor):
   - `DISCORD_GUILD_ID=<ID_DO_SERVIDOR>`
   - `DISCORD_CHANNEL_ID=<ID_DO_CANAL>`
   - `DISCORD_ALLOWED_USER_IDS=<ID_USUARIO_1,ID_USUARIO_2>` (lista vazia bloqueia todas as publicações por segurança)
   - `CLIPES_DISCORD_MAX_MB=10` (opcional; prévia é recomprimida para 720x1280 se passar de 90% do limite, preservando o original 1080x1920)

### 2. Google Cloud (YouTube Data API v3)

1. No Google Cloud Console, habilite a **YouTube Data API v3**.
2. Em **OAuth consent screen**, configure o app e altere o *Publishing status* para **"In production"** (em *"Testing"*, o *refresh token* expira em 7 dias).
3. Em **Credentials → Create Credentials → OAuth client ID**, escolha **Desktop app** (app instalado) e salve `YOUTUBE_CLIENT_ID` e `YOUTUBE_CLIENT_SECRET` no `segredos.env`.
4. Cota diária local conservadora: `CLIPES_YT_MAX_DIA=6` (padrão; `videos.insert` consome ~1.600 das 10.000 unidades/dia padrão). Clipes acima de 180 s são recusados para Shorts.

### 3. TikTok for Developers (Content Posting API)

1. No portal `developers.tiktok.com`, adicione a **Content Posting API** (Direct Post) com escopo `video.publish` (além de `user.info.basic`).
2. Configure o redirect URI local `http://127.0.0.1:8765/callback` (ou porta de `CLIPES_OAUTH_PORT`).
3. Salve `TIKTOK_CLIENT_KEY` e `TIKTOK_CLIENT_SECRET` no `segredos.env` (cota local padrão: `CLIPES_TIKTOK_MAX_DIA=5`).
4. Enquanto o app não for auditado, a conta de destino no TikTok **precisa estar marcada como Conta Privada** nas configurações do app (`SELF_ONLY`); contas públicas falham com `unaudited_client_can_only_post_to_private_accounts`.

### 4. Autorização OAuth e execução do bot

Autorize cada plataforma uma vez (escuta em `127.0.0.1:8765` e grava `~/.config/clipes-lives/tokens.json` com modo `600`; tokens rotacionados do TikTok são regravados automaticamente):

```sh
set -a && . ~/.config/clipes-lives/segredos.env && set +a
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth youtube
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth tiktok
```

Subir o serviço contínuo do bot (um único processo por servidor Discord):

```sh
set -a && . ~/.config/clipes-lives/segredos.env && set +a
docker compose up -d bot
```

Comandos disponíveis no canal configurado e suas threads: `publicar <clip_id>`, `/publicar id:<clip_id>` e `/listar`.

### 5. Auditoria para publicação pública (futuro)

Nesta versão a privacidade é fixa no código (`private` / `SELF_ONLY`). Para liberar publicação pública após auditoria:

- **YouTube**: preencher o formulário *YouTube API Services – Audit and Quota Extension Form* (*Complete a compliance audit*) com descrição arquitetural e screencast do fluxo de upload acionado pelo usuário.
- **TikTok**: submeter revisão no portal com domínio verificado (Termos de Uso e Política de Privacidade), screencast completo do fluxo OAuth e da tela de publicação, e integração a `/v2/post/publish/creator_info/query/` exibindo opções de privacidade e consentimento conforme as diretrizes de UX da plataforma.

## Limpeza

Para remover container, imagem e volume de cache de modelos:

```sh
docker compose down --rmi all -v
```
