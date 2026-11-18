# Relatório de Pesquisa A (Argon) — Ondas A e C: LiveCliper, IA em Nuvem, Metadados, Gancho no Topo, Discord e Publicação (TikTok/Shorts)

- **Agente:** `pesq-argon` (Gemini 4 Argon)
- **Data:** `2026-11-17`
- **Escopo:** Itens **1, 3, 5, 6, 7 e 8** de `pesquisa/ondas-ABC/PEDIDO.md` (análise de referência do `cardosoGu/LiveCliper`, camada de IA em nuvem Gemini → Claude → Qwen local, título e slug de arquivo, descrição curta/longa, overlay de gancho no topo em ASS/FFmpeg, bot do Discord com threads/recompressão/`publicar <id>` e publishers TikTok Content Posting API / YouTube Data API v3).
- **Convenção de fontes:** Cada afirmação técnica cita `(arquivo:linha)` do repositório `clipes-lives`, `(cardosoGu/LiveCliper:arquivo:linha)` do clone temporário inspecionado fora da worktree (já removido após leitura), ou `(URL oficial)`. O que não possui confirmação documental é marcado explicitamente como **`NÃO VERIFICADO`**.

---

## Resumo Executivo das Decisões Recomendadas por Item

| Item do Pedido | Onda | Decisão Recomendada |
|---|---|---|
| **(a) Referência `LiveCliper`** | A / C | Usar apenas como **inspiração arquitetural** (repositório sem arquivo `LICENSE`, todos os direitos reservados por padrão no GitHub; zero cópia de código, `PEDIDO.md:4`). Aproveitar os conceitos de: (1) gancho curto em duas cores no topo nos primeiros 5 s; (2) cascata `modelo × chave` no Gemini seguida de Claude com teto diário em USD; (3) fila assíncrona de envio ao Discord com recompressão para caber no limite; (4) máquina de modos `off \| private \| public` nos publishers. Descartar o envio de vídeo bruto/frames ao LLM e o segundo passe `boxblur` no topo por custo de tokens e CPU. |
| **(8) IA em Nuvem + Fallback Local** | A | Implementar cascata de **texto estruturado** em `clipes/llm.py`: **1º Gemini API Free Tier** (rodízio `modelo × chave` via REST `urllib.request`, custo US$ 0) → **2º Claude API** (`claude-haiku-4-5` como padrão de baixo custo, `< US$ 0,001/clipe`, travado por `CLAUDE_DAILY_BUDGET_USD`) → **3º Qwen3-1.7B local** (`llama-server`, acionado **somente** se a nuvem falhar ou estiver desligada, poupando ~2 min de CPU e 2 GB RAM no Ryzen 5 5500U) → **4º Fallback heurístico determinístico** (`generate_title`). Registrar nova decisão revertendo o "100% local" nesta etapa e documentar que, no Free Tier do Gemini, a transcrição enviada pode ser usada pelo Google para melhoria de produtos (`https://ai.google.dev/gemini-api/terms`). |
| **(6) Título + Nome do Arquivo (`clip_NN_<slug>.mp4`)** | A | Manter `titulo` validado (`<= 60 chars`, `?` ou `!`, máx. 1 emoji, anti-alucinação e anti-eco em `clipes/llm.py:418-439`) e gerar o `<slug>` **deterministicamente em Python** a partir do `clip.title` validado (`slugify_title`: minúsculas ASCII via `_fold`, sem stopwords nas bordas, `<= 40 chars`, padrão `clip_{idx:02d}_{slug}.mp4` ou `clip_{idx:02d}_part{k}of{N}_{slug}.mp4`). Gerar também `clip_id` estável no `clips.json` (`<video_id>_c<NN>`). |
| **(7) Versão Longa da Descrição** | A | Estender `CLIP_JSON_SCHEMA` (`clipes/llm.py:33-49`) com o campo `descricao_longa` (`150..500 chars`, 2 a 4 frases contextualizando o assunto, participantes e situação sem inventar entidades fora da transcrição, validada por `validate_no_hallucinated_entities`). Salvar `caption` (curta, 3 linhas, ideal para TikTok) e `description_long` (longa + créditos + URL + parte + hashtags, ideal para YouTube Shorts e thread do Discord) no `clips.json` e em `.txt`. |
| **(5) Gancho no Topo do Clipe (ASS/FFmpeg)** | A | Adicionar `titulo_topo` (`<= 35 chars`, pergunta ou promessa curta, sem emoji, sem entregar o desfecho) ao JSON da IA e renderizar **exclusivamente via ASS (`Style: TopHook`)** dentro de `build_ass` (`clipes/render.py:98`), posicionado em `\an5\pos(540,365)` (faixa `Y = 270..450 px`), ativo de `0.0 s` a `5.0 s` (`\fad(120,350)` + *pop* `\fscx75\fscy75\t(0,220,\fscx106\fscy106)\t(220,320,\fscx100\fscy100)`), com até 2 linhas de `<= 18 chars` alternando branco (`&H00FFFFFF&`) e amarelo (`&H0000FFFF&`). Respeita a safe zone superior do TikTok/Reels/Shorts (`Y >= 240 px`) e não colide com `PartBadge` (`Y=490`) nem `Credit` (`Y=600`). |
| **(1) Bot do Discord (Guild `1572417049932599479`, Canal `1572417050771595386`)** | C | Usar **`discord.py >= 2.4`** com `Intents.default()` + `message_content=True`. Para cada execução/job, abrir uma **thread pública** no canal `1572417050771595386` e postar cada clipe com ID estável, notas, descrições curta/longa e vídeo. Se o MP4 exceder `95%` do limite de upload (`DISCORD_UPLOAD_LIMIT_MB`, padrão `9.5` MB, ou `guild.filesize_limit`), gerar `preview_<stem>.mp4` em `720x1280` com bitrate calculado pela duração (`(limit_bytes*0.92*8/duration) - 96 kbps`), mantendo o MP4 `1080x1920` original intacto para publicação. |
| **(3) Comando `publicar <id>` (TikTok + YouTube Shorts em modo Privado)** | C | Reverter `AD-006` (`STATE.md:9`) mediante nova decisão arquitetural: publicação **somente por comando explícito `publicar <id>` (ou `/publicar <id>`)** do `DISCORD_ADMIN_USER_ID`, após validar `policy.json` (`permits` + `delay_h`). Implementar `TikTokPublisher` (Content Posting API v2 Direct Post `FILE_UPLOAD`, travado em `SELF_ONLY` até auditoria) e `YouTubePublisher` (Data API v3 `videos.insert` `resumable=True`, travado em `private` até auditoria de compliance). |

---

## 1. Item (a): Análise Técnica do Design de `cardosoGu/LiveCliper`

O repositório `https://github.com/cardosoGu/LiveCliper` (commit `92bf1b2`, inspecionado em `/tmp/LiveCliper-research` e removido em seguida) **não possui arquivo `LICENSE`** (`cardosoGu/LiveCliper` raiz), o que significa que, pelos termos do GitHub, todo o código é protegido por *copyright default* (*all rights reserved*). Conforme `PEDIDO.md:4`, nenhum trecho de código deve ser copiado; abaixo está a análise funcional da arquitetura dele comparada ao `clipes-lives`.

### 1.1. Gancho no Topo do Clipe (`title_ass` + `_blur_chain`)
- **Como o LiveCliper faz:**
  - No prompt (`cardosoGu/LiveCliper:src/liveclips/ai/prompt.py:34-36`), pede um campo `title` definido como *"gancho em PT-BR que aparece grande sobre a webcam nos primeiros 5 segundos. Escreva como PERGUNTA ou PROMESSA que dá vontade de ver o resto (ex.: 'ELE VAI CONSEGUIR?', 'ESPERA O FINAL...'). No máximo ~35 caracteres (cabe em 2 linhas curtas), sem emoji, sem mentir sobre o conteúdo e sem entregar o desfecho"*.
  - Em `cardosoGu/LiveCliper:src/liveclips/processing/ffmpeg.py:104-166`, a função `wrap_title(title, max_chars=18, max_lines=3)` remove emojis e quebra o texto em linhas de até 18 caracteres. A função `title_ass(title, center_y)` gera um arquivo `title.ass` separado (`PlayResX: 1080, PlayResY: 1920`, `WrapStyle: 2`), fonte `Montserrat Black` tamanho `86`, `Outline=8`, `Shadow=6`, `Alignment=5`, alternando as cores das linhas entre branco (`&H00FFFFFF&`) e amarelo (`&H0000D4FF&` em BGR).
  - A animação dura de `0.0 s` a `5.4 s` (`POP_END=0.3`, `HOLD_END=5.0`, `TITLE_END=5.4`, `ffmpeg.py:130-154`), aplicando *pop* de escala (`60% -> 108% -> 100%` nos primeiros `300 ms`) e fade-out nos últimos `400 ms`.
  - Em paralelo, `_blur_chain` (`ffmpeg.py:168-175`) duplica o stream de vídeo no `filter_complex`, recorta a faixa superior (`CAM_H=768` com webcam ou `CANVAS_H//3=640` sem webcam, `layout.py:95-100`), aplica `trim=end=5.5,boxblur=28:3,colorchannelmixer=rr=0.45:gg=0.45:bb=0.45,format=yuva420p,fade=in/out` e faz `overlay` sob o texto nos primeiros 5,4 s.
- **O que aproveitar e o que descartar no `clipes-lives`:**
  - **Aproveitar:** a regra editorial do gancho (`<= 35 chars`, pergunta/promessa sem entregar o punchline, sem emoji), a quebra em linhas curtas (`<= 18 chars`), a alternância cromática branco/amarelo por linha (`\N`) e a animação ASS de *pop* + `\fad` nos primeiros ~5 s.
  - **Descartar:** (1) o segundo arquivo `.ass` separado e (2) o `_blur_chain` no `filter_complex`. No `clipes-lives`, o vídeo 16:9 centralizado ocupa `Y = 656..1264` (`clipes/render.py:308-316`), deixando `Y = 0..656` **já escuro (barras pretas por padrão, `CLIPES_BLUR_BG=0`) ou já borrado (`CLIPES_BLUR_BG=1`)**. Incluir o estilo `TopHook` diretamente no `build_ass` existente (`clipes/render.py:98`) tem **custo zero extra de filtro FFmpeg/CPU**.

### 1.2. Legenda Queimada e Descrição de Publicação
- **Como o LiveCliper faz:**
  - **Legenda no vídeo (`cardosoGu/LiveCliper:src/liveclips/processing/subtitles.py:11-163`):** agrupa até `MAX_WORDS=3` ou `MAX_CHARS=20` por bloco (`MAX_GAP_S=0.6`, `MAX_CUE_S=2.6`), converte tudo para CAIXA ALTA (`.upper()`) e destaca a palavra falada com cor rosa/roxa (`&H00FF5FC8&`) e leve zoom (`\fscx112\fscy112`). No render dividido (`layout.py:15-16`), ancora a legenda na costura (`SEAM_Y=768`) ou em `FULL_CAPTION_Y=1280`.
  - **Descrição do post (`cardosoGu/LiveCliper:src/liveclips/publishing/caption.py:7-15`):** `build_caption` monta `{verdict.title}\n\n{verdict.caption}\n\n🎥 Créditos: {credit}\n\n{tags}` até `max_len=2200`, truncando apenas o corpo se estourar o limite, nunca cortando créditos nem hashtags.
- **Comparação com o `clipes-lives`:**
  - Nosso `clipes/render.py:182-215` já faz word-highlight em `.ass` (`PlayRes 1080x1920`, branco + amarelo `&H0000FFFF&`, `SAFE_MARGIN_V=560`, `SAFE_MARGIN_L=120`, `SAFE_MARGIN_R=200`), e `clipes/llm.py:508-523` já formata a legenda curta de 3 linhas (`format_three_line_caption`). O que falta no `clipes-lives` é a **descrição longa** (Item 7) para YouTube Shorts e Discord, mantendo a preservação garantida de créditos/link/hashtags no rodapé.

### 1.3. Cascata Gemini → Claude com Teto Diário
- **Como o LiveCliper faz (`cardosoGu/LiveCliper:src/liveclips/ai/`):**
  - **Configuração (`core/config.py:89-93,108-113`):** aceita `GEMINI_API_KEY` e `GEMINI_API_KEY1..N` (ordenadas numericamente e deduplicadas) e `GEMINI_MODEL` como lista separada por vírgulas.
  - **Ordem de tentativa no Gemini (`ai/gemini.py:40-61`):** faz um loop duplo `for model in model_list(cfg.gemini_model): for key in keys:`. Ou seja, esgota todas as chaves no modelo preferido antes de cair para o modelo secundário.
  - **Fallback pago no Claude (`ai/evaluator.py:36-59`, `ai/claude.py:13-62`):** controla o gasto diário na tabela `kv` do SQLite com a chave `claude_spend:YYYY-MM-DD`. Antes de chamar a API da Anthropic, verifica `claude_spent_today() >= cfg.claude_daily_budget_usd` (padrão `US$ 3.00`). Após cada chamada, calcula `claude_cost_usd(model, input_tokens, output_tokens)` e soma no banco (`kv_incr_float`). Se `stop_reason == "refusal"`, devolve veredito seguro de rejeição (`safe=False, score=0`).
- **Diferença crítica para o `clipes-lives`:**
  - O LiveCliper envia o **arquivo MP4 inteiro** ao Gemini (`Part.from_bytes` até 18 MB ou Files API, `ai/gemini.py:47-53`) e **8 frames JPEG 720p** ao Claude (`ai/claude.py:38-45`) porque lá a IA decide sozinha se o clipe de gameplay sem fala presta e onde fica a webcam (`facecam`).
  - No `clipes-lives`, o pipeline local (`signals.py`, `candidates.py`, `asr.py`, `topics.py`, `snap.py`, `reframe.py`) **já detecta picos, transcreve, segmenta tópicos e detecta rosto (YuNet)** localmente. Enviar vídeo/frames para a nuvem custaria ~15.000 tokens por clipe no Gemini/Claude sem necessidade. No `clipes-lives`, a cascata de nuvem deve enviar **apenas o prompt textual sanitizado (`_build_clip_prompt`, ~400–600 tokens de entrada)** e ter o **Qwen3-1.7B local como 3º nível** antes do fallback heurístico.

### 1.4. Bot do Discord (Comandos, Fila, Recompressão e Spec de Threads)
- **Como o LiveCliper faz:**
  - Em `cardosoGu/LiveCliper:src/liveclips/control/discord_bot.py:16-125`, usa `discord.py>=2.4` com `discord.Client(intents=discord.Intents.default())` e `app_commands.CommandTree`. Na conexão (`on_ready`), copia os slash commands globais para a guild do canal (`copy_global_to` + `tree.sync(guild=guild)`), o que faz os comandos aparecerem instantaneamente no servidor (sync global do Discord leva até 1 h).
  - Em `cardosoGu/LiveCliper:src/liveclips/control/monitor.py:56-102`, isola o Discord atrás de uma `asyncio.Queue(maxsize=200)` (`_outbox`): o pipeline nunca bloqueia esperando upload para o Discord.
  - Em `cardosoGu/LiveCliper:src/liveclips/processing/ffmpeg.py:248-254`, `make_preview` recomprime qualquer clipe para `scale=-2:1280`, `-b:v 1200k -maxrate 1500k -bufsize 3000k`, `-b:a 96k`, `-t 60`.
  - Na spec `cardosoGu/LiveCliper:docs/superpowers/specs/2026-11-17-supoclip-discord-design.md:28-60,85-98`, desenha o fluxo por **thread** (cria uma thread por vídeo, posta cada clipe numerado na thread) e melhora a recompressão: só recomprime se passar de `DISCORD_UPLOAD_LIMIT_MB` (padrão 10 MB), calculando o bitrate de vídeo pela duração para caber em **95% do limite** com áudio AAC a 96k.

### 1.5. Publishers TikTok e YouTube no LiveCliper
- **TikTok (`cardosoGu/LiveCliper:src/liveclips/publishing/platforms/tiktok.py:17-79`):**
  - Usa `httpx.AsyncClient` direto na API v2 (`https://open.tiktokapis.com/v2`): renova o token em `/oauth/token/`, salva eventual novo `refresh_token` via callback `on_refresh` no SQLite `kv`, inicializa upload em `/post/publish/video/init/` (`source="FILE_UPLOAD"`, 1 chunk do tamanho total do arquivo), faz `PUT` na `upload_url` com `Content-Range` e consulta `/post/publish/status/fetch/` a cada 5 s por até 60 tentativas (5 min).
  - **Falha identificada no LiveCliper:** ele não chama `/v2/post/publish/creator_info/query/` antes de postar, endpoint que o TikTok exige durante a auditoria da Content Posting API (`https://developers.tiktok.com/doc/content-sharing-guidelines/`).
- **YouTube (`cardosoGu/LiveCliper:src/liveclips/publishing/platforms/youtube.py:11-60`):**
  - Usa `google-api-python-client` (`videos().insert` com `MediaFileUpload(..., resumable=True)`) dentro de `asyncio.to_thread`, preenchendo `snippet.title[:100]`, `snippet.description[:5000]`, `categoryId="20"`, `status.privacyStatus` (`private` ou `public`) e `selfDeclaredMadeForKids=False`.

---

## 2. Item (d) / Item 8 (Onda A): Camada de IA em Nuvem (Gemini + Claude) + Qwen Local

### 2.1. Decisão Recomendada
Implementar em `clipes/llm.py` uma cascata determinística de **4 níveis** para enriquecimento textual e nota de desempate, mantendo a mesma validação pós-geração (`validate_title`, `is_title_echo`, `validate_hook`, `validate_no_hallucinated_entities`, `sanitize_hashtags` em `clipes/llm.py:363-505`):

```
[1] Gemini API (Free Tier / Paid) — loop: modelos (GEMINI_MODELS) × chaves (GEMINI_API_KEYS)
       │ (se 429/503/timeout/erro de schema em todos os pares modelo×chave, ou sem chave)
       ▼
[2] Anthropic Claude API — se ANTHROPIC_API_KEY presente e gasto_hoje < CLAUDE_DAILY_BUDGET_USD
       │ (se teto diário atingido, erro de API, recusa ou sem chave)
       ▼
[3] Qwen3-1.7B-Q4_K_M local (llama-server) — sobe SOMENTE para os clipes pendentes se CLIPES_LLM_LOCAL_FALLBACK=1
       │ (se binário/GGUF ausente, timeout ou --no-llm)
       ▼
[4] Heurística determinística (generate_title + sanitize_hashtags em clipes/render.py:26 e llm.py:577)
```

- **Sem SDKs pesados no container:** tanto a API do Gemini (`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`) quanto a API Messages da Anthropic (`POST https://api.anthropic.com/v1/messages`) são endpoints HTTP JSON simples que aceitam `JSON Schema` nativo. Usar `urllib.request` da biblioteca padrão do Python (já utilizado para o `llama-server` em `clipes/llm.py:15,320`) evita instalar `google-genai`, `anthropic`, `grpcio` e `pydantic` na imagem Docker (`Dockerfile:34-40`).

### 2.2. Modelos Atuais, Cotas e Custos (Verificados em Nov/2026)

| Provedor / Nível | Modelos Recomendados (Ordem de Cascata) | Cota Gratuita / Limites | Custo (para 1 clipe ≈ 600 tokens in + 250 tokens out) | Fonte Oficial |
|---|---|---|---|---|
| **1. Google Gemini API** (`generativelanguage.googleapis.com`) | Padrão: `gemini-2.5-flash,gemini-2.5-flash-lite` (ou `gemini-3.5-flash` se disponível na conta; configurável via `GEMINI_MODELS`) | **Free Tier (Google AI Studio sem billing):** ~5 a 15 RPM, 250.000 TPM e **20 a 500 RPD** por projeto Google Cloud (varia por modelo Flash vs. Flash-Lite). Cada chave de projeto distinto adiciona uma cota RPD independente. | **US$ 0,00** no Free Tier.<br>No Paid Tier: ~US$ 0,15–0,75 / 1M in e US$ 0,60–3,75 / 1M out (**~US$ 0,0005 a US$ 0,0014 por clipe**). | [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits) / [Gemini API Pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| **2. Anthropic Claude API** (`api.anthropic.com/v1/messages`) | Padrão: `claude-haiku-4-5` (ou `claude-haiku-5-5` / `claude-sonnet-4-5` via `CLAUDE_MODEL`) | **Sem Free Tier.** Exige créditos pré-pagos em `platform.claude.com` (mínimo US$ 5). Teto controlado localmente por `CLAUDE_DAILY_BUDGET_USD` (padrão recomendado: **`US$ 1.00/dia`**). | **Haiku:** US$ 0,10–0,80 / 1M in e US$ 0,50–4,00 / 1M out (**~US$ 0,0002 a US$ 0,0015 por clipe** — US$ 1/dia cobre **> 600 clipes/dia**).<br>**Sonnet:** US$ 2,00–3,00 / 1M in e US$ 10,00–15,00 / 1M out (**~US$ 0,005 por clipe**). | [Anthropic Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing) |
| **3. Local (`llama-server`)** | `Qwen3-1.7B-Q4_K_M.gguf` (`Dockerfile:55`, `clipes/llm.py:25`) | Ilimitado offline, mas consome ~2,0 GB RAM e ~8–15 s de CPU intensa por clipe (`CONSOLIDADO.md:70,91`). | **US$ 0,00** (custo térmico/tempo de CPU local). | `clipes/llm.py:210-273`, `CONSOLIDADO.md:70` |

> **Nota sobre os nomes de modelos do LiveCliper:** O `cardosoGu/LiveCliper` usa os strings `gemini-3.5-flash,gemini-3.8-flash` (`config.py:110`) e `claude-opus-5-5` / `claude-haiku-5-5` (`claude.py:14-16`). Como a disponibilidade exata de IDs de modelos varia conforme o lançamento e a conta, `GEMINI_MODELS` e `CLAUDE_MODEL` devem vir de variáveis de ambiente com defaults seguros e tabela de preços por prefixo (`haiku`, `sonnet`, `opus`).

### 2.3. Privacidade dos Dados (Implicação Obrigatória para Registro em `STATE.md`)
Reverter a decisão "100% local" na etapa de LLM (`PEDIDO.md:16`) significa que **o nome do canal, o título da transmissão e até 350 palavras da transcrição de cada clipe (`clipes/llm.py:557-562`) saem da máquina do usuário via HTTPS**:
1. **Google Gemini API — Diferença crítica entre Free Tier e Paid Tier:**
   - Segundo os Termos Adicionais da Gemini API (`https://ai.google.dev/gemini-api/terms`, seção *"Unpaid Services"* vs. *"Paid Services"*):
     - No **Free Tier (Unpaid Services)**: o Google **pode usar os prompts, contextos e respostas enviados para fornecer, manter, desenvolver e melhorar seus produtos e modelos de machine learning**, incluindo revisão por avaliadores humanos.
     - No **Paid Tier (quando há conta de faturamento Cloud Billing vinculada ao projeto)**: o Google **não utiliza** os prompts nem as saídas para treinar seus modelos.
2. **Anthropic Claude API:**
   - Segundo os Termos Comerciais da Anthropic (`https://www.anthropic.com/legal/commercial-terms` e `https://privacy.anthropic.com`), a Anthropic **não treina seus modelos com dados enviados via API comercial** por padrão; os logs são retidos temporariamente apenas para segurança/Trust & Safety (até 30 dias).
3. **Mitigação arquitetural no `clipes-lives`:**
   - Enviar **exclusivamente texto** (canal, título da live e transcrição de fala pública de uma live já transmitida publicamente na Twitch/YouTube), **nunca enviando quadros de vídeo, áudio bruto, variáveis de ambiente ou dados locais do host**.
   - Manter a flag `--no-llm` (`clipes/cli.py:55`) e adicionar `CLIPES_LLM_MODE=cloud|local|off` (padrão `cloud`) para que o usuário possa forçar `local` a qualquer momento.

### 2.4. Alternativas Descartadas
- **Enviar vídeo MP4 ou 8 frames JPEG para Gemini/Claude (como faz o LiveCliper):** Descartado. Aumenta o consumo de tokens em ~25x por clipe, esgota a cota diária gratuita do Gemini em poucas lives, aumenta a latência de upload na conexão doméstica e envia imagem biométrica do streamer para a nuvem sem necessidade, pois o `clipes-lives` já pontua sinais e enquadra localmente (`signals.py`, `snap.py`, `reframe.py`).
- **Subir o `llama-server` sempre no início de `enrich_clips_with_llm`:** Descartado. Hoje `clipes/llm.py:662` abre `with llama_server_session():` antes do loop de clipes. Com a nuvem no 1º/2º nível, o `llama-server` deve ser instanciado **de forma preguiçosa (*lazy*)**, apenas se sobrar algum clipe sem resposta da nuvem. Em execuções normais com Gemini ativo, o `llama-server` sequer é iniciado, economizando ~2 GB de RAM e ~2 min de carga térmica no Ryzen 5 5500U (`CONTINUAR.md:6`).
- **Usar `claude-opus` como reserva padrão:** Descartado para geração de metadados textuais. Para gerar título, gancho, descrição de 3 frases e 5 hashtags a partir de 350 palavras de transcrição, um modelo da classe Haiku (`claude-haiku-4-5` / `claude-haiku-5-5`) custa **20x a 40x menos** que o Opus e entrega JSON estruturado em `< 1,5 s`.

---

## 3. Itens 6 e 7 (Onda A): Título, Nome do Arquivo (`clip_NN_<slug>.mp4`), Descrição Curta e Longa

### 3.1. Novo Schema Unificado (`CLIP_JSON_SCHEMA` em `clipes/llm.py`)
Atualmente `CLIP_JSON_SCHEMA` (`clipes/llm.py:33-49`) pede `titulo`, `gancho`, `hashtags`, `nota` e `motivo`. Para cobrir os **Itens 5, 6 e 7** em **uma única chamada de LLM por clipe** (tanto na nuvem quanto no Qwen local), o schema deve passar a:

```python
CLIP_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "titulo_topo": {"type": "string", "maxLength": 35},
        "titulo": {"type": "string", "maxLength": 60},
        "gancho": {"type": "string", "maxLength": 140},
        "descricao_longa": {"type": "string", "maxLength": 500},
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
        },
        "nota": {"type": "number", "minimum": 0, "maximum": 10},
        "motivo": {"type": "string", "maxLength": 120},
    },
    "required": [
        "titulo_topo", "titulo", "gancho", "descricao_longa",
        "hashtags", "nota", "motivo",
    ],
    "additionalProperties": False,
}
```

### 3.2. Geração e Validação de Cada Campo
1. **`titulo` (Item 6 — Título do Vídeo, `<= 60 chars`):**
   - Mantém as travas existentes em `clipes/llm.py:418-439`: `validate_title` (`<= 60 chars`, contém `?` ou `!`, `<= 1` emoji, `validate_no_hallucinated_entities`) e `is_title_echo` (rejeita se `>= 70%` das palavras vierem do título da live).
   - **Melhoria no prompt (`_build_clip_prompt`, `clipes/llm.py:554`):** instruir explicitamente o modelo a focar no **conflito, revelação, reação ou pergunta específica da fala do trecho**, proibindo títulos genéricos como *"Momento engraçado na live"* ou repetir o título do episódio.
2. **Nome do arquivo `clip_NN_<slug>.mp4` (Item 6):**
   - **Decisão:** derivar `<slug>` de forma **100% determinística em Python** a partir do `clip.title` final (já validado), usando a função `_fold` já existente em `clipes/llm.py:341-343`:
     - Normalizar acentos (`NFKD` → ASCII minúsculo), trocar caracteres não `[a-z0-9]+` por `_`, remover stopwords curtas (`de`, `da`, `do`, `para`, `com`, `que`, `um`, `uma`, `no`, `na`, `o`, `a`, `e`) quando o título tiver mais de 4 palavras, limitar a **até 6 palavras e 40 caracteres**, sem `_` nas pontas (fallback: `corte`).
     - Padrão de nome em `clipes/render.py:356` e `clipes/cli.py:362`:
       - Clipe único (`part_total == 1`): `clip_{idx:02d}_{slug}.mp4` (ex.: `clip_01_quanto_custou_o_carro_novo.mp4`).
       - Série (`part_total > 1`): `clip_{idx:02d}_part{clip.part_index}of{clip.part_total}_{slug}.mp4`.
   - **Alternativa descartada:** pedir o `slug` como campo separado do JSON do LLM. Descartada porque gasta tokens de saída, aumenta a chance de erro no Qwen 1.7B local e pode divergir quando `validate_title` cai no fallback heurístico.
3. **`gancho` / Legenda Curta (`caption`, `clipes/llm.py:508-523`):**
   - Mantém o formato enxuto de 3 linhas (`format_three_line_caption`):
     ```text
     <gancho de até 140 chars>
     Crédito: <canal> · Live completa: <url> [· Parte k/N]
     #tag1 #tag2 #tag3 #tag4
     ```
   - Usada no TikTok (onde descrições curtas evitam que o texto suba sobre o vídeo) e como resumo rápido.
4. **`descricao_longa` (Item 7 — Versão Longa da Descrição):**
   - **Regra no prompt:** *"2 a 4 frases (150 a 450 caracteres) contextualizando sobre o que o streamer/convidados estão falando, o motivo da reação ou a conclusão do trecho, usando apenas fatos presentes na transcrição, sem URLs e sem emojis excessivos."*
   - **Validação (`validate_long_description`):** `40 <= len(clean) <= 500`, sem URLs (`_URL_OR_MENTION_RE`), e passa por `validate_no_hallucinated_entities(clean, transcript, reference_text)` (`clipes/llm.py:363`).
   - **Fallback determinístico (se o LLM falhar ou alucinar uma entidade na descrição longa):** construir a descrição longa a partir do `clip.title` + resumo limpo das primeiras e últimas frases completas da transcrição do clipe (`clip.text[:320]`).
   - **Formato composto (`format_long_description`):**
     ```text
     <titulo>

     <descricao_longa>

     Crédito: <canal>
     Live completa: <url> [· Parte k/N]

     #tag1 #tag2 #tag3 #tag4
     ```
   - Salvar no `clips.json` nos campos `description_long` (texto completo pronto para YouTube Shorts e Discord) e `llm_description` (apenas o parágrafo contextual).

---

## 4. Item (e) / Item 5 (Onda A): Overlay de Texto no Topo do Clipe (`ASS / FFmpeg`) e Safe Zone

### 4.1. Geometria do Canvas `1080 × 1920` no `clipes-lives` e Safe Zones Verificadas
No nosso pipeline atual (`clipes/render.py:14-16,135-165,308-316`):
- **Quadro 16:9 centralizado:** ocupa `1080 × 608 px`, de **`Y = 656 px` até `Y = 1264 px`**.
- **Área superior acima do vídeo 16:9:** vai de **`Y = 0 px` até `Y = 656 px`**.
- **Elementos que já existem nessa área superior (`clipes/render.py:146-165`):**
  - `PartBadge` (`Parte k/N`, quando `part_total > 1`): `\an8\pos(540, 490)` → ocupa **`Y ≈ 490..548 px`**.
  - `Credit` (`twitch.tv/<canal>`): `\an7\pos(120, 600)` → ocupa **`Y ≈ 600..642 px`**.
- **Safe Zone Superior das Plataformas (1080×1920):**
  - **TikTok:** os primeiros **`240 px` superiores (`Y = 0..240 px`)** são cobertos pela barra de status do sistema, botão de busca, ícone de LIVE e abas *"Seguindo | Para Você"* ([TikTok Business Help Center — In-Feed Ad Safe Zone Specs](https://ads.tiktok.com/resources/help/article/tiktok-interactive-add-on-download-card-ad-specifications?lang=en), verificado em `2026-11-17`). A coluna de botões da direita (perfil, like, comentários) só começa em **`Y = 840 px`** para baixo.
  - **Instagram Reels e YouTube Shorts:** a UI superior (câmera, pesquisa, menu) ocupa **`Y = 0..250 px`** (chegando a `~280 px` em aparelhos com *Dynamic Island* / *notch* alto, `pesquisa/A-plataformas-argon.md:84-89`).
- **Conclusão geométrica exata:**
  - A janela vertical **100% livre** de UI das plataformas **e** livre de colisão com `PartBadge` (`Y=490`) e `Credit` (`Y=600`) é **`Y = 260 px` até `Y = 475 px`** (`215 px` de altura útil!), cujo centro exato é **`Y = 365 px`**.

```
Y = 0   ┌──────────────────────────────────────┐
        │   UI Superior TikTok/Reels/Shorts    │  <- Inseguro (0..250 px)
Y = 250 ├──────────────────────────────────────┤
        │  ┌────────────────────────────────┐  │
Y = 365 │  │  GANCHO DO TOPO (LINHA 1 BR)   │  │  <- TopHook ASS (\an5\pos(540,365))
        │  │  GANCHO DO TOPO (LINHA 2 AM)   │  │     0.0s -> 5.0s (2 linhas <= 18 chars)
        │  └────────────────────────────────┘  │
Y = 490 │            [Parte 1/2]               │  <- PartBadge (\an8\pos(540,490))
Y = 600 │  twitch.tv/canal                     │  <- Credit (\an7\pos(120,600))
Y = 656 ├══════════════════════════════════════┤
        │                                      │
        │         VÍDEO 16:9 (1080x608)        │
        │                                      │
Y = 1264├══════════════════════════════════════┤
Y = 1360│       LEGENDA KARAOKÊ (Cap)          │  <- MarginV=560 (base em Y=1360)
Y = 1920└──────────────────────────────────────┘
```

### 4.2. Especificação da Implementação em `clipes/render.py` e `clipes/llm.py`
1. **Regras de validação do `titulo_topo` (`validate_top_hook` em `clipes/llm.py`):**
   - Remover emojis (`_EMOJI_RE.sub("", text)`), colapsar espaços, exigir `4 <= len(clean) <= 38` e validar entidades (`validate_no_hallucinated_entities(clean, transcript, reference_text)`).
   - Se o `titulo_topo` do LLM for inválido ou vazio: derivar deterministicamente de `clip.title` removendo emojis e truncando na última palavra completa até `<= 35 chars` (com `?` ou `…`).
2. **Quebra de linhas (`wrap_top_hook(text, max_chars=18, max_lines=2)` em `clipes/render.py`):**
   - Converte para `.upper()`, escapa caracteres ASS (`_ass_escape`) e divide em **no máximo 2 linhas de até 18 caracteres** (até ~35–36 chars no total).
   - Se tiver 2 linhas, a Linha 1 recebe branco (`{\c&H00FFFFFF&}`) e a Linha 2 recebe amarelo vivo (`{\c&H0000FFFF&}` em BGR), separadas por `\N`.
3. **Estilo e Evento ASS em `build_ass` (`clipes/render.py:98-165`):**
   - Adicionar ao cabeçalho `[V4+ Styles]`:
     ```text
     Style: TopHook,DejaVu Sans,64,&H00FFFFFF,&H0000FFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,6,3,5,120,120,0,1
     ```
   - Adicionar evento em `[Events]` quando `top_hook` estiver presente e `content_dur > 0`:
     - Duração: `hook_end = min(5.0, content_dur)` (padrão de `PEDIDO.md:13,36`: aparece nos primeiros ~5 s e depois some).
     - Animação:
       ```text
       Dialogue: 2,0:00:00.00,0:00:05.00,TopHook,,0,0,0,,{\an5\pos(540,365)\fad(120,350)\fscx75\fscy75\t(0,220,\fscx106\fscy106)\t(220,320,\fscx100\fscy100)}{\c&H00FFFFFF&}LINHA 1\N{\c&H0000FFFF&}LINHA 2
       ```
   - **Caso com `--reframe` ativo (`crop is not None`, vídeo preenchendo `1080x1920` inteiro, `clipes/render.py:301-303`):**
     - Como o contorno `Outline=6` + `Shadow=3` preto já garante alto contraste e o texto some aos `5.0 s`, o mesmo `Style: TopHook` funciona tanto sobre barras pretas/blur quanto sobre o vídeo reenquadrado, sem precisar de segundo passe de `boxblur`.

---

## 5. Item (b) / Item 1 (Onda C): Integração com Discord, Limite de Upload, Threads e `publicar <id>`

### 5.1. Biblioteca Recomendada e Intents
- **Biblioteca recomendada:** **`discord.py >= 2.4,<3.0`** (instalar no `Dockerfile:67` e `pyproject.toml:10`).
  - **Motivos:** biblioteca padrão em Python 3.12 (`asyncio` nativo), suporta criação de threads (`Message.create_thread` / `TextChannel.create_thread`), slash commands (`discord.app_commands.CommandTree`), botões persistentes (`discord.ui.View`) e expõe diretamente o limite de upload do servidor via `guild.filesize_limit` ([discord.py Guild.filesize_limit docs](https://discordpy.readthedocs.io/en/stable/api.html#discord.Guild.filesize_limit)).
- **Alternativas descartadas:**
  - *Webhooks HTTP puros:* descartados porque são unidirecionais (postam mensagens/arquivos, mas não conseguem escutar o comando `publicar <id>` sem expor um servidor HTTP público com verificação de assinatura Ed25519).
  - *`py-cord` / `nextcord` / `hikari`:* descartados por menor atividade de manutenção no Python 3.12+ em comparação ao `discord.py 2.4+`.
- **Intents necessárias:**
  - `intents = discord.Intents.default()` (`guilds=True`, ` messages=True` para metadados) + **`intents.message_content = True`**.
  - **Por que habilitar `message_content = True`?** Porque o pedido (`PEDIDO.md:38`) especifica o comando textual `publicar <id>`. No Discord API v10, bots só recebem o texto de mensagens normais (`message.content`) se o **Message Content Privileged Intent** estiver ligado no Developer Portal (ou se o usuário mencionar `@Bot publicar <id>`). Além disso, registrar também o **Slash Command `/publicar id:<id>`** garante funcionamento mesmo se o intent for desmarcado.

### 5.2. Limites de Upload Atuais do Discord e Estratégia de Recompressão
- **Limites da plataforma Discord (verificados):**
  - Em setembro de 2024, o Discord reduziu o limite gratuito de 25 MB para **10 MB**; em agosto de 2026, o limite padrão gratuito foi atualizado para **20 MiB** (com servidores Nível 2 = **50 MB** e Nível 3 = **100 MB**). Contudo, bots em servidores sem Boost podem encontrar tetos de **10 MiB (`10.485.760` bytes)** ou **20 MiB (`20.971.520` bytes)** dependendo da configuração da guilda.
- **Estratégia recomendada (zero chute, zero perda de qualidade na publicação):**
  1. Definir `DISCORD_UPLOAD_LIMIT_MB` (padrão conservador: **`9.5` MB**, configurável por env) e, quando conectado à guilda `1572417049932599479`, consultar também `min(cfg_limit_bytes, int(guild.filesize_limit * 0.95))`.
  2. Se o arquivo renderizado `clip_NN_<slug>.mp4` já for menor que o teto (`size <= max_bytes`): **enviar o arquivo original direto**, sem gastar CPU recomprimindo.
  3. Se exceder `max_bytes`: gerar `cache_dir / f"discord_{stem}.mp4"` com `ffmpeg` calculando o bitrate pela duração (`rendered_duration`):
     ```python
     # Alvo: 90% do limite em bits, descontando 96 kbps do áudio AAC
     total_kbps = int((max_bytes * 0.90 * 8) / (1000 * max(rendered_duration, 1.0)))
     video_kbps = max(400, min(2500, total_kbps - 96))
     ```
     Comando FFmpeg:
     ```bash
     ffmpeg -y -v error -i <src.mp4> -vf "scale=720:1280" \
       -c:v libx264 -preset veryfast -b:v <video_kbps>k -maxrate <video_kbps*1.2>k -bufsize <video_kbps*2>k \
       -pix_fmt yuv420p -c:a aac -b:a 96k -movflags +faststart <discord_preview.mp4>
     ```
  4. **Regra de ouro:** o arquivo original `1080x1920` (`out_dir / clip_NN_<slug>.mp4`) **nunca é sobrescrito** pela prévia do Discord; quando o usuário rodar `publicar <id>`, os publishers do TikTok e YouTube usam o MP4 `1080x1920` original.

### 5.3. Organização em Threads e Comandos no Canal `1572417050771595386`
1. **Ao concluir um lote de clipes (VOD ou janela de live):**
   - O bot envia uma mensagem-cabeçalho no canal `1572417050771595386` (servidor `1572417049932599479`):
     `🎬 **<canal>** — <título da live> · <N> clipe(s) gerados · URL: <url>`
   - Cria imediatamente uma **thread pública** a partir dessa mensagem:
     `thread = await header_msg.create_thread(name=f"{channel} · {title[:70]}", auto_archive_duration=1440)`
   - Dentro da thread, posta 1 mensagem por clipe contendo:
     - **ID estável:** `` `<clip_id>` `` (ex.: `UBv07VGeD24_c01` ou `gaules_1763419200_c01`)
     - **Arquivo:** `` `clip_01_<slug>.mp4` `` | **Duração:** `48.2s` | **Score:** `0.812` | **Nota IA:** `8.5/10` (`gemini:gemini-2.5-flash`)
     - **Gancho de topo:** `<titulo_topo>`
     - **Título:** `<titulo>`
     - **Legenda curta (TikTok):** bloco `text`
     - **Descrição longa (YouTube Shorts):** bloco `text`
     - **Status de política (`policy.json`):** `Liberado para publicar` ou `⏳ Aguardar delay_h até <data/hora>`
     - Anexo: vídeo MP4 (original se couber, ou prévia `720p` recomprimida)
     - Instrução: *"Para publicar no TikTok e YouTube Shorts (modo privado até auditoria), envie `publicar <clip_id>` ou `/publicar <clip_id>`."*
2. **Comandos suportados (restritos a `DISCORD_ADMIN_USER_ID` quando configurado):**
   - `publicar <id>` (mensagem no canal/thread) e `/publicar id:<id>` (slash command): dispara a publicação do clipe `<id>` no TikTok e no YouTube Shorts e responde na thread com o resultado (`post_id` / link de cada plataforma).
   - `status` / `/status`: mostra fila, PCs ativos (quando integrado à Onda B), cotas de IA do dia (`gemini` / gasto `claude_spend`) e últimos clipes.
   - `listar` / `/listar`: lista os últimos 10 `<clip_id>` prontos para publicar.
   - `bloquear <canal>` / `desbloquear <canal>`: atualiza `policy.json`.

### 5.4. Passo a Passo do Discord Developer Portal (Para Execução Posterior via Chrome)
> **Restrição (`PEDIDO.md:7,26`):** Nenhuma credencial é criada agora nem salva em arquivo versionado. Quando for configurar pelo navegador Chrome:

1. Acessar `https://discord.com/developers/applications` e clicar em **New Application** (ex.: nome `clipes-lives`).
2. No menu lateral, abrir **Bot**:
   - Clicar em **Reset Token**, confirmar e copiar o token para a variável `DISCORD_BOT_TOKEN` no `.env` local (fora do Git).
   - Descer até a seção **Privileged Gateway Intents** e ativar **Message Content Intent** → clicar em **Save Changes**.
3. No menu lateral, abrir **OAuth2**:
   - Em **OAuth2 URL Generator**, marcar os escopos (`Scopes`): **`bot`** e **`applications.commands`**.
   - Em **Bot Permissions** (que aparece abaixo), marcar:
     - `View Channels`
     - `Send Messages`
     - `Create Public Threads`
     - `Send Messages in Threads`
     - `Attach Files`
     - `Embed Links`
     - `Read Message History`
     - `Add Reactions`
     - `Use Slash Commands`
     *(Inteiro de permissões correspondente: `395137255488`)*.
   - Copiar a **Generated URL**, abri-la no Chrome, selecionar o servidor de ID `1572417049932599479` e clicar em **Authorize**.
4. No aplicativo do Discord (Configurações de Usuário → Avançado → ativar **Modo Desenvolvedor**):
   - Clicar com o botão direito no próprio perfil → **Copiar ID do usuário** e preencher `DISCORD_ADMIN_USER_ID` no `.env`.
   - Confirmar `DISCORD_GUILD_ID=1572417049932599479` e `DISCORD_CHANNEL_ID=1572417050771595386` no `.env`.

---

## 6. Item (c) / Item 3 (Onda C): TikTok Content Posting API e YouTube Data API v3 (Shorts)

### 6.1. Reversão da `AD-006`
A decisão `AD-006` (`STATE.md:9`) definia *"publicação e anti-shadowban fora de escopo"*. Com o Item 3 (`PEDIDO.md:11,38`), deve ser registrada no `STATE.md` uma nova decisão (**`AD-008`**):
- *Publicação habilitada exclusivamente sob disparo humano explícito (`publicar <id>` via Discord), operando em modo **PRIVADO** (`TIKTOK_MODE=private` -> `SELF_ONLY`; `YT_MODE=private` -> `privacyStatus="private"`) até a aprovação formal das auditorias das APIs, e condicionada a `policy.permits(channel)` e ao cumprimento de `delay_h` (`ready_at <= now`).*

### 6.2. Comparativo Técnico: TikTok Content Posting API vs. YouTube Data API v3

| Aspecto | TikTok Content Posting API (Direct Post v2) | YouTube Data API v3 (`videos.insert` para Shorts) |
|---|---|---|
| **Documentação Oficial** | `https://developers.tiktok.com/doc/content-posting-api-get-started` e `https://developers.tiktok.com/doc/content-posting-api-reference-direct-post` | `https://developers.google.com/youtube/v3/docs/videos/insert` e `https://support.google.com/youtube/answer/15424877` |
| **Escopos OAuth 2.0** | `user.info.basic`, `video.publish` (Direct Post) | `https://www.googleapis.com/auth/youtube.upload` (ou `.../auth/youtube` se incluir deleção via API) |
| **Comportamento SEM Auditoria (Modo Privado)** | Clientes não auditados **só podem publicar com `privacy_level = "SELF_ONLY"`** (privado). Além disso, a conta TikTok de teste **precisa estar configurada como Conta Privada** no app do TikTok no momento do envio; caso contrário a API retorna erro `unaudited_client_can_only_post_to_private_accounts`. | Projetos Google Cloud que não passaram pelo *YouTube API Services Compliance Audit* têm qualquer vídeo enviado via API **travado automaticamente como Privado (`locked as private`)**, independentemente do `privacyStatus` enviado no payload, e não podem ser alterados para público nem manualmente no YouTube Studio. |
| **Limites e Cotas Diárias** | Sem auditoria: máx. **5 contas de usuários distintas por 24 h**; limite de ~15–30 posts/24 h por criador (consultado dinamicamente em `/v2/post/publish/creator_info/query/`). Tamanho do arquivo: até **4 GB** (upload em 1 chunk permitido até **64 MB**; nossos clipes de 60 s têm ~15–35 MB). | Cota padrão do projeto GCP: **10.000 unidades/dia** gerais (historicamente `videos.insert` = **1.600 unidades/chamada** = máx. **6 uploads/dia**; na tabela atualizada do Google Developers há também um bucket dedicado de **100 chamadas `videos.insert`/dia** a 1 unidade do bucket de upload — considerar **6 uploads/dia** como piso conservador até verificar o painel Quotas do projeto GCP do usuário). |
| **Cuidado Crítico com Refresh Token** | O endpoint `POST https://open.tiktokapis.com/v2/oauth/token/` (`refresh_token` válido por 365 dias) **pode devolver um novo `refresh_token` rotacionado** a cada renovação. O código **deve** persistir o novo token (em arquivo local `.tokens.json` fora do Git ou no Mongo criptografado/kv) para não perder o acesso na próxima execução. | No Google Cloud Console, se a *OAuth Consent Screen* estiver com status **"Testing"**, o `refresh_token` **expira em 7 dias** ([Google OAuth 2.0 Doc](https://developers.google.com/identity/protocols/oauth2#expiration)). Para o `refresh_token` não expirar em 7 dias durante a fase privada, mudar o *Publishing status* da tela OAuth para **"In production"** (mesmo antes da auditoria do YouTube, o usuário dono do projeto consegue autorizar a própria conta). |
| **Como o vídeo entra como Short / Vertical** | Vídeo MP4 H.264 `1080x1920`, `post_info.title` recebe a legenda (`caption` de até 2.200 UTF-16 runes). | Não há endpoint separado para Shorts: todo vídeo enviado a `videos.insert` com proporção **vertical (9:16) ou 1:1** e duração **<= 180 s (3 min)** é classificado automaticamente como Short pelo YouTube. Recomenda-se incluir `#Shorts` no `snippet.title` (`<= 100 chars`) ou `snippet.description` (`<= 5.000 bytes`). |
| **O que é exigido para aprovar a Auditoria (Modo Público)** | Submeter revisão no portal `developers.tiktok.com` com: (1) domínio web verificado com Termos de Uso e Política de Privacidade; (2) vídeo de gravação de tela mostrando o fluxo OAuth completo e a tela de publicação; (3) chamada obrigatória a `/v2/post/publish/creator_info/query/` exibindo opções de privacidade, duet/stitch/comment e aviso de consentimento da diretriz de UX (`https://developers.tiktok.com/doc/content-sharing-guidelines/`). | Preencher o formulário oficial **YouTube API Services – Audit and Quota Extension Form** (`https://support.google.com/youtube/contact/yt_api_form`), selecionando *"Complete a compliance audit"*, anexando descrição arquitetural e screencast demonstrando como o usuário aciona o upload. |

### 6.3. Fluxo Técnico Recomendado para `clipes/publish.py`
1. **Entrada:** `publish_clip(clip_id: str, out_root: Path, platforms: tuple[str, ...] = ("tiktok", "youtube"))`.
2. **Localização e Travas de Segurança:**
   - Localiza o `clip_entry` pelo `clip_id` nos manifestos `clips.json` (ou coleção `clips` no Mongo na Onda B/C).
   - Verifica `policy.permits(channel)` e `time.time() >= ready_at` (respeitando `delay_h` do canal, ex.: 24 h para Gaules, `CONSOLIDADO.md:130`).
   - Verifica `TIKTOK_MODE` e `YT_MODE` (`off | private | public`, padrão `private`).
   - Carrega o arquivo MP4 `1080x1920` original (`out_dir / clip_entry["file"]`), **nunca** o arquivo de prévia do Discord.
3. **TikTok (`TikTokPublisher`, via `urllib.request` ou `httpx`):**
   - Passo 1: `POST /v2/oauth/token/` → obtém `access_token` e persiste `refresh_token` atualizado.
   - Passo 2: `POST /v2/post/publish/creator_info/query/` → valida que a conta pode postar agora e que o `privacy_level` configurado (`SELF_ONLY` quando `TIKTOK_MODE=private`, `PUBLIC_TO_EVERYONE` quando `TIKTOK_MODE=public`) está em `privacy_level_options` (se não estiver e o modo for `private`, força `SELF_ONLY`; se o modo for `public` e só houver `SELF_ONLY`, aborta com erro claro informando que o app ainda não foi auditado).
   - Passo 3: `POST /v2/post/publish/video/init/` com `source="FILE_UPLOAD"`, `video_size=size`, `chunk_size=size`, `total_chunk_count=1` (para `size <= 64 MB`; se `> 64 MB`, divide em chunks de 10 MB) e `title = clip_entry["caption"][:2200]`.
   - Passo 4: `PUT <upload_url>` com `Content-Range: bytes 0-{size-1}/{size}`.
   - Passo 5: Polling a cada 5 s (até 60x) em `POST /v2/post/publish/status/fetch/` até `PUBLISH_COMPLETE`.
4. **YouTube (`YouTubePublisher`):**
   - Para evitar instalar `google-api-python-client` se quisermos manter a imagem leve (ou usando REST direto): o Resumable Upload do YouTube Data API v3 é apenas 2 chamadas HTTP (`POST https://oauth2.googleapis.com/token` para renovar o `access_token`, seguido de `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` que retorna o header `Location` para `PUT` do binário MP4) ([YouTube Resumable Upload Guide](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)).
   - `snippet.title`: `f"{clip_entry['title'][:90]} #Shorts"` (`<= 100 chars`).
   - `snippet.description`: `clip_entry["description_long"][:5000]`.
   - `snippet.categoryId`: `"20"` (Gaming) ou `"24"` (Entertainment).
   - `status`: `{"privacyStatus": "private" if yt_mode == "private" else "public", "selfDeclaredMadeForKids": False}`.
5. **Persistência do resultado:**
   - Grava no `clips.json` (e no Mongo na Onda C) o bloco `publications: [{"platform": ..., "post_id": ..., "url": ..., "privacy": ..., "published_at": ...}]`, impedindo republicação acidental do mesmo `clip_id` (a menos que `--force` seja passado).

### 6.4. Riscos de Conteúdo Reutilizado, Direitos Autorais e Mitigações
Conforme verificado nas políticas oficiais das plataformas (`pesquisa/E-originalidade-argon.md:19-77`, `CONSOLIDADO.md:30,159-166`):
1. **YouTube Shorts (> 60 s + Content ID):** Qualquer Short com **mais de 60 segundos** que receba reivindicação do Content ID (ex.: música tocando ao fundo na live) fica **bloqueado no mundo todo** (`https://support.google.com/youtube/answer/15424877`).
   - *Mitigação já presente e que deve ser preservada:* manter partes em `<= 58 s` de conteúdo + `1,2 s` de encerramento = **`<= 59,2 s`** (`CONSOLIDADO.md:30`).
2. **YouTube YPP (*Reused Content* e *Inauthentic Content*, atualizado em 15/07/2025, `https://support.google.com/youtube/answer/1311392`) e TikTok (*Ineligible for the For You feed*, `https://www.tiktok.com/support/faq_detail?id=7543604781940791864`):**
   - Cortes de lives de terceiros apenas legendados não qualificam para monetização direta de plataforma (AdSense YPP / TikTok Creator Rewards) e podem sofrer filtro de recomendação se subidos em rajada ou idênticos a outros canais.
   - *Mitigações no `clipes-lives`:* (a) aprovação humana via `publicar <id>` no Discord (não publica tudo em massa automaticamente); (b) adição do **gancho interpretativo no topo** (Item 5), **descrição longa contextualizada** (Item 7), **crédito na tela** e **cartão de encerramento**; (c) cumprimento estrito do `policy.json` (`allowed`, `blocked` e `delay_h`, como as 24 h exigidas pelo Gaules para evitar DMCA Strike por "furar a live").

---

## 7. Tarefas Sugeridas por Onda (Para o Planejador `tlc-spec-driven`)

### Onda A — Camada de IA em Nuvem, Título/Slug, Descrição Longa e Gancho no Topo
*(Ordem serial recomendada em `PEDIDO.md:19`: primeiro item 8, depois itens 6, 7 e 5, pois compartilham `clipes/llm.py`, `clipes/models.py`, `clipes/render.py` e `clipes/cli.py`).*

- **T-A1 (Item 8 — Clientes de Nuvem e Cascata em `clipes/llm.py`):**
  - Implementar `gemini_complete(prompt, schema, models, api_keys, timeout)` (loop `modelo × chave` via REST `urllib.request`) e `claude_complete(prompt, schema, model, api_key, timeout)` com controle de orçamento diário (`CLAUDE_DAILY_BUDGET_USD`, persistido atomicamente em `OUT_DIR/.llm_budget.json`).
  - Refatorar `enrich_clips_with_llm` para executar a cascata `Gemini -> Claude -> Qwen local (lazy llama_server_session) -> fallback heurístico`, preenchendo `clip.llm_provider`.
  - Registrar decisão arquitetural no `STATE.md` (reversão parcial do "100% local" no LLM e nota de privacidade do Gemini Free Tier).
- **T-A2 (Itens 6 e 7 — Schema Estendido, Descrição Longa, Slug e ID Estável em `llm.py`, `models.py`, `cli.py`):**
  - Adicionar `top_hook`, `description_long`, `llm_provider` e `clip_id` em `SnappedClip` (`clipes/models.py:87`).
  - Atualizar `CLIP_JSON_SCHEMA` e `_build_clip_prompt` (`clipes/llm.py:33,554`) com `titulo_topo` (`<= 35 chars`) e `descricao_longa` (`150..500 chars`), mais validadores `validate_top_hook`, `validate_long_description` e `format_long_description`.
  - Implementar `slugify_title(title: str) -> str` e atualizar a nomenclatura de saída em `render.py:356` e `cli.py:362` para `clip_{idx:02d}_{slug}.mp4` (e `clip_{idx:02d}_part{k}of{N}_{slug}.mp4`), gravando `clip_id` estável e `description_long` no `clips.json` e no `README.md`.
- **T-A3 (Item 5 — Overlay de Gancho no Topo em `clipes/render.py`):**
  - Implementar `wrap_top_hook(text, max_chars=18, max_lines=2)` e adicionar o estilo `TopHook` + evento `Dialogue` (`0.0 s` a `min(5.0, content_dur)`, `\an5\pos(540,365)`, *pop* + `\fad(120,350)`, linhas alternando branco e amarelo) em `build_ass` (`clipes/render.py:98`).
- **T-A4 (Testes Unitários da Onda A em `tests/`):**
  - Testes cobrindo: cascata Gemini (múltiplas chaves/modelos) → Claude (respeito ao teto diário em USD) → Qwen local (não sobe `llama-server` quando Gemini responde) → fallback heurístico; geração de `slug` sem acentos/caracteres inválidos; validação anti-alucinação em `descricao_longa` e `titulo_topo`; geração do evento `TopHook` no `.ass` dentro da safe zone (`Y=365`, `<= 5.0 s`).

### Onda C — Bot do Discord e Publicação (`publicar <id>`) em TikTok e YouTube Shorts
- **T-C1 (Item 1 — Recompressão Inteligente e Bot do Discord em `clipes/discord_bot.py` + `render.py`):**
  - Adicionar `discord.py>=2.4` no `Dockerfile` e `pyproject.toml`.
  - Implementar `ensure_discord_size(mp4_path, cache_dir, max_bytes, duration) -> Path` (retorna o original se couber ou recomprime para `720x1280` com bitrate calculado pela duração sem tocar no original).
  - Implementar o bot do Discord (guild `1572417049932599479`, canal `1572417050771595386`) com fila assíncrona `_outbox`, abertura de **thread pública por job/execução**, postagem de prévia + metadados (`clip_id`, score, nota IA, legenda curta e descrição longa) e comandos `status`, `listar`, `bloquear`, `desbloquear` e `publicar <id>` (via mensagem e slash command `/publicar`).
- **T-C2 (Item 3 — Publishers TikTok e YouTube Shorts em `clipes/publish.py`):**
  - Registrar decisão revertendo `AD-006` no `STATE.md`.
  - Implementar `TikTokPublisher` (OAuth refresh com persistência de novo `refresh_token`, consulta a `creator_info/query`, `video/init` `FILE_UPLOAD`, `PUT` binário, polling de status, travado em `SELF_ONLY` quando `TIKTOK_MODE=private`).
  - Implementar `YouTubePublisher` (OAuth refresh + Resumable Upload em `videos.insert` com `privacyStatus="private"` quando `YT_MODE=private`, `categoryId="20"`, `selfDeclaredMadeForKids=False`).
  - Ligar o comando `publicar <id>` do Discord ao `publish_clip`, validando `DISCORD_ADMIN_USER_ID`, `policy.permits(channel)`, `ready_at` (`delay_h`) e idempotência (não republicar clipe já publicado sem flag explícita).
- **T-C3 (Testes Unitários da Onda C em `tests/`):**
  - Testes com mocks HTTP para: cálculo de bitrate da prévia do Discord; criação de thread e autorização por `DISCORD_ADMIN_USER_ID`; fluxo completo do `TikTokPublisher` (incluindo rotação de `refresh_token` e bloqueio de `public` em conta não auditada); fluxo do `YouTubePublisher`; bloqueio de publicação antes de `ready_at` (`delay_h`).

---

## 8. Perguntas em Aberto (Com Defaults Assumidos Caso o Usuário Não Responda)

1. **Privacidade no Gemini Free Tier vs. Paid Tier:** O usuário aceita que as transcrições dos clipes sejam enviadas ao **Gemini Free Tier** (onde o Google pode usar os dados para melhorar produtos), ou prefere vincular Cloud Billing (Paid Tier, onde o Google não treina com os dados) / usar apenas Claude API + Qwen local?
   - *Default assumido (`PEDIDO.md:16`):* usar Gemini Free Tier em cascata de chaves/modelos, pois a transcrição vem de lives públicas, registrando a implicação no `STATE.md`.
2. **Teto diário padrão do Claude (`CLAUDE_DAILY_BUDGET_USD`) e modelo:** Usar `claude-haiku-4-5` com teto de **`US$ 1.00/dia`** (cobre > 600 clipes/dia em texto) ou `US$ 3.00/dia` como no LiveCliper?
   - *Default assumido:* `CLAUDE_MODEL=claude-haiku-4-5` e `CLAUDE_DAILY_BUDGET_USD=1.00` (configuráveis via `.env`).
3. **Restrição de quem pode rodar `publicar <id>` no Discord:** Apenas o `DISCORD_ADMIN_USER_ID` ou qualquer membro que tenha acesso ao canal `1572417050771595386` no servidor `1572417049932599479`?
   - *Default assumido:* se `DISCORD_ADMIN_USER_ID` estiver definido no `.env`, restringir exclusivamente a ele; se estiver vazio, aceitar comandos apenas dentro do canal configurado (`1572417050771595386`) e de suas threads.
4. **Comportamento do `publicar <id>` quando apenas uma das duas plataformas (TikTok ou YouTube) estiver configurada no `.env`:** Publicar na que estiver ativa (`TIKTOK_MODE != off` ou `YT_MODE != off`) e reportar a outra como desligada, ou exigir ambas?
   - *Default assumido:* publicar de forma independente em todas as plataformas habilitadas (`mode != "off"`) e reportar no Discord o status individual de cada uma.

FIM