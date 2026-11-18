# Pesquisa C: crédito/encerramento e modelos leves em CPU

Pesquisador: agente Cursor, 2026-11-17. **Medições feitas neste host: Intel i5-10500 (6C/12T desktop), NÃO no Ryzen 5 5500U do piso.** Usei container com `--memory 6g --cpus 6` e `-t 6`. O 5500U (Zen2, 15 W) deve ficar um pouco mais lento; estimo 10–40% a menos (**não verificado**). Artefatos: `~/.cache/pesq-bench/` (modelos, prompt, scripts).

## Parte 1: crédito e encerramento (o que vi)

- **Cortes do Flow (Shorts `suoXsKfW9uo`, `erKItlSoAOA`, quadros extraídos):** logo pequeno do Flow no canto inferior esquerdo, fixo o vídeo todo. Não há @ nem link na tela. O crédito fica na descrição ("ASSISTA COMPLETO: link", "VIRE MEMBRO…"). **Sem cartão final, sem fade:** o vídeo termina no fim de uma frase ("…que te dê dinheiro.") e emenda no loop.
- **Cortes Podpah (YouTube):** a primeira linha da descrição é "ASSISTA O EPISÓDIO COMPLETO: <link>".
- **Canais de fã (GAME PLAY CORTES, CORTES DO GMF, Cortando de Tudo):** a descrição lista "Lives na roxa: <canal>" + YouTube do streamer + "canal NÃO oficial" + e-mail para remoção.
- **Twitch Share → TikTok:** coloca automaticamente `#<canal>`, sem opção de remover ([Twitch Help](https://help.twitch.tv/articles/en_US/Knowledge/how-to-use-clips)).
- Não achei estudo medido sobre cartão final, CTA ou loop. O padrão BR dominante é **terminar na frase de payoff** (corte limpo na pausa) e dar o crédito no texto.

Receita proposta (atende a decisão 4 sem matar o loop):
- Crédito em ASS com estilo `Credit` no topo à esquerda, **dentro da safe zone** (topo ≥ 288, esquerda ≥ 120). Por exemplo, `Style: Credit,DejaVu Sans,38,&H40FFFFFF,&H00000000,&H80000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,7,120,0,300,1` e um evento de 0 até o fim com `twitch.tv/<canal>`.
- Encerramento de 1,2 s: segurar o último quadro + fade de áudio + cartão. Sem fade para preto, para o loop ficar suave.
  - vídeo: `...[v_base]tpad=stop_mode=clone:stop_duration=1.2[v_hold];[v_hold]subtitles='clip.ass'[v_out]`
  - áudio: `-af "apad=pad_dur=1.2,afade=t=out:st={D-0.3}:d=1.5,loudnorm=I=-14:TP=-1.5:LRA=11"` e `-t {D+1.2}` na saída
  - ASS no intervalo `[D, D+1.2]`: "Parte 2 →" (se houver continuação) ou "Live completa: twitch.tv/<canal>", centrado em y≈900 (`Alignment 5`)
- O link completo da live/VOD (com timestamp) vai na descrição do post, gerado pelo pipeline.

## Parte 2: modelos

### a) LLM de texto (medido: llama.cpp b12470, GGUF Q4_K_M, `llama-server --jinja`, JSON schema)

Prompt: transcrição pt-BR estilo Whisper (~280 tokens) → `{titulo, legenda, hashtags[3-5], nota 0-10, motivo}`. Gerou ~120–160 tokens.

| Modelo | Download | Mem. cgroup / RSS máx | pp tok/s | tg tok/s | s/clipe (quente) | Qualidade pt-BR (1 amostra) | Licença (HF API) |
|---|---|---|---|---|---|---|---|
| Qwen2.5-1.5B-Instruct | 1,07 GB | 1,55 / 1,73 GiB | 157* | 24,9* | ~6–8 | título genérico, **inventou** "roubaram a moto do professor e eu fui parar na faculdade" | apache-2.0 |
| Qwen3-1.7B (sem think) | 1,06 GB | 1,29 / 1,98 GiB | 135* | 21,6* | ~7 (est.) | título ok; "#Viral"; inventou "a situação foi viral" | apache-2.0 |
| Llama-3.2-3B-Instruct | 1,93 GB | 2,68 / 3,49 GiB | 70* | 12,0* | ~12 (est.) | título fraco; hashtags com hífen (inválidas) | llama3.2 (gated) |
| **Qwen3-4B-Instruct-2507** | 2,50 GB | 2,79 / 4,36 GiB | ~54 | ~8,9 | **~15–19** | melhor título ("Roubaram a moto do professor? A verdade é absurda!"); a legenda inverteu um fato | apache-2.0 |
| Gemma-3-4B-it | 2,49 GB | 2,65 / 4,09 GiB | 60† | 8,2† | ~20 | bom título; tags genéricas | gemma (gated) |

\* `llama-bench` pp512/tg128. † primeira requisição (frio). A primeira chamada após carregar custa +10–20 s, por causa do page-in do mmap. "RSS máx" inclui páginas do arquivo mapeado, que o kernel consegue reclamar.

O JSON schema (gramática) quase não custa no tg (23,8 vs 23,7 tok/s no Qwen2.5).

Achado crítico: **todos deram nota 7–9** para o mesmo trecho, então a nota 0–10 absoluta de um LLM pequeno não discrimina. Sugestão:
- calcular a nota principal a partir dos sinais (chat e loudness), que já existem;
- pedir ao LLM uma rubrica com âncoras ("10 = …, 5 = …, 0 = …") ou um **ranking comparativo** de 5 candidatos por vez;
- usar temperatura 0;
- validar o título contra a transcrição (descartar nomes ou fatos ausentes).

**Recomendação:** padrão **Qwen3-4B-Instruct-2507 Q4_K_M** (Apache-2.0, ~2,8–4,4 GB, ~15–20 s por clipe no i5). Fallback para RAM/tempo: **Qwen3-1.7B Q4_K_M** com `--reasoning-budget 0` (~1,3–2 GB, ~3x mais rápido). Via `llama-cpp-python` ou `llama-server`, que é um binário estático de 23 MB e evita compilar wheel. Gemma e Llama são gated e têm licença própria, por isso evito.

### b) Embeddings para segmentar assunto

| Opção | RAM | Velocidade | Separação no teste |
|---|---|---|---|
| paraphrase-multilingual-MiniLM-L12-v2 (ONNX, fastembed) | 0,78 GB RSS do processo | **175 frases/s** (medido) | cos mesmo assunto 0,52 vs assunto diferente 0,02 |
| multilingual-e5-large (medido só porque o fastembed não tem o e5-small) | 2,3 GB | 38 frases/s | 0,87 vs 0,82: sem prefixo `query:` não separa |
| multilingual-e5-small | ~0,45 GB ([MTEB](https://leaderboard.mteb.org/models/intfloat/multilingual-e5-small)) | ~15 ms por frase ONNX em Ryzen 7730U ([Medium](https://medium.com/@janschachtschabel/multilingual-embedding-models-in-2026-what-actually-works-on-cpu-d4bf3fe3d335)) | não medido |
| TextTiling puro (léxico) | ~0 | instantâneo | **não medido**. Em fala coloquial a sobreposição lexical é fraca (opinião, não verificado) |

Recomendação: **MiniLM-L12 via `onnxruntime`**, que já vem como dependência do faster-whisper (VAD). Embedding por frase do Whisper, similaridade de janela deslizante (3 frases), e borda de assunto onde houver vale abaixo de média − 1σ, combinado com pausa ≥ 0,8 s. Custo: < 2 s por janela de 3 min. TextTiling fica como fallback sem modelo.

### c) Rosto/webcam (reenquadramento)

- **YuNet (OpenCV `FaceDetectorYN`), medido:** **14,4 ms por quadro a 640x360, 1 thread**. Achou o rosto "webcam" no canto. A 320x180 **perdeu** o rosto pequeno, então para overlay de webcam use ≥ 640 px. Referência: 1,6 ms a 320² no i7-12700K ([paper](https://doi.org/10.1007/s11633-023-1423-y)).
- **MediaPipe BlazeFace short-range:** entrada 128x128, 2,94 ms no Pixel 6 ([Google](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector)). É feito para selfie a curta distância. Para webcam pequena no canto do jogo, **não verificado** e provavelmente pior.
- Amostragem a 1 fps: `ffmpeg -ss A -t D -i src -vf fps=1,scale=640:360 -f rawvideo -pix_fmt bgr24 -` lido em numpy. Clipe de 60 s = 60 quadros ≈ 1 s de detecção + decode (**não medido**). Depois, mediana da caixa e crop fixo (ou suavizado).

### d) Risada/grito (YAMNet / PANNs)

- YAMNet: 3,7 M pesos, 69,2 M multiplicações por quadro de 0,96 s. Tem as classes Laughter (13), Screaming (11), Shout, Cheering e Applause ([README](https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/README.md), [class map](https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/yamnet_class_map.csv)). PANNs MobileNetV2: 4,1 M parâmetros, mAP 0,383, contra 0,431 do CNN14 de 81 M ([arXiv](https://arxiv.org/html/1912.10211v5)).
- Computação desprezível (2 h de áudio ≈ 7.500 quadros). O custo real é dependência: o YAMNet oficial é TF/TF-Hub; existe ONNX de terceiros (**não verificado**).
- **Vale o custo?** Ainda não. Loudness + chat já pegam o pico. Usar só se o teste real mostrar picos falsos de música/jogo alto. Nesse caso, rodar YAMNet-ONNX apenas nas janelas candidatas.

### e) ASR

| Modelo (faster-whisper int8) | WER pt-BR | Velocidade CPU | RAM |
|---|---|---|---|
| small (atual) | FLEURS 6,8–7,7%; Common Voice 13,5% ([TypeWhisper FLEURS](https://typewhisper.github.io/typewhisper-benchmark/?suite=clean-speech-fleurs-pt&view=suite), [CV](https://typewhisper.github.io/typewhisper-benchmark/?suite=clean-speech-commonvoice-pt&view=suite), [Handy](https://models.handy.computer/languages/pt/)) | **medido no projeto: 24 min em 703 s ≈ 2,0x tempo real** | ~1,5 GB ([README faster-whisper](https://github.com/SYSTRAN/faster-whisper/)) |
| large-v3-turbo | FLEURS 3,8–4,2%; CV 5,9% | Handy em AMD 4750U CPU: 1,4x contra 5,9x do small, ou seja, ~4x mais lento. **No nosso caso, ~0,5x tempo real (estimado, não medido)** | ~1,5–2,2 GB |
| distil-large-v3 | **descartar: modelo só de inglês** ([HF](https://huggingface.co/distil-whisper/distil-large-v3)) | — | — |

Recomendação: manter **small** no volume. Turbo só quando valer a pena, por exemplo no re-ASR apenas do clipe final de 60 s (~2 min de CPU), para a legenda sair melhor.

## Recomendação final e orçamento (por clipe de ~60 s, sequencial)

| Etapa | Modelo | RAM pico | Tempo (i5-10500, 6 threads) |
|---|---|---|---|
| ASR janela de 90 s | whisper small int8 | ~1,5 GB | ~44 s (703 s / 16 janelas, medido) |
| Segmentar assunto | MiniLM-L12 ONNX | ~0,8 GB | < 2 s |
| Reenquadrar | YuNet 640x360 @1 fps | < 0,2 GB (est.) | ~1 s + decode |
| Render | ffmpeg | não medido | ~60 s (medido, inclui download) |
| Texto + nota | Qwen3-4B Q4_K_M (descarregar Whisper antes) | ~2,8–4,4 GB | ~15–20 s + ~15 s de carga por lote |

Carregar o LLM **uma vez por lote** de clipes, depois do ASR de todos. Pico de RAM ≈ 4,4 GB (LLM sozinho), dentro dos 6 GB. Total ≈ 2–2,5 min de CPU por clipe, ou seja, ~25–30 clipes/hora no i5 (5500U: menos, não medido). Com folga para 1 post a cada 45 min.
