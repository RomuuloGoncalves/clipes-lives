# Design: gerador de clipes (arquitetura)

Spec: `spec.md`. Pesquisa: `docs/pesquisa-tecnologias.md` e `docs/pesquisa-github.md` no repo `clipes-lives`.

## Antes de tudo: spike do sharkyclips (T0)

O [sharkyclips](https://github.com/utkualsir/sharkyclips) (MIT, achado pelo agy) já tem CLI `ingest → transcribe → highlights → analyze → render` com quase os mesmos sinais deste design. O T0 roda ele (e o chopify) em 3 VODs reais neste Linux e compara com o checklist de sentido. Resultado bom → este design encolhe para um wrapper (config pt-BR de reações/palavras-chave + modo live + avaliação) e as tasks T5–T26 são substituídas. Resultado ruim → segue este design, copiando módulos MIT dele (chat GQL/Kick, sinais, snap de bordas) com atribuição.

## Decisão em uma frase

Pipeline Python de **um pacote só**, rodado pela linha de comando, em que **sinais baratos e medidos acham os picos**, **a transcrição com timestamps de palavra define as bordas** e **um LLM local pequeno só escolhe entre bordas válidas e dá o título**. FFmpeg renderiza. Cada etapa grava um arquivo na pasta de trabalho, que também serve de cache.

## Stack

| Peça | Escolha | Observação |
|---|---|---|
| Linguagem | Python 3.11+ | `tomllib`, `subprocess`, `json`, `urllib` da stdlib |
| Captura VOD | `yt-dlp` (CLI) | metadados, heatmap, áudio, `--download-sections`, chat do YouTube |
| Chat Twitch VOD | GQL pública `VideoCommentsByOffsetOrCursor` via `urllib` (como o sharkyclips, MIT) | sem login; plano B `TwitchDownloaderCLI` |
| Chat Kick VOD | endpoint público de chat da Kick (como o sharkyclips) | sem login; não testado |
| Captura live | `streamlink` → `ffmpeg -f segment` | P2 |
| Chat live | Twitch: IRC anônimo via websocket; YouTube: yt-dlp `live_chat` | P2; chat-downloader está quebrado |
| Transcrição | `faster-whisper` int8, `word_timestamps=True`, `vad_filter=True` | `small` no passe inteiro, `large-v3-turbo` nos clipes finais (confirmar no T3) |
| Numérico | `numpy` | séries de 1 s |
| LLM | Ollama via HTTP (`urllib`, sem SDK), `format=<JSON schema>`, `temperature=0`, `keep_alive=0` | Gemini/Groq opcionais pela mesma interface OpenAI-compatível |
| Facecam | `opencv-python-headless` (Haar cascade embutido) | MediaPipe só se o Haar falhar |
| Vídeo | `ffmpeg` (CLI) | crop/scale/overlay, `subtitles=` (libass), `loudnorm`, libx264 |
| Testes | `pytest` | |
| Lint | `ruff` | |

Dependências pip: `yt-dlp`, `faster-whisper`, `numpy`, `opencv-python-headless`; dev: `pytest`, `ruff`. Binários: `ffmpeg`, `ollama`; `streamlink` na P2; `TwitchDownloaderCLI` só como plano B.

## Módulos

```
clipes/
  cli.py         argparse: vod | live | eval | bench
  config.py      carrega config.toml (pesos, limiares, modelo, layout)
  fetch.py       yt-dlp: info+heatmap, áudio, trechos de vídeo; chat (yt-dlp live_chat / TwitchDownloaderCLI)
  chat.py        normaliza os formatos de chat em [Msg(t, user, text)]
  transcribe.py  faster-whisper -> words.json [{w, start, end}]
  signals.py     séries de 1 s: chat_unicos, chat_reacao, loudness, silencio_explosao, heatmap, keywords
  score.py       z-score robusto, atraso do chat, fusão, picos, NMS
  bounds.py      frases a partir das palavras; corte heurístico com sentido
  curate.py      LLM: escolhe índices de frase + título; validação + fallback
  layout.py      detecta facecam; escolhe split/blur
  captions.py    gera .ass word-level
  render.py      comando ffmpeg por clipe
  report.py      clips.json + index.html
  evaluate.py    recall/precisão vs ground truth; checklist de sentido
  live.py        (P2) gravação segmentada + janela deslizante
config.toml
tests/
```

Não há classes base, plugins ou filas. Cada módulo expõe poucas funções puras sobre dados simples (listas, dicts, arrays numpy) e o `cli.py` encadeia tudo.

## Pasta de trabalho (contrato entre etapas e cache)

`work/<plataforma>-<id>/`

| Arquivo | Produzido por | Conteúdo |
|---|---|---|
| `info.json` | fetch | saída do `yt-dlp -j` (duração, título, `heatmap`) |
| `audio.m4a` | fetch | áudio inteiro |
| `chat.jsonl` | fetch + chat | `{"t": seg, "user": str, "text": str}` por linha, já no tempo do vídeo |
| `words.json` | transcribe | `[{"w","start","end"}]` + idioma |
| `series.npz` | signals | uma série por sinal, 1 valor por segundo |
| `candidates.json` | score | picos com score total e por sinal |
| `cuts.json` | bounds + curate | início, fim, título, motivo, origem (`llm` ou `heuristica`) |
| `src/<n>.mp4` | fetch | trecho de vídeo com folga de 5 s |
| `out/<n>.mp4`, `out/<n>.ass` | captions + render | clipe final |
| `clips.json`, `index.html` | report | resumo para revisão |

Regra de cache: se o arquivo existe, a etapa é pulada (`--force <etapa>` refaz).

## Fluxo VOD

```
url ─► fetch.info (heatmap) ─► fetch.audio ─► transcribe (small) ─┐
   └► fetch.chat ─► chat.normalize ───────────────────────────────┤
                                                                   ▼
            signals ─► score (z-score, atraso do chat, fusão, picos, NMS) ─► top 3N candidatos
                                                                   ▼
            bounds (frases + corte heurístico) ─► curate (LLM escolhe índices + título | fallback) ─► top N
                                                                   ▼
            fetch.sections (só os N trechos) ─► layout ─► transcribe turbo no trecho ─► captions ─► render ─► report
```

## Fluxo LIVE (P2)

```
streamlink url ─► ffmpeg -f segment (30 s) ─► rec/seg_000123.ts
chat (IRC anônimo / chat-downloader) ─► chat.jsonl com relógio de parede
a cada 5 min: concatena os últimos 10 min ─► mesmo pipeline do VOD (sem heatmap, sem download de trechos)
          ─► descarta cortes com sobreposição > 30% com clipes já gerados
fim da live ─► processa a janela final
```

Alinhamento do relógio: o tempo de cada segmento vem do relógio de parede na gravação; o chat também. O atraso do chat é estimado pelo mesmo `score.chat_lag` do VOD.

## Modelo de pontuação

Para cada sinal `i` com série bruta `x_i(t)` em bins de 1 s:

1. **Normalização robusta**: `z_i(t) = (x_i(t) − mediana_{±150 s}) / (1,4826·MAD_{±150 s} + ε)`, cortado em `[0, 6]`.
2. **Atraso do chat**: `lag = argmax_{3..20 s} corr(loudness(t), chat(t + lag))`; se a correlação máxima < 0,1, `lag = 5 s` (valor do sharkyclips). As séries de chat são deslocadas por `−lag`.
3. **Fusão**: `S(t) = Σ_i w_i · z_i(t) / Σ_{i disponível} w_i`, suavizada por média móvel de 3 s.
4. **Bônus de concordância**: `+0,5 × (k − 1)` se `k ≥ 2` sinais independentes (chat, áudio, heatmap, texto) têm `z > 2,5` em `±5 s`.
5. **Picos**: máximos locais com `S ≥ max(2,0; P95(S))`, distância mínima de 20 s, top `3N`.
6. **NMS** depois do corte: sobreposição > 30% fica o de maior score.

Pesos padrão (`config.toml`, a calibrar com `clipes eval`; o agy propõe outro ponto de partida, chat 0,30 / eventos vocais 0,25 / loudness 0,20 / externo 0,10, e a calibração decide). Na live, a mediana é causal (só o passado):

| Sinal | Peso | Nota |
|---|---|---|
| `chat_unicos` | 1,0 | chatters únicos/s, sem bots (`nightbot`, `streamelements`, `moobot`, `fossabot`, `wizebot`, `botrix`), com filtro de Poisson para chat pequeno |
| `chat_reacao` | 0,8 | fração de risada/espanto/"clip" |
| `heatmap` | 0,8 | interpolado de ~100 pontos para 1 s |
| `loudness` | 0,7 | EBU R128 momentâneo |
| `silencio_explosao` | 0,5 | queda seguida de pico em < 3 s, ou salto sobre a média dos 3 s anteriores (jumpscare) |
| `keywords` | 0,4 | lista GTA VI + exclamações |

Tipo do momento, para o título e o filtro: `comedia` se domina `chat_reacao` com risada; `susto` se `silencio_explosao` + espanto; `hype` se `keywords` GTA VI; senão `reacao`.

## Corte com sentido (bounds + curate)

**Heurística (sempre roda, é o fallback):**
1. Frases: junta palavras até pontuação final ou pausa ≥ 0,5 s.
2. Janela `[t_p − 90, t_p + 30]`.
3. Início: frase que começa em `[t_p − 45, t_p − 6]` (mínimo de 6 s de setup). Nota: `+pausa_antes`, `−conectivo_inicial`, `−distância_ao_pico/45`. Fica a maior.
4. Fim: primeira frase que termina depois de `t_p + 3` e depois que o loudness volta a `z < 1` por 2 s. Limite: início + 60 s.
5. Bordas: início − 0,3 s, fim + 0,8 s, sem invadir a palavra vizinha.
6. Duração fora de 20–60 s → tenta a próxima frase de início; nada serve → descarta.
7. Sem fala na janela: usa pausas do loudness e marca `sem_fala`.

**LLM (padrão: só título + veredito `autocontido`, que multiplica o score por 0,85 se falso; refino de borda atrás da flag `llm_bordas`, ligado só se o `eval` mostrar ganho):**
- Entrada: frases numeradas da janela `[i] (mm:ss–mm:ss) texto`, marcadores `[PICO]`, `[CHAT risada ×6]`, `[ÁUDIO grito]`, o tipo do momento e o corte heurístico como sugestão.
- Schema: `{"keep": bool, "start_idx": int, "end_idx": int, "title": str, "reason": str}`.
- Validação: índices existem, pico dentro, 20–60 s, título ≤ 70 caracteres. Falhou → heurística.
- Ranking final: uma chamada com os `≤ 2N` sobreviventes lado a lado (título + resumo de 1 linha + score) pedindo a ordem; falhou → ordem pelo score.

## Medir qualidade

- **Ground truth parcial**: (a) clipes de viewers da Twitch (offset na VOD) e (b) picos do heatmap do YouTube (top 10 por `value`). O usuário fornece um `gt.json` `[{"start","end"}]`; um helper converte o heatmap do `info.json`.
- **Métricas** (do agy): Recall@5/@10 e precisão@N (acerto = sobreposição ≥ 30% ou centro do GT dentro do clipe); MidWordCutRate (meta 0%); SentenceEdgeRate (≥ 85%); PeakPositionRatio (0,35–0,75 em ≥ 80% dos clipes).
- **GT extra**: timestamps `MM:SS` em comentários do YouTube com ≥ 5 curtidas; clipes da Twitch a < 30 s agrupados.
- **Comparar configs**: harness sobre `series.npz` + `words.json` cacheados (< 1 s por config) imprime o diff de texto dos candidatos que mudaram.
- **Sem vazamento**: com heatmap como GT, a detecção roda com `w_heatmap = 0`.
- **Ablação**: `clipes eval --sem chat_unicos` etc. mostra quanto cada sinal contribui.
- **Checklist G1–G5** (`sentido.csv`): G1 sem corte no meio de palavra e ≥ 150 ms de pausa nas bordas (eliminatório); G2 início em frase, sem conectivo órfão, 6–15 s de setup; G3 pico dentro com 5 s/3 s de margem e ≥ 2 sinais (eliminatório); G4 fim depois da reação cair, sem assunto novo; G5 20–60 s e legenda sem sobreposição. Aprovado = G1 e G3 + ≥ 4/5.
- Ciclo: rodar nas 5 VODs de referência → ajustar pesos em `config.toml` → rodar de novo. Sem otimização automática de pesos no MVP.

## Custo estimado (1 h de VOD, Ryzen 5 5500U, não medido)

| Etapa | Tempo |
|---|---|
| info + áudio + chat | 2–5 min (rede) |
| transcrição `small` int8, batch 4 (ou só janelas dos picos: ~5–10 min) | 8–20 min |
| sinais + score | < 2 min |
| LLM 3B–4B em ~16 candidatos + ranking | 5–12 min |
| download de 8 trechos 1080p | 1–3 min (rede) |
| turbo nos 8 trechos | 2–4 min |
| render de 8 clipes | 4–12 min |
| **Total** | **~25–60 min** |

Pico de RAM: transcrição (~1,5–3,6 GB) **ou** LLM (~2,5–3 GB), nunca juntos.

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| yt-dlp/TwitchDownloader quebram por mudança de site | sem entrada | atualizar ferramentas; mensagem clara; testes com fixtures, não com rede |
| Chat replay indisponível (Kick, chat desligado, ferramenta quebrada) | sinal mais forte ausente | pesos renormalizados; áudio + texto + heatmap seguem |
| Atraso do chat estimado errado | clipe começa tarde | correlação + limite 3–20 s + borda decidida pela fala, não pelo chat |
| LLM 3–4B ruim em pt-BR | títulos fracos, bordas ruins | LLM só escolhe entre opções válidas; fallback heurístico; Gemini opcional |
| RAM de 9 GB | travar o notebook | etapas sequenciais, `keep_alive=0`, `batch_size` configurável, áudio em blocos |
| CPU lenta demais | > 1 h por VOD | benchmark T3 primeiro; trocar para whisper.cpp ou `base` se preciso |
| Facecam mal detectada | crop feio | override em `config.toml`; fallback `blur` |
| Direitos autorais de conteúdo de terceiros | uso | uso pessoal; publicação fora do escopo |
| Música alta (DMCA, trailer) dispara loudness | falso positivo | baseline móvel; bônus só com 2+ sinais |
