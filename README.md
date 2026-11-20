# clipes-lives

Gerador gratuito e 100% CPU-only de clipes verticais (`1080x1920`, H.264/AAC) com legendas queimadas para lives e VODs (YouTube e Twitch), rodando integralmente em Docker.

## Requisitos

- Roda apenas em Linux/Docker (as travas de arquivo usam `fcntl`); no Windows use Docker/WSL2.
- Apenas **Docker** e **Docker Compose** (nada instalado no host, sem GPU; chaves de API como `GEMINI_API_KEYS`, Discord, YouTube, TikTok e Twitch são opcionais).

## Início rápido (Docker)

Em cada PC (Linux; no Windows use uma distro WSL2, ver "Windows" abaixo):

```sh
git clone https://github.com/RomuuloGoncalves/clipes-lives.git && cd clipes-lives
git checkout master
docker compose build                 # constrói a imagem (1x, ou após git pull)

# credenciais fora do repositório (ver "Configuração fora do repositório")
mkdir -p ~/.config/clipes-lives && chmod 700 ~/.config/clipes-lives
touch .env && chmod 600 .env && nano .env
```

Rodar (um comando por PC, uma vez; o resto é pelo Discord):

```sh
./clipar servico                             # serviço único do PC: acompanha as lives, busca geral e VODs (fica rodando)
./clipar "<url do VOD>"                      # um VOD direto no terminal
docker compose up -d bot                     # bot do Discord (1 só processo por servidor, num PC só)
```

No Discord, `/live adicionar alvo:<canal>` coloca uma live num PC com vaga, `/clipar url:<url>` enfileira um VOD e `/status` mostra os PCs (ver a tabela em "Bot do Discord").

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

O `./clipar` calcula automaticamente o limite de CPUs (`CLIPES_CPUS`: metade dos núcleos no desktop ou 1/4 em notebook/bateria ou com menos de 4 GB livres, mínimo 1, replicado em `CLIPES_CPU_THREADS`) e de RAM (`CLIPES_MEM`: 60% da memória disponível, teto de 5 GB), respeitando valores definidos no ambiente (`*` em `CLIPES_MEM` ou `CLIPES_CPUS` = sem limite; com CPU sem limite, as threads passam a ser o `nproc`); roda com prioridade baixa e liga o `./vigia-temp` (detalhes em "Temperatura e vigia" abaixo). Para uso contínuo, `./clipar servico` (ver "Serviço único" abaixo) mantém a fila de janelas, a busca geral e os VODs em um processo só, com `CLIPES_JOBS` workers (padrão 1), cada um processando um job por vez; se parar, retoma da fila.

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

Rodar direto com `docker compose run --rm clipes <url>` ou `servico` também vale para o vigia térmico e para os limites de RAM/CPU do painel, sem `./clipar`: o serviço `vigia` do `docker-compose.yml` faz isso e aplica RAM/CPU ao vivo (`docker update`). Detalhes em [docs/vigia-docker.md](docs/vigia-docker.md). Sob `./clipar`, o `vigia` do compose fica ocioso e vale o `./vigia-temp` do host.

Detecção (`--mode`): `peaks` usa só os picos de áudio/chat/heatmap; `quota` transcreve rápido (Whisper small, beam 1) até `--asr-budget-ratio` da duração e escolhe os melhores trechos para garantir `--min-per-hour` clipes por hora; `auto` (padrão) usa picos e só aciona a cota quando os picos fortes não a preenchem. `--no-llm` desliga título/legenda/hashtags por IA local. O `clips.json` registra `detection_mode`, `floor_mode` e `quota_target`.

Variáveis de ambiente de renderização:

- `CLIPES_BLUR_BG` (padrão `0`): por padrão o vídeo 16:9 fica centralizado sobre barras pretas; `1` liga o fundo borrado (`split`/`boxblur`/`overlay`), que consome mais CPU.
- `CLIPES_LEGENDA_IDIOMA` (padrão `pt`): traduz automaticamente a legenda queimada para `pt-BR` quando o ASR detecta um idioma diferente de português; use `off` ou `original` para manter o idioma original.
- `CLIPES_LEGENDA_ORIGINAL` (padrão `0`): quando `1`, exibe o texto original menor (`44 px`, `#D0D0D0`) acima da tradução `pt-BR` (`64 px`) no modo bilíngue.
- Barra fixa de marca no topo (`1080xH`):
  - `CLIPES_BAR`: liga automaticamente quando `CLIPES_BRAND_NAME` está definido; use `0` para desligar ou `1` para ligar só com o título (sem nome/handle).
  - `CLIPES_BRAND_NAME`: `@handle` do canal (ex.: `@cortes_lr1`; o `@` é adicionado se faltar).
  - `CLIPES_BRAND_DISPLAY`: nome de exibição em negrito ao lado do avatar (ex.: `CortesLR`); se vazio, mostra só o handle.
  - `CLIPES_BRAND_LOGO`: caminho do logo **dentro do contêiner** (padrão `/app/assets/logo.jpg`; para usar outro arquivo, coloque em `~/.config/clipes-lives/` e aponte para `/config/<arquivo>`). Se o arquivo não existir, a barra renderiza apenas o texto.
  - `CLIPES_BAR_STYLE`: `claro` (padrão; fundo branco, texto preto, handle cinza) ou `escuro` (fundo preto, texto branco, handle cinza-claro).
  - `CLIPES_BAR_H`: altura da barra em pixels (padrão `420`, aceito de `300` a `600`).
- Layout dividido (`CLIPES_LAYOUT`): empilha barra opcional, recorte da webcam e recorte do jogo em `1080x1920`, com a legenda falada centralizada na emenda (`barra + cam`) e crédito no rodapé (`y=1860`). Quando nenhuma webcam de overlay é detectada (ou a fonte já é vertical), o render cai para o layout `full` sem erro.
  - `CLIPES_LAYOUT`: `split-starcuts` (padrão quando vazio), `split-barra`, `split-cheio`, `split-fina` ou `full` (mantém o layout tradicional sem detecção de webcam).
  - **Layout global (painel):** o seletor "Layout dos clipes (global)" grava `config_global/_id=global` no Mongo e vale para todos os PCs via heartbeat, só para clipes novos. Precedência: **global do painel > `CLIPES_LAYOUT` do `.env` > padrão `split-starcuts`**. `CLIPES_LAYOUT` salvo por PC (`workers.config`) é ignorado, com aviso único no log; a chave não aparece mais no modal por PC.
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

## Temperatura e vigia

O vigia pausa o container quando o PC esquenta e o retoma quando esfria. Ele grava a temperatura em `<saída>/.temp` a cada 5 s (log em `<saída>/.temp.log`); o painel lê esse valor. Há dois vigias, e só um escreve por vez: o serviço `vigia` do `docker-compose.yml` (sobe com o `clipes`, funciona com `docker compose run` direto e aplica RAM/CPU do painel ao vivo via `docker update`; detalhes em [docs/vigia-docker.md](docs/vigia-docker.md)) e o `./vigia-temp` do host, que o `./clipar` liga. Sob `./clipar`, o do compose fica ocioso.

Ajustes pelo painel (aba **Temperatura**, por PC) ou no `.env`:

- `CLIPES_VIGIA` (padrão `1`): `0` desliga a pausa. O vigia segue gravando `.temp`, e o painel mostra a temperatura em cinza.
- `CLIPES_TEMP_MAX` (60 a 100, padrão `85`): pausa o container ao passar deste valor.
- `CLIPES_TEMP_MIN` (40 a 95, padrão `75`): retoma o container abaixo deste valor. `MIN` precisa ser menor que `MAX` pelos valores efetivos; o painel rejeita o contrário.

O valor configurado pelo painel chega ao PC no próximo heartbeat (`CLIPES_BEAT_S`), que grava `<saída>/.vigia` com `ativo max min`. O vigia relê esse arquivo a cada ciclo; sem ele, ou com valor inválido, usa `1 85 75`.

- Leitura velha: se `.temp` tiver mais de 30 s, a temperatura é tratada como ausente (`n/d` no painel), então a última leitura não parece atual com o vigia parado.
- Uma instância por saída: se já houver um `vigia-temp` vivo (`<saída>/.vigia.pid`), o novo sai com código 0. O `./clipar` inicia o vigia só se não houver um vivo e encerra só o que ele mesmo iniciou.
- **Windows (Docker Desktop / WSL2):** o hwmon do Linux não enxerga o sensor do host. O serviço `vigia` do compose lê o LibreHardwareMonitor por HTTP: abra o LibreHardwareMonitor como administrador com **Options → Remote Web Server → Run** (porta 8085) e confira `http://localhost:8085/data.json` no navegador (`CLIPES_LHM_URL` troca o endereço). O `./vigia-temp` no WSL2 consulta o Windows por `powershell.exe`, tentando primeiro o LibreHardwareMonitor (WMI, `root/LibreHardwareMonitor`, com o servidor WMI ligado) e depois `MSAcpi_ThermalZoneTemperature`; essa consulta roda no máximo a cada 15 s. Se nenhuma fonte responder em 5 s, o vigia grava `sem-sensor`, o `./clipar` imprime um aviso para abrir o LibreHardwareMonitor e o painel mostra `n/d`.

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
CLIPES_SLOTS="twitch=1,kick=1,youtube=1,busca=1"   # opcional: 0 desliga a plataforma neste PC

# Discord e publicação privada (opcional)
DISCORD_BOT_TOKEN="<TOKEN_DO_BOT>"
DISCORD_GUILD_ID="<ID_DO_SERVIDOR>"
DISCORD_CHANNEL_ID="<ID_DO_CANAL>"
DISCORD_ALLOWED_USER_IDS="<ID_USUARIO_1>,<ID_USUARIO_2>"
DISCORD_LOG_CHANNEL_ID="<ID_CANAL_BOT_LOG>"   # opcional
YOUTUBE_CLIENT_ID="<GOOGLE_CLIENT_ID>"
YOUTUBE_CLIENT_SECRET="<GOOGLE_CLIENT_SECRET>"
TIKTOK_CLIENT_KEY="<TIKTOK_CLIENT_KEY>"
TIKTOK_CLIENT_SECRET="<TIKTOK_CLIENT_SECRET>"
```

O arquivo `~/.config/clipes-lives/tokens.json` (modo `600`) é montado em `/config` nos serviços `clipes` e `bot` do `docker-compose.yml` (personalizável via `CLIPES_CONFIG_DIR`).

## Operação Multi-PC, Painel na Vercel e Serviço Contínuo

### 1. MongoDB Atlas M0 (coordenação sem vídeo)

O MongoDB coordena apenas quem grava cada live, os alvos (`alvos`: lives e VODs pedidos pelo Discord), o estado dos jobs, o heartbeat dos PCs (com `slots` e `ocupados`) e comandos do painel — nenhum vídeo passa pelo banco. Sem `CLIPES_MONGO_URI`, cada PC opera localmente com `DiskQueue`.

1. Crie um cluster gratuito **Atlas M0** com o banco `clipes_lives`.
2. Em **Network Access**, adicione `0.0.0.0/0` (necessário para PCs com IP residencial dinâmico e funções serverless da Vercel), protegendo o acesso com senhas fortes.
3. Em **Database Access**, crie dois usuários com permissão mínima:
   - `workers`: permissão `readWrite` apenas nas coleções `lives`, `jobs`, `workers`, `commands` e `alvos` (os PCs tomam e renovam alvos com `update` em `alvos`).
   - `painel`: role customizada com leitura (`find`) em `lives`, `jobs`, `workers`, `alvos` e `insert`/`update` nas coleções `commands`, `alvos` e `workers` (sem isso, adicionar/remover alvo ou salvar configurações por PC pelo painel falha com `not authorized`).
   - O bot (`docker compose up -d bot`) grava `alvos` com `insert`/`update`, o mesmo que o painel precisa para escrever na coleção.
4. No `.env` de cada máquina, defina `CLIPES_MONGO_URI` com o usuário `workers` e um apelido distinto em `CLIPES_PC_ALIAS` ou `CLIPES_ALIAS` (ex.: `PC-1`, `PC-2`, `PC-3`).

> O Atlas M0 pausa automaticamente após 30 dias sem conexões; se o Mongo ficar fora do ar, os PCs seguem processando na fila local e tentam reconectar a cada 60 s.

### 2. Painel de monitoramento na Vercel (`painel/`)

1. Importe o repositório na Vercel definindo o **Root Directory** como `painel`.
2. Configure as variáveis de ambiente do projeto na Vercel:
   - `PAINEL_SENHA`: senha forte usada no Basic Auth.
   - `PAINEL_MONGO_URI`: connection string do usuário `painel` (menor privilégio).
   - Opcionais: `PAINEL_MONGO_DB` (padrão `clipes_lives`) e `CLIPES_BEAT_S` (padrão `20`).
3. O painel atualiza a cada 10 s mostrando status online/offline (inclusive PCs offline já registrados ou pré-configurados), CPU, RAM, disco livre, temperatura (`n/d` sem sensor), GPU, job atual, slots e ocupados por plataforma, fila, a lista de alvos ativos com o dono, o botão **liberar recursos** (exige confirmação e digitar `LIBERAR`, encerrando subprocessos e saindo com código `75` para o `./clipar` reiniciar o ciclo após `CLIPES_RESTART_S=60`) e o botão **configurar** por PC (mais **+ Configurar novo PC** para pré-cadastrar por `pc_alias`). As colunas **Job atual** (um id por job em andamento), **Capacidade** (`<lives ativas>/<sustentáveis> lives · <jobs> jobs · <média> s por job`; destacada com `fila vai crescer` quando as lives ativas passam das sustentáveis) e **RAM** (`livre / limite`, `sem limite` ou `?` se ilegível, com selo `diverge` quando o pedido do painel difere do limite do contêiner) mostram a capacidade de cada PC.
4. **Configuração por PC via painel (`POST /api/config`)**: edita todas as 44 variáveis de ambiente não sensíveis por PC em `db.workers.config`, com precedência `MongoDB (painel) > .env local > padrão do código` (campo vazio restaura o padrão) e recarga em runtime no boot e a cada heartbeat (`CLIPES_BEAT_S`). `CLIPES_MEM` (ex.: `4g`, `*` = sem limite), `CLIPES_CPUS` (ex.: `2`, `*` = sem limite) e `CLIPES_RAM_POR_JOB` (ex.: `2560m`) ficam no grupo **Recursos (RAM/CPU)**: o worker grava o pedido em `out/.recursos` e sai com código `75` quando o limite atual do contêiner difere; o `./clipar` relê o arquivo (precedência painel > `.env` > automático) e recria o contêiner com o novo limite. Com o serviço `vigia` do compose (ver "Temperatura e vigia"), o limite muda ao vivo, inclusive com `docker compose run` direto; sem ele (ex.: `docker compose up -d bot`) só vale sob `./clipar`. Voltar ao automático pelo painel só vale no próximo reinício. Variáveis secretas (`CLIPES_MONGO_URI`, tokens e chaves de API) são bloqueadas por whitelist estrita e permanecem apenas no `.env` e `tokens.json` locais.

### 3. Windows (Docker Desktop + WSL2)

Nos PCs com Windows (ex.: `PC-2` e `PC-3`):

1. Instale o **Docker Desktop** usando o backend **WSL2**.
2. Crie `%UserProfile%\.wslconfig` para limitar RAM e núcleos da VM (ajuste ao hardware da máquina) e reinicie o WSL com `wsl --shutdown`. No Windows, o limite real de RAM é o da VM: `CLIPES_MEM` não passa desse valor.

```ini
[wsl2]
memory=10GB
processors=12
```

3. Execute o `./clipar` a partir de uma distro Linux dentro do WSL2, mantendo `OUT_DIR` dentro do sistema de arquivos da distro (evite `/mnt/c` pelo impacto em I/O).
4. A temperatura vem do LibreHardwareMonitor no Windows, com **Options → Remote Web Server → Run** (porta 8085), conforme "Temperatura e vigia". Sem sensor, o `./clipar` imprime o aviso de `sem-sensor` e o painel exibe `n/d`.

### 4. Serviço único por PC (`./clipar servico`) e slots

Em cada PC, rode uma vez:

```sh
./clipar servico
```

Um único processo faz tudo: o `Supervisor` acompanha os alvos da coleção `alvos` do MongoDB (sem `CLIPES_MONGO_URI`, os alvos vêm do argv: `./clipar servico <canal|url>...`), a busca geral IRC da Twitch roda sobre o `channels.txt` de cada PC (um canal por linha; `#` comenta) e há 1 fila com até `CLIPES_JOBS` workers. Cada worker processa 1 job por vez, nesta ordem: janelas de live e clipes da busca (por `z_score`) e, só com a fila vazia, VODs.

- **Adicionar e parar pelo Discord**: `/live adicionar` grava o alvo em `alvos`; um PC com vaga da plataforma toma o alvo (claim atômico) em até 30 s. `/live remover` marca o alvo como `removido` e o PC dono fecha a janela em andamento. `/live lista` mostra o PC dono e o estado de cada alvo.
- **Slots (`CLIPES_SLOTS`)**: padrão `twitch=1,kick=1,youtube=1,busca=1`. Cada valor é o máximo de sessões daquela plataforma neste PC; `0` desliga a plataforma aqui (ex.: `CLIPES_SLOTS=twitch=2,kick=0,busca=0`). Plataforma desconhecida, valor não inteiro ou negativo faz o serviço sair com código 2 e a mensagem `CLIPES_SLOTS invalido: <trecho>`. `busca=0` não abre conexão IRC. `CLIPES_SLOTS` substitui `CLIPES_MAX_LIVES` e o antigo `bench-live`.
- **Jobs em paralelo (`CLIPES_JOBS`, 1 a 8; vazio = 1)**: quantos jobs o PC processa ao mesmo tempo. O valor efetivo é o menor entre o pedido, as CPUs (limite do contêiner ou núcleos) e a RAM dividida por `CLIPES_RAM_POR_JOB` (padrão `2560m`, ou seja 2,5 GB por job; a RAM é o menor entre o limite do contêiner e a memória total). Cada live tem no máximo 1 job por vez, e os VODs continuam um por vez; logo `CLIPES_JOBS` ajuda quando há várias lives. Recomendação: `2` ou mais em PCs com várias lives. Whisper, llama-server e onnx dividem as threads da CPU entre os jobs efetivos. O log `[jobs] CLIPES_JOBS=N limitado a E` aparece quando o pedido é cortado.
- **Heartbeat**: cada PC publica `slots` e `ocupados` por plataforma em `workers` (o painel mostra).
- **Lease**: se o PC dono para de renovar um alvo por 90 s, outro PC com vaga pode tomá-lo. Ao reiniciar, o PC retoma os alvos de que ainda é dono. Mantenha `CLIPES_LEASE_S` >= `90` (padrão): avisos síncronos ao Discord (timeout de até ~60 s) pausam o laço principal.
- **Sem live duplicada**: a busca geral ignora picos de canais que são alvo `ativo` em `alvos` (de qualquer PC), então uma live não é gravada por dois PCs.
- **Disco por live**: cada gravador mantém cerca de 20 minutos de buffer em `queue/.buffer/` (~900 MB por live a 6 Mb/s), respeitando o teto global `BUFFER_CAP_BYTES` (~1,5 GB). `CLIPES_MIN_FREE_GB` evita fechar novas janelas quando o disco aperta, mas não limita o buffer em andamento.
- **Backlog de janelas (`CLIPES_MAX_BACKLOG`, padrão `6`, mínimo `1`)**: janelas fechadas que aguardam o worker, por sessão. Passou do limite, a mais antiga é descartada (aviso `janela perdida`). Se o disco livre cair abaixo de `CLIPES_MIN_FREE_GB`, a descarte acontece antes do limite. A fonte já fica congelada em `source.mkv`, então segurar mais janelas custa só disco.
- **YouTube (experimental)**: a gravação de lives do YouTube usa `yt-dlp -f "b[height<=1080]/b" --hls-use-mpegts -o -` sem chat. Se alguma live não entregar formato muxado via `yt-dlp`, o plano B é testar `streamlink <url> best`.
- **Comandos removidos**: `./clipar monitor`, `monitor --follow`, `<url> --ate-acabar`, `acompanhar` e `bench-live` saem com código 2 e a mensagem `removido: use ./clipar servico (e /live adicionar no Discord)`.
- **Smoke manual**: rode `./clipar servico` em um PC, adicione 1 live da Twitch e 1 do YouTube com `/live adicionar` e confira nos logs e na fila que janelas das duas são geradas, sem dois jobs simultâneos em `processing/`.

### 5. Consultas de diagnóstico e purga (MongoDB Compass / `mongosh`)

Para rodar manualmente no `mongosh` do MongoDB Compass (nunca automatize acesso ao banco em scripts ou agentes):

```js
// Diagnóstico: PCs, lives ativas, contagem de jobs (24 h) e comandos pendentes
db.workers.find({}, { alias: 1, heartbeat_at: 1, job_atual: 1, slots: 1, ocupados: 1, "capacity.temp_c": 1, "capacity.disco_livre_gb": 1 }).sort({ heartbeat_at: -1 });
db.alvos.find({ estado: "ativo" }, { _id: 1, tipo: 1, plataforma: 1, owner: 1, fase: 1, pc_fixo: 1, lease_until: 1 }).sort({ plataforma: 1 });
db.lives.find({ status: "live" }).sort({ lease_until: 1 });
db.jobs.aggregate([{ $match: { updated_at: { $gte: new Date(Date.now() - 86400000) } } }, { $group: { _id: "$status", total: { $sum: 1 } } }]);
db.commands.find({ status: { $in: ["pending", "executando"] } }).sort({ criado_em: -1 });

// Purga: conferir e remover jobs/comandos finalizados há mais de 30 dias (limite de 0,5 GB do M0)
db.jobs.countDocuments({ status: { $in: ["done", "rejected", "lost", "failed"] }, updated_at: { $lt: new Date(Date.now() - 30 * 86400000) } });
db.jobs.deleteMany({ status: { $in: ["done", "rejected", "lost", "failed"] }, updated_at: { $lt: new Date(Date.now() - 30 * 86400000) } });
db.commands.deleteMany({ status: { $in: ["feito", "rejeitado"] }, criado_em: { $lt: new Date(Date.now() - 30 * 86400000) } });
db.alvos.deleteMany({ estado: { $in: ["feito", "falhou", "removido"] }, criado_em: { $lt: new Date(Date.now() - 30 * 86400000) } });
```

## Gate de qualidade, duração e métricas (anti-flop)

Cada clipe passa por um **gate** ao fim do render (`clips.json` e `registro` ganham `gate = {"ok": bool, "motivos": [...], "versao": 1}`). O gate reprova o que a síntese chama de eliminatório para retenção.

| Motivo | Reprova quando |
| ------ | -------------- |
| `hook_ausente` | o hook da tela está vazio |
| `hook_truncado` | o hook termina em `…`/`...` ou não coube em 2 linhas |
| `hook_invalido` | `U+FFFD` ou letra CJK/Hangul/Kana em clipe de idioma latino |
| `fala_tardia` | a 1ª palavra vem depois de `start + CLIPES_HOOK_MAX_ATRASO` |
| `duracao_fora` | duração fora de `[CLIPES_DUR_MIN, CLIPES_DUR_MAX]` |
| `barras_pretas` | layout `black_bars_16x9` |
| `resolucao` | master diferente de 1080x1920 |
| `caption_url` | URL na caption |
| `caption_hashtags` | 0 hashtags, mais que `CLIPES_HASHTAGS` ou alguma banida |
| `caption_repete_hook` | 1ª linha da caption igual ao hook |

Modos (`CLIPES_GATE`):

- `bloquear` (padrão): `publicacao` recusa o clipe com `gate.ok=false`, listando os motivos, sem reservar cota nem agendar. No Discord o cartão mostra `Bloqueado: <motivos>` e sem botões de publicação.
- `avisar`: publica, mas anexa os motivos ao status. Use para calibrar antes de travar.
- `off`: o gate não é avaliado.

Também no gate: clipe do mesmo canal com trecho sobreposto em ≥ 50%, mesmo texto inicial ou imagem parecida com um clipe já publicado ou pendente é recusado como `duplicado de <clip_id>`. `CLIPES_DEDUP_PHASH=0` desliga só o critério de imagem.

### Chaves globais novas

São chaves de escopo **global**: valem para todos os PCs (o painel as edita na área de config global, não por PC; quando `CLIPES_MONGO_URI` está ativo, a publicação do bot lê as mesmas chaves do Mongo a cada ciclo). Global vence o valor por PC.

| Chave | Padrão | Efeito |
| ----- | ------ | ------ |
| `CLIPES_GATE` | `bloquear` | `bloquear`, `avisar` ou `off` (ver acima) |
| `CLIPES_HOOK_MAX_ATRASO` | `1.0` | segundos entre o início do clipe e a 1ª fala; acima disso o início é cortado (`primeira_palavra - 0,2 s`) se o clipe continuar com duração válida |
| `CLIPES_DUR_MIN` / `CLIPES_DUR_MAX` | `15` / `45` | duração do clipe em segundos (faixas 8-60 e 15-120; `MIN` precisa ser menor que `MAX`, senão a validação recusa). `cli` usa esses valores sem `--min-dur`/`--max-dur`; a live usa `MAX` como teto dos jobs; partes de narrativa longa respeitam o mesmo teto |
| `CLIPES_LAYOUT_FALLBACK` | `blur` | `blur` (fundo desfocado) ou `recorte` (9:16 centrado no jogo) quando não há webcam ou o split falha. `black_bars_16x9` não é mais fallback |
| `CLIPES_CARTAO_FINAL` | `0` | `0`: sem trecho congelado e sem cartão "Live completa"/"Parte k+1"; `1`: comportamento anterior |
| `CLIPES_HASHTAGS` | `3` | 1 a 5 hashtags, só específicas (sem `#cortes`, `#livesbr`, `#clipsbr`, `#twitchtv*`, `#live`, `#conflito`, `#reacao`) |
| `CLIPES_DEDUP_PHASH` | `6` | distância máxima do dHash para considerar duplicata; `0` desliga o critério de imagem |
| `CLIPES_VISIBILIDADE` | `padrao` | ver abaixo |

Duração: a faixa 15-45 s é o passo conservador da síntese (não há número oficial). Para testar uma variável por vez, mude `CLIPES_DUR_MIN`/`CLIPES_DUR_MAX` e compare com a sua própria mediana.

### Visibilidade

- `CLIPES_VISIBILIDADE=padrao` (padrão): exatamente o comportamento de hoje (`private` no YouTube, `SELF_ONLY` no TikTok).
- `private`: força `private` / `SELF_ONLY`.
- `public`: YouTube publica `public`; TikTok publica `PUBLIC_TO_EVERYONE`, sujeito às opções que a conta permite. **No TikTok, público exige app auditado** (ver §6); sem auditoria o post é recusado com `unaudited_client_can_only_post_to_private_accounts`.

A privacidade usada fica em `publicacoes.<plataforma>.privacidade`. O painel mostra um aviso quando a visibilidade efetiva não é `public`: posts privados não medem flop.

### Métricas (manual)

Não há coleta automática (exige escopos de API e app auditado). As métricas entram no registro por `metricas.<plataforma>` com `fonte` (`manual` ou `csv`) e `atualizado_em`.

Registrar um clipe à mão:

```sh
docker compose run --rm clipes metricas registrar <clip_id> --plataforma tiktok --views 1200 --tempo-medio 8.5 --completo 42 --curtidas 30 --compartilhamentos 4
```

Importar o CSV exportado do Studio:

```sh
docker compose run --rm clipes metricas importar /caminho/export.csv --plataforma youtube
```

- Exporte do **YouTube Studio** em Analytics → Conteúdo (lista de Shorts) → exportar como CSV; do **TikTok Studio** em Analytics → Vídeos → exportar dados. Os menus mudam com frequência; confira na sua tela.
- O CSV aceita cabeçalhos em PT e EN (UTF-8 com BOM, `,` ou `;`; números com `.` ou `,` e `%`). Se uma coluna não for reconhecida, sobrescreva com `--col-id`, `--col-views` etc.
- A linha casa com o clipe pelo id ou URL do vídeo gravado em `publicacoes.<plataforma>`. O comando imprime quantas casaram e quais não. No TikTok o export pode trazer só o `publish_id`, que não casa: use `registrar`.

### Checklist manual [RENDER]

Rode antes de liberar uma mudança de render (não é coberto pelo gate automático):

- [ ] 1 clipe por layout no app (webcam/split, `blur` e `recorte`): nenhuma barra preta e o jogo centralizado.
- [ ] Loop sem cartão: com `CLIPES_CARTAO_FINAL=0`, o último frame não é congelado nem há cartão "Live completa"; o áudio termina com fade curto dentro do conteúdo.
- [ ] Hook na barra: o hook aparece completo nas 2 linhas, sem `…` e sem caractere estranho.

## Bot do Discord e Publicação Privada

O `./clipar servico` envia cada live para o canal `#clipes` (`DISCORD_CHANNEL_ID`) como 1 cartão + 1 thread por `live_id`, editados no lugar, e os avisos (recusa, sem vaga, janela descartada por backlog, falha, serviço iniciado/parado, prévia que não coube) para `#bot-log`, nunca para o `#clipes`. O canal de avisos é resolvido nesta ordem: `DISCORD_LOG_CHANNEL_ID` (opcional); o cache `OUT_DIR/.discord_log_channel`; um canal de texto `bot-log` já existente na guilda; um `bot-log` criado pelo bot (exige permissão Manage Channels). Sem nada disso, os avisos vão só para o stdout. Avisos repetidos em até 60 s são suprimidos, e o próximo envio desse texto leva `(+N repetidos)`. O serviço `bot` aceita comandos para publicar como **privado** no YouTube Shorts (`privacyStatus="private"`) e no TikTok (`privacy_level="SELF_ONLY"`), usando as variáveis definidas em `.env` e os tokens gravados em `/config/tokens.json`.

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
   - `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` e `DISCORD_ALLOWED_USER_IDS` (lista vazia bloqueia todas as publicações e comandos).
   - **Recomendado: IDs explícitos em `DISCORD_ALLOWED_USER_IDS`.** Com `*`, qualquer membro que enxergue o canal pode adicionar lives, enfileirar VODs, publicar e descartar; o bot loga `aviso: comandos liberados para qualquer membro do canal` na inicialização.
   - `DISCORD_LOG_CHANNEL_ID` (opcional): canal `#bot-log` para avisos. Vazio = o bot usa ou cria um `bot-log` na guilda (ver acima); nunca cai no `#clipes`.
   - `CLIPES_DISCORD_MAX_MB=10` (padrão; limite de anexo de bot em servidor sem boost). Se um master passar de 98% desse limite, o bot gera uma prévia 720x1280 mirando 90% do limite.

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

Conferir os tokens (sem imprimir token, client id ou secret):

```sh
docker compose run --rm clipes auth status           # ok | ausente | expirado-renovavel | invalido, por plataforma, com caminho e expiração
docker compose run --rm clipes auth status --renovar # tenta renovar os tokens expirados e grava os novos
```

Sai com código 0 só se YouTube e TikTok estiverem `ok`.

Subir o serviço contínuo do bot (um único processo por servidor Discord; exige `DISCORD_BOT_TOKEN` definido, caso contrário sai com código 2 em reinício contínuo):

```sh
docker compose up -d bot
```

Todos os comandos abaixo funcionam só no canal configurado (`DISCORD_CHANNEL_ID`), nas suas threads e para os IDs em `DISCORD_ALLOWED_USER_IDS` (a mesma checagem vale para `/live`, `/clipar`, `/status` e `Descartar`; quem não está autorizado recebe `sem permissao`). Controle de lives e VODs exige `CLIPES_MONGO_URI`; sem ele a resposta é `controle requer CLIPES_MONGO_URI`.

| Comando | O que faz |
| ------- | --------- |
| `/live adicionar alvo:<canal\|url> [pc:<alias>]` | Grava o alvo em `alvos` e responde `alvo adicionado: <rótulo>`; um PC com vaga da plataforma toma a live. `pc` fixa o PC dono. Responde `ja acompanhado por <PC>` se já estiver ativo, `alvo invalido` se o formato não for aceito e `sem vaga` se nenhum PC tiver slot livre |
| `/live remover alvo:<canal\|url\|chave>` | Marca o alvo como `removido`; o PC dono fecha a janela em andamento e para em até 30 s |
| `/live lista` | Lista os alvos ativos, o PC dono (ou `sem PC`) e o estado (`gravando`, `aguardando live`, `sem vaga`) |
| `/clipar url:<url>` | Enfileira um VOD (Twitch, Kick ou YouTube) e responde `na fila: posição N`; `ja na fila` se a URL já está ativa e `fila cheia (20)` com 20 VODs pendentes |
| `/status` | Estado dos PCs: slots e ocupados por plataforma, job atual, fila, temperatura e disco |
| `/publicar id:<clip_id> [agora:True]` (ou `publicar <clip_id> [agora]`) | Publica como privado no YouTube e/ou TikTok; clipe `descartado` responde `clipe descartado` sem publicar |
| `/listar` | Lista os 10 últimos `clip_id` do Discord |
| `/buscar texto:<palavras>` (ou `buscar <palavras>`) | Busca clipes no registro local por texto |
| Botões do clipe | **YouTube**, **TikTok**, **Ambos**, toggle **Agendar** e **Descartar** (marca o clipe `descartado`, troca a linha de estado para `🗑️` e remove os botões) |

A linha de estado de cada clipe começa com `🟡` (pendente), `🗓️` (agendado, com data e hora), `✅` (publicado, com plataformas) ou `🗑️` (descartado).

Para acompanhar uma live, não é preciso mexer no terminal: `/live adicionar alvo:gaules` basta. Controle de canais da busca geral continua no `channels.txt` de cada PC.

- **Publicar por destino e toggle Agendar:** cada mensagem de clipe enviada ao Discord traz 2 linhas de botões — linha 1 com **YouTube** (`publicar:youtube:<clip_id>`), **TikTok** (`publicar:tiktok:<clip_id>`) e **Ambos** (`publicar:ambos:<clip_id>`), e linha 2 com o toggle **Agendar: nao** (`agendar:off:<clip_id>`) / **Agendar: sim** (`agendar:on:<clip_id>`), iniciando desligado (`off`). Somente usuários em `DISCORD_ALLOWED_USER_IDS` no canal configurado (`DISCORD_CHANNEL_ID`) ou suas threads podem usá-los. Clicar no toggle alterna `off` ↔ `on`; ao clicar em um destino com `Agendar: nao`, o pedido vai para publicação imediata (`(agora)`, igual a `publicar <clip_id> agora`), e com `Agendar: sim` (ou em mensagens antigas sem o toggle) agenda no próximo slot (`(agendado)`). Cada plataforma aceita acrescenta `<Plataforma> por <usuário> (agora|agendado)` à mensagem; plataformas sem token retornam `<plat>: desligada` e não são marcadas. Os botões (incluindo o toggle) permanecem na mensagem até que YouTube e TikTok tenham sido pedidos (via **Ambos** ou pelos dois cliques em sequência); botões legados `aprovar:<clip_id>` continuam funcionando como **Ambos**.

- **Agenda de publicação:** no `docker-compose.yml`, `CLIPES_AGENDA_HORARIOS` vem com padrão `11:30,19:00` no fuso `CLIPES_TZ` (`America/Sao_Paulo`). Cada pedido aprovado recebe o próximo horário livre daquela plataforma com folga mínima de 30 minutos (1 publicação por horário por plataforma); defina `CLIPES_AGENDA_HORARIOS=` (vazio) no `.env` para publicar na hora, ou use `publicar <clip_id> agora` para publicar imediatamente.

> **Workers multi-PC:** todo PC que executa `./clipar servico` e publica seus próprios clipes também precisa de `DISCORD_BOT_TOKEN` no `.env`; sem ele o worker pula prévias e `run_pending`, deixando pedidos `pending` na fila.

### 5. Registro e liberar disco

Todo clipe processado por CLI ou worker é indexado em `OUT_DIR/registro.json` (fonte da verdade local, gravada com `flock` e escrita atômica) com hash SHA-256, tamanho e IDs do Discord (`thread_id`, `message_id`, `attachment_id`). Quando `CLIPES_MONGO_URI` está ativo, uma cópia é enviada para a coleção `clips` (`_id = clip_id`) para busca entre PCs e publicação remota; URLs assinadas do CDN nunca são salvas e são obtidas na hora via API do Discord.

- `CLIPES_MASTER_MODE`: `discord` (padrão; codifica o master uma única vez dentro de `CLIPES_DISCORD_MAX_MB`, re-renderiza uma vez da fonte com 85% do bitrate se passar de 98% do limite e marca `cabe_discord=false` se ainda não couber) ou `livre` (mantém `-crf 21` sem teto de bitrate, gera prévia separada quando precisar e nunca apaga o arquivo local).
- `CLIPES_DISCORD_MAX_MB`: limite usado para o envio ao Discord (padrão `10`). Uploads rejeitados com HTTP 413 caem automaticamente para aviso sem anexo (`cabe_discord=false`).
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
1. Envie um clipe de ~19 MB no canal configurado para confirmar o limite do servidor (se voltar HTTP 413, reduza `CLIPES_DISCORD_MAX_MB`).
2. Rode uma clipagem curta com `CLIPES_RETENCAO_H=0`, confirme no `registro.json` o `status="so_discord"` e a remoção do `.mp4` local (mantendo `.txt` e `clips.json`).
3. Envie `publicar <clip_id>` (YouTube privado) e verifique que o download temporário em `.tmp_publicar/` publica com o mesmo SHA-256 e é removido em seguida.

### 6. Auditoria para publicação pública (futuro)

Por padrão a privacidade é fixa no código (`private` / `SELF_ONLY`); `CLIPES_VISIBILIDADE=public` a libera (ver "Visibilidade" acima). Para liberar publicação pública após auditoria:

- **YouTube**: preencher o formulário *YouTube API Services – Audit and Quota Extension Form* (*Complete a compliance audit*) com descrição arquitetural e screencast do fluxo de upload acionado pelo usuário.
- **TikTok**: submeter revisão no portal com domínio verificado (Termos de Uso e Política de Privacidade), screencast completo do fluxo OAuth e da tela de publicação, e integração a `/v2/post/publish/creator_info/query/` exibindo opções de privacidade e consentimento conforme as diretrizes de UX da plataforma.

## Limpeza

Para remover container, imagem e volume de cache de modelos:

```sh
docker compose down --rmi all -v
```
