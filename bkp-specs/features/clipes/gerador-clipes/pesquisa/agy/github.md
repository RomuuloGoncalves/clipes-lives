# Levantamento de Projetos Open Source: Auto-Clipping e Highlight de Lives/VODs

> **Data da verificação:** 2026-11-16 (dados coletados via GitHub REST API pública e inspeção direta dos repositórios/`README.md`/código-fonte).
> **Termos de busca executados:** `"stream highlight detection"`, `"twitch auto clip"`, `"vod highlight generator"`, `"opusclip open source alternative"`, `"shorts generator whisper ffmpeg"`, `"chat replay highlight"`, `"livestream clipper"`.

---

## 1. Projetos Ativos Analisados (2025–2026)

### 1.1. [`utkualsir/sharkyclips`](https://github.com/utkualsir/sharkyclips) (ex-ClipForge)
- **Estrelas:** 7 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-16` (versão `1.2.50`)
- **Licença:** MIT
- **Como detecta picos:** Motor multimodal local de 1 Hz (`backend/sharkyclips/highlights/`). Combina:
  1. **`loudness`** (`signals/loudness.py`): saltos de volume dB em 2 Hz contra mediana móvel de 60 s (ignorando silêncio `< -55 dB`) + salto abrupto sobre os 3 s anteriores.
  2. **`laughter`** (`signals/laughter.py`): classificador AudioSet **CED-mini INT8** (~10 MB via `sherpa-onnx` em CPU, sem PyTorch) agrupando 527 classes em `laughter`, `shouting` e `cheering`.
  3. **`chat`** (`signals/chat.py`): velocidade do chat em janela de 5 s contra mediana móvel de 121 s, limitada por *z-score* de Poisson em 10 s (evita falsos positivos em chats lentos), mais ondas de *copy-paste* (`>= 3` autores em 5 s) e regex de reações (`KEKW`, `LUL`, risadas), com *shift* temporal de `-5 s` para compensar atraso do chat.
  4. **`speech` / `keywords`** (`signals/speech.py`): taxa de palavras/segundo (WPS) via `faster-whisper` e padrão *pause-then-burst* (pausa de 1,2–8,0 s seguida de explosão de fala = *setup → punchline*).
  5. **LLM local opcional (Ollama `qwen3.5:4b`/`9b`):** avalia se o trecho é autocontido (`self_contained`) e ajusta bordas em até ±10 s.
- **Qualidade percebida (`README` / Issues):** Apesar de recém-publicado (poucas estrelas, 1 issue aberta [#1](https://github.com/utkualsir/sharkyclips/issues/1) pedindo suporte a espanhol), a engenharia do código e da documentação técnica ([`docs/how-it-works.md`](https://github.com/utkualsir/sharkyclips/blob/HEAD/docs/how-it-works.md)) é a mais limpa e diretamente aplicável ao nosso caso. Possui suíte `pytest` e lint `ruff`.
- **O que reaproveitar:**
  - Lógica completa de `scorer.py` (combinação ponderada com bônus de concordância quando `>= 2` sinais passam de 0,5 simultaneamente + filtro gaussiano).
  - Algoritmo de ajuste de bordas por palavras (`_snap_start` e `_snap_end` em `candidates.py`), que pontua fins de frase (`+2.0`) e pausas `>= 0,8 s` (`+1.5`).
  - Extrator de chat sem login (`chat.py`): GraphQL público da Twitch em 8 threads paralelas (`VideoCommentsByOffsetOrCursor`) e `live_chat` do YouTube via `yt-dlp`.
  - Inferência de risada/grito em CPU via `sherpa-onnx` + `ced-mini` INT8 (`signals/laughter.py`).

---

### 1.2. [`mehbul/chopify`](https://github.com/mehbul/chopify)
- **Estrelas:** 75 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-06` (PR [#13](https://github.com/mehbul/chopify/pull/13) fechado em `2026-11-09`)
- **Licença:** MIT
- **Como detecta picos:** Avalia cada início de sentença e testa uma escada de durações (15 s a 300 s) buscando finais naturais com pontuação (`score_clips.py`). Não exige LLM por padrão:
  - Extrai linha do tempo de áudio local a 1 Hz via `ffmpeg` (`audio_features.npz`: *loudness*, picos, *spectral flux* e transientes).
  - Extrai análise visual cacheada a 2 fps em `160x90` via OpenCV (picos de movimento, mudanças de UI/cena, presença de *facecam* via YuNet e "arco de ação" completo com bordas calmas).
  - Adapta pesos automaticamente ao perfil detectado (`talk`, `mixed` ou `gameplay/action`).
  - Para VODs longos (`>= 2 h`), baixa apenas o áudio primeiro, transcreve em blocos de 30 min com *overlap* de 30 s (`transcript.partNNN.json`) e só baixa os trechos de vídeo aprovados.
- **Qualidade percebida (`README` / Issues):** Excelente maturidade de código Python 3.11+, CI ativo no GitHub Actions, 0 issues abertas (todas resolvidas até nov/2026). Documentação honesta sobre limitações (pontuação 0–10 é heurística de edição, não garantia de viralidade).
- **O que reaproveitar:**
  - Estratégia *audio-first* para VODs longos (`>= 2 h`): baixar áudio leve, detectar candidatos em blocos de 30 min com *overlap* de 30 s, e baixar vídeo apenas dos segmentos finais com *padding* de 10 s.
  - Heurística de "arco de ação" e quietude nas bordas (`score_clips.py` e `tighten.py`).
  - Motor de legendas `.ass` (`captions.py`) com 13 presets (`hormozi`, `mrbeast`, `karaoke`, etc.) renderizados via `libass`.

---

### 1.3. [`ColinGPT9/clips-studio`](https://github.com/ColinGPT9/clips-studio) (Clips Kitty) + [`ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin`](https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin)
- **Estrelas:** 283 ★ (`clips-studio`) / 4 ★ (`GTA-6-Plugin`) *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-17` (UTC) / `2026-10-28` (plugin GTA VI)
- **Licença:** AGPL-3.0 (`clips-studio`) / MIT (`GTA-6-Plugin`)
- **Como detecta picos:** Fusão multimodal em *bins* de 1 s (áudio: picos de volume, densidade de *bursts* de risada/aplauso e formato *silêncio → explosão*; visual: cortes de cena, movimento e variação de área facial; audiência: chat da Twitch medido por **autores únicos** e *heatmap* "Most Replayed" do YouTube como bônus limitado). Os candidatos vão para um LLM local (Ollama / Gemma) com a linha do tempo de eventos injetada no prompt, seguido de uma chamada de *rerank* *head-to-head* entre finalistas. O plugin de GTA V/VI (`colingpt9/gta-vi-highlights`) adiciona regras léxicas de perseguição, batida, *stunt*, combate e caos de NPC, retendo candidatos se a fala de suporte for truncada.
- **Qualidade percebida (`README` / Issues):** Muito ativo (64 issues abertas, lançamentos semanais na Microsoft Store), porém pesado em dependências quando usado inteiro (exige 16 GB RAM; issues recentes como [#244](https://github.com/ColinGPT9/clips-studio/issues/244) relatam falhas de DLL do PyTorch no rastreamento visual no Windows).
- **O que reaproveitar:**
  - Ideia arquitetural de **injetar a linha do tempo de picos acústicos/chat como texto estruturado no prompt do LLM** ("aos +11s o áudio subiu +14dB e o chat teve 42 msgs de risada") e fazer **comparação *head-to-head*** dos top candidatos em vez de pedir nota absoluta a modelos pequenos.
  - Layout 9:16 bipartido para *Gaming/Reaction* (facecam em cima + gameplay embaixo) sem depender de rastreamento facial pesado em todo frame.
  - Regras de domínio do plugin MIT `Grand-Theft-Auto-6-Clips-Kitty-Plugin` (filtrar histórias passadas/hipotéticas e exigir fala completa sem truncamento).

---

### 1.4. [`mutonby/openshorts`](https://github.com/mutonby/openshorts)
- **Estrelas:** 8.710 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-16`
- **Licença:** MIT
- **Como detecta picos:** Transcrição palavra a palavra via `faster-whisper` + detecção de cortes de cena via `PySceneDetect`, enviando janelas de transcrição e limites de cena para o Gemini Flash-Lite ou LLM local via `LLM_BASE_URL` (Ollama/llama.cpp com `OLLAMA_CONTEXT_LENGTH=16384`).
- **Qualidade percebida (`README` / Issues):** Projeto mais estrelado da categoria em 2026 (41 issues/PRs abertos, desenvolvimento diário intenso, ex.: PRs [#151](https://github.com/mutonby/openshorts/pull/151)–[#154](https://github.com/mutonby/openshorts/pull/154) em `2026-11-16`). Ponto fraco para lives de gameplay: a seleção de momentos depende quase 100% de transcrição + LLM (não analisa chat replay nem picos de risada/grito no áudio), falhando em momentos de reação puramente sonora ou visual. No entanto, o *reframing* 9:16 (`TRACK`, `GENERAL`, `SPLIT` para 2 pessoas, `SCREENCAST` tela + webcam) é excelente.
- **O que reaproveitar:**
  - Modos de *layout* vertical 9:16 (`SCREENCAST` e `SPLIT` com legenda na costura entre os dois quadros).
  - Benchmarks reais documentados: em CPU, um vídeo de 8 min leva 5–8 min para processar; LLMs locais de 3B falham na saída JSON estruturada, exigindo modelos `>= 7B–8B` ou heurísticas determinísticas.

---

### 1.5. [`xixihhhh/hotclip`](https://github.com/xixihhhh/hotclip)
- **Estrelas:** 471 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-10-26` (v0.33.0; PR [#20](https://github.com/xixihhhh/hotclip/pull/20) em `2026-10-30`)
- **Licença:** AGPL-3.0
- **Como detecta picos:** Combina 9 canais de evidência locais: ASR local com timestamps por palavra, densidade de chat ao vivo (com teto por autor para bloquear *flood* de um único usuário), picos de volume, densidade de cortes de câmera (`TransNetV2` ONNX 31 MB), emoção facial (`YuNet` + `FER+` ONNX de poucos MB) e LLM local (`qwen3.5:4b`). Crucialmente, **o LLM nunca inventa timestamps**: ele é obrigado a citar o trecho literal da transcrição e o sistema faz *reverse-alignment* nos timestamps das palavras, seguido de um detector de fala sub-1 MB para proteger *gaps* que ainda contenham voz/risada.
- **Qualidade percebida (`README` / Issues):** Altíssimo rigor técnico (3 issues abertas, possui comando `pnpm quality:eval` que mede CER/WER, erro de borda P95 e `Recall@3/@5`). Focado originalmente no ecossistema chinês (Bilibili/Douyin, Electron/TypeScript), mas com suporte multi-idioma e documentação completa em inglês (`README.en.md`).
- **O que reaproveitar:**
  - Técnica de **ancoragem por citação (*quote reverse-alignment*)**: se usar LLM, pedir a citação textual do início/fim e mapear de volta aos timestamps do Whisper (elimina alucinação numérica de timestamps).
  - **Jump-cuts conscientes de fala/risada**: só remover silêncios internos quando não houver palavras, picos acústicos nem risada/aplauso, mantendo 0,25 s de respiro.
  - Estrutura do comando de avaliação `quality:eval` (ver `docs/agy/avaliacao.md`).

---

### 1.6. [`Anil-matcha/AI-Youtube-Shorts-Generator`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) (ex-`SamurAIGPT`)
- **Estrelas:** 6.165 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-12`
- **Licença:** MIT
- **Como detecta picos:** No modo `--mode local`, baixa com `yt-dlp`, transcreve com `faster-whisper`, fatia vídeos `> 30 min` em blocos de 20 min com *overlap* de 60 s (`CHUNK_SIZE_SECONDS=1200`, `CHUNK_OVERLAP_SECONDS=60`), envia a transcrição para um LLM com prompt de critérios de viralidade (`shorts_generator/highlights.py`), faz deduplicação por sobreposição (`> 50%` overlap mantém maior nota) e aplica *crop* vertical via OpenCV.
- **Qualidade percebida (`README` / Issues):** Código curto e fácil de ler, mas em 2026 o modo padrão (`--mode api`) virou vitrine comercial para a API paga `MuAPI`. No modo local, PRs abertos ([#83](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/pull/83), [#84](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/pull/84)) mostram bugs com OpenCV 5, caminhos não-ASCII e ausência total de sinais de áudio/chat.
- **O que reaproveitar:** Apenas a simplicidade do fatiamento em chunks com *overlap* e deduplicação IoU (`> 50%`) em `shorts_generator/highlights.py`.

---

### 1.7. [`FujiwaraChoki/supoclip`](https://github.com/FujiwaraChoki/supoclip)
- **Estrelas:** 3.328 ★ *(verificado em 2026-11-16)*
- **Último commit (`pushed_at`):** `2026-11-17` (UTC)
- **Licença:** AGPL-3.0
- **Como detecta picos:** Transcrição (AssemblyAI por padrão, ou Whisper local) + seleção puramente via LLM (3 a 7 segmentos por vídeo) com notas de *hook*, *engagement*, *value* e *shareability*.
- **Qualidade percebida (`README` / Issues):** Aplicação web robusta (Next.js + FastAPI + ARQ, 74 issues/PRs abertos), mas explicitamente **não suporta lives ao vivo** ("live streams are not supported" no `README.md`) e ignora sinais de chat e áudio (gritos/risadas).
- **O que reaproveitar:** Arquitetura de fila assíncrona (FastAPI + worker) e templates de animação de legendas, caso se deseje uma UI web futura.

---

### 1.8. [`Blueturboguy07/publikclip`](https://github.com/Blueturboguy07/publikclip) e [`artbyjazi/autoclip`](https://github.com/artbyjazi/autoclip)
- **`publikclip`:** 72 ★ | Último commit: `2026-11-11` | Licença: AGPL-3.0 | 6 issues abertas. Destaque: audita cada nota de viralidade e **desconta a nota de humor do LLM quando o detector acústico de risada não confirma risada real no áudio**; adiciona tags `[laughs]` nas legendas e *punch-in* (zoom rápido) na câmera quando há risada/energia vocal. Porém exige login no *gate* `publik` por padrão (embora o pipeline Python rode local).
- **`autoclip`:** 352 ★ | Último commit: `2026-08-01` (PR [#11](https://github.com/artbyjazi/autoclip/pull/11) aberto em `2026-11-16`) | Licença: MIT | 9 issues abertas. Pipeline Python 3.11/3.12 local (`faster-whisper` + Ollama + MediaPipe + `libass`), com PR [#9](https://github.com/artbyjazi/autoclip/pull/9) recente adicionando ingestão de VODs da Twitch.

---

### 1.9. Ferramentas Especializadas de Suporte (Áudio, Cena e Chat)
- **[`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor):** 5.949 ★ | Último commit: `2026-11-16` | Licença: Unlicense (Domínio Público) | 0 issues abertas. Reescrito em Nim/Python, extremamente rápido em CPU para detectar silêncio e movimento (`--edit "(or audio:0.03 motion:0.06)"`, `--margin 0.3s,1.5sec`). Ideal para pré-filtrar "dead air" ou medir atividade de áudio/movimento.
- **[`lay295/TwitchDownloader`](https://github.com/lay295/TwitchDownloader):** 4.060 ★ | Último commit: `2026-11-15` | Licença: MIT | 286 issues abertas. Padrão-ouro mantido ativamente para baixar VODs, clipes e histórico de chat da Twitch via CLI (`TwitchDownloaderCLI chatdownload -u <id> -o chat.json`) sem conta/login.
- **[`Breakthrough/PySceneDetect`](https://github.com/Breakthrough/PySceneDetect):** 5.514 ★ | Último commit: `2026-11-17` | Licença: BSD-3-Clause | 59 issues abertas. Biblioteca padrão em Python/OpenCV (`ContentDetector` / `AdaptiveDetector`) para achar cortes secos de câmera/cena em CPU.

---

## 2. Projetos Descartados (Abandonados, Quebrados ou Inadequados)

| Repositório | Estrelas | Último Commit | Motivo do Descarte |
|---|---:|---|---|
| [`ClipsAI/clipsai`](https://github.com/ClipsAI/clipsai) | 575 ★ | `2024-01-17` | **Abandonado há quase 3 anos.** Dependências antigas do WhisperX/Pyannote, exige token HuggingFace e usa *TextTiling* pensado para sermões/palestras, inútil para lives de gameplay/reação. |
| [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader) | 1.200 ★ | `2025-11-05` | **Semi-abandonado / quebrado em 2026.** Sem commits há 1 ano; issues [#290](https://github.com/xenova/chat-downloader/issues/290) (`2026-10-31`, *"Twitch Downloads still don't work"*) e [#286](https://github.com/xenova/chat-downloader/issues/286) mostram quebra nos parsers da Twitch e YouTube. Substituir por `yt-dlp` + GraphQL direto da Twitch (`sharkyclips` / `TwitchDownloader`). |
| [`nirvagold/stream-clipper`](https://github.com/nirvagold/stream-clipper) | 5 ★ | `2025-12-31` | **Sem atividade em 2026 + Freemium.** O `README.md` trava detecção de chat, palavras-chave, remoção de marca d'água e *crop* 9:16 atrás de uma versão "Pro" paga. |
| [`hougesen/twitch-highlight-finder`](https://github.com/hougesen/twitch-highlight-finder) | 9 ★ | `2024-05-10` | **Inativo há > 2 anos.** Apenas CLI simples em Rust para contar frequência de mensagens sem análise de áudio nem transcrição. |
| [`artkulak/twitch-stream-highlights-detection`](https://github.com/artkulak/twitch-stream-highlights-detection) | 59 ★ | `2021-05-26` | **Abandonado (2021).** |
| [`DennisPing/Twitch-Autoclip`](https://github.com/DennisPing/Twitch-Autoclip) | 2 ★ | `2021-07-25` | **Abandonado (2021).** Usa API v5 antiga da Twitch (desligada). |
| [`FLWL/autovod`](https://github.com/FLWL/autovod) | 6 ★ | `2020-08-27` | **Abandonado (2020).** |
| [`porplax/auto-highlighter`](https://github.com/porplax/auto-highlighter) | 4 ★ | `2024-11-26` | **Abandonado há 2 anos** e sem licença declarada (`None`). Corta janelas fixas de 30 s apenas por volume, gerando cortes no meio de frases. |

---

## 3. Ranking Final dos 5 Projetos Mais Úteis para o `clipes-lives`

1. **🥇 1º Lugar — [`utkualsir/sharkyclips`](https://github.com/utkualsir/sharkyclips) (MIT, nov/2026):**
   O único projeto open source em Python (MIT) que já resolve exatamente o nosso problema em CPU: fusão a 1 Hz de salto de volume com mediana móvel, risada/grito via modelo ONNX INT8 de 10 MB (`sherpa-onnx` + `ced-mini`), explosão de chat da Twitch/YouTube com compensação de *delay* (`-5 s`) e filtro de Poisson, padrão *pause-then-burst* na fala, e *word-snapping* nas bordas para nunca cortar palavras ao meio.
2. **🥈 2º Lugar — [`mehbul/chopify`](https://github.com/mehbul/chopify) (MIT, nov/2026):**
   Melhor arquitetura *CPU-first* sem LLM obrigatório para VODs longos (`>= 2 h`): pipeline *audio-first* resumível em blocos de 30 min, extração de features de áudio a 1 Hz via `ffmpeg`, análise visual leve a 2 fps (`160x90`), pontuação de finais de frase e motor de legendas `.ass` (`libass`).
3. **🥉 3º Lugar — [`xixihhhh/hotclip`](https://github.com/xixihhhh/hotclip) (AGPL-3.0, out/2026):**
   Referência de engenharia para **clipes com sentido**: ancoragem de timestamps por citação reversa (*quote reverse-alignment*), *jump-cuts* que preservam risadas/respiros, proteção contra *flood* de 1 usuário no chat e suíte determinística de avaliação (`quality:eval` com `Recall@K` e erro de borda P95).
4. **4º Lugar — [`ColinGPT9/clips-studio`](https://github.com/ColinGPT9/clips-studio) + [`Grand-Theft-Auto-6-Clips-Kitty-Plugin`](https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin) (AGPL-3.0 / MIT, nov/2026):**
   Excelente fonte para o layout 9:16 de *Gaming/Reaction* (webcam + jogo empilhados), ranqueamento *head-to-head* de candidatos e heurísticas específicas de GTA (perseguição, *stunt*, caos de NPC, retenção de candidato se a fala for truncada).
5. **5º Lugar — [`lay295/TwitchDownloader`](https://github.com/lay295/TwitchDownloader) + [`WyattBlue/auto-editor`](https://github.com/WyattBlue/auto-editor) (MIT / Unlicense, nov/2026):**
   Dupla de utilitários CLI ultrarrápidos em CPU para ingestão confiável de chat/VODs da Twitch sem login e medição/corte instantâneo de silêncio e movimento via `ffmpeg`.
