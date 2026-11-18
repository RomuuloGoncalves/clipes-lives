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

## Configuração fora do repositório (`segredos.env`)

Credenciais, tokens e URIs **nunca** entram no repositório. Crie `~/.config/clipes-lives/segredos.env` com permissão `600` (o `./clipar` carrega esse arquivo automaticamente e avisa se o modo não for `600`):

```sh
mkdir -p ~/.config/clipes-lives
chmod 700 ~/.config/clipes-lives
touch ~/.config/clipes-lives/segredos.env
chmod 600 ~/.config/clipes-lives/segredos.env
```

Exemplo fictício de variáveis aceitas no `segredos.env` (use apenas as que for utilizar):

```sh
# Fila multi-PC (opcional; sem ela cada PC usa fila local em disco)
CLIPES_MONGO_URI="mongodb+srv://workers:<SENHA_FORTE>@<CLUSTER_FICTICIO>.mongodb.net/clipes_lives"
CLIPES_ALIAS="PC-1"

# Discord e publicação privada (opcional)
DISCORD_BOT_TOKEN="<TOKEN_DO_BOT>"
DISCORD_GUILD_ID="<ID_DO_SERVIDOR>"
DISCORD_CHANNEL_ID="<ID_DO_CANAL>"
DISCORD_ALLOWED_USER_IDS="<ID_USUARIO_1>,<ID_USUARIO_2>"
YOUTUBE_CLIENT_ID="<GOOGLE_CLIENT_ID>"
YOUTUBE_CLIENT_SECRET="<GOOGLE_CLIENT_SECRET>"
TIKTOK_CLIENT_KEY="<TIKTOK_CLIENT_KEY>"
TIKTOK_CLIENT_SECRET="<TIKTOK_CLIENT_SECRET>"
```

O arquivo `~/.config/clipes-lives/tokens.json` (modo `600`) é montado em `/config` nos serviços `clipes` e `bot` do `docker-compose.yml` (personalizável via `CLIPES_CONFIG_DIR`).

## Operação Multi-PC, Painel na Vercel e Live Contínua

### 1. MongoDB Atlas M0 (coordenação sem vídeo)

O MongoDB coordena apenas quem grava cada live, o estado dos jobs, o heartbeat dos PCs e comandos do painel — nenhum vídeo passa pelo banco. Sem `CLIPES_MONGO_URI`, cada PC opera localmente com `DiskQueue`.

1. Crie um cluster gratuito **Atlas M0** com o banco `clipes_lives`.
2. Em **Network Access**, adicione `0.0.0.0/0` (necessário para PCs com IP residencial dinâmico e funções serverless da Vercel), protegendo o acesso com senhas fortes.
3. Em **Database Access**, crie dois usuários com permissão mínima:
   - `workers`: permissão `readWrite` apenas nas coleções `lives`, `jobs`, `workers` e `commands`.
   - `painel`: role customizada com leitura (`find`) em `lives`, `jobs`, `workers` e `insert`/`update` apenas na coleção `commands`.
4. No `segredos.env` de cada máquina, defina `CLIPES_MONGO_URI` com o usuário `workers` e um apelido distinto em `CLIPES_ALIAS` (ex.: `PC-1`, `PC-2`, `PC-3`).

> O Atlas M0 pausa automaticamente após 30 dias sem conexões; se o Mongo ficar fora do ar, os PCs seguem processando na fila local e tentam reconectar a cada 60 s.

### 2. Painel de monitoramento na Vercel (`painel/`)

1. Importe o repositório na Vercel definindo o **Root Directory** como `painel`.
2. Configure as variáveis de ambiente do projeto na Vercel:
   - `PAINEL_SENHA`: senha forte usada no Basic Auth.
   - `PAINEL_MONGO_URI`: connection string do usuário `painel` (menor privilégio).
   - Opcionais: `PAINEL_MONGO_DB` (padrão `clipes_lives`) e `CLIPES_BEAT_S` (padrão `20`).
3. O painel atualiza a cada 10 s mostrando status online/offline, CPU, RAM, disco livre, temperatura (`n/d` sem sensor), GPU, job atual, `max_lives` e o botão **liberar recursos** (exige confirmação e digitar `LIBERAR`, encerrando subprocessos e saindo com código `75` para o `./clipar` reiniciar o ciclo após `CLIPES_RESTART_S=60`).

### 3. Windows (Docker Desktop + WSL2)

Nos PCs com Windows (ex.: `PC-2` e `PC-3`):

1. Instale o **Docker Desktop** usando o backend **WSL2**.
2. Crie `%UserProfile%\.wslconfig` para limitar RAM e núcleos da VM (ajuste ao hardware da máquina) e reinicie o WSL com `wsl --shutdown`:

```ini
[wsl2]
memory=10GB
processors=12
```

3. Execute o `./clipar` a partir de uma distro Linux dentro do WSL2, mantendo `OUT_DIR` dentro do sistema de arquivos da distro (evite `/mnt/c` pelo impacto em I/O).
4. O contêiner no WSL2 não enxerga sensores térmicos do host Windows: o `./clipar` imprime o aviso de `sem-sensor` e continua sem alterar limites, e o painel exibe `n/d` na temperatura.

### 4. Calibração (`bench-live`) e live contínua (`--ate-acabar`)

Antes de permitir mais de 1 live simultânea (`max_lives`) em um PC, meça a razão de processamento em uma pasta de job já gravada com `source.mkv` de pelo menos 300 s:

```sh
docker compose run --rm clipes bench-live /out/queue/done/<job_id>
```

O comando grava `OUT_DIR/.perfil.json` (`razao`, `cpus`) e calcula `max_lives = max(1, floor(0.6 / razao))`. Sem o arquivo (ou se `cpus` mudar), o limite permanece `1`.

Para clipar uma live em janelas alinhadas de 10 min a cada 5 min até ela acabar (respeitando `CLIPES_MIN_FREE_GB=5` de disco livre):

```sh
./clipar "https://www.twitch.tv/<canal>" --ate-acabar
./clipar monitor --follow <canal>
```

### 5. Consultas de diagnóstico e purga (MongoDB Compass / `mongosh`)

Para rodar manualmente no `mongosh` do MongoDB Compass (nunca automatize acesso ao banco em scripts ou agentes):

```js
// Diagnóstico: PCs, lives ativas, contagem de jobs (24 h) e comandos pendentes
db.workers.find({}, { alias: 1, heartbeat_at: 1, job_atual: 1, max_lives: 1, "capacity.temp_c": 1, "capacity.disco_livre_gb": 1 }).sort({ heartbeat_at: -1 });
db.lives.find({ status: "live" }).sort({ lease_until: 1 });
db.jobs.aggregate([{ $match: { updated_at: { $gte: new Date(Date.now() - 86400000) } } }, { $group: { _id: "$status", total: { $sum: 1 } } }]);
db.commands.find({ status: { $in: ["pending", "executando"] } }).sort({ criado_em: -1 });

// Purga: conferir e remover jobs/comandos finalizados há mais de 30 dias (limite de 0,5 GB do M0)
db.jobs.countDocuments({ status: { $in: ["done", "rejected", "lost", "failed"] }, updated_at: { $lt: new Date(Date.now() - 30 * 86400000) } });
db.jobs.deleteMany({ status: { $in: ["done", "rejected", "lost", "failed"] }, updated_at: { $lt: new Date(Date.now() - 30 * 86400000) } });
db.commands.deleteMany({ status: { $in: ["feito", "rejeitado"] }, criado_em: { $lt: new Date(Date.now() - 30 * 86400000) } });
```

## Bot do Discord e Publicação Privada

O monitor pode enviar prévias dos clipes gerados para um canal do Discord e o serviço `bot` aceita comandos para publicar como **privado** no YouTube Shorts (`privacyStatus="private"`) e no TikTok (`privacy_level="SELF_ONLY"`), usando as variáveis definidas em `segredos.env` e os tokens gravados em `/config/tokens.json`.

### 1. Discord Developer Portal

1. Crie uma aplicação em `https://discord.com/developers/applications`.
2. Em **Bot**:
   - Gere o token e salve em `DISCORD_BOT_TOKEN` no `segredos.env`.
   - Em **Privileged Gateway Intents**, ative **Message Content Intent** e salve (necessário para o comando em texto `publicar <id>`; o slash `/publicar` funciona mesmo sem ele).
3. Em **OAuth2 → URL Generator**:
   - **Scopes**: marque `bot` e `applications.commands`.
   - **Bot Permissions**: marque `View Channels`, `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Attach Files`, `Embed Links`, `Read Message History`, `Add Reactions` e `Use Slash Commands`.
   - Abra a URL gerada e autorize o bot no seu servidor.
4. No `segredos.env` (obtidos pelo Modo Desenvolvedor do Discord):
   - `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` e `DISCORD_ALLOWED_USER_IDS` (lista vazia bloqueia todas as publicações por segurança).
   - `CLIPES_DISCORD_MAX_MB=10` (opcional; prévia é recomprimida para 720x1280 se passar de 90% do limite, preservando o original 1080x1920).

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

Autorize cada plataforma uma vez (escuta em `127.0.0.1:8765` por padrão e grava `~/.config/clipes-lives/tokens.json` com modo `600`; tokens rotacionados do TikTok são regravados automaticamente). Dentro do contêiner, passe `--bind 0.0.0.0` (ou `CLIPES_OAUTH_BIND=0.0.0.0`) para o `-p 127.0.0.1:8765:8765` encaminhar o callback do navegador sem expor a porta fora do `127.0.0.1` no host:

```sh
set -a && . ~/.config/clipes-lives/segredos.env && set +a
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth youtube --bind 0.0.0.0
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth tiktok --bind 0.0.0.0
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
