# clipes-lives

Gerador gratuito e 100% CPU-only de clipes verticais (`1080x1920`, H.264/AAC) com legendas queimadas para lives e VODs (YouTube e Twitch), rodando integralmente em Docker.

## Requisitos

- Apenas **Docker** e **Docker Compose** (nada instalado no host, sem GPU, sem chaves de API ou login).

## Início rápido (Docker)

Em cada PC (Linux; no Windows use uma distro WSL2, ver "Windows" abaixo):

```sh
git clone https://github.com/RomuuloGoncalves/clipes-lives.git && cd clipes-lives
git checkout onda-ab                 # até o merge no master
docker compose build                 # constrói a imagem (1x, ou após git pull)

# credenciais fora do repositório (ver "Configuração fora do repositório")
mkdir -p ~/.config/clipes-lives && chmod 700 ~/.config/clipes-lives
touch .env && chmod 600 .env && nano .env
```

Rodar:

```sh
./clipar "<url do VOD>"                      # um VOD
./clipar "https://www.twitch.tv/<canal>" --ate-acabar   # live inteira, em janelas, até acabar
./clipar monitor --follow <canal>            # monitor contínuo de um canal
docker compose up -d bot                     # bot do Discord (1 só processo por servidor)
```

Operação:

```sh
docker compose ps                    # o que está rodando
docker compose logs -f bot           # logs do bot
docker compose down                  # parar tudo
docker ps -q | xargs -r docker stop  # parar qualquer contêiner
docker network prune                 # se o compose falhar por falta de sub-redes
docker compose run --rm test         # testes
```

O valor de `CLIPES_MONGO_URI` e dos tokens é pessoal: peça ao dono do projeto por um canal privado e nunca o publique em chat, issue ou commit. O painel de status está em https://clipes-painel.vercel.app.

## Uso

Construir a imagem:

```sh
docker compose build
```

Processar um VOD ou live ativa. Use o `./clipar` (cria a pasta de saída como seu usuário; sem ele o Docker a cria como root, com cadeado). Saída padrão em `./out/<AAAA-MM-DD - Título [id]>/`, personalizável via `OUT_DIR`:

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
- Barra fixa de marca no topo (`1080xH`):
  - `CLIPES_BAR`: liga automaticamente quando `CLIPES_BRAND_NAME` está definido; use `0` para desligar ou `1` para ligar só com o título (sem nome/handle).
  - `CLIPES_BRAND_NAME`: `@handle` do canal (ex.: `@cortes_lr1`; o `@` é adicionado se faltar).
  - `CLIPES_BRAND_DISPLAY`: nome de exibição em negrito ao lado do avatar (ex.: `CortesLR`); se vazio, mostra só o handle.
  - `CLIPES_BRAND_LOGO`: caminho do logo **dentro do contêiner** (padrão `/app/assets/logo.jpg`; para usar outro arquivo, coloque em `~/.config/clipes-lives/` e aponte para `/config/<arquivo>`). Se o arquivo não existir, a barra renderiza apenas o texto.
  - `CLIPES_BAR_STYLE`: `claro` (padrão; fundo branco, texto preto, handle cinza) ou `escuro` (fundo preto, texto branco, handle cinza-claro).
  - `CLIPES_BAR_H`: altura da barra em pixels (padrão `420`, aceito de `300` a `600`).
- Layout dividido (`CLIPES_LAYOUT`): empilha barra opcional, recorte da webcam e recorte do jogo em `1080x1920`, com a legenda falada centralizada na emenda (`barra + cam`) e crédito no rodapé (`y=1860`). Quando nenhuma webcam de overlay é detectada (ou a fonte já é vertical), o render cai para o layout `full` sem erro.
  - `CLIPES_LAYOUT`: `split-barra` (padrão quando vazio), `split-cheio`, `split-fina` ou `full` (mantém o layout tradicional sem detecção de webcam).
  - Variantes e orçamento de altura (`barra + webcam + jogo = 1920`):
    - `split-barra` (padrão): `340 + 520 + 1060` px. Barra branca com logo, nome, handle e título fixo; sem gancho sobre a webcam.
    - `split-cheio`: `0 + 768 + 1152` px. Sem barra; logo e nome com contorno sobre a webcam, e gancho de `0` a `5,4 s` sobre a webcam escurecida.
    - `split-fina`: `170 + 700 + 1050` px. Barra escura (`#14141C`) com logo, nome e handle, e gancho de `0` a `5,4 s` sobre a webcam escurecida.
  - `CLIPES_SPLIT_BAR_H` e `CLIPES_SPLIT_CAM_H`: sobrescrevem as alturas de barra e webcam; o jogo fica com o restante (`1920 - barra - cam`). Se a webcam ficar `< 400` px ou o jogo `< 900` px, o render ignora o ajuste e volta às alturas padrão da variante.
  - `CLIPES_FACECAM_TTL_H`: validade em horas do cache local da detecção em `OUT_DIR/.facecam/<chave>.json` (padrão `12`). O cache é revalidado por clipe e invalidado se a resolução da fonte mudar.
  - Override manual em `~/.config/clipes-lives/facecam.json` (montado em `/config/facecam.json`): define `[x, y, w, h]` normalizados (`0` a `1`) por canal ou `"nenhuma"` para forçar `full`, sobrescrevendo a detecção local. Exemplo para o Alanzoka (`x=10..430`, `y=8..320` em `1920x1080`):
    ```json
    {
      "alanzoka": [0.0052, 0.0074, 0.2188, 0.2889],
      "canal_sem_cam": "nenhuma"
    }
    ```
  - Smoke manual: rode um VOD com webcam nas três variantes e confira no `clips.json` se cada clipe registrou `layout: "split_webcam"`, `layout_fallback: false` e `facecam`:
    ```sh
    CLIPES_LAYOUT=split-barra ./clipar "<url do VOD com webcam>" --max-clips 1
    CLIPES_LAYOUT=split-cheio ./clipar "<url do VOD com webcam>" --max-clips 1
    CLIPES_LAYOUT=split-fina  ./clipar "<url do VOD com webcam>" --max-clips 1
    ```

Se um vídeo ou live contínua (ex.: só música) não gerar picos/clipes válidos, o pipeline encerra normalmente (código `0`), grava `clips.json` vazio com o motivo e imprime aviso claro.

Rodar a suíte de testes dentro do container:

```sh
docker compose run --rm test
```

## Saída

Em `${OUT_DIR:-./out}/<AAAA-MM-DD - Título [id]>/` (reaproveita a pasta antiga só com o id, para não perder cache):

- `clip_01.mp4` ... `clip_NN.mp4` (1080x1920, vídeo centralizado sobre fundo desfocado + legendas `.ass`)
- `clips.json` (score, sinais que dispararam, início/fim, título, transcrição e métricas)
- `README.md` (resumo da execução)

## Configuração (`.env` na raiz do projeto)

Todas as credenciais, tokens e URIs ficam em **um só arquivo**: `.env` na raiz (já está no `.gitignore`; nunca o commite). O `./clipar` carrega esse arquivo, o `docker compose` também o lê sozinho e avisa se o modo não for `600`. Para usar outro caminho, defina `CLIPES_SECRETS`.

```sh
touch .env && chmod 600 .env
```

Exemplo fictício de variáveis aceitas no `.env` (use apenas as que for utilizar):

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
4. No `.env` de cada máquina, defina `CLIPES_MONGO_URI` com o usuário `workers` e um apelido distinto em `CLIPES_ALIAS` (ex.: `PC-1`, `PC-2`, `PC-3`).

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

Antes de permitir mais de 1 live simultânea (`max_lives`) em um PC, grave uma amostra de live de 6 minutos (mínimo 300 s) e rode o `bench-live`:

```sh
./clipar "https://www.twitch.tv/<canal>" --live-minutes 6
mkdir -p out/_bench
cp "out/<pasta_da_live>/.cache/live_source.mkv" out/_bench/source.mkv
./clipar bench-live /out/_bench
```

O comando grava `OUT_DIR/.perfil.json` (`razao`, `cpus`) e calcula `max_lives = max(1, floor(0.6 / razao))`. Sem o arquivo (ou se `cpus` mudar), o limite permanece `1`.

Para clipar uma live em janelas alinhadas de 10 min a cada 5 min até ela acabar (respeitando `CLIPES_MIN_FREE_GB=5` de disco livre):

```sh
./clipar "https://www.twitch.tv/<canal>" --ate-acabar
./clipar monitor --follow <canal>
```

#### Multi-live (`acompanhar`)

Para acompanhar duas ou mais lives (Twitch e YouTube) no mesmo processo, com uma fila única e um worker sequencial:

```sh
./clipar acompanhar gaules "https://www.youtube.com/@canal/live"
```

Formatos aceitos: `<canal>`, `twitch.tv/<canal>`, `youtube.com/@<handle>/live`, `youtube.com/watch?v=<id>` e `youtu.be/<id>`. Alvos repetidos são unificados e alvos offline no início são ignorados.

- **Limite simultâneo (`CLIPES_MAX_LIVES`)**: se `CLIPES_MAX_LIVES` for um inteiro `>= 1`, ele define o máximo de lives gravando ao mesmo tempo; caso contrário vale o resultado de `bench-live` em `OUT_DIR/.perfil.json` (ou `1` sem medição).
- **Recusa visível e fila de espera**: lives acima do limite (ou com lease ocupada por outro PC no Mongo) ficam em `aguardando` na ordem dos argumentos e começam assim que uma vaga abre. Cada recusa aparece no log (`[acompanhar] recusada ...`) e é enviada uma vez para o `DISCORD_CHANNEL_ID`. Janelas descartadas por backlog também avisam no Discord.
- **Disco por live**: cada gravador mantém cerca de 20 minutos de buffer em `queue/.buffer/` (~900 MB por live a 6 Mb/s), respeitando o teto global `BUFFER_CAP_BYTES` (~1,5 GB). `CLIPES_MIN_FREE_GB` evita fechar novas janelas quando o disco aperta, mas não limita o buffer em andamento.
- **Lease no MongoDB (2 PCs)**: o worker mantém `beat`/`renew` ativos em segundo plano durante o processamento de cada job, mas avisos síncronos ao Discord (`post_aviso`, timeout até ~60 s) pausam o laço principal; mantenha `CLIPES_LEASE_S` >= `90` (padrão).
- **YouTube (experimental)**: a gravação de lives do YouTube usa `yt-dlp -f "b[height<=1080]/b" --hls-use-mpegts -o -` sem chat. Se alguma live não entregar formato muxado via `yt-dlp`, o plano B é testar `streamlink <url> best`.
- **Smoke manual**: rode 1 live da Twitch + 1 live do YouTube com `CLIPES_MAX_LIVES=2` por ~15 minutos (`./clipar acompanhar <twitch> <youtube>`) e confira nos logs e na fila que janelas das duas lives são geradas sem dois jobs simultâneos em `processing/`.

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

O monitor pode enviar prévias dos clipes gerados para um canal do Discord e o serviço `bot` aceita comandos para publicar como **privado** no YouTube Shorts (`privacyStatus="private"`) e no TikTok (`privacy_level="SELF_ONLY"`), usando as variáveis definidas em `.env` e os tokens gravados em `/config/tokens.json`.

### 1. Discord Developer Portal

1. Crie uma aplicação em `https://discord.com/developers/applications`.
2. Em **Bot**:
   - Gere o token e salve em `DISCORD_BOT_TOKEN` no `.env`.
   - Em **Privileged Gateway Intents**, ative **Message Content Intent** e salve (necessário para o comando em texto `publicar <id>`; o slash `/publicar` funciona mesmo sem ele).
3. Em **OAuth2 → URL Generator**:
   - **Scopes**: marque `bot` e `applications.commands`.
   - **Bot Permissions**: marque `View Channels`, `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Attach Files`, `Embed Links`, `Read Message History`, `Add Reactions` e `Use Slash Commands`.
   - Abra a URL gerada e autorize o bot no seu servidor.
4. No `.env` (obtidos pelo Modo Desenvolvedor do Discord):
   - `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` e `DISCORD_ALLOWED_USER_IDS` (lista vazia bloqueia todas as publicações; `*` libera qualquer usuário que enxergue o canal).
   - `CLIPES_DISCORD_MAX_MB=20` (opcional; no modo padrão `discord`, o master já mira esse teto em uma única codificação, caindo para 720x1280 se o bitrate ficar abaixo de 1500 kbps).

### 2. Google Cloud (YouTube Data API v3)

1. No Google Cloud Console, habilite a **YouTube Data API v3**.
2. Em **OAuth consent screen**, configure o app e altere o *Publishing status* para **"In production"** (em *"Testing"*, o *refresh token* expira em 7 dias).
3. Em **Credentials → Create Credentials → OAuth client ID**, escolha **Desktop app** (app instalado) e salve `YOUTUBE_CLIENT_ID` e `YOUTUBE_CLIENT_SECRET` no `.env`.
4. Cota diária local conservadora: `CLIPES_YT_MAX_DIA=6` (padrão; `videos.insert` consome ~1.600 das 10.000 unidades/dia padrão). Clipes acima de 180 s são recusados para Shorts. O contador local (`OUT_DIR/.cotas_publicacao.json`) é **por PC** (não global entre PCs) e zera pelo dia **UTC** (a cota oficial do YouTube zera à meia-noite no fuso do Pacífico).

### 3. TikTok for Developers (Content Posting API)

1. No portal `developers.tiktok.com`, adicione a **Content Posting API** (Direct Post) com escopo `video.publish` (além de `user.info.basic`).
2. Configure o redirect URI local `http://127.0.0.1:8765/callback` (ou porta de `CLIPES_OAUTH_PORT`).
3. Salve `TIKTOK_CLIENT_KEY` e `TIKTOK_CLIENT_SECRET` no `.env` (cota local padrão: `CLIPES_TIKTOK_MAX_DIA=5`).
4. Enquanto o app não for auditado, a conta de destino no TikTok **precisa estar marcada como Conta Privada** nas configurações do app (`SELF_ONLY`); contas públicas falham com `unaudited_client_can_only_post_to_private_accounts`.

### 4. Autorização OAuth e execução do bot

Autorize cada plataforma uma vez (escuta em `127.0.0.1:8765` por padrão e grava `~/.config/clipes-lives/tokens.json` com modo `600`; tokens rotacionados do TikTok são regravados automaticamente). Dentro do contêiner, passe `--bind 0.0.0.0` (ou `CLIPES_OAUTH_BIND=0.0.0.0`) para o `-p 127.0.0.1:8765:8765` encaminhar o callback do navegador sem expor a porta fora do `127.0.0.1` no host:

```sh
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth youtube --bind 0.0.0.0
docker compose run --rm -p 127.0.0.1:8765:8765 clipes auth tiktok --bind 0.0.0.0
```

Subir o serviço contínuo do bot (um único processo por servidor Discord; exige `DISCORD_BOT_TOKEN` definido, caso contrário sai com código 2 em reinício contínuo):

```sh
docker compose up -d bot
```

Comandos disponíveis apenas no canal configurado (`DISCORD_CHANNEL_ID`), suas threads e para IDs em `DISCORD_ALLOWED_USER_IDS`: `publicar <clip_id>` (ou `publicar <clip_id> agora`), `/publicar id:<clip_id>` (opcional `agora:True`), `/listar`, `/buscar texto:<palavras>` e `buscar <palavras>`.

- **Publicar por destino:** cada mensagem de clipe enviada ao Discord traz 3 botões — **YouTube** (`publicar:youtube:<clip_id>`), **TikTok** (`publicar:tiktok:<clip_id>`) e **Ambos** (`publicar:ambos:<clip_id>`). Somente usuários em `DISCORD_ALLOWED_USER_IDS` no canal configurado (`DISCORD_CHANNEL_ID`) ou suas threads podem usá-los. Cada plataforma aceita acrescenta `<Plataforma> por <usuário>` à mensagem; plataformas sem token retornam `<plat>: desligada` e não são marcadas. Os botões permanecem na mensagem até que YouTube e TikTok tenham sido pedidos (via **Ambos** ou pelos dois cliques em sequência); botões legados `aprovar:<clip_id>` continuam funcionando como **Ambos**.

- **Agenda de publicação:** no `docker-compose.yml`, `CLIPES_AGENDA_HORARIOS` vem com padrão `11:30,19:00` no fuso `CLIPES_TZ` (`America/Sao_Paulo`). Cada pedido aprovado recebe o próximo horário livre daquela plataforma com folga mínima de 30 minutos (1 publicação por horário por plataforma); defina `CLIPES_AGENDA_HORARIOS=` (vazio) no `.env` para publicar na hora, ou use `publicar <clip_id> agora` para publicar imediatamente.

> **Workers multi-PC:** todo PC que executa o `monitor` e publica seus próprios clipes também precisa de `DISCORD_BOT_TOKEN` no `.env`; sem ele o worker pula prévias e `run_pending`, deixando pedidos `pending` na fila.

### 5. Registro e liberar disco

Todo clipe processado por CLI ou worker é indexado em `OUT_DIR/registro.json` (fonte da verdade local, gravada com `flock` e escrita atômica) com hash SHA-256, tamanho e IDs do Discord (`thread_id`, `message_id`, `attachment_id`). Quando `CLIPES_MONGO_URI` está ativo, uma cópia é enviada para a coleção `clips` (`_id = clip_id`) para busca entre PCs e publicação remota; URLs assinadas do CDN nunca são salvas e são obtidas na hora via API do Discord.

- `CLIPES_MASTER_MODE`: `discord` (padrão; codifica o master uma única vez dentro de `CLIPES_DISCORD_MAX_MB`, re-renderiza uma vez da fonte com 85% do bitrate se passar de 98% do limite e marca `cabe_discord=false` se ainda não couber) ou `livre` (mantém `-crf 21` sem teto de bitrate, gera prévia separada quando precisar e nunca apaga o arquivo local).
- `CLIPES_DISCORD_MAX_MB`: limite usado para o envio ao Discord (padrão `20`). Uploads rejeitados com HTTP 413 caem automaticamente para aviso sem anexo (`cabe_discord=false`).
- `CLIPES_REGISTRO_VERIFICAR`: `1` por padrão; após o upload do master no modo `discord`, baixa o anexo em stream e confirma `size` e `sha256` antes de marcar `status="no_discord"`.
- `CLIPES_RETENCAO_H`: vazio por padrão (retenção **desligada**). Quando definido com um número `>= 0` (horas após o ACK), a varredura ao final de cada envio ao Discord apaga o `.mp4` e `.cache/discord_<stem>.mp4` dos clipes `no_discord` sem pedido de publicação `pending`/`running` (marcando `status="so_discord"`), mantendo `.txt`, `.descricao.txt` e `clips.json`.

> **Atenção:** quando `CLIPES_RETENCAO_H` apaga o `.mp4` local, o Discord passa a ser a única cópia do vídeo. Se a mensagem ou o anexo forem apagados no Discord, a publicação marca o clipe como `perdido`.

| Risco | Avaliação | Mitigação |
| ----- | --------- | --------- |
| Política / ToS do Discord | Incerto (médio): links assinados visam evitar hospedagem pública para terceiros | Servidor privado do próprio operador, sem URL pública; registro local guarda os metadados |
| Mensagem, canal ou bot apagados | Alta gravidade, baixa probabilidade | Retenção desligada por padrão; ACK por SHA-256; detecção de `perdido` ao publicar |
| Limite de anexo (ex.: servidor sem Boost em 10 MiB) | Médio | Teste de upload de 19 MB no servidor; HTTP 413 marca `cabe_discord=false` e mantém o arquivo local |
| Qualidade do master limitada (~2,3 Mb/s em 60 s / 20 MiB) | Médio (Shorts/TikTok recomprimem no upload) | `CLIPES_MASTER_MODE=livre` preserva qualidade máxima sem apagar o local |
| Rate limit da API ao verificar/baixar | Baixo (1 GET por clipe) | `DiscordRest` trata HTTP 429 com `retry_after` |
| Corrida entre worker e CLI | Baixo | `flock` + escrita atômica em `registro.json` e skip de clipes com pedido `pending`/`running` |

**Smoke manual recomendado:**
1. Envie um clipe de ~19 MB no canal configurado para confirmar o limite do servidor (se voltar HTTP 413, defina `CLIPES_DISCORD_MAX_MB=10`).
2. Rode uma clipagem curta com `CLIPES_RETENCAO_H=0`, confirme no `registro.json` o `status="so_discord"` e a remoção do `.mp4` local (mantendo `.txt` e `clips.json`).
3. Envie `publicar <clip_id>` (YouTube privado) e verifique que o download temporário em `.tmp_publicar/` publica com o mesmo SHA-256 e é removido em seguida.

### 6. Auditoria para publicação pública (futuro)

Nesta versão a privacidade é fixa no código (`private` / `SELF_ONLY`). Para liberar publicação pública após auditoria:

- **YouTube**: preencher o formulário *YouTube API Services – Audit and Quota Extension Form* (*Complete a compliance audit*) com descrição arquitetural e screencast do fluxo de upload acionado pelo usuário.
- **TikTok**: submeter revisão no portal com domínio verificado (Termos de Uso e Política de Privacidade), screencast completo do fluxo OAuth e da tela de publicação, e integração a `/v2/post/publish/creator_info/query/` exibindo opções de privacidade e consentimento conforme as diretrizes de UX da plataforma.

## Limpeza

Para remover container, imagem e volume de cache de modelos:

```sh
docker compose down --rmi all -v
```
