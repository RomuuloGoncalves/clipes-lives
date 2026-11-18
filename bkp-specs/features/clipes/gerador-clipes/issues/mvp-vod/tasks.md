# Gerador de clipes: Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path. The skill is the source of truth for the full flow (per-task cycle, sub-agent delegation, adequacy review, Verifier, discrimination sensor).

**If the skill cannot be activated, STOP and tell the user - do not proceed without it.**

Todas as tasks são do repo **`clipes-lives`** (`~/Projetos/clipes-lives`). Modo ponytail: menor código que passa no critério; nada de abstração com uma implementação só.

---

**Spec**: `spec.md` · **Design**: `design.md` · **Contexto/aberto**: `context.md`
**Status**: Draft (aguardando respostas do `context.md`)

---

## Test Coverage Matrix

> Repo vazio (só `chore: init`): sem guideline nem testes existentes → strong defaults aplicados. Integração usa mídia sintética gerada por `ffmpeg` em `tests/fixtures/` (sem rede). Rede real só no smoke manual.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| Lógica pura (chat, signals, score, bounds, curate, captions, report, evaluate, montagem de comando ffmpeg) | unit | Todos os ramos; 1:1 com os ACs da spec; todo edge case listado | `tests/test_<modulo>.py` | `pytest -q -m "not integration"` |
| Bordas com binário/modelo (fetch, transcribe, render, layout, live.record) | integration | Caminho feliz + erro com fixture local; marcado `@pytest.mark.integration` | `tests/test_<modulo>_int.py` | `pytest -q -m integration` |
| CLI | integration | Cada subcomando: feliz + erro de entrada | `tests/test_cli_int.py` | `pytest -q -m integration` |
| Config / pyproject | none | build gate | - | build gate |

## Gate Check Commands

| Gate Level | When to Use | Command |
| --- | --- | --- |
| Quick | Tasks só com unit | `pytest -q -m "not integration"` |
| Full | Tasks com integration | `pytest -q` |
| Build | Fim de fase / config | `ruff check . && pytest -q` |

---

## Execution Plan

### Phase 1: Base e benchmark

```
T1 → T2 → T3
T1 → T4
T0
```

### Phase 2: Ingestão e transcrição

```
T5 → T6 → T10
T5 → T9
T7 → T9
T8 → T9
T5 → T11
```

### Phase 3: Sinais e score

```
T12 → T15
T13 → T15
T15 → T16
T14 → T16
```

### Phase 4: Corte com sentido

```
T17 → T18 → T20 → T21
T19 → T20
```

### Phase 5: Render e saída (fecha o MVP VOD)

```
T22 → T24
T23 → T24
T24 → T26
T25 → T26
```

### Phase 6: Avaliação e calibração

```
T27 → T29
T28 → T29
T29 → T30
```

### Phase 7: Live (P2)

```
T31 → T34
T32 → T34
T33 → T34
T34 → T35
```

### Phase 8: Extras (P3, só se a avaliação pedir)

```
T36
T37
```

---

## Task Breakdown

### Phase 1: Base e benchmark

### T0: [repo: clipes-lives] Spike: rodar sharkyclips e chopify em VODs reais

**What**: Instalar a CLI do sharkyclips (`backend/cli.py ingest/transcribe/highlights/analyze/render`) e do chopify neste Linux, rodar em 3 VODs (Twitch com chat, YouTube, Kick), medir tempo/RAM e preencher o checklist de sentido; recomendar wrapper ou pipeline próprio.
**Where**: `docs/spike-sharkyclips.md`
**Depends on**: None
**Requirement**: CLIP-01

**Done when**:
- [ ] Tempo e RAM por hora de VOD registrados para os dois
- [ ] Checklist de sentido manual em ≥ 10 clipes de cada
- [ ] Recomendação escrita, levada ao usuário (pergunta 11 do `context.md`) antes do T5

**Tests**: none
**Gate**: build

---

### T1: [repo: clipes-lives] pyproject com dependências e ferramentas

**What**: `pyproject.toml` com pacote `clipes`, deps (`yt-dlp`, `faster-whisper`, `numpy`, `opencv-python-headless`), extras dev (`pytest`, `ruff`), marker `integration`, entrypoint `clipes = clipes.cli:main`.
**Where**: `pyproject.toml`
**Depends on**: None
**Requirement**: CLIP-01

**Done when**:
- [ ] `pip install -e .[dev]` funciona num venv limpo
- [ ] `ruff check .` e `pytest -q` rodam (0 testes ok)

**Tests**: none
**Gate**: build

---

### T2: [repo: clipes-lives] config.toml com os defaults do design

**What**: Arquivo com pesos dos sinais, limiares, janela de baseline (150 s), duração 20–60 s, N=8, modelos Whisper, endpoint/modelo do LLM, layout `auto`, caixa da facecam opcional, lista de palavras-chave e tokens de reação.
**Where**: `config.toml`
**Depends on**: T1
**Requirement**: CLIP-06

**Done when**:
- [ ] Todos os valores da tabela de pesos e da seção "Corte com sentido" do design estão presentes

**Tests**: none
**Gate**: build

---

### T3: [repo: clipes-lives] Loader de config

**What**: `load(path=None) -> dict` com `tomllib`, mesclando o `config.toml` do usuário sobre o padrão do repo e validando tipos dos pesos e da duração.
**Where**: `clipes/config.py`
**Depends on**: T2
**Requirement**: CLIP-06

**Done when**:
- [ ] Peso não numérico ou `min_dur >= max_dur` gera erro claro
- [ ] Override parcial mantém os outros defaults

**Tests**: unit
**Gate**: quick

---

### T4: [repo: clipes-lives] Benchmark da máquina

**What**: `clipes bench` mede RTF do faster-whisper (`small` e `large-v3-turbo`, int8, batch 1 e 4) num áudio de 2 min, tok/s do Ollama e tempo/h do CED-mini, e grava `bench.json`; o resultado decide o modelo e se a transcrição é do VOD inteiro (≤ 15 min/h) ou só das janelas dos picos.
**Where**: `clipes/bench.py`
**Depends on**: T1
**Requirement**: CLIP-01

**Done when**:
- [ ] `bench.json` com RTF, RAM de pico e tok/s
- [ ] Números registrados em `docs/pesquisa-tecnologias.md` no lugar das estimativas

**Tests**: integration
**Gate**: full

---

### Phase 2: Ingestão e transcrição

### T5: [repo: clipes-lives] fetch.info: plataforma, id e metadados

**What**: `info(url) -> dict` chama `yt-dlp -j`, identifica plataforma (youtube/twitch/kick) e id, grava `info.json`; erros de vídeo privado/inexistente/plataforma não suportada viram exceção com mensagem.
**Where**: `clipes/fetch.py`
**Depends on**: T3
**Requirement**: CLIP-01

**Done when**:
- [ ] URL não suportada → exceção antes de chamar yt-dlp
- [ ] Saída de erro do yt-dlp é repassada com sugestão de `yt-dlp -U`
- [ ] Cache: `info.json` existente não chama yt-dlp

**Tests**: integration
**Gate**: full

---

### T6: [repo: clipes-lives] fetch.audio: só o áudio inteiro

**What**: `audio(workdir, url)` baixa `bestaudio` para `audio.m4a`, com cache.
**Where**: `clipes/fetch.py`
**Depends on**: T5
**Requirement**: CLIP-02

**Done when**:
- [ ] Nenhum stream de vídeo é baixado
- [ ] Re-execução não baixa de novo

**Tests**: integration
**Gate**: full

---

### T7: [repo: clipes-lives] Parser do chat replay do YouTube

**What**: `parse_youtube(path) -> list[Msg]` lê o JSON lines `live_chat` do yt-dlp e devolve `Msg(t, user, text)` com `t` em segundos do vídeo, incluindo emojis no texto.
**Where**: `clipes/chat.py`
**Depends on**: None
**Requirement**: CLIP-03

**Done when**:
- [ ] Fixture real (anonimizada) de `live_chat` parseada com t correto
- [ ] Linhas sem mensagem (eventos, membros) são ignoradas

**Tests**: unit
**Gate**: quick

---

### T8: [repo: clipes-lives] Parser do chat da Twitch (TwitchDownloader)

**What**: `parse_twitch(pages) -> list[Msg]` converte os comentários da GQL `VideoCommentsByOffsetOrCursor` (offset em segundos, usuário, fragmentos com emotes); o JSON do TwitchDownloaderCLI tem o mesmo formato de comentário e serve de plano B.
**Where**: `clipes/chat.py`
**Depends on**: None
**Requirement**: CLIP-03

**Done when**:
- [ ] Emotes viram texto (`KEKW`, `LUL`) para o sinal de reação
- [ ] Fixture anonimizada parseada

**Tests**: unit
**Gate**: quick

---

### T9: [repo: clipes-lives] fetch.chat: baixa e normaliza o chat

**What**: `chat(workdir, info)` busca o chat (YouTube: yt-dlp `--sub-langs live_chat`; Twitch: GQL paginada via `urllib`; Kick: endpoint público), usa o parser certo e grava `chat.jsonl`; sem chat (desligado, VOD apagada, endpoint quebrado) devolve `None` e registra o motivo.
**Where**: `clipes/fetch.py`
**Depends on**: T5, T7, T8
**Requirement**: CLIP-03

**Done when**:
- [ ] Falha HTTP/endpoint mudou → aviso, segue sem chat
- [ ] Paginação da GQL testada com respostas gravadas (fixture)

**Tests**: integration
**Gate**: full

---

### T10: [repo: clipes-lives] Transcrição com timestamps de palavra

**What**: `transcribe(audio_path, model, start=0, end=None) -> dict` com faster-whisper int8, `word_timestamps=True`, `vad_filter=True`, `batch_size` da config; grava `words.json` com idioma detectado.
**Where**: `clipes/transcribe.py`
**Depends on**: T6
**Requirement**: CLIP-07

**Done when**:
- [ ] Fixture de fala sintética (`espeak`/gravação curta) gera palavras com start < end crescentes
- [ ] Trecho `start/end` funciona (usado no turbo dos clipes finais)

**Tests**: integration
**Gate**: full

---

### T11: [repo: clipes-lives] fetch.sections: baixa só os trechos

**What**: `sections(workdir, url, cuts)` usa `yt-dlp --download-sections` com folga de 5 s para gerar `src/<n>.mp4`, com cache.
**Where**: `clipes/fetch.py`
**Depends on**: T5
**Requirement**: CLIP-02

**Done when**:
- [ ] Comando montado com os intervalos certos (teste da montagem)
- [ ] Trecho existente não é baixado de novo

**Tests**: integration
**Gate**: full

---

### Phase 3: Sinais e score

### T12: [repo: clipes-lives] Séries de chat

**What**: `chat_series(msgs, dur, cfg) -> dict[str, ndarray]` com `chat_unicos` (usuários distintos/s, sem bots da lista da config, com filtro de Poisson para chat pequeno) e `chat_reacao` (fração de msgs com tokens de risada/espanto/"clip").
**Where**: `clipes/signals.py`
**Depends on**: T3
**Requirement**: CLIP-04

**Done when**:
- [ ] Mesmo usuário spammando 10× no mesmo segundo conta 1 em `chat_unicos`
- [ ] `kkkkk`, `KEKW`, `????` contam como reação; `ok` não
- [ ] Mensagens de `nightbot`/`streamelements` são ignoradas
- [ ] Chat de 2 msgs/min com um pulo para 5 não vira pico (Poisson)

**Tests**: unit
**Gate**: quick

---

### T13: [repo: clipes-lives] Séries de áudio

**What**: `audio_series(audio_path) -> dict[str, ndarray]` com `loudness` (ebur128 momentâneo via ffmpeg, agregado por segundo) e `silencio_explosao` (queda abaixo da baseline seguida de pico em < 3 s, ou salto sobre a média dos 3 s anteriores); o cálculo de `silencio_explosao` é função pura sobre o array.
**Where**: `clipes/signals.py`
**Depends on**: T3
**Requirement**: CLIP-04

**Done when**:
- [ ] Array sintético silêncio→pico marca o pico; pico sem silêncio antes não
- [ ] Parse da saída do ebur128 testado com texto fixo

**Tests**: unit
**Gate**: quick

---

### T14: [repo: clipes-lives] Séries de heatmap e palavras-chave

**What**: `heatmap_series(info, dur)` interpola o `heatmap` do yt-dlp para 1 s (zeros se ausente); `keyword_series(words, dur, cfg)` conta palavras-chave por segundo (sem acento, minúsculas).
**Where**: `clipes/signals.py`
**Depends on**: T3
**Requirement**: CLIP-04

**Done when**:
- [ ] Heatmap ausente → série nula e sinal marcado indisponível
- [ ] "GTA 6", "gta vi", "Vice City" casam

**Tests**: unit
**Gate**: quick

---

### T15: [repo: clipes-lives] Normalização robusta e atraso do chat

**What**: `robust_z(x, half_window=150)` (mediana/MAD móveis, corte 0–6) e `chat_lag(loud, chat) -> int` (correlação cruzada 3–20 s, padrão 5 s se corr < 0,1).
**Where**: `clipes/score.py`
**Depends on**: T12, T13
**Requirement**: CLIP-05

**Done when**:
- [ ] Série constante alta → z ≈ 0 (live barulhenta não vira pico)
- [ ] Chat sintético atrasado 12 s do áudio → lag = 12
- [ ] Ruído sem correlação → lag = 5

**Tests**: unit
**Gate**: quick

---

### T16: [repo: clipes-lives] Fusão, picos e NMS

**What**: `candidates(series, cfg) -> list[dict]` aplica pesos renormalizados pelos sinais disponíveis, suavização de 3 s, bônus de concordância, picos (≥ max(2, P95), distância 20 s, top 3N) e `nms(cuts, 0.3)`; cada candidato traz score total e por sinal.
**Where**: `clipes/score.py`
**Depends on**: T15, T14
**Requirement**: CLIP-06

**Done when**:
- [ ] Pico em chat + áudio no mesmo instante vence pico mais alto só no áudio (bônus)
- [ ] Sinal ausente não derruba o score dos outros
- [ ] Picos a < 20 s viram um
- [ ] Sobreposição > 30% fica o maior

**Tests**: unit
**Gate**: quick

---

### Phase 4: Corte com sentido

### T17: [repo: clipes-lives] Frases a partir das palavras

**What**: `sentences(words) -> list[dict]` junta palavras até pontuação final ou pausa ≥ 0,5 s, com `start`, `end`, `text`, `pause_before`.
**Where**: `clipes/bounds.py`
**Depends on**: T10
**Requirement**: CLIP-07

**Done when**:
- [ ] Ponto final e pausa longa quebram frase; vírgula não

**Tests**: unit
**Gate**: quick

---

### T18: [repo: clipes-lives] Corte heurístico com sentido

**What**: `heuristic_cut(sents, peak, loud_z, cfg) -> dict | None` segue os passos 2–7 da seção "Corte com sentido" do design.
**Where**: `clipes/bounds.py`
**Depends on**: T17
**Requirement**: CLIP-07

**Done when**:
- [ ] Bordas sempre em início/fim de frase, com pre-roll 0,3 s e pós-roll 0,8 s
- [ ] Pico contido com ≥ 6 s antes e ≥ 3 s depois
- [ ] 20–60 s ou `None`
- [ ] Prefere início sem conectivo ("e aí", "por isso") quando há alternativa
- [ ] Janela sem fala → bordas por pausa de áudio e flag `sem_fala`

**Tests**: unit
**Gate**: quick

---

### T19: [repo: clipes-lives] Cliente LLM mínimo

**What**: `chat_json(messages, schema, cfg) -> dict` via `urllib` para Ollama (`/api/chat`, `format=schema`, `temperature=0`, `keep_alive=0`) ou endpoint OpenAI-compatível (Gemini/Groq) quando configurado; timeout e erro viram exceção.
**Where**: `clipes/curate.py`
**Depends on**: T3
**Requirement**: CLIP-08

**Done when**:
- [ ] Servidor HTTP fake local (stdlib) responde e o JSON volta parseado
- [ ] Servidor fora do ar → exceção tratável

**Tests**: unit
**Gate**: quick

---

### T20: [repo: clipes-lives] Refino de borda e título pelo LLM com fallback

**What**: `refine(cand, sents, heur, cfg, llm=chat_json) -> dict` monta o prompt (frases numeradas + marcadores + corte heurístico) e pede título + `autocontido` (score ×0,85 se falso); com a flag `llm_bordas` também pede índices de frase, valida (índices, pico dentro, 20–60 s, título ≤ 70) e cai na heurística se falhar.
**Where**: `clipes/curate.py`
**Depends on**: T18, T19
**Requirement**: CLIP-08

**Done when**:
- [ ] Flag `llm_bordas` desligada → bordas da heurística, só título vem do LLM
- [ ] `autocontido=false` multiplica o score por 0,85
- [ ] Flag ligada + LLM fake com índices válidos → corte do LLM, origem `llm`
- [ ] Índice fora, pico fora, duração errada, JSON inválido, exceção → heurística, origem `heuristica`
- [ ] `keep=false` descarta o candidato

**Tests**: unit
**Gate**: quick

---

### T21: [repo: clipes-lives] Ranking comparativo final

**What**: `rank(cuts, n, cfg, llm=chat_json) -> list` pede ao LLM a ordem dos ≤ 2N sobreviventes lado a lado; resposta inválida → ordem por score; devolve top N.
**Where**: `clipes/curate.py`
**Depends on**: T20
**Requirement**: CLIP-08

**Done when**:
- [ ] Permutação válida é aplicada; permutação com id faltando ou repetido → ordem por score

**Tests**: unit
**Gate**: quick

---

### Phase 5: Render e saída

### T22: [repo: clipes-lives] Detecção de facecam e escolha de layout

**What**: `detect_layout(video_path, cfg) -> dict` amostra ~30 frames, roda Haar cascade do OpenCV, tira a mediana da caixa; rosto estável em ≥ 60% dos frames → `split` com a caixa; senão `blur`; caixa da config tem prioridade.
**Where**: `clipes/layout.py`
**Depends on**: T3
**Requirement**: CLIP-09

**Done when**:
- [ ] Vídeo sintético sem rosto → `blur`
- [ ] Caixa na config → `split` com essa caixa sem rodar detector

**Tests**: integration
**Gate**: full

---

### T23: [repo: clipes-lives] Legenda ASS word-level

**What**: `to_ass(words, clip_start, cfg) -> str` gera ASS 1080×1920 com 2–4 palavras por evento, palavra atual destacada, fonte grande com contorno, margem inferior na safe area; tempos relativos ao clipe.
**Where**: `clipes/captions.py`
**Depends on**: None
**Requirement**: CLIP-09

**Done when**:
- [ ] Tempos relativos corretos e sem sobreposição de eventos
- [ ] Caracteres especiais (`{`, `\`) escapados

**Tests**: unit
**Gate**: quick

---

### T24: [repo: clipes-lives] Render ffmpeg 9:16

**What**: `render_cmd(src, cut, layout, ass_path, out) -> list[str]` monta o filtro (`split`: facecam em cima + jogo embaixo; `blur`: fundo borrado + vídeo no meio), `subtitles=`, `loudnorm`, libx264 veryfast crf 21, AAC; `render(...)` executa.
**Where**: `clipes/render.py`
**Depends on**: T22, T23
**Requirement**: CLIP-09

**Done when**:
- [ ] Teste de integração: vídeo sintético 1280×720 de 30 s → saída 1080×1920 (ffprobe), duração ±0,1 s
- [ ] Os dois layouts renderizam

**Tests**: integration
**Gate**: full

---

### T25: [repo: clipes-lives] clips.json e index.html

**What**: `write_report(workdir, clips)` grava `clips.json` (título, início, fim, score total e por sinal, tipo, origem do corte, avisos como "sem chat") e um `index.html` estático com `<video>` + título + score + motivo.
**Where**: `clipes/report.py`
**Depends on**: None
**Requirement**: CLIP-10

**Done when**:
- [ ] Títulos com `<`/`&` saem escapados no HTML
- [ ] JSON tem todos os campos acima

**Tests**: unit
**Gate**: quick

---

### T26: [repo: clipes-lives] CLI `clipes vod <url>` ponta a ponta

**What**: `main()` com subcomando `vod` encadeando info → áudio → chat → transcrição → sinais → score → bounds → curate → trechos → layout → turbo nos trechos → legenda → render → report, com `--n`, `--force <etapa>`, `--out`; erro de entrada → código ≠ 0.
**Where**: `clipes/cli.py`
**Depends on**: T24, T25
**Requirement**: CLIP-01

**Done when**:
- [ ] Teste de integração com um "VOD" local sintético (`file://`, sem chat) gera ≥ 1 clipe
- [ ] URL inválida → código 1 e mensagem
- [ ] Smoke manual: 1 VOD real da Twitch e 1 do YouTube

**Tests**: integration
**Gate**: build

---

### Phase 6: Avaliação e calibração

### T27: [repo: clipes-lives] Métricas contra ground truth

**What**: `metrics(clips, gt, words) -> dict` com Recall@5/@10, precisão@N (acerto = sobreposição ≥ 30% ou centro do GT dentro do clipe), MidWordCutRate, SentenceEdgeRate e PeakPositionRatio; `heatmap_gt(info, k=10)`.
**Where**: `clipes/evaluate.py`
**Depends on**: T16
**Requirement**: CLIP-11

**Done when**:
- [ ] Casos à mão: acerto total, nenhum, parcial, GT duplicado
- [ ] Corte dentro de uma palavra conta em MidWordCutRate

**Tests**: unit
**Gate**: quick

---

### T28: [repo: clipes-lives] Checklist de sentido

**What**: `sentido_rows(cut, sents, signals) -> dict` com os portões G1–G5 do design (G1 e G3 eliminatórios) e coluna para nota manual; grava `sentido.csv`.
**Where**: `clipes/evaluate.py`
**Depends on**: T18
**Requirement**: CLIP-11

**Done when**:
- [ ] Corte no meio de palavra reprova em G1
- [ ] Pico fora da margem reprova em G3
- [ ] Conectivo inicial ("mas então") reprova em G2

**Tests**: unit
**Gate**: quick

---

### T29: [repo: clipes-lives] CLI `clipes eval`

**What**: Subcomando `eval <workdir> [--gt gt.json | --gt-heatmap] [--sem <sinal>]` que reroda score/bounds sem renderizar, imprime métricas, grava `sentido.csv` e, com `--compare <config_b.toml>`, imprime o diff de texto dos candidatos que mudaram; com `--gt-heatmap` zera o peso do heatmap.
**Where**: `clipes/cli.py`
**Depends on**: T27, T28
**Requirement**: CLIP-11

**Done when**:
- [ ] `--gt-heatmap` roda com `w_heatmap = 0` (teste)
- [ ] Ablação `--sem chat_unicos` muda o resultado num caso sintético

**Tests**: integration
**Gate**: full

---

### T30: [repo: clipes-lives] Calibração com 5 VODs de referência

**What**: Rodar `vod` + `eval` nas 5 VODs do usuário, ajustar pesos e limiares no `config.toml` e registrar antes/depois (recall, precisão, nota de sentido) num relatório.
**Where**: `docs/avaliacao.md`
**Depends on**: T29
**Requirement**: CLIP-11

**Done when**:
- [ ] Tabela antes/depois por VOD
- [ ] Meta da spec atingida ou motivo documentado

**Tests**: none
**Gate**: build

---

### Phase 7: Live (P2)

### T31: [repo: clipes-lives] Gravação de live segmentada

**What**: `record(url, workdir)` roda `streamlink ... -O | ffmpeg -f segment -segment_time 30 -reset_timestamps 1` gravando `rec/seg_*.ts` e um índice com o relógio de parede de cada segmento; reconecta com backoff e registra buracos.
**Where**: `clipes/live.py`
**Depends on**: T26
**Requirement**: CLIP-12

**Done when**:
- [ ] Fonte local (ffmpeg `-re` servindo HLS) gera segmentos e índice
- [ ] Queda simulada → reconexão + buraco registrado

**Tests**: integration
**Gate**: full

---

### T32: [repo: clipes-lives] Chat ao vivo da Twitch (IRC anônimo)

**What**: `twitch_irc(channel, out)` conecta em `wss://irc-ws.chat.twitch.tv:443` como `justinfan<n>`, entra no canal e grava `chat.jsonl` com relógio de parede; parse de `PRIVMSG` é função pura.
**Where**: `clipes/live_chat.py`
**Depends on**: T26
**Requirement**: CLIP-12

**Done when**:
- [ ] Parse de linhas IRC reais (fixture) testado
- [ ] Teste manual numa live pública confirma que o anônimo ainda funciona (se falhar, registrar em `context.md` pergunta 10)

**Tests**: unit
**Gate**: quick

---

### T33: [repo: clipes-lives] Chat ao vivo do YouTube

**What**: `youtube_live(url, out)` lê o `live_chat` do yt-dlp durante a live e grava `chat.jsonl` com relógio de parede (chat-downloader está quebrado).
**Where**: `clipes/live_chat_yt.py`
**Depends on**: T26
**Requirement**: CLIP-12

**Done when**:
- [ ] Conversão das linhas `live_chat` ao vivo testada com fixture
- [ ] Teste manual numa live pública

**Tests**: unit
**Gate**: quick

---

### T34: [repo: clipes-lives] Laço de janela deslizante

**What**: `loop(workdir, cfg)` a cada 5 min concatena os últimos 10 min de segmentos, roda o pipeline de VOD (sem heatmap, sem download de trechos), descarta clipes com sobreposição > 30% com os já gerados e processa a janela final quando a gravação termina.
**Where**: `clipes/live.py`
**Depends on**: T31, T32, T33
**Requirement**: CLIP-12

**Done when**:
- [ ] Simulação com arquivo local em segmentos: clipes saem por janela, sem duplicata entre janelas
- [ ] Fim da gravação → janela final processada e saída limpa

**Tests**: integration
**Gate**: full

---

### T35: [repo: clipes-lives] CLI `clipes live <url>`

**What**: Subcomando `live` que sobe gravação + chat + laço em processos filhos e encerra tudo no Ctrl+C sem perder clipes prontos.
**Where**: `clipes/cli.py`
**Depends on**: T34
**Requirement**: CLIP-12

**Done when**:
- [ ] Ctrl+C simulado (SIGINT) encerra filhos e mantém `clips.json`
- [ ] Smoke manual numa live pública da Twitch

**Tests**: integration
**Gate**: build

---

### Phase 8: Extras (P3)

### T36: [repo: clipes-lives] Sinal de risada/grito por evento de áudio

**What**: `audio_events(audio_path) -> dict` roda CED-mini INT8 via sherpa-onnx (10 MB, como o sharkyclips; YAMNet como alternativa) e devolve séries `risada` e `grito` por segundo; ativado por config. Só entra se `clipes eval` mostrar ganho de recall.
**Where**: `clipes/audio_events.py`
**Depends on**: T30
**Requirement**: CLIP-13

**Done when**:
- [ ] Fixture com risada gravada pontua mais que fala neutra
- [ ] Tempo de CPU por hora registrado em `docs/avaliacao.md`

**Tests**: integration
**Gate**: full

---

### T37: [repo: clipes-lives] Painel de revisão Gradio

**What**: `clipes painel <workdir>` lista clipes com player, aprovar/descartar, ±s nas bordas e re-render do clipe.
**Where**: `clipes/panel.py`
**Depends on**: T30
**Requirement**: CLIP-14

**Done when**:
- [ ] Ajuste de borda re-renderiza só aquele clipe e atualiza `clips.json`

**Tests**: integration
**Gate**: full

---

## Validação pré-aprovação

### Cross-check diagrama × `Depends on` (dentro da fase)

| Fase | Arestas do diagrama | Confere com `Depends on` |
| --- | --- | --- |
| 1 | T1→T2, T2→T3, T1→T4 (T0 isolado) | ✅ |
| 2 | T5→T6, T6→T10, T5→T9, T7→T9, T8→T9, T5→T11 | ✅ |
| 3 | T12→T15, T13→T15, T15→T16, T14→T16 | ✅ |
| 4 | T17→T18, T18→T20, T20→T21, T19→T20 | ✅ |
| 5 | T22→T24, T23→T24, T24→T26, T25→T26 | ✅ |
| 6 | T27→T29, T28→T29, T29→T30 | ✅ |
| 7 | T31→T34, T32→T34, T33→T34, T34→T35 | ✅ |
| 8 | (sem arestas internas) | ✅ |

### Co-localização de testes

| Task | Camada | Matriz pede | Task tem |
| --- | --- | --- | --- |
| T0, T1, T2, T30 | config/doc | none | none ✅ |
| T3, T7, T8, T12–T21, T23, T25, T27, T28, T32, T33 | lógica pura | unit | unit ✅ |
| T4, T5, T6, T9, T10, T11, T22, T24, T26, T29, T31, T34, T35, T36, T37 | binário/modelo/CLI | integration | integration ✅ |
