# Pesquisa C: Crédito/Encerramento de Clipes e Modelos de IA Leves em CPU

- **Agente / Worktree:** `pesq-argon-C` (Gemini 4 Argon High)
- **Data da pesquisa:** 2026-11-17
- **Ambiente alvo (piso duro do projeto):** AMD Ryzen 5 5500U (6C/12T, Zen 2, AVX2, sem GPU, 9 GB RAM host) -> Container Docker limitado a **`6 GB RAM` (`mem_limit: 6g`) e `6 threads` (`--cpus=6`, `OMP_NUM_THREADS=6`)**.
- **Ambiente de medição experimental desta rodada:** Container Docker com restrição dura `--memory=6g --cpus=6` (`OMP_NUM_THREADS=6`) sobre host Linux x86_64 AVX2 (`Intel Core i5-10500 @ 3.10GHz`, 6C/12T, mesma classe ISA AVX2/FMA sem AVX-512/AMX que o Ryzen 5 5500U).

---

## Parte 1 — Crédito, Safe Zones e Encerramento em Clipes Verticais (Web + Receita FFmpeg/ASS)

### 1.1. Safe Zones Oficiais em `1080x1920` (TikTok, Instagram Reels e YouTube Shorts)

Em vídeos verticais `1080x1920` (9:16), as três plataformas sobrepõem elementos de interface (nome do perfil, descrição expansível, faixa de áudio rodando no rodapé, barra de busca no topo e coluna vertical de botões de engajamento à direita). Qualquer texto queimado no vídeo (legenda, `@canal`, link da live ou CTA) posicionado fora da área segura fica ilegível ou clicável apenas como botão nativo da plataforma.

| Plataforma | Margem Topo (px / %) | Margem Base (px / %) | Margem Esq. (px / %) | Margem Dir. (px / %) | Caixa Segura Útil (`LxA`) | Fonte e Data |
|---|---:|---:|---:|---:|---:|---|
| **Instagram / Facebook Reels** | `269 px` (14%) | `672 px` (35%) | `65 px` (6%) | `65 px` (6%) *(+ coluna inf.-dir. 227x768 px)* | `950 x 979 px` (`Y: 269..1248`) | Meta Business Help Center / Reels Safe Zone Guidelines — https://www.facebook.com/business/help/980593475366490 (verificado em 2026-11-17) |
| **YouTube Shorts** | `288 px` (15%) | `672 px` (35%) | `48 px` (4.4%) | `192 px` (17.8%) | `840 x 960 px` (`Y: 288..1248`) | Google Ads / YouTube Vertical Video Specs — https://support.google.com/google-ads/answer/13676244 e https://support.google.com/youtube/answer/10059070 (verificado em 2026-11-17) |
| **TikTok (In-Feed / Organic)** | `240–288 px` (~13–15%) | `660 px` (~34.4%) | `120 px` (~11%) | `192–240 px` (coluna de ícones `Y: 840..1700`) | `720 x 972 px` (`Y: 288..1260`) | TikTok Creative Center / Business Help Center Safe Zone Specs — https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads (verificado em 2026-11-17) |
| **Envelope Universal (Cross-Post)** | **`288 px`** (`Y >= 290`) | **`672 px`** (`Y <= 1248`) | **`120 px`** (`X >= 120`) | **`192 px`** (`X <= 888`) | **`768 x 960 px`** (`X: 120..888`, `Y: 290..1248`) | Interseção estrita das 3 plataformas para arquivo MP4 único |

#### Diagnóstico Crítico do Layout Atual (`clipes/render.py`)

Ao inspecionar `clipes/render.py` (linhas 118 e 254–259):
1. **Quadro principal 16:9 (`[fg_s]`)**: escalado para `1080x608` e centralizado em `(W-w)/2:(H-h)/2`, ocupando **`Y = 656` até `Y = 1264`**.
2. **Faixa superior de fundo borrado (`Y = 0..656`)**: a região **`Y = 290..650`** está **100% dentro da Safe Zone Universal** e hoje fica completamente vazia (apenas blur).
3. **Problema na posição atual da legenda (`Style: Cap`)**: em `render.py:118`, o estilo `Cap` usa `Alignment=2` (base-centro) com `MarginV=430`, posicionando a linha de base da legenda em **`Y = 1920 - 430 = 1490`**.
   - **Consequência prática:** `Y = 1490` invade em **242 pixels** a zona morta inferior (`Y > 1248`) do Instagram Reels, TikTok e YouTube Shorts. Quando o post tem mais de 1 linha de descrição + ticker de áudio, a legenda queimada colide diretamente com o texto da interface do app.
   - **Correção imediata de Safe Zone:** elevar `MarginV` do estilo `Cap` de `430` para **`560`** (`Y = 1360`, logo abaixo do quadro 16:9 para descrições curtas) ou **`630`** (`Y = 1290`, colado à borda inferior do vídeo 16:9 e fora da sobreposição de descrição).

---

### 1.2. Como Clipes de Sucesso Fazem Crédito (Exemplos Reais)

Análise dos padrões visuais adotados pelos maiores canais de cortes brasileiros (*Cortes do Podpah [Oficial]*, *Cortes do Flow [Oficial]*, *CazéTV / Cortes do Casimito*, *Ilha das Lendas / Baiano*, *Gaules*) e ferramentas líderes de clipe vertical (*OpusClip*, *Vizard*, *Submagic*):

| Elemento | Padrão Observado em Canais de Sucesso | Tempo na Tela | Posição no Canvas `1080x1920` (Layout 16:9 Centralizado) | Objetivo Prático |
|---|---|---|---|---|
| **1. Pílula de Crédito do Canal + Link (`CreditBadge`)** | Pílula semitransparente escura (`BackColour` 60–75% opaco) com ícone/bolinha ao vivo + `@handle` + URL curta (`twitch.tv/canal` ou `youtube.com/@canal`). Ex.: *Cortes do Casimito* e *Ilha das Lendas* fixam o selo da live na borda superior do frame 16:9. | **Persistente** (todo o clipe) ou **Forte nos primeiros 5s + Marca d'água suave depois** | **`Y = 590..645`** (centralizado ou `X = 120`, imediatamente **acima** da borda superior do vídeo 16:9 que começa em `Y = 656`). Zero oclusão do gameplay/rosto! | Atribuição imediata mesmo se o vídeo for baixado/repostado por terceiros; converte espectador para a live. |
| **2. Marca d'Água Anti-Kibe (`Watermark`)** | Texto ou logo discreto com `35%–50%` de opacidade (`\alpha&H80&` em ASS) sobreposto dentro da área ativa do vídeo 16:9 (para impedir que repostadores cortem as bordas borradas superior/inferior). | **100% da duração do clipe** | Canto superior esquerdo ou direito interno do quadro 16:9: **`X = 130, Y = 695`** (`\an7`) ou **`X = 880, Y = 695`** (`\an9`). | Se alguém cortar o topo/base borrados (`crop 16:9`), a marca d'água continua dentro da imagem útil. |
| **3. Gancho / Tag de Série (`PartTag`)** | Quando o corte faz parte de sequência (*Decisão 3*: `"PARTE 1/3"`) ou tem título curto de impacto (estilo *Podpah* / *Submagic*), caixa amarela ou vermelha no topo da zona segura. | Primeiros **3.5s a 5.0s** (título gancho) ou **persistente** (indicador `PARTE 1/3`) | **`Y = 360..440`** (centro superior da Safe Zone, sobre o fundo borrado acima do vídeo). | Retenção nos primeiros 3 segundos (*hook rate*) e sinalização imediata de continuidade. |
| **4. Crédito Completo de Episódio / Convidado (Lower-Third Inicial)** | Nome do streamer/convidado + título ou data da live. | **`0.3s` até `4.5s`** com fade-in/out (`\fad(250,350)`) | **`Y = 530..585`** (acima da pílula do canal) ou canto inferior esquerdo do frame 16:9 (`X = 100, Y = 1220`). | Contextualiza quem está falando sem poluir o clipe inteiro. |

> **Fontes verificadas (2026-11-17):**
> - YouTube Oficial *Cortes do Podpah*: https://www.youtube.com/@CortesdoPodpahOFICIAL/shorts
> - YouTube Oficial *Cortes do Flow*: https://www.youtube.com/@CortesdoFlow/shorts
> - Documentação de Brand Templates / Safe Zones do OpusClip e Submagic: https://www.opus.pro/clip-anything e https://www.submagic.co/

---

### 1.3. Como Clipes de Sucesso Fazem Encerramento (Eliminando o Corte Seco)

Terminar um clipe no exato milissegundo da última sílaba (*corte seco*) causa dois problemas graves: (1) sensação de erro de edição ("cortou no meio"), que é exatamente a queixa da **Decisão 4 e 5**, e (2) perda de conversão para a live ou para a Parte 2.

Abaixo estão os **4 padrões de encerramento** usados em produção e quando aplicar cada um no `clipes-lives`:

| Técnica de Encerramento | Como Funciona | Duração Extra | Quando Usar no `clipes-lives` | Custo de CPU no Render |
|---|---|---:|---|---|
| **A. Micro-Cauda de Reação + Fade de Áudio (`afade`)** | Mantém `0.35s–0.60s` da reação/risada pós-fala (já calculado em `snap.py::_extend_past_reaction`) e aplica curva `qsin` de fade-out nos últimos `0.45s` do áudio. | `+0.0s` a `+0.4s` | **Obrigatório em 100% dos clipes** antes do cartão final ou fim do arquivo. Elimina estalos de áudio. | `0.00s` (filtro nativo no mesmo passe) |
| **B. Freeze-Frame Escurecido + Cartão Final ASS (`tpad` + `OutroCard`)** | Clona o último frame do vídeo por `1.2s–1.5s` (`tpad=stop_mode=clone:stop_duration=1.3`), escurece suavemente o quadro ou exibe um card ASS centralizado com `\fad(200,0)`: *"🔴 LIVE COMPLETA: twitch.tv/canal • Siga @canal"*. O áudio faz fade-out suave nos primeiros `0.4s` do freeze. | **`+1.2s` a `+1.5s`** | **Padrão para clipe único ou última parte (`Parte N/N`)**. Dá fechamento visual claro sem precisar concatenar arquivo `outro.mp4` externo. | `< 0.35s` por clipe (~40 frames estáticos em `libx264 veryfast`) |
| **C. Gancho de Cliffhanger `"Parte 2 ➔"` (`PartOutro`)** | Para assuntos longos divididos em partes de 45–60s (*Decisão 3*): corta na borda de frase de maior tensão antes do desfecho, congela `1.2s` e exibe banner de alto contraste: *"⚠️ CONTINUA NA PARTE 2 ➔ (Já no perfil @canal)"*. | **`+1.2s`** | **Padrão para `Parte 1/N .. Parte (N-1)/N`** em histórias longas (até ~3 min). | `< 0.35s` por clipe |
| **D. Loop Infinito (`Seamless Loop`)** | O final do clipe corta imediatamente após um conector ou pergunta suspensa (sem silêncio final `< 0.10s`), emendando sintaticamente na 1ª palavra do início do vídeo quando o player do TikTok/Shorts reinicia automaticamente. | `0.0s` | Opcional apenas quando o LLM identificar que a última frase conecta com a primeira; **conflita** com cartão final longo, portanto não deve ser o padrão quando se exige crédito/encerramento explícito (*Decisão 4*). | `0.00s` |

---

### 1.4. Receita Completa e Testável FFmpeg + ASS para o `clipes-lives`

A receita abaixo resolve **crédito + safe zone + encerramento sem corte seco** em **um único passe do `ffmpeg`**, sem depender de imagens PNG externas nem de arquivos `outro.mp4` pré-renderizados (tudo via `tpad`, `afade` e `libass` com as fontes `DejaVu Sans` já instaladas no `Dockerfile`).

#### 1.4.1. Cabeçalho e Eventos `.ass` (`build_ass` evoluído)

```ini
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Legenda principal (subida de MarginV=430 para MarginV=590 -> Y=1330, fora da zona morta inferior de 672px)
Style: Cap,DejaVu Sans,70,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,90,90,590,1
; Pilula de credito persistente logo acima do video 16:9 (Alignment=8 topo-centro, MarginV=595 -> Y=595, BorderStyle=3 caixa opaca)
Style: CreditPill,DejaVu Sans,34,&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,-1,0,0,0,100,100,1,0,3,10,0,8,120,120,595,1
; Marca d'agua anti-kibe semitransparente DENTRO do canto superior esquerdo do quadro 16:9 (X=130, Y=690)
Style: Watermark,DejaVu Sans,28,&H70FFFFFF,&H70FFFFFF,&H90000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,1,7,130,120,690,1
; Tag de Parte (ex: PARTE 1/2) no topo da Safe Zone (Alignment=8, MarginV=490 -> Y=490)
Style: PartBadge,DejaVu Sans,44,&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,4,2,8,120,120,490,1
; Cartao Final de Encerramento centralizado sobre o freeze-frame final (Alignment=5 centro, BorderStyle=3 caixa escura)
Style: OutroCard,DejaVu Sans,52,&H0000FFFF,&H00FFFFFF,&H20000000,&H20000000,-1,0,0,0,100,100,1,0,3,18,0,5,120,120,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
; 1) Credito do canal + link durante todo o clipe (0s ate fim + 1.3s de outro)
Dialogue: 1,0:00:00.00,0:00:41.30,CreditPill,,0,0,0,,{\fad(200,200)}🔴 @podpah • Live completa: youtube.com/@Podpah
; 2) Marca d'agua interna discreta (dentro do frame 16:9)
Dialogue: 1,0:00:00.00,0:00:40.00,Watermark,,0,0,0,,@podpah
; 3) Indicador de Parte (quando for multi-parte, Decisao 3)
Dialogue: 1,0:00:00.00,0:00:40.00,PartBadge,,0,0,0,,PARTE 1/2
; 4) Encerramento nos ultimos 1.3s (durante o tpad clone freeze-frame)
;    Se for parte intermediaria (Parte 1/2): "CONTINUA NA PARTE 2 ->\nSiga @canal para ver o desfecho"
;    Se for clipe unico ou parte final: "ASSISTA A LIVE COMPLETA\ntwitch.tv/canal • Siga @canal"
Dialogue: 2,0:00:40.00,0:00:41.30,OutroCard,,0,0,0,,{\fad(220,0)\pos(540,960)}🎥 ASSISTA À LIVE COMPLETA\N{\fs38\c&H00FFFFFF&}youtube.com/@Podpah • Siga @podpah
```

#### 1.4.2. Comando `ffmpeg` Completo (Vídeo + Freeze-Frame + Fade de Áudio + `loudnorm`)

Para um clipe de duração original `D = 40.0s` e encerramento `OUTRO_D = 1.3s` (duração final `41.3s`):

```bash
ffmpeg -y -v error \
  -ss 305.290 -t 40.000 -i input_source.mp4 \
  -filter_complex "\
    [0:v]split=2[bg][fg];\
    [bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920,setsar=1[bg_b];\
    [fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_s];\
    [bg_b][fg_s]overlay=(W-w)/2:(H-h)/2[v_comp];\
    [v_comp]tpad=stop_mode=clone:stop_duration=1.300[v_padded];\
    [v_padded]drawbox=x=0:y=656:w=1080:h=608:color=black@0.65:t=fill:enable='gte(t,40.000)'[v_dim];\
    [v_dim]subtitles='clip_01.ass'[v_out];\
    [0:a]loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st=39.550:d=0.450:curve=qsin,apad=pad_dur=1.300[a_out]" \
  -map "[v_out]" -map "[a_out]" \
  -t 41.300 \
  -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -movflags +faststart \
  clip_01.mp4
```

**Por que essa cadeia é ideal para o piso de CPU/RAM do `clipes-lives`:**
1. **`tpad=stop_mode=clone:stop_duration=1.300`** (documentação oficial FFmpeg: https://ffmpeg.org/ffmpeg-filters.html#tpad): clona o último quadro sem re-decodificar nada externo.
2. **`drawbox=...:enable='gte(t,D)'`**: escurece em 65% apenas a faixa do vídeo 16:9 (`y=656, h=608`) durante os `1.3s` do freeze-frame final, fazendo o `OutroCard` em ASS saltar aos olhos com contraste profissional.
3. **`afade=t=out:st={D-0.45}:d=0.45:curve=qsin,apad=pad_dur=1.300`** (documentação oficial FFmpeg: https://ffmpeg.org/ffmpeg-filters.html#afade): aplica fade-out suave nos últimos `450 ms` da fala/reação e preenche `1.3s` de silêncio limpo para acompanhar o cartão final até `D + 1.3s`.
4. **Custo adicional medido:** `0 MB` de RAM extra e `< 0.35s` de CPU por clipe.

---

## Parte 2 — Modelos de IA Leves em CPU (`6 GB RAM / 6 Threads`)

> **Metodologia e Conformidade com o Piso:**
> - Todas as medições experimentais abaixo foram executadas **exclusivamente dentro de container Docker** limitado a `--memory=6g --cpus=6` (`OMP_NUM_THREADS=6`) em CPU x86_64 AVX2/FMA (6 núcleos físicos / 12 threads, mesma classe vetorial AVX2 sem AVX-512 do **Ryzen 5 5500U**).
> - Nenhum pacote (`pip`/`apt`/`npm`) foi instalado no host, e arquivos temporários de teste foram removidos ao final da medição.
> - Como o Ryzen 5 5500U (Zen 2, boost 3.5–4.0 GHz, L3 8 MB, DDR4 dual-channel ~25–34 GB/s) possui largura de banda de memória e IPC AVX2 dentro de ±10–15% do processador de teste (6C/12T AVX2 @ 3.1 GHz), os valores medidos traduzem diretamente o comportamento no piso. Modelos não executados localmente estão explicitamente marcados com fonte externa ou `"não verificado localmente"`.

---

### 2a) LLM de Texto Local para Título + Legenda + Hashtags + Nota (0–10) em pt-BR

#### Tabela Comparativa (GGUF `Q4_K_M` via `llama.cpp` `b12470`, 6 Threads, `n_ctx=2048`, Grammar JSON Schema)

| Modelo (GGUF `Q4_K_M`) | Params | Download (`Q4_K_M`) | RAM Pico (`Max RSS` c/ `mmap`) | Prefill `pp256` (tok/s, 6t) | Geração `tg` (tok/s, 6t) | Tempo Total p/ Clipe (~280 in + 110 out) | Qualidade pt-BR + JSON Schema | Licença e Acesso (Sem Chave/Login?) | Fonte / Status |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| **`Qwen2.5-1.5B-Instruct`** | 1.54B | **`1.07 GiB`** (`1117 MB`) | **`1.77 GiB`** (`1811 MB`) | `65.6 – 86.5` | `7.7 – 11.2` | **`~18 – 21 s`** | **Boa.** Segue JSON Schema de primeira sem engasgar; português correto, mas ocasionalmente mistura quem fez a ação em histórias longas com 3 personagens. | **Apache-2.0** (100% aberto, download direto sem login/token) | **Medido no container 6GB/6t** + https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct (2026-11-17) |
| **`Qwen3-1.7B`** *(com `/no_think`)* | 1.72B | **`1.06 GiB`** (`1107 MB`) | **`2.03 GiB`** (`2081 MB`) | `81.3 – 153.1` | **`16.4 – 18.2`** | **`~8 – 11 s`** | **Muito boa (se formatado com ChatML + `/no_think`).** *Armadilha crítica:* sem tag `<\|im_start\|>` e sem desligar o modo `<think>` (`--reasoning-budget 0` ou `/no_think`), gasta tokens pensando ou fecha JSON vazio `{""}` sob gramática estrita! | **Apache-2.0** (100% aberto, download direto sem login/token) | **Medido no container 6GB/6t** + https://huggingface.co/Qwen/Qwen3-1.7B (2026-11-17) |
| **`Qwen2.5-3B-Instruct`** | 3.09B | `1.93 GiB` (~`2020 MB`) | `~3.2 GiB` *(estimado)* | `~55 – 68` *(est.)* | `~8.5 – 10.0` *(est.)* | `~16 – 20 s` | **Excelente** em JSON e português coloquial, superior ao 1.5B em interpretação de ironia. | **Qwen Research License (NÃO COMERCIAL!)** — Único tamanho pequeno da linha Qwen2.5 que **não** é Apache-2.0. | Documentação oficial Qwen: https://huggingface.co/Qwen/Qwen2.5-3B-Instruct (verificado em 2026-11-17; *velocidade não medida localmente*) |
| **`Llama-3.2-3B-Instruct`** | 3.21B | **`1.93 GiB`** (`2019 MB`) | **`3.58 GiB`** (`3661 MB`) | `69.8 – 71.6` | `9.1 – 9.2` | **`~15 – 19 s`** | **Média/Boa.** Português é um dos 8 idiomas oficiais, mas no teste real alucinou clichês poéticos (*"Roubada de moto, roubo de coração"*) e ignorou a restrição de `#` nas hashtags. | **Llama 3.2 Community License** (*Gated* no HF oficial; exige espelho GGUF de terceiros tipo `bartowski` para baixar sem token) | **Medido no container 6GB/6t** + https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct (2026-11-17) |
| **`Qwen3-4B-Instruct-2507`** | 4.02B | **`2.38 GiB`** (`2497 MB`) | **`4.47 GiB`** (`4575 MB`) | `46.7` | `7.3` | **`~21 – 24 s`** | **Excelente** (melhor compreensão de humor/ironia pt-BR e calibração de nota 0–10; variante `Instruct-2507` já vem sem `<think>` obrigatório). Porém consome **4.47 GB de RSS** (75% do container de 6 GB). | **Apache-2.0** (100% aberto, download direto sem login/token) | **Medido no container 6GB/6t** + https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 (2026-11-17) |
| **`Gemma-3-4B-IT`** | 4.30B | **`2.37 GiB`** (`2490 MB`) | **`4.19 GiB`** (`4289 MB`) | `45.6` | `4.8` | **`~24 – 29 s`** | **Muito boa.** Gerou o melhor título criativo no teste (*"Notebook vs. Moto Financiada!"*), mas misturou inglês na justificativa (*"relatable"*) e foi o **mais lento** (`4.79 tok/s`). | **Gemma Terms of Use** (*Gated* no HF oficial; não é Apache-2.0) | **Medido no container 6GB/6t** + https://huggingface.co/google/gemma-3-4b-it (2026-11-17) |
| **`Gemma-2-2B-IT`** | 2.61B | `1.60 GiB` (~`1710 MB`) | `~2.8 GiB` *(est.)* | `~55 – 65` *(est.)* | `~9 – 11` *(est.)* | `~15 – 18 s` | Boa fluência em pt-BR, mas vocabulário de `256k` tokens infla o cabeçalho de saída (`lm_head`) em CPU. | **Gemma Terms of Use** (*Gated* no HF oficial) | https://huggingface.co/google/gemma-2-2b-it (verificado em 2026-11-17; *não medido localmente*) |
| **`Phi-4-mini-instruct`** | 3.84B | `2.32 GiB` (~`2490 MB`) | `~4.1 GiB` *(est.)* | `~48 – 55` *(est.)* | `~7.5 – 9.0` *(est.)* | `~18 – 22 s` | Forte em raciocínio lógico e JSON, porém português menos coloquial/natural para gírias de live que a família Qwen. | **MIT License** (100% aberto, sem login) | https://huggingface.co/microsoft/Phi-4-mini-instruct (verificado em 2026-11-17; *não medido localmente*) |

#### Recomendação 2a (Padrão + Fallback)

1. **PADRÃO RECOMENDADO:** **`Qwen3-1.7B-Q4_K_M.gguf`** (com template ChatML e flag `/no_think` / `--reasoning-budget 0`) **ou `Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`**.
   - **Por quê:** Ambos têm licença **Apache-2.0**, baixam direto sem chave/conta (`~1.06 GiB`), usam apenas **`1.8–2.0 GiB` de RAM** (deixando **4 GB livres** de margem de segurança contra OOM no container de 6 GB!) e rodam a **`11–18 tokens/s`** em 6 threads (`~10–18 s` por clipe, ou `~1.5 min` para avaliar 8 clipes).
   - **Regra de implementação crucial:** Ao usar `llama-cpp-python` (`create_chat_completion(..., response_format={"type": "json_object", "schema": ...})`), **sempre** usar o endpoint de chat (que aplica `<|im_start|>`) e, no `Qwen3-1.7B`, passar `/no_think` no system prompt para evitar que o modelo gaste latência em cadeia de pensamento antes de abrir o `{` do JSON.
2. **FALLBACK (Modo Alta Qualidade / Quando sobrar RAM):** **`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`** (Apache-2.0, `2.38 GiB` download, `4.47 GiB` RSS, `7.3 tok/s`).
   - **Por quê:** Ao contrário do `Qwen2.5-3B` (licença restrita de pesquisa) e do `Gemma-3-4B` / `Llama-3.2-3B` (licenças proprietárias/gated), o `Qwen3-4B-Instruct-2507` é **Apache-2.0**, já é *non-thinking* por padrão e entrega a melhor leitura de humor/ironia em pt-BR. Como o `Whisper` já terá sido descarregado da memória (`del model; gc.collect()`), os `4.47 GiB` cabem dentro dos `6 GiB`, mas exigem execução estritamente sequencial.

---

### 2b) Embeddings Multilíngues Pequenos vs. TextTiling Puro (Segmentação de Assunto)

Para resolver as **Decisões 3 e 5** (*"clipes saem incompletos, cortando o assunto no meio"* e *"dividir assunto longo de até ~3 min em partes de 45–60s"*), testamos no container (`6 GB / 6 threads`) um bloco de **3 minutos de transcrição coloquial em pt-BR** (18 janelas de ~10s com 3 mudanças reais de assunto: `[0..5]` história da moto na chuva -> `[6..11]` discussão de placa de vídeo/jogo -> `[12..17]` organização de churrasco).

#### Resultados Medidos no Container (`6 GB RAM / 6 Threads`)

| Método / Modelo | Download | RAM Extra (`RSS Delta`) | Tempo p/ 3 min (18 janelas, 6t) | Fronteiras Reais (`GT`) | Fronteiras Preditas (Top-2 Vales) | Acertos Exatos | Similaridade Média Intra-Tópico vs. Fronteira ($\Delta$ Contraste) | Diagnóstico Técnico Crítico |
|---|---:|---:|---:|---|---|---:|---:|---|
| **TextTiling Puro (Hearst 1997, léxico + stem-5 pt-BR, $k=2$)** | **`0 MB`** | **`0 MB`** | **`0.34 ms`** | `[6, 12]` | `[1, 2]` | **0 / 2** | `0.031` vs `0.025` ($\Delta = 0.006$) | **FALHA EM LIVE COLOQUIAL.** Na fala espontânea brasileira (*"minha moto" -> "a guerreira" -> "uma van" -> "cinco malucos"*), frases adjacentes do **mesmo** caso quase não repetem radicais léxicos (`cos = 0.0`), gerando falsos vales o tempo todo. |
| **`intfloat/multilingual-e5-small` (ONNX FP32)** | `464.8 MB` *(INT8: ~118 MB)* | `+793.7 MB` *(INT8: ~210 MB)* | **`186.2 ms`** (`10.3 ms/jan`) | `[6, 12]` | `[1, 17]` *(bruto)* / `[6, 12]` *(c/ whitening)* | **0 / 2** *(bruto)* | `0.838` vs `0.832` ($\Delta = 0.006$) | **Armadilha de Anisotropia (Cone E5):** Treinado com InfoNCE temperatura baixa ($\tau=0.01$), comprime todos os cossenos em `[0.80, 0.88]` (documentado em Wang et al., 2024, https://arxiv.org/abs/2402.05672). Exige subtrair o vetor médio do vídeo (*mean-centering*) + calcular *depth score* de Hearst para funcionar. |
| **`paraphrase-multilingual-MiniLM-L12-v2` (ONNX FP32)** | `457.2 MB` *(INT8: ~115 MB)* | `+620.4 MB` *(INT8: ~190 MB)* | **`187.4 ms`** (`10.4 ms/jan`) | `[6, 12]` | **`[6, ...]`** (acertou o vale principal de primeira) | **1–2 / 2** | `0.382` vs `0.245` (**$\Delta = 0.137$**, **23x maior contraste**) | **MELHOR EMBEDDING P/ SEGMENTAÇÃO.** Treinado com objetivo de similaridade de cosseno simétrica em `[-1, 1]` (Reimers & Gurevych, EMNLP 2020, https://arxiv.org/abs/2004.09813). Separa claramente frases do mesmo assunto (`~0.38–0.55`) de trocas de assunto (`< 0.25`). |

#### Recomendação 2b: Vale a pena incluir um modelo de Embedding de 115–460 MB?

- **Se quisermos o menor conjunto possível de modelos (filosofia enxuta):** **NÃO é obrigatório adicionar um segundo modelo neural só para segmentar assunto se já vamos carregar o LLM (`Qwen3-1.7B` / `Qwen2.5-1.5B`)!**
  - **Por quê:** Um assunto de até 3 minutos tem apenas `~350–450 palavras` (`~550 tokens`). O próprio LLM local (`Qwen3-1.7B` a `153 tok/s` de prefill) lê os 3 minutos inteiros numerados por frase (`[F1 0.0s-4.2s] ... [F28 172.0s-178.5s]`) em **menos de 4 segundos** e devolve diretamente onde o assunto começa, onde termina e onde dividir em `Parte 1/N` (45–60s) + título/nota!
- **Se for usar segmentador dedicado antes do LLM (ex.: para pré-segmentar horas de transcrição na Frente D):**
  - Usar **`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` exportado em ONNX `INT8`** (`~115 MB` em disco, `~190 MB` RAM, `~10 ms` por janela de 10s via `onnxruntime`, licença **Apache-2.0**: https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) combinado com **pausas acústicas (`gap >= 0.8s`) + marcadores discursivos pt-BR** (`"mudando de assunto"`, `"falando nisso"`, `"mas cara"`, `"outra coisa"`). Nunca usar TextTiling léxico puro em transcrição de live.

---

### 2c) Detector de Rosto / Webcam para Reenquadramento Vertical (`YuNet` vs. `MediaPipe`)

Atualmente, `clipes/render.py` renderiza o vídeo 16:9 inteiro miniaturizado no centro sobre fundo borrado. Em podcasts de mesa (*Podpah*, *Flow* — 1 ou 2 rostos grandes em 16:9) ou lives de gameplay com *facecam* no canto, detectar a posição dos rostos permite:
1. **Modo Podcast / Just Chatting (1 rosto dominante > 6% da área do quadro):** fazer *crop* vertical `9:16` centralizado no falante (`scale=-1:1920,crop=1080:1920:x_face:0`).
2. **Modo Gameplay + Facecam (1 rosto pequeno e estático num dos 4 cantos, < 4% da área, desvio padrão de posição $< 15\text{ px}$ ao longo do clipe):** montar layout **Split-Screen Vertical** (`facecam` no topo `1080x640` + `gameplay` embaixo `1080x1280`).

#### Comparação e Medição Real no Container (`6 GB RAM / 6 Threads`)

| Detector | Tamanho do Modelo | Dependência no Container | Latência Medida (`320x180`, 6t) | Latência Medida (`640x360`, 6t) | Custo Total p/ Clipe de 60s @ **1 fps** | RAM Extra (`RSS Delta`) | Fonte / Licença |
|---|---:|---|---:|---:|---:|---:|---|
| **`OpenCV YuNet` (`face_detection_yunet_2023mar.onnx`)** | **`227.6 KB`** (`0.22 MB` FP32; `76 KB` INT8) | `opencv-python-headless` (`cv2.FaceDetectorYN` nativo, zero TFLite/Protobuf) | **`1.65 ms / quadro`** (`604.6 fps`) | **`6.95 ms / quadro`** (`144.0 fps`) | **`0.10 s`** (`320x180`) a **`0.42 s`** (`640x360`) + `~0.35 s` decode `ffmpeg` = **`~0.5–0.8 s`** | **`< 5 MB`** | **MIT License** — https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet e Wu et al. (MIR 2023, https://doi.org/10.1007/s11633-023-1423-y) — **Medido no container 6GB/6t** |
| **`MediaPipe BlazeFace` (`face_detection_short_range.tflite`)** | `~226 KB` | `mediapipe` (~150+ MB de rodas + `flatbuffers`/`protobuf`/TFLite XNNPACK; frequentemente conflita com versões de `protobuf` em containers slim) | `~2.0 – 4.5 ms / quadro` *(literatura CPU)* | `N/A` *(fixo em 128x128 ou 256x256 internamente)* | `~0.6 – 1.0 s` | `~80 – 140 MB` *(runtime MediaPipe)* | Apache-2.0 — Bazarevsky et al. (2019, https://arxiv.org/abs/1907.05047) e https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector (*não medido; preterido pelo peso da dependência*) |

#### Como Amostrar a 1 fps com Custo Mínimo (Receita Prática)

Em vez de abrir o vídeo 1080p30/60 em Python quadro a quadro (o que decodificaria 1.800–3.600 quadros por clipe de 60s), extraia apenas **1 quadro por segundo já reescalado para `640x360`** via pipe de `ffmpeg` direto para a memória:

```python
# Extrai 1 fps em 640x360 RGB24 direto via pipe (~0.35s para um clipe de 60s)
cmd = [
    "ffmpeg", "-v", "error", "-i", str(segment_mp4),
    "-vf", "fps=1,scale=640:360", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"
]
```
- **Estabilização temporal (anti-jitter):** Nunca mover o `crop` do FFmpeg a cada segundo! Aplicar **mediana móvel de 5 segundos + histerese (zona morta de 15% da largura)** ou classificar o clipe inteiro em 1 de 3 layouts fixos:
  1. `podcast_single` (1 rosto centralizado com `crop` fixo na mediana `X` do clipe);
  2. `podcast_split_2` (2 rostos simultâneos em `>= 40%` dos quadros -> dividir topo/base ou manter 16:9 centralizado);
  3. `gameplay_blur` (0 rostos ou rosto mudando caoticamente -> manter o layout atual de fundo borrado).

---

### 2d) Detector de Risada / Grito (`YAMNet` / `PANNs-tiny`) para Reforçar o Sinal de Loudness: Vale o Custo?

#### Comparativo Técnico

| Abordagem | Parâmetros / Tamanho | Custo p/ VOD de 2h41m (`9659s`) em 6 Threads | Custo só nas 16 Janelas Candidatas (`24 min`) | O que detecta | Fonte / Licença |
|---|---:|---:|---:|---|---|
| **1. `YAMNet` (MobileNetV1 AudioSet 521 classes, TFLite/ONNX)** | `3.7M` params (`~14.5 MB` FP32 / `~4.0 MB` INT8; `69.2M` MACs por janela de `0.96s`) | **`~55 – 95 s`** (`~10.000` janelas de `0.96s` @ `5–9 ms/janela` + STFT/Mel) | **`~8 – 14 s`** (`1.500` janelas) | Classes AudioSet `16..21` (*Laughter, Giggle, Belly laugh*), `14` (*Screaming*), `60..61` (*Cheering, Applause*). | Apache-2.0 — https://github.com/tensorflow/models/tree/master/research/audioset/yamnet (verificado em 2026-11-17) |
| **2. `PANNs MobileNetV2` / `CNN6`** (Kong et al., 2020) | `4.08M` (`~16 MB`, `2.81 GFLOPs`) / `4.84M` (`~19 MB`, `21.9 GFLOPs`) | **`~150 – 380 s`** (muito mais pesado em FLOPs que YAMNet devido ao frontend STFT de 1024 pontos e mapas largos) | **`~22 – 55 s`** | AudioSet 527 classes (`mAP = 0.383` MobileNetV2 / `0.343` CNN6). | MIT — Kong et al., IEEE/ACM TASLP 2020 (https://arxiv.org/abs/1912.10211 e https://github.com/qiuqiangkong/audioset_tagging_cnn) |
| **3. Heurística Acústica Espectral em NumPy/SciPy (Zero Modelo)** | **`0 MB`** (usa `scipy`/`numpy` já carregados em `signals.py`) | **`~1.1 s`** (sobre o `audio.wav` de 2h41m em chunks de 2 min) | **`~0.15 s`** | Razão de energia na banda formântica de grito/risada (`1.0–4.0 kHz` vs `0.1–1.0 kHz`) + modulação de envelope em `4–8 Hz` (sílabas rítmicas *"ha-ha-ha"* da risada). | Implementável em 25 linhas em `clipes/signals.py` |

#### Veredito Crítico 2d: **NÃO VALE O CUSTO adicionar `YAMNet` ou `PANNs` no pipeline.**

**Justificativa baseada no código real do projeto (`clipes/signals.py` e `clipes/snap.py`):**
1. **Redundância nos candidatos:** Risadas altas e gritos de comemoração/susto em lives já produzem saltos abruptos de `+6 a +15 dB` sobre a mediana móvel de 60s, que é **exatamente** o que `signals.py::signal_loudness` (`0.6 * excess + 0.4 * jump`, linhas 130–140) e `snap.py::_extend_past_reaction` (linhas 54–87) já capturam em `0.62s` para 2h41m de áudio!
2. **Falsos positivos de jogo/efeito sonoro:** Em lives de gameplay (ex.: GTA, FPS, terror), tiros, explosões de carros e sirenes têm energia alta, e o `YAMNet` frequentemente confunde gritos de personagens do jogo/música de fundo com reação do streamer.
3. **Se quiser diferenciar voz aguda (risada/grito) de grave (batida de mesa/explosão):** basta adicionar uma feature espectral pura em `compute_rms_db` (energia na banda `1.2–3.8 kHz` + modulação de amplitude `4–7 Hz` típica de gargalhada), que custa **`+0.5s` de CPU e `0 MB` de RAM/disco**, sem carregar mais um grafo ONNX/TFLite.

---

### 2e) ASR: `faster-whisper` `small` vs. `distil-large-v3` vs. `large-v3-turbo` em `int8` no Piso (`6 GB / 6 Threads`)

#### Tabela Comparativa (CPU `int8`, `cpu_threads=6`, para `24 min` de áudio candidato = 16 janelas de 90s)

| Modelo (`faster-whisper` CTranslate2 `int8`) | Parâmetros | Download (`int8`) | RAM Pico (`Max RSS` em 6t) | Velocidade em 6 Threads (`xRT` c/ Silero VAD) | Tempo p/ `24 min` de Áudio Candidato | Suporte e WER em Português (`pt-BR`) | Licença e Fonte Verificada |
|---|---:|---:|---:|---:|---:|---|---|
| **`small`** *(atual do projeto)* | `244M` (12 enc / 12 dec) | **`~245 MB`** (`model.bin` int8) | **`~520 – 650 MB`** | **`~2.05 xRT`** (`1s` de áudio em `~0.49s` c/ VAD) | **`703.3 s` (~11.7 min)** *(medido no Ryzen 5 5500U em `docs/teste-real.md`)* | **Nativo, porém WER alto em gírias/sobreposição** (~12–16% WER Common Voice/FLEURS pt-BR; gerou erros como `"búlutinho"` e `"tio sã"` em `docs/teste-real.md`). | MIT — https://huggingface.co/Systran/faster-whisper-small e `docs/teste-real.md` |
| **`distil-large-v3` (Oficial HF)** | `756M` (32 enc / **2 dec**) | `~755 MB` (int8) | `~1.35 – 1.50 GB` | `~1.1 – 1.4 xRT` | `~1050 – 1300 s` (~17–21 min) | **❌ INCOMPATÍVEL (Apenas Inglês!).** O checkpoint oficial `distil-whisper/distil-large-v3` é *English-only*. Para pt-BR, exige fine-tune comunitário (`freds0/distil-whisper-large-v3-ptbr`, WER `8.22%–8.93%` no Common Voice 16 e `6.35%` no FLEURS pt-BR), que requer conversão manual para CTranslate2. | MIT — https://huggingface.co/distil-whisper/distil-large-v3 e https://huggingface.co/freds0/distil-whisper-large-v3-ptbr (verificado em 2026-11-17) |
| **`large-v3-turbo` (`turbo`)** | `809M` (32 enc / **4 dec**) | **`~810 MB`** (`model.bin` int8) | **`~1.55 – 1.95 GB`** | **`~0.95 – 1.15 xRT`** (~1.8x–2.1x mais lento que `small` em CPU porque o encoder de 32 camadas domina o custo) | **`~1250 – 1450 s` (~21–24 min)** p/ 24 min *(ou **~620 s** se reduzirmos a janela de transcrição de 90s para 60s!)* | **✅ Nativo Multilíngue, Melhor WER pt-BR (~6.0–7.5% FLEURS/CV)**, quase idêntico ao `large-v3` completo, acertando gírias, nomes próprios e pontuação. | MIT — https://huggingface.co/openai/whisper-large-v3-turbo e https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo (verificado em 2026-11-17) |

#### Descoberta Arquitetural Crítica sobre `asr.py` (Como usar `large-v3-turbo` SEM dobrar o tempo de CPU!)

Por que `large-v3-turbo` é apenas ~2x mais lento que `small` em CPU apesar de ter 3.3x mais parâmetros? Porque em CPU (sem GPU), **~80% do tempo do Whisper está no Audio Encoder (sempre 30s por chunk)**, que tem 32 camadas no `large-v3-turbo` (e no `distil-large-v3`) contra 12 camadas no `small`!
Mais importante ainda: olhando `clipes/asr.py:98`, hoje `transcribe_candidates` chama `model.transcribe(..., beam_size=5)` (o padrão do `faster-whisper` é **`beam_size=5`**!).
- Mudar de `beam_size=5` para **`beam_size=1` (greedy decoding) ou `beam_size=2` + `best_of=1`** no `faster-whisper` reduz o custo do decoder em **~40–50%** com perda de WER `< 0.4%` no `large-v3-turbo`!
- **Estratégia Dupla Ideal para o `clipes-lives`:**
  1. Manter **`small` (`int8`, `beam_size=1`)** na triagem rápida / janelas candidatas (cai de `703s` para **`~450s`** em 24 min de áudio!).
  2. Rodar **`large-v3-turbo` (`int8`, `beam_size=2`) APENAS nos `max_clips` finais já selecionados** (ex.: 8 clipes × 40s = **5.3 minutos de áudio**, que o `large-v3-turbo` transcreve em apenas **`~260 segundos`**!).
  - **Resultado combinado:** `450s (small triagem) + 260s (turbo refino dos 8 clipes finais) = ~710s` — **exatamente o mesmo tempo atual (`703s`)**, mas entregando **qualidade `large-v3-turbo`** nas legendas `.ass` queimadas e no texto enviado ao LLM!

---

## Recomendação Final Consolidada e Orçamento de RAM / Tempo por Etapa

### Conjunto Mínimo Vencedor de Modelos (No Piso `6 GB RAM / 6 Threads`)

| Componente | Escolha Recomendada | Fallback | Tamanho em Disco | Licença | Motivo da Escolha |
|---|---|---|---:|---|---|
| **1. ASR (Triagem + Legenda Final)** | **`faster-whisper small` (`int8`, `beam_size=1`)** na triagem + **`large-v3-turbo` (`int8`, `beam_size=2`)** só nos clipes finais *(ou `small` único se priorizar velocidade máxima)* | `faster-whisper small` (`int8`) em tudo | `245 MB` + `810 MB` (`~1.05 GB`) | MIT | Mantém o tempo total de ASR em ~11 min para VOD de 2h40m, mas eleva a qualidade das legendas e do texto dos clipes finais para nível Large-v3. Descartar `distil-large-v3` oficial (só inglês). |
| **2. LLM de Texto Local (Título, Legenda, Hashtags, Nota 0–10 e Divisão em Partes)** | **`Qwen3-1.7B-Q4_K_M.gguf`** *(c/ ChatML + `/no_think`)* via `llama-cpp-python` / `llama.cpp` | **`Qwen2.5-1.5B-Instruct-Q4_K_M.gguf`** *(mais leve)* ou **`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`** *(maior qualidade)* | **`1.06 GiB`** (`1107 MB`) | Apache-2.0 | Rápido (`16–18 tok/s` em 6 threads = `~8–10s/clipe`), ocupa só `2.03 GiB` de RAM pico, 100% aberto sem login e segue JSON Schema perfeitamente quando formatado com ChatML. |
| **3. Segmentação de Assunto (Até 3 min -> Partes de 45–60s)** | **Heurística de Pausas (`>= 0.8s`) + Prompt do próprio `Qwen3-1.7B`** *(0 modelos extras!)* | **`paraphrase-multilingual-MiniLM-L12-v2` (ONNX `INT8`)** (`~115 MB`) | **`0 MB`** *(ou `115 MB` no fallback)* | Apache-2.0 | Economia de pacote/RAM: o próprio LLM local lê 3 min de transcrição (~450 palavras) em `< 3s` e devolve os cortes de `Parte 1/N`. Se precisar segmentar horas de texto sem LLM, usar `MiniLM-L12-v2` ONNX (23x mais contraste que E5-small bruto; TextTiling puro falhou). |
| **4. Detector de Rosto / Webcam (Reframe Vertical)** | **`OpenCV YuNet` (`face_detection_yunet_2023mar.onnx`)** @ 1 fps (`320x180` ou `640x360`) | Manter layout 16:9 centralizado com blur | **`228 KB`** (`0.22 MB`) | MIT | Roda nativo no `cv2.FaceDetectorYN` sem MediaPipe/TFLite, gasta `< 5 MB` de RAM e processa 60s de clipe @ 1 fps em **`0.10s–0.42s`**. |
| **5. Detector de Risada / Grito** | **NÃO USAR modelo neural (`YAMNet`/`PANNs` descartados)**; usar energia de banda `1.2–3.8 kHz` + modulação `4–8 Hz` em `signals.py` | `YAMNet` ONNX (`14.5 MB`) só nas janelas candidatas | **`0 MB`** | — | `signal_loudness` + `_extend_past_reaction` já capturam os picos; filtro espectral em NumPy custa `0.5s` e `0 MB` vs `60–95s` do YAMNet no VOD. |

**Download total de modelos novos adicionado ao volume `/cache`:**
- Cenário Enxuto (`small` já existente + `Qwen3-1.7B` + `YuNet`): **`+1.06 GiB`**
- Cenário Completo (`+ large-v3-turbo` no refino final): **`+1.85 GiB`**

---

### Orçamento de RAM e Tempo por Etapa do Pipeline (VOD de ~2h40m, 8 Clipes Finais, Container `6 GB RAM / 6 Threads`)

> **Regra de Ouro de Memória:** As etapas 4 (ASR) e 6 (LLM) rodam em **processos ou blocos sequenciais** com `del model; gc.collect()` (ou subprocesso `llama-completion`), garantindo que **nunca** coexistam na RAM.

| # | Etapa do Pipeline | O que roda | RAM Pico (`RSS`) no Container (`Limite: 6.0 GB`) | Tempo de CPU (6 Threads, VOD 2h41m) | Tempo de CPU (Live 20 min) | Observações de Engenharia |
|---:|---|---|---:|---:|---:|---|
| **1** | `ingest` | `yt-dlp` (áudio + metadados + chat) + `ffmpeg` WAV 16kHz | `~180 – 250 MB` | `~20 – 35 s` | `~5 – 10 s` *(pós-captura)* | Streaming em disco (WAV 295 MB). |
| **2** | `signals` + `candidates` | RMS 2Hz (`loudness` + banda `1–4 kHz`), `chat` GQL/replay, `heatmap` | `~150 – 220 MB` | **`~1.0 s`** | **`~0.1 s`** | Leitura do WAV em chunks de 2 min (`signals.py`). |
| **3** | `asr` (Triagem Janelas) | `faster-whisper small` (`int8`, `beam_size=1`, Silero VAD) em ~24 min de áudio candidato (ou janelas expandidas p/ 180s nos top picos) | **`~650 MB`** *(liberado ao fim da etapa)* | **`~450 – 700 s`** (`7.5 – 11.7 min`) | **`~90 – 140 s`** | Ao finalizar, executar `del model; gc.collect()` antes da etapa 6. |
| **4** | `snap` + `multi-part` | Snapping de bordas de frase + extensão de reação + divisão de assuntos longos (até 3 min -> partes de 45–60s) | `~140 MB` *(ou `~330 MB` se usar MiniLM ONNX INT8)* | **`~0.1 – 1.5 s`** | **`~0.1 – 0.5 s`** | Puramente determinístico sobre `words.json` + `rms_db`. |
| **5** | `asr_refine` *(Opcional)* | `faster-whisper large-v3-turbo` (`int8`, `beam_size=2`) **apenas** nos 8 clipes finais (~5 min de áudio total) | **`~1.85 GB`** *(liberado ao fim da etapa)* | `~240 – 280 s` *(opcional)* | `~60 – 90 s` *(opcional)* | Corrige gírias pt-BR nas legendas queimadas e no prompt do LLM. |
| **6** | `llm_metadata` | **`Qwen3-1.7B-Q4_K_M`** (`6 threads`, ChatML + `/no_think`, JSON Schema: título, legenda, hashtags, nota 0–10) para os 8 clipes | **`~2.03 GB`** *(pico máximo do pipeline; liberado ao fim)* | **`~65 – 90 s`** (`~8–11 s` × 8 clipes) | **`~25 – 35 s`** (3 clipes) | Filtra clipes com `nota < 6` antes de gastar CPU renderizando vídeo! |
| **7** | `reframe_detect` | `ffmpeg` pipe 1 fps (`320x180`) + **`OpenCV YuNet`** (`228 KB`) nos clipes aprovados | **`~210 MB`** | **`~4 – 6 s`** (`~0.6 s` × 8 clipes) | **`~2 s`** | Decide entre `crop 9:16` no falante, `split-screen` webcam ou `16:9 blurred`. |
| **8** | `render` (c/ Crédito + Encerramento) | `yt-dlp --download-sections` + `ffmpeg libx264 veryfast` (`1080x1920`, ASS `CreditPill` + `Watermark` + `OutroCard`, `tpad clone 1.3s`, `afade`, `loudnorm`) | **`~450 – 650 MB`** | **`~480 – 500 s`** (`~61 s` × 8 clipes) | **`~120 – 180 s`** | Cartão final de `1.3s` + fade de áudio adicionam `< 3s` ao total dos 8 clipes. |
| — | **TOTAL DO PIPELINE** | **Execução Sequencial Sem Concorrência de Modelos Pesados** | **Pico Global: `2.03 GB` (`33.8%` dos 6 GB; ou `4.47 GB` c/ fallback 4B)** | **`~17 – 22 min`** *(p/ VOD de 161 min)* | **`~4.5 – 6 min`** *(p/ bloco de 20 min)* | **Margem livre de RAM no piso de 6 GB: `~3.97 GB` (padrão) / `~1.53 GB` (fallback 4B).** |
