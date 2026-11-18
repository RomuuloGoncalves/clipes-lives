# Pesquisa C: crédito/encerramento de clipes e modelos de IA leves em CPU

Data da pesquisa/acesso às fontes: **2026-11-17**. Legenda: **[medido]** = rodei em Docker (`--memory 6g --cpus 6`) nesta pesquisa; **[fonte]** = número citado com URL; **[estimativa]** = conta minha, rotulada; **nao verificado** = sem fonte nem medição.

> Limitação das medições: a máquina de teste é o notebook de desenvolvimento (12 threads, ~31 GB RAM, CPU exata não registrada), limitada pelo Docker a 6 GB/6 CPUs. Não é o Ryzen 5 5500U; tempos absolutos podem divergir. Só vale a razão entre modelos. Tudo rodou **dentro de container** (ver "Pegada no host" no fim).

---

## Parte 1: crédito e encerramento

### 1.1 O que a web diz (e o que NÃO consegui ver)

Não consegui assistir a vídeos reais nem medir tempo de tela de clipes de sucesso: **"exemplos reais vistos" = nao verificado**. O que há são guias e posts de blog (secundários, vários de vendors):

| Fato | Fonte (acesso 2026-11-17) | Confiança |
|---|---|---|
| Margens de trabalho em 1080x1920: topo ~15% (~288 px), base 25-35% (480-672 px), laterais 10-15% (108-162 px). Os próprios autores dizem que são "working margins", não especificação oficial; guias oficiais de Google/Meta/TikTok são para anúncios. Atualizado 2026-09-07. | https://reap.video/blog/short-form-video-safe-zones | média |
| Caixa central 900x1400 px como zona segura cruzada; coluna de botões do TikTok ~164 px à direita (fonte única). | https://blitzcutai.com/blog/best-caption-placement-short-form-video e https://adaptlypost.com/en/blog/social-media-safe-zones-2026-complete-guide | baixa-média |
| Números de margem divergem entre sites (Shorts: 48/192 px lateral segundo Google Ads via Socialync; 96 px dir./300 px base segundo Adaptlypost). Recomendação comum: conferir em preview real no app. | https://www.socialync.io/tools/safe-zones/youtube-shorts , https://adaptlypost.com/free-tools/safe-zone-checker | baixa |
| Encerramento: guias de Shorts dizem que quase não há espaço para outro tradicional (hook primeiro; CTA curto 0-2 s no TikTok/Reels). Um guia fala em outro de 3 s. Fontes discordam. Qual página deu a tabela de tempos: nao verificado. | resultados de busca; ex.: https://www.trymypost.com/blog/youtube-shorts-end-screen-strategy-2026 | baixa |
| "Follow for Part 2" animado: +18% de inscritos em um Short (teste interno de um vendor, não independente). | https://fluxnote.io/guides/call-to-action-youtube-shorts | baixa |
| Loop: terminar de forma que reencaixe no primeiro quadro; CTA que aparece tarde demais perde o espectador no reinício. | idem (resultados de busca) | baixa |
| **Crédito sozinho não torna o conteúdo "original"**: YouTube derruba alcance de re-uploads; política de conteúdo reutilizado vale mesmo com permissão do criador e pode desmonetizar o canal inteiro. Datas divergem entre fontes (out/2026): conferir na política oficial. | https://outlierkit.com/resources/youtube-shorts-original-content-update/ , https://vidiq.com/blog/post/youtube-reused-content-policy-guide/ , https://ppc.land/re-uploaded-shorts-lose-reach-as-youtube-favours-original-clips/ | média (secundárias; política oficial: nao verificado) |
| Nenhuma fonte achada sobre "etiqueta de canal / @ / link na descrição" ajudar ou proteger em clipes de streamer. | busca | nao verificado |

**Implicação para o produto (decisão do usuário nº 4):** crédito + link são boa prática e necessários, mas não resolvem risco de "conteúdo reutilizado". O valor próprio (legenda, título, corte editorial, partes com gancho) é o que conta; vale registrar isso no README. Link clicável só funciona na **descrição/comentário fixado**; dentro do vídeo é só texto (não clicável) → usar `@canal` no vídeo e o link completo na legenda do post.

### 1.2 Receita proposta (testada: sintaxe e quadro renderizados em container)

Layout atual (`clipes/render.py`): vídeo 16:9 de 1080x608 centralizado (y 656-1264) sobre fundo borrado; legenda `Alignment=2`, `MarginV=430` (linha de base em y≈1490, ou seja 22% da base, **ligeiramente abaixo dos 25% recomendados**; subir `MarginV` para ~480-520 é barato).

**Crédito (primeiros ~4 s, depois marca discreta):** texto no fundo borrado logo acima do vídeo, y≈520 (fora dos 288 px do topo, x≥120 fora das laterais), fade in/out:

```ass
Style: Credit,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,0,7,120,160,0,1
Dialogue: 0,0:00:00.30,0:00:04.30,,Credit,,0,0,0,,{\pos(120,520)\fad(300,400)}Clipe de @CANAL
Dialogue: 0,0:00:04.30,0:00:CLIP_END,,Credit,,0,0,0,,{\pos(120,520)\alpha&H99&}@CANAL
```
(o segundo evento é a marca d'água a ~40% de opacidade durante o resto do clipe; `\alpha&H99&` = 60% transparente.) Tempo de tela de 4 s para o crédito completo e marca permanente discreta é **decisão de design minha**, não derivada de fonte.

**Encerramento (nunca corte seco): congelar último quadro + fade de áudio + cartão 1,5-2 s.** Receita validada (saída de 10,00 s, quadro com cartão conferido visualmente):

```bash
# D = duração do clipe; cartão de 2 s
-filter_complex "[0:v]...layout...[v_base];\
 [v_base]tpad=stop_mode=clone:stop_duration=2,fade=t=out:st=$((D+1.4)):d=0.6,subtitles='clip.ass'[v_out]"
-af "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st=$(D-0.5):d=0.5,apad=pad_dur=2"
-t $((D+2))
```
Cartão no mesmo `.ass` (aparece sobre o quadro congelado):
```ass
Style: Card,DejaVu Sans,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,0,5,120,160,0,1
Dialogue: 0,0:00:D,0:00:D+2,,Card,,0,0,0,,{\pos(540,900)\fad(250,0)}Parte 2 →\NLive completa no link da bio
```
- Partes 1..N-1: "Parte N+1 →" (gancho + seta). Última parte / clipe único: "@CANAL · live completa no link". Loop: omitir o fade-to-black e terminar no quadro que reencaixa (opcional, nao testado em audiência).
- Ordem dos filtros de áudio: `afade`/`apad` **depois** do `loudnorm` (o `-af` atual do projeto é só `loudnorm`).
- Cuidado: `tpad` com `stop_duration` exige `-t D+2` (senão o ffmpeg encerra no vídeo original se o áudio não for estendido com `apad`).
- Custo: desprezível (filtros leves; o `subtitles` já é usado).

---

## Parte 2: modelos

### 2.1 Medições [medido] (container 6 GB / 6 CPUs, áudio pt-BR de 250 s do Podpah #1049, 600-850 s)

Config: `int8`, `cpu_threads=6`, `beam_size=1`, VAD ligado, **sem** word timestamps (o projeto usa beam padrão=5 e word timestamps, por isso 0,49 xRT no teste real contra 0,29 aqui).

| ASR (faster-whisper int8) | xRT medido (menor=melhor) | Pico RSS do processo | Carga | Qualidade pt-BR observada |
|---|---:|---:|---:|---|
| small | **0,29** (73 s p/ 250 s) | 1,6 GB | 5,5 s | boa; erros de palavra ("ferramento", "ele só pesa") |
| large-v3-turbo | **0,38** (95 s) | ≤3,1 GB* | 104 s (inclui download) | melhor ("ferramenta"); 869 vs 837 palavras reconhecidas |
| distil-large-v3 | 0,70 (174 s) | ≤3,1 GB* | 55 s | **inutilizável em pt-BR**: saiu em inglês ("Because we... What you say, Gabriel?"), 417 palavras. O modelo é só inglês. |

\* RSS máximo é cumulativo no processo; turbo e distil compartilham o teto de 3,07 GB, o real de cada um é ≤ isso.
WER pt-BR: **nao medido** (sem transcrição de referência). Os pontos acima são inspeção de 350 caracteres, não WER.
Fontes de apoio: tabela oficial do faster-whisper (small int8, i7-12700K 8 threads, 13 min de áudio: 1m42s, 1477 MB RAM, beam 5): https://github.com/SYSTRAN/faster-whisper (acesso 2026-11-17). Large-v3 turbo int8 vs distil int8 vs large-v3 (RTX 2080 Ti, LibriSpeech **inglês**, WER 1,92% / 2,39% / 4,59%): https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/discussions/3 .
**Escala para o piso [estimativa]:** turbo ≈ 1,3x o tempo do small → no teste real (703 s/24 min com small) viraria ~900 s, +1,4 GB de RAM. Piso 5500U mais lento que o notebook: não medido.

### 2.2 Tabela de decisão (todos os candidatos)

| Item | Candidato | Tamanho / RAM | Velocidade | Qualidade pt-BR | Licença | Status |
|---|---|---|---|---|---|---|
| a) LLM | **Qwen3-4B Q4_K_M** | arquivo 2,5 GB [fonte HF]; RAM ~3-3,5 GB com ctx 2k [estimativa] | CPU 6 thr: ~5-8 tok/s [estimativa por banda de memória, **nao medido**] | 100+ idiomas citados; pt não nomeado no card | Apache-2.0 [fonte] | **padrão** |
| a) | **Qwen3-1.7B Q8_0** | arquivo 1,83 GB [medido: 1.834.426.016 B] | ~2x do 4B [estimativa] | idem; pt não nomeado | Apache-2.0 [fonte] | **fallback / pontuação em massa** |
| a) | Phi-4-mini 3.8B | Q4 nao verificado | nao medido | pt nomeado na lista [fonte] | MIT [fonte] | alternativa |
| a) | Gemma 3 4B | 4B | nao medido | "140+ idiomas", pt não nomeado [fonte] | termos Gemma, download com aceite (gated) [fonte] | descartar p/ padrão (gated) |
| a) | Qwen2.5-3B | 3,09B | nao medido | pt nomeado [fonte] | **qwen-research** (restritiva) [fonte] | **evitar** |
| a) | Llama 3.2 3B | ~2,5 GB Q4 (blog) | 25-45 tok/s "CPU moderna" (blog, inconsistente) | nao verificado | licença nao verificado | só se o resto falhar |
| b) | multilingual-e5-small | 118M params (21,6M ativos) [blog Bekko], 384 dim | **nao medido** | multilíngue (pt incluso no treino mE5: nao verificado nesta sessão) | MIT: nao verificado | opcional |
| b) | paraphrase-multilingual-MiniLM-L12-v2 | 117,7M, ONNX 0,44 GB, seq. máx. 128 | nao medido | pt sim (modelo "paraphrase multilingual") | nao verificado | opcional |
| b) | TextTiling/léxico sem modelo | 0 MB | ms | não comparado | n/a | **padrão** |
| c) | YuNet (OpenCV) | modelo ~0,2 MB (nao verificado) | 1,6 ms @320x320 (i7-12700K, paper); ~5 ms @320 e 22 ms @640 (i7-10710U, blog OpenCV) | n/a | Apache/MIT (nao verificado) | opcional (só p/ reenquadrar) |
| c) | MediaPipe | n/a | sem fonte | n/a | n/a | nao verificado |
| d) | YAMNet | 3,73M params, 14,2 MB (Qualcomm card) | ~30 ms por janela de 0,96 s em Raspberry Pi 4 fp32 (fonte única, nao verificada) | n/a (áudio) | Apache-2.0 (nao verificado aqui) | adiar |
| d) | PANNs CNN14 | 162 MB fp16 (conversão LiteRT) | só dado de GPU | n/a | nao verificado | descartar (pesado) |
| e) | whisper small int8 | 1,6 GB RSS [medido] | 0,29 xRT [medido] | boa | MIT (faster-whisper) | **padrão** |
| e) | large-v3-turbo int8 | ≤3,1 GB [medido] | 0,38 xRT [medido] | melhor | nao verificado (modelo OpenAI) | opção "qualidade" |
| e) | distil-large-v3 | ≤3,1 GB | 0,70 xRT | **falha em pt-BR** [medido] | n/a | **descartado** |

Fontes: Qwen3-4B-GGUF https://huggingface.co/Qwen/Qwen3-4B-GGUF ; Qwen3-1.7B https://huggingface.co/Qwen/Qwen3-1.7B ; Qwen2.5-3B https://huggingface.co/Qwen/Qwen2.5-3B-Instruct ; Phi-4-mini https://huggingface.co/microsoft/Phi-4-mini-instruct ; Gemma 3 https://huggingface.co/google/gemma-3-4b-it ; Llama 3.2 blog https://bestllmfor.com/reviews/llama3-3b-deep-review/ ; Bekko https://huggingface.co/blog/hotchpotch/bekko-embedding ; MiniLM ONNX https://huggingface.co/LightEmbed/sbert-paraphrase-multilingual-MiniLM-L12-v2-onnx ; YuNet paper https://link.springer.com/10.1007/s11633-023-1423-y ; YuNet blog OpenCV https://opencv.org/opencv-face-detection-cascade-classifier-vs-yunet/ ; YAMNet https://huggingface.co/qualcomm/YamNet ; PANNs https://huggingface.co/litert-community/PANNs-CNN14-AudioSet-LiteRT (todas acessadas 2026-11-17).

**Tentativa de medir LLM:** baixei Qwen3-4B Q4_K_M e Qwen3-1.7B Q8_0 e o llama.cpp b12470 no container, mas a execução do `llama-bench` não produziu saída útil e, ao chegar a regra de "nada fora do Docker/volume", interrompi. **tok/s e RAM do LLM ficam "nao medido"**; os números acima são estimativas de banda de memória (modelo de 2,5 GB lido por token; ~40-50 GB/s teórico em DDR4 dual-channel → teto ~15-20 tok/s, realista 5-8). Medir antes de decidir: `llama-bench -m <gguf> -t 6 -p 512 -n 64` dentro do container.

### 2.3 Respostas por item

**a) LLM.** Padrão: **Qwen3-4B-Q4_K_M** via llama.cpp (Apache-2.0, 2,5 GB, ctx 2-4k basta: 1 parte ≤60 s ≈ 150-250 palavras ≈ 400 tokens). Fallback: **Qwen3-1.7B (Q8_0 1,8 GB, ou Q4 se existir)**, também para a pontuação em massa da frente D (saída = 1 número, custo dominado pelo prefill). Evitar Qwen2.5-3B (licença research) e Gemma (gated). Qualidade pt-BR de nenhum foi medida: **montar 10-20 trechos reais e comparar à mão** antes de fixar. `/no_think` do Qwen3 (desliga raciocínio, poupa tokens): nao verificado nesta sessão. Pedir JSON com `response_format`/gramática do llama.cpp (nao testado) para evitar parse frágil. Roda sequencial: descarregar o Whisper antes (nunca juntos).
**b) Embeddings.** Não entram na v1. Segmentação de assunto via TextTiling léxico (janelas de ~8-10 frases, similaridade de cosseno de bag-of-words com stopwords pt, vale = fronteira) + pausas + fim de frase, ~30 linhas numpy, 0 MB. Subir para multilingual-e5-small (via ONNX Runtime) só se o léxico der cortes ruins; **nenhuma fonte achada compara os dois em transcrição pt-BR: nao verificado**. Custo estimado de qualquer um dos dois é desprezível frente ao ASR (centenas de frases/hora), mas RAM/velocidade reais **nao medidos**.
**c) Rosto.** YuNet a 1 fps: `ffmpeg -ss S -t D -i src -vf fps=1,scale=640:-1 -f rawvideo` e 1 detecção por quadro (~5-22 ms) → 60 quadros por clipe ≈ <2 s; mediana das caixas → 1 `crop` fixo por clipe (webcam de streamer é estática), nada por quadro. Só vale se for reenquadrar; o layout atual (16:9 inteiro sobre fundo borrado) não precisa. Dependência nova: `opencv-python-headless`. Adiar.
**d) Risada/grito.** Adiar. Na v1 o sinal de loudness + texto ("kkk", "haha", "!") + chat cobre. YAMNet (14 MB, 521 classes) rodaria só nas janelas candidatas (≈ 100 janelas de 0,96 s por candidato) e custaria segundos, **mas** exige runtime TFLite/ONNX e eu não tenho evidência de ganho: se entrar, só na frente D (modo cota, sem picos). Existência da classe "Laughter" no AudioSet: conhecimento prévio, **nao verificado em fonte** nesta sessão.
**e) ASR.** Manter **small int8** como padrão; `large-v3-turbo` como opção `CLIPES_WHISPER_MODEL=large-v3-turbo` quando a qualidade do texto importar (legenda/título): +31% tempo, +1,4 GB. **distil-large-v3 fora** (inglês).

### 2.4 Recomendação final: o menor conjunto

1. **Whisper small int8** (já existe) → opção turbo por env var. Zero dependência nova.
2. **Um LLM:** Qwen3-4B-Q4_K_M (padrão) / Qwen3-1.7B (fallback + scoring em massa), via **binário `llama-server`/`llama-cli` do llama.cpp** chamado por subprocess/HTTP (evita compilar `llama-cpp-python`: **no ambiente de teste o `pip install llama-cpp-python` falhou ao compilar** por falta de compilador/wheel). Interface mínima `complete(prompt)->str`; trocável sem custo extra.
3. **Sem** embeddings, sem detector de rosto, sem YAMNet na v1.

### 2.5 Orçamento por etapa (VOD de 1 h, 1 modo de picos; piso 6 GB/6 thr; estimativas marcadas)

Premissa: ~8 candidatos finais (16 janelas de 90 s = 24 min de áudio, como no teste real).

| Etapa | Tempo | Pico de RAM | Nota |
|---|---:|---:|---|
| ingest | 15-25 s | <0,5 GB | [medido no teste real] |
| sinais 1 Hz + picos | <1 s | <0,3 GB | [medido: 0,62 s p/ 2h41] |
| ASR small (janelas) | ~700 s (0,49 xRT, beam 5+word ts) | ~1,6-2 GB | [medido no teste real; 0,29 xRT em beam 1] |
| (ASR turbo, opcional) | ~900 s | ~3,1 GB | [estimativa] |
| snap + segmentação TextTiling | <1 s | <0,1 GB | |
| **LLM** (descarrega Whisper antes) 8 clipes × (~600 tok in + ~150 out) | ~8 × 30-40 s ≈ 4-5 min c/ 4B; ~2 min c/ 1,7B | ~3-3,5 GB (4B) | **[estimativa, nao medido]** |
| render | ~60 s/clipe → ~8 min | <1 GB | [medido] |
| **Total** | **~25 min** (small) por hora de VOD | max 3,5 GB (etapas sequenciais) | LLM e Whisper nunca coexistem |

Maior alavanca de tempo continua sendo ASR e render; o LLM entra como ~+15% do total.
