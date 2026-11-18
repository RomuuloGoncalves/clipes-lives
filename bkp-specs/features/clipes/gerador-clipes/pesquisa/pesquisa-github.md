# Pesquisa: projetos open source de auto-clipping

Data da coleta: 2026-11-16. Estrelas, último push e licença vieram da API do GitHub (`gh api repos/<repo>`) nesse dia. O "como detecta" vem do README de cada projeto (baixado e lido). Não rodei nenhum deles.

## Resumo

Nenhum projeto pronto atende ao pedido inteiro (Linux, CPU-only com ~9 GB de RAM, sem chave paga, live + VOD, chat como sinal, foco em "sentido" do corte). Os maiores (OpenShorts, AI-Youtube-Shorts-Generator, SupoClip) foram feitos para **podcast/entrevista**: o LLM lê só a transcrição e escolhe trechos "virais". Em live de jogo/reação isso falha, porque o momento bom muitas vezes é um grito, uma risada ou o chat explodindo, e o texto sozinho não mostra isso.

Os que usam sinais de audiência (chat, heatmap, áudio) são pequenos, recentes e às vezes sem licença. Servem de **referência de design**, não de base de código:

- **Clips Kitty** (AGPL): o LLM recebe a transcrição **mais uma linha do tempo de eventos** (pico de áudio, chat, heatmap). É a ideia mais próxima da que recomendo.
- **TWITCH-Generator** (sem licença): cada sinal é medido **em relação a uma linha de base móvel de ±2,5 min**, então uma live sempre barulhenta não vira "sempre empolgante". Os pesos de fusão ficam num arquivo de config.
- **autoclipnew / vod-to-viral** (sem licença): baixam **só o áudio e o chat** da VOD inteira e depois **só os trechos de vídeo escolhidos**. Economiza 90%+ de banda e disco.

**Atualização após consolidar com o agy:** o [sharkyclips](https://github.com/utkualsir/sharkyclips) (MIT, out/2026) já implementa quase essa arquitetura, com uma CLI sem interface que dá para rodar no Linux. Por isso o plano começa com um **spike**: rodar o sharkyclips (e o chopify) em VODs reais antes de escrever código. Se ele servir, o projeto vira um wrapper fino (config pt-BR + modo live). Senão, vira um pipeline próprio e pequeno que copia módulos MIT dele. Ver "Consolidação" abaixo.

## Clippers completos

| Projeto | ★ | Último push | Licença | Como detecta o momento | O que reaproveitar | Por que não serve como base |
|---|---|---|---|---|---|---|
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | 8710 | 2026-11-16 | MIT | Gemini 3.1 Flash-Lite lê transcrição + cortes de cena e escolhe 3–15 trechos de 15–60 s com "virality score". Pode apontar `LLM_BASE_URL` para Ollama. | Enquadramento 9:16 por cena (TRACK com MediaPipe+YOLOv8, GENERAL com fundo borrado, SPLIT), legendas word-level com faster-whisper. | Só transcrição como sinal, sem chat/áudio. Feito para podcast. Muitas features de SaaS (publicação, ElevenLabs, fal.ai). README diz 5–8 min por vídeo de 8 min em CPU. |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | 6165 | 2026-11-12 | MIT | LLM (OpenAI/Gemini) classifica o tipo de vídeo e ranqueia a transcrição por "hooks, picos emocionais, revelações". Modo local usa faster-whisper + OpenCV. | Dedupe de trechos sobrepostos por score; prompt com "score + hook + motivo" por clipe. | O LLM é sempre remoto (modo padrão usa MuAPI, pago). Só transcrição. |
| [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip) | 3328 | 2026-11-17 | AGPL-3.0 | LLM escolhe 3–7 trechos da transcrição; scores de hook/engajamento/valor. Aceita VOD da Twitch e da Kick; **live não é suportada**. | Templates de legenda animada. | Transcrição padrão pela AssemblyAI (API paga; o modo Whisper local é um tutorial à parte). Docker + conta de usuário. AGPL. |
| [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) (Clips Kitty) | 283 | 2026-11-17 | AGPL-3.0 | 5 sinais em bins de 1 s: transcrição, áudio (picos de loudness, densidade de rajadas, padrão silêncio→explosão), visual (cortes, movimento, área do rosto), **reação** (chat da Twitch por *chatters únicos*, heatmap "most replayed" do YouTube). O LLM local (Ollama, Gemma) recebe a transcrição + a linha do tempo dos eventos. Corta em limite de frase pelos timestamps de palavra. | **Conceito central** (LLM vê sinais + texto), contagem por chatters únicos, padrão silêncio→explosão, layout "Gaming/Reaction" (webcam numa metade, jogo na outra). | App **só para Windows** (Electron); pede **16 GB de RAM** ("8 GB analisa o vídeo e depois…"). AGPL: copiar código obriga o projeto a ser AGPL. |
| [cccdemon/TWITCH-Generator](https://github.com/cccdemon/TWITCH-Generator) | 0 | 2026-11-16 | **nenhuma** | `highlight = wC·chat + wA·áudio + wT·texto + wV·vídeo`; chat = velocidade, spam de emote, "clip it", risada; áudio = rajadas EBU R128 acima do nível do próprio stream; **cada sinal relativo a uma linha de base móvel de ±2,5 min**; um passe de LLM (Claude) ranqueia todos os candidatos lado a lado. Corte 9:16 com facecam em cima e jogo embaixo. | Fórmula de fusão, normalização por linha de base móvel, um passe final de ranking comparativo, `moments.json` com os componentes do score. | Sem licença (não dá para copiar código). Exige `ANTHROPIC_API_KEY` (pago). Sem estrelas, provavelmente uso pessoal. |
| [PIGJET/autoclipnew](https://github.com/PIGJET/autoclipnew) (ClipForge) | 0 | 2026-11-17 | **nenhuma** | yt-dlp traz metadados e **heatmap**; densidade de chat; RMS do áudio em janela móvel; funde candidatos próximos com bônus quando sinais independentes concordam; faster-whisper (base, CPU int8) **só nas janelas candidatas**. | Bônus de concordância entre sinais; transcrever só as janelas candidatas; não baixar o vídeo inteiro. | Sem licença; o próprio README diz que parte da demo usa inferência "stubbed". |
| [LuisSotelo/vod-to-viral](https://github.com/LuisSotelo/vod-to-viral) | 12 | 2026-06-15 | **nenhuma** | Fusão RMS + palavras-chave + atividade de chat (peso extra para mod/VIP/streamer). Baixa só o JSON do chat e o áudio, depois `yt-dlp` baixa só os trechos de 30–45 s. Gemini para título. | Download cirúrgico; layout Twitch com coordenadas fixas da facecam (`VERTICAL_FACE_X/Y/W`); legenda ASS word-by-word; respeito às safe areas do TikTok/Shorts. | Sem licença; Gemini obrigatório; publica automaticamente (fora do escopo). |
| [Farkoal2128/AutoClip-MVP](https://github.com/Farkoal2128/AutoClip-MVP) | 4 | 2026-10-31 | MIT | Detecção por IA (Ollama) propõe candidatos para revisão. | Fluxo "propor → revisar". | Só Windows; muito pequeno. |
| [bendawg2010/Auto-clipper](https://github.com/bendawg2010/Auto-clipper) | 11 | 2026-04-19 | MIT | YOLO por jogo, análise de pixels, gatilho de voz "clip it" (Whisper). | Ideia do gatilho de voz. | Específico de jogos com YOLO treinado; sem chat. |
| [Shaarav4795/ClippedAI](https://github.com/Shaarav4795/ClippedAI) | 241 | 2026-09-04 | NOASSERTION | Usa a lib ClipsAI (segmentação por tópico na transcrição) + Groq para títulos. | — | Exige token HF com pyannote (diarização, pesado em CPU); licença indefinida. |

## Bibliotecas e peças

| Projeto | ★ | Último push | Licença | Papel no nosso pipeline |
|---|---|---|---|---|
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | 208442 | 2026-11-16 | Unlicense | Metadados, **heatmap do YouTube** (campo `heatmap` no extrator, conferido em `yt_dlp/extractor/youtube/_video.py`), áudio, `--download-sections`, chat replay do YouTube como legenda `live_chat`, `--live-from-start`. Suporta Kick live/VOD/clip (`kick.py`), com issues recentes de quebra (out/2026). |
| [streamlink/streamlink](https://github.com/streamlink/streamlink) | 11935 | 2026-11-14 | BSD-2 | Gravação de live (HLS) para Twitch/YouTube/Kick. Release 8.6.3 (2026-10-29). |
| [lay295/TwitchDownloader](https://github.com/lay295/TwitchDownloader) | 4060 | 2026-11-15 | MIT | `TwitchDownloaderCLI chatdownload` baixa o chat de VOD da Twitch em JSON com offset; binário Linux; release 1.56.5 (2026-07-20). |
| [xenova/chat-downloader](https://github.com/xenova/chat-downloader) | 1200 | 2025-11-05 | MIT | **Não usar.** Chat do YouTube (live e replay) em tese; o agy aponta quebra também no YouTube (#290, #286). **Twitch quebrado**: issue #283 "Twitch chat downloading fails" aberta desde 2025-11-11, 19 comentários. Último push há 1 ano: frágil. |
| [taizan-hokuto/pytchat](https://github.com/taizan-hokuto/pytchat) | 370 | 2021-07-24 | MIT | **Arquivado**, não usar. |
| [PetterKraabol/Twitch-Chat-Downloader](https://github.com/PetterKraabol/Twitch-Chat-Downloader) | 651 | 2022-12-26 | MIT | Abandonado, não usar. |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 26807 | 2026-11-12 | MIT | Transcrição CPU int8 com timestamps de palavra e VAD Silero embutido. |
| [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 55253 | 2026-11-16 | MIT | Alternativa em C++; ver comparação em `pesquisa-tecnologias.md`. |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 25387 | 2026-11-12 | BSD-2 | Alinhamento forçado (wav2vec2) para timestamps mais precisos; pesado para CPU, não é necessário no MVP. |
| [linto-ai/whisper-timestamped](https://github.com/linto-ai/whisper-timestamped) | 2862 | 2026-09-28 | AGPL-3.0 | Timestamps de palavra com confiança; AGPL e mais lento que faster-whisper. |
| [snakers4/silero-vad](https://github.com/snakers4/silero-vad) | 10677 | 2026-10-22 | MIT | Detecção de fala/silêncio para encaixar as bordas (já vem dentro do faster-whisper). |
| [alphacep/vosk-api](https://github.com/alphacep/vosk-api) | 15269 | 2026-10-15 | Apache-2.0 | ASR leve; sem pontuação, o que atrapalha achar frases. Descartado. |
| [huggingface/distil-whisper](https://github.com/huggingface/distil-whisper) | 4118 | 2025-01-08 | MIT | Modelos destilados focados em inglês; sem pt-BR oficial. Descartado. |
| [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | 5514 | 2026-11-17 | BSD-3 | Mudança de cena; sinal fraco em live (câmera fixa + jogo). Fora do MVP. |
| [qiuqiangkong/audioset_tagging_cnn](https://github.com/qiuqiangkong/audioset_tagging_cnn) (PANNs) | 1799 | 2024-07-25 | MIT | Classificador de eventos de áudio (AudioSet: Laughter, Screaming, Shout). Parado desde 2024, mas os pesos são estáveis. |
| [jrgillick/laughter-detection](https://github.com/jrgillick/laughter-detection) | 298 | 2024-07-25 | MIT | Detector de risada dedicado; parado. |
| YAMNet ([tensorflow/models](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet); export ONNX comunitário [audiomagic/yamnet-onnx](https://huggingface.co/audiomagic/yamnet-onnx)) | — | — | Apache-2.0 | 521 classes AudioSet, mono 16 kHz, janela de 0,96 s a cada 0,48 s. Fase 2. |
| [google-ai-edge/mediapipe](https://github.com/google-ai-edge/mediapipe) | 37719 | 2026-11-17 | Apache-2.0 | Detecção de rosto para achar a facecam. |
| [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) | 575 | 2024-01-17 | MIT | Segmentação por tópico (TextTiling sobre a transcrição) para achar começo e fim de assunto. **Parado desde 2024-01**; vale a ideia, não a dependência. |
| [Dorian25/ytb-most-replayed](https://github.com/Dorian25/ytb-most-replayed) | 27 | 2023-09-25 | MIT | Scraper de heatmap; desnecessário, o yt-dlp já entrega o campo. |

## Consolidação com a pesquisa do agy (Gemini Argon)

Relatório do agy: `docs/agy/github.md` na worktree `agy-argon-pesquisa`. Ele achou cinco projetos que eu não tinha encontrado. Conferi estrelas, datas e licenças pela API do GitHub (2026-11-16) e li os READMEs do sharkyclips e do chopify. Dos outros, só conferi as afirmações marcadas com ✔.

| Projeto | ★ | Criado → último push | Licença | Linguagem | O que muda no plano |
|---|---|---|---|---|---|
| [utkualsir/sharkyclips](https://github.com/utkualsir/sharkyclips) | 7 | 2026-10-24 → 2026-11-16 | MIT | Python + React | **Quase a mesma arquitetura que propus, já pronta.** CLI sem interface (`ingest → transcribe → highlights → analyze → render`) ✔. Sinais: `loudness`, `laughter` (CED-mini via sherpa-onnx, 10 MB, CPU) ✔, `speech` (fala rápida e pausa seguida de rajada) ✔, `chat` (vs mediana móvel de 2 min, spam de reação, ondas de copia-e-cola, **deslocado ~5 s para trás**) ✔. Os pesos dos sinais ausentes são renormalizados ✔. **Chat replay da Twitch, do YouTube e da Kick sem login** (endpoint público da Kick) ✔. Bordas encaixadas em palavras; o LLM (Ollama, qwen3.5 4B/9B, JSON estruturado) julga se o trecho é autocontido (×0,85 se não for) ✔. Contras: app grande (React, FastAPI, publicação, bleep de palavrão); instaladores só para Windows e macOS, mas o CI roda no Ubuntu ✔; sem pt-BR entre os 7 idiomas de UI e título ✔; projeto de 3 semanas com 7★. |
| [mehbul/chopify](https://github.com/mehbul/chopify) | 75 | 2026-06-22 → 2026-11-06 | MIT | Python | Seleção **sem LLM** por padrão ✔; LLM só para rerank opcional ✔; caminho audio-first para VODs ≥ 2 h ✔; 13 estilos de legenda ✔; CI no Linux ✔. Sem chat. Bom para comparar no spike. |
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) | 472 | 2026-07-04 → 2026-10-26 | AGPL-3.0 | TypeScript | **O LLM precisa citar a transcrição e o tempo vem de alinhar a citação de volta às palavras; nunca chuta tempo** ✔. Avaliação `quality:eval` com recall@3/@5 e erro de borda P95 ✔. Mesmo princípio do meu "LLM devolve índices de frase". |
| [Blueturboguy07/publikclip](https://github.com/Blueturboguy07/publikclip) | 72 | 2026-08-12 → 2026-11-11 | AGPL-3.0 | Python | Segundo o agy, desconta a nota de humor do LLM quando o detector de risada não confirma (não conferido). |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | 352 | 2026-07-31 → 2026-08-01 | MIT | Python | faster-whisper + Ollama + MediaPipe + libass; PR de ingestão da Twitch aberto (segundo o agy). |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 5949 | → 2026-11-16 | Unlicense | Nim | Detecção rápida de silêncio e movimento. Útil para medir, mas não necessário. |
| [ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin](https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin) | 4 | 2026-10-24 → 2026-10-28 | MIT | Python | Regras léxicas de GTA (perseguição, batida, stunt, caos de NPC). Fonte de palavras-chave. |

### Divergências entre os dois relatórios

| Tema | Eu | agy | Consolidado |
|---|---|---|---|
| Base do projeto | Pipeline próprio pequeno | sharkyclips em 1º lugar como "resolve exatamente o problema" | **Spike primeiro** (task T0): rodar a CLI do sharkyclips e do chopify em VODs reais no Linux. Se o sharkyclips der bons cortes em pt-BR, o projeto vira um **wrapper fino** em volta dele (config pt-BR + live). Senão, pipeline próprio copiando módulos MIT dele (chat, scorer, snap). Decisão do usuário (`context.md`). |
| Chat da Kick | "Nenhuma ferramenta mantida" | sharkyclips usa endpoint público da Kick | **Eu estava errado**: há caminho sem login. Kick entra com chat. |
| Chat da Twitch VOD | TwitchDownloaderCLI (binário) | GQL público direto (`VideoCommentsByOffsetOrCursor`, como sharkyclips) | Os dois usam a mesma GQL. Python direto evita o binário .NET; o binário fica como plano B. |
| chat-downloader | Quebrado só para Twitch | Quebrado para Twitch **e** YouTube (#290, #286) | Não usar. Chat do YouTube vem do yt-dlp `live_chat` (replay e live). |
| Atraso do chat | Padrão 8 s, estimado por correlação | Fixo em ~5 s (sharkyclips) | Padrão **5 s** (valor que um projeto real usa), ajustado pela correlação no limite 3–20 s. |
| Risada/grito | YAMNet (fase 2) | CED-mini via sherpa-onnx (10 MB, CPU) | **CED-mini** (já usado em CPU por um projeto real). |
| LLM pequeno | 3–4B com schema JSON | OpenShorts: "7-8B models return valid JSON reliably, 3B ones do not" ✔ | Com ~3 GB livres, 7–8B não cabe. Ficar no 4B **com decodificação restrita por schema** (sharkyclips faz isso com qwen3.5:4b) e validação + fallback. Os índices de frase (meus) e a citação reversa (hotclip) protegem igual contra timestamp alucinado. |
| ClipsAI (TextTiling) | Ideia para borda | Inútil para gameplay | Concordo: fica fora. |

## Mortos ou irrelevantes

- [ros-dorian/TwitchHack](https://github.com/ros-dorian/TwitchHack) (4★, 2019): janelas de 10 s por pico de áudio + palavras "pog/gg". Abandonado.
- [momo1478/ClipChamp](https://github.com/momo1478/ClipChamp) (1★, 2019): só gráfico de chat. Abandonado.
- [aionixOS/Openclip](https://github.com/aionixOS/Openclip) (45★, 2026-10-01, MIT): só YouTube + transcrição.

## Literatura (sinal de chat)

- Fu et al., *Video Highlight Prediction Using Audience Chat Reactions* (arXiv [1707.08559](https://arxiv.org/abs/1707.08559)): usa chat de transmissões de LoL na Twitch e rotula highlights com base em clipes da comunidade. Confirma que **clipes criados por viewers servem como ground truth**.
- LIGHTOR, *Towards Extracting Highlights From Recorded Live Videos* (arXiv [1910.12201](https://arxiv.org/abs/1910.12201)): highlights a partir de chat, com ground truth crowdsourced. Também trata do atraso do chat em relação ao vídeo (não verifiquei o número exato no texto).
- Ringer, tese de doutorado em York, 2022 ([etheses.whiterose.ac.uk/32406](https://etheses.whiterose.ac.uk/32406)): detecção multimodal (áudio, vídeo, chat) em livestream.

## Não verificado

- Não rodei nenhum projeto; qualidade real dos cortes é desconhecida.
- Estrelas de projetos com 0–12★ podem mudar rápido; dois deles (TWITCH-Generator, autoclipnew) tinham push no mesmo dia da coleta, sinal de projeto recém-criado.
- Chat replay da Kick: eu não tinha achado; o agy mostrou que o sharkyclips usa um endpoint público da Kick sem login. Ainda não testado.
