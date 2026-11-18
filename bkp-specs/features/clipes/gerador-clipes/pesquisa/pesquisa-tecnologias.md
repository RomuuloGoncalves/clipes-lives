# Pesquisa: tecnologias (CPU-only, grátis)

Data: 2026-11-16. Máquina-alvo medida neste worktree: **AMD Ryzen 5 5500U (6 núcleos/12 threads, notebook), ~9 GB de RAM total (~3 GB livres com o desktop aberto), sem GPU dedicada.** `ffmpeg`, `yt-dlp` e `streamlink` ainda não estão instalados.

Tudo que é "estimado" abaixo **não foi medido nesta máquina**. A primeira tarefa do plano é um benchmark que troca essas estimativas por números reais.

## 1. Captura

### yt-dlp vs streamlink

| | yt-dlp (208k★, Unlicense, release 2026.10.26) | streamlink (11.9k★, BSD-2, 8.6.3 de 2026-10-29) |
|---|---|---|
| VOD YouTube/Twitch/Kick | Sim | Não é o foco |
| Live | Sim; `--live-from-start` no YouTube (experimental) | Sim; é o objetivo da ferramenta (HLS, troca de qualidade, anúncios da Twitch) |
| Baixar só um trecho | `--download-sections "*10:15-11:00"` | Não |
| Só áudio | `-f bestaudio` | Possível com `--stream-segment...`/qualidade `audio_only` na Twitch |
| Metadados | Duração, título, capítulos, **`heatmap`** (Most Replayed) | Mínimos |
| Chat | YouTube: replay como legenda `live_chat` (`--write-subs --sub-langs live_chat`) | Não |

**Decisão:** yt-dlp para VOD (metadados + heatmap + áudio + só os trechos escolhidos). streamlink para live (é o que melhor aguenta horas de HLS). Os dois são CLIs chamados via `subprocess`; não precisa de wrapper.

Fontes: README do yt-dlp (opções `--live-from-start`, `--download-sections`, `live_chat`), `yt_dlp/extractor/youtube/_video.py` (testes com `'heatmap': 'count:100'`), `yt_dlp/extractor/kick.py` (`KickIE`, `KickVODIE`, `KickClipIE`).

### Estratégia de download (VOD)

Não baixar o vídeo inteiro primeiro. Ideia vista em autoclipnew e vod-to-viral:

1. `yt-dlp -j URL`: metadados + heatmap (segundos, sem download).
2. Áudio inteiro (`-f bestaudio`, ~60–100 MB/h) e chat inteiro.
3. Detectar candidatos só com áudio + chat + heatmap + transcrição.
4. `yt-dlp --download-sections` só dos N trechos finais em 1080p, com folga de 5 s.

### Live em andamento

Duas formas:

- **(a) Buffer rolante com segmentos HLS** (streamlink → ffmpeg `-f segment -segment_time 30`), análise incremental a cada segmento.
- **(b) Gravar em disco e rodar o pipeline de VOD numa janela deslizante** (ex.: a cada 5 min, analisa os últimos 10 min com sobreposição).

**Decisão: (b).** Reaproveita 100% do código de VOD, e o atraso de ~5 min é aceitável porque não há publicação automática. (a) só se o usuário pedir clipe em menos de 1 min.

Ressalvas: no YouTube, o heatmap só aparece depois da live, quando a VOD já tem visualizações. Na Twitch, a VOD fica disponível logo após o fim (se o canal salva VODs). Nesses casos, o modo VOD já cobre "live já finalizada".

### Chat replay

| Plataforma | VOD (replay) | Live | Login |
|---|---|---|---|
| YouTube | yt-dlp `live_chat` (JSON lines com `videoOffsetTimeMsec`) | yt-dlp `live_chat` durante a live (chat-downloader está quebrado, ver aviso) | Não |
| Twitch | GQL pública `VideoCommentsByOffsetOrCursor` direto em Python (como o sharkyclips, MIT); plano B: **TwitchDownloaderCLI** `chatdownload` (MIT, 4k★, binário Linux, mesma GQL) | IRC anônimo via `wss://irc-ws.chat.twitch.tv:443` com nick `justinfan<n>` (só leitura) | Não |
| Kick | Endpoint público de chat da Kick, sem login (usado pelo sharkyclips; ~5 s de chat por chamada, paralelizável). Não testado. | Não verificado | Não |

Avisos:
- chat-downloader está **quebrado para Twitch** (issue #283, aberta desde 2025-11-11) e, segundo o agy, também para YouTube (#290, #286); o último push foi em 2025-11-05. **Não usar.**
- O login anônimo `justinfan` aparece só em fóruns da comunidade ([discuss.dev.twitch.com](https://discuss.dev.twitch.com/t/anonymous-connection-to-twitch-chat/20392)). A doc oficial pede token com `chat:read`. Funciona hoje segundo libs ativas (twitch-irc em Rust), mas precisa de teste. Plano B: token de app gratuito da Twitch (exige conta, não cartão).
- Kick: o chat entra pelo endpoint público, se o spike confirmar; senão segue sem chat.

### Clipes criados por viewers (Twitch)

São o melhor ground truth disponível: alguém achou o momento bom o bastante para clipar. A API Helix `Get Clips` pede `client-id` (conta de dev gratuita). A GQL pública (usada pelo TwitchDownloader) não exige conta, mas não é documentada. **Não verificado** se dá para obter o `vod_offset` de cada clipe sem conta. Fica para a fase de avaliação.

## 2. Transcrição

### Números publicados (faster-whisper README, modelo `small`, 13 min de áudio, i7-12700K com 8 threads)

| Implementação | Precisão | Tempo | RAM |
|---|---|---|---|
| openai/whisper | fp32 | 6m58s | 2335 MB |
| whisper.cpp | fp32 | 2m05s | 1049 MB |
| whisper.cpp (OpenVINO) | fp32 | 1m45s | 1642 MB |
| faster-whisper | fp32 | 2m37s | 2257 MB |
| faster-whisper | int8 | 1m42s | 1477 MB |
| faster-whisper `batch_size=8` | int8 | 51s | 3608 MB |

Proporção entre tamanhos em CPU (ailia SDK, Mac M2, 40 s de áudio): small 9,4 s; large-v3-turbo 18,4 s; medium 29,3 s. Ou seja, turbo ≈ 2× small e medium ≈ 3× small. O turbo é mais barato que o medium porque mantém o encoder do large-v3 e reduz o decoder de 32 para 4 camadas.

Qualidade em pt-BR ([tech4humans, Audio-Transcription-Models-Comparison-PT-BR](https://huggingface.co/datasets/tech4humans/Audio-Transcription-Models-Comparison-PT-BR)): Whisper large-v3-turbo tem o melhor WER (0,067), à frente do large-v3 (0,079). Não achei WER de `small`/`medium` no mesmo dataset.

### Estimativa para 1 h de VOD no Ryzen 5 5500U (estimada, não medida)

Suposição: o 5500U (notebook de 15 W, Zen 2) é de **2× a 3×** mais lento que o i7-12700K com 8 threads.

| Modelo (int8) | RTF no 12700K | 1 h no 5500U, sem batch | 1 h no 5500U, `batch_size=4–8` |
|---|---|---|---|
| small | 0,13 | 16–24 min | 8–12 min |
| large-v3-turbo | ~0,26 | 30–50 min | 15–25 min |
| medium | ~0,40 | 50–75 min | 25–40 min |

RAM: `batch_size=8` com small usou 3,6 GB no benchmark. Com ~3 GB livres, começar com `batch_size=4` e medir.

### Comparação

| Opção | Prós | Contras | Veredito |
|---|---|---|---|
| **faster-whisper** (26.8k★, MIT) | int8 + batch é o mais rápido em CPU; `word_timestamps=True`; VAD Silero embutido (`vad_filter=True`); API Python | Depende de CTranslate2 | **Escolhido** |
| whisper.cpp (55k★, MIT) | Bom em fp32; quantização GGML; binário leve | Integração por subprocess + parse de JSON; batch menos maduro | Plano B se o faster-whisper ficar lento no benchmark |
| distil-whisper | 6× mais rápido | Modelos oficiais focados em inglês | Descartado (pt-BR) |
| Vosk | Muito leve, streaming | Sem pontuação nem maiúsculas, o que destrói a detecção de frases (requisito central) | Descartado |
| whisperX | Alinhamento de palavra melhor | wav2vec2 + pyannote, pesado em CPU e RAM | Desnecessário: o word-timestamp do faster-whisper basta para bordas de ~0,2 s |

**Decisão (a confirmar no benchmark):** um passe inteiro com `small` int8 (timestamps de palavra, base dos sinais de texto e das bordas) e um re-transcrever **só os clipes finais** com `large-v3-turbo` para a legenda (N × 60 s ≈ 2–3 min de CPU). Se o benchmark mostrar turbo ≤ 25 min/h, usar só o turbo e simplificar.

## 3. Sinais de pico

Princípio, tirado do TWITCH-Generator e do Clips Kitty: **cada sinal é normalizado contra a linha de base da própria live** (z-score robusto numa janela móvel de ±2,5 min). Live barulhenta ou chat sempre rápido não vira "tudo é pico". Nenhum sinal sozinho decide; candidatos com sinais independentes concordando ganham bônus.

| Sinal | Como medir | Custo CPU | Disponibilidade | Força / fraqueza |
|---|---|---|---|---|
| **Chat: velocidade** | msgs/s em bins de 1 s | ~0 | YouTube, Twitch | Forte. Spam de bot infla: usar **chatters únicos/s** (ideia do Clips Kitty). |
| **Chat: reação** | fração de mensagens com risada (`kkkk+`, `KEKW`, `LUL`, `OMEGALUL`, `😂`), espanto (`??+`, `WTF`, `MDS`, `caralho`), `clip`/`CLIPA` | ~0 | YouTube, Twitch | Distingue tipo (comédia vs susto) → ajuda o título. |
| **Chat: atraso** | O chat reage **depois** do vídeo (delay do stream + tempo de digitar). Estimar por correlação cruzada entre loudness e chat em cada VOD; padrão 5 s (valor usado pelo sharkyclips), limitado a 3–20 s. | ~0 | — | Sem isso, o pico do chat aponta para depois do momento. O valor de 5 s vem de um projeto real; medir mesmo assim. |
| **Áudio: loudness** | `ffmpeg -af ebur128` (loudness momentânea, 400 ms) ou RMS por bin de 1 s; z-score contra linha de base | ~1 min/h | Sempre | Grito, explosão, risada alta. Música/jogo alto gera falso positivo → baseline móvel. |
| **Áudio: padrão silêncio→explosão** | queda < baseline seguida de pico em < 3 s | ~0 | Sempre | Ótimo para susto (jumpscare). |
| **Evento de áudio (risada/grito)** | **CED-mini INT8 via sherpa-onnx** (10 MB, AudioSet, CPU; usado pelo sharkyclips). Alternativas: YAMNet ONNX ou PANNs | Não medido; modelo de 10 MB, deve ser ≪ Whisper | Sempre | Diferencia grito de explosão do jogo. **Fase 2**: medir se melhora o recall antes de adotar. |
| **Heatmap "Most Replayed"** | campo `heatmap` do yt-dlp (~100 pontos com `start_time`, `end_time`, `value` 0–1) | ~0 | Só YouTube, só VOD com audiência suficiente | Sinal humano direto, mas grosso (1% da duração = 36 s numa live de 1 h). |
| **Clipes de viewers (Twitch)** | offset de cada clipe na VOD | ~0 | Twitch | Melhor ground truth. Usar **na avaliação**, não como sinal, para não vazar. |
| **Palavras-chave (texto)** | na transcrição: "GTA 6/VI", "trailer", "Rockstar", "Vice City", "Lucia", "Jason", "data", "adiou", "não acredito", "meu deus", "olha isso" | ~0 após Whisper | Sempre | Bom para o tema GTA VI; lista editável em config. |
| **Mudança de cena** | PySceneDetect | médio (decodifica vídeo) | Sempre | Fraco em live (câmera fixa). **Fora do MVP.** |

## 4. Curadoria com LLM

O LLM **não procura picos** (os sinais fazem isso). Ele só (1) escolhe as bordas entre frases já numeradas, (2) descarta candidatos sem sentido e (3) sugere título. Isso mantém a chamada pequena (~1,5k tokens de entrada por candidato) e limita a alucinação.

| Opção | Custo | Limite | Privacidade | Veredito |
|---|---|---|---|---|
| **Ollama local** (187k★, MIT), modelo 4B (qwen3.5:4b como o sharkyclips, ou gemma3:4b). Atenção: o README do OpenShorts diz "7-8B models return valid JSON reliably, 3B ones do not"; 8B não cabe nos ~3 GB livres, então usar 4B **com schema** e validação | 0 | Velocidade: sem benchmark para o 5500U; fontes indicam ~10–20 tok/s em CPU para 4B e ~10 tok/s para Llama 3.2 3B ([itsfoss](https://itsfoss.com/testing-local-llms-without-gpu), [insiderllm](https://insiderllm.com/guides/cpu-only-llms-what-actually-works/)). ~15 candidatos ≈ 5–10 min (estimado). RAM: ~2,5–3 GB. | Local | **Padrão.** Saída com `format=<JSON schema>` (decodificação restrita, [docs.ollama.com/capabilities/structured-outputs](https://docs.ollama.com/capabilities/structured-outputs)). |
| Gemini free tier (Flash-Lite) | 0, sem cartão | Fontes de out–nov/2026 dizem **500 req/dia, 15 req/min** para Flash-Lite e 20/dia para Flash (blogs; os números de abr/2026 eram 1.500/dia). Conferir no AI Studio. Os dados podem ser usados para treino no free tier. | Remoto | Opcional, mais rápido e melhor em pt-BR. |
| Groq free tier | 0, sem cartão (segundo blogs) | Números conflitantes: 250 a 14.400 req/dia e 6k–30k tokens/min por modelo. Conferir no console. | Remoto | Opcional. |

Como evitar alucinação:
1. A entrada é uma lista numerada de frases com início/fim em segundos e marcadores de eventos (`[CHAT x6 risada]`, `[ÁUDIO pico]`).
2. A saída tem schema fixo: `{keep: bool, start_idx: int, end_idx: int, title: str, reason: str}`. **Nunca um timestamp livre.**
3. O código valida: índices dentro da janela, duração 20–60 s, pico contido no trecho. Se falhar, usa o corte heurístico.
4. Temperatura 0. Título ≤ 70 caracteres, só palavras que aparecem na transcrição ou no tema GTA VI (sem inventar fato).
5. Ranking final em **uma chamada comparativa** com os K melhores lado a lado (ideia do TWITCH-Generator), em vez de notas isoladas.

RAM: Whisper e Ollama **não podem rodar ao mesmo tempo** com ~3 GB livres. Execução sequencial; descarregar o modelo com `keep_alive=0`.

## 5. Bordas com sentido

Este é o requisito central. Regra: **o pico é o payoff; o clipe precisa conter o setup.**

1. Janela de contexto em volta do pico `t_p`: `[t_p − 90 s, t_p + 30 s]`.
2. Segmentar a transcrição em frases (pontuação do Whisper + pausas de VAD ≥ 0,5 s).
3. **Início**: começo de frase entre `t_p − 45 s` e `t_p − 5 s`, preferindo pausa longa antes (mudança de assunto) e não começar com conectivo que dependa de antes ("e aí", "por isso", "então ele").
4. **Fim**: fim de frase depois que o áudio volta perto da baseline após o pico (a reação terminou), mínimo `t_p + 3 s`.
5. Encaixar no silêncio mais próximo (≤ 0,4 s de deslocamento). Pre-roll de 0,3 s e pós-roll de 0,8 s, sem cortar palavra.
6. Duração-alvo 20–60 s. Se passar de 60 s, o LLM escolhe outro início; se não couber, descarta (melhor nenhum clipe que um sem sentido).
7. Dois candidatos com sobreposição > 30%: fica o de maior score (NMS).

Referência de método: ClipsAI usa segmentação por tópico (TextTiling) para início/fim de assunto. Fica como alternativa se a heurística de pausa não bastar.

## 6. Vídeo

**FFmpeg direto** (subprocess, um comando por clipe). MoviePy e afins só adicionam uma camada e são mais lentos.

- Layouts 9:16 (1080×1920):
  - `split`: facecam em cima (~1/3) e jogo embaixo (~2/3), comum em live de GTA/reação.
  - `center`: crop central (só câmera ou só jogo).
  - `blur`: vídeo 16:9 inteiro no meio, com fundo borrado do próprio vídeo.
- **Facecam**: costuma ser um overlay fixo. Basta detectar uma vez por VOD: amostrar ~30 frames, rodar o detector de rosto (MediaPipe ou o Haar cascade do OpenCV, que já vem com o `opencv-python-headless`) e tirar a mediana da caixa. Override manual em config (como o `VERTICAL_FACE_X/Y/W` do vod-to-viral). Sem rastreamento por frame no MVP.
- **Legendas**: ASS gerado pelo código, word-level (palavra atual destacada, 2–4 palavras por linha, fonte grande com contorno, dentro da safe area do Shorts/TikTok), queimado com o filtro `subtitles=` (libass).
- Zoom sutil: `zoompan`/`scale` com expressão no pico. Fora do MVP (custo de render e risco de ficar feio).
- Encode: `libx264 -preset veryfast -crf 21`, AAC 160k, `loudnorm` no áudio. Estimado 30–90 s de CPU por clipe de 45 s em 1080×1920 no 5500U (não medido).

## 7. Painel de revisão

React não se justifica para um usuário local. Opções:

| Opção | Custo | Veredito |
|---|---|---|
| Nenhum: pasta com `clips/*.mp4` + `clips.json` | 0 | **MVP** |
| `index.html` estático gerado (lista de `<video>` com título, score e motivo) | ~40 linhas | **MVP**, abre direto no navegador |
| Gradio | 1 dependência, ~100 linhas | Fase final, só se precisar de botões (aprovar/ajustar borda/re-render) |
| Streamlit | idem | Igual ao Gradio; Gradio tem componente de vídeo mais direto |
| React + API | alto | Descartado |

## Fontes

- faster-whisper README (benchmark): https://github.com/SYSTRAN/faster-whisper
- yt-dlp README e extratores: https://github.com/yt-dlp/yt-dlp
- Proporção de tamanhos do Whisper em CPU (ailia, M2): https://medium.com/axinc-ai/whisper-large-v3-turbo-high-accuracy-and-fast-speech-recognition-model-be2f6af77bdc
- WER pt-BR: https://huggingface.co/datasets/tech4humans/Audio-Transcription-Models-Comparison-PT-BR
- YAMNet: https://www.tensorflow.org/tutorials/audio/transfer_learning_audio ; ONNX: https://huggingface.co/audiomagic/yamnet-onnx
- Gemini free tier (secundárias): https://www.aifreeapi.com/en/posts/google-gemini-api-free-tier , https://imisofts.com/blog/gemini-api-free-tier-limits/
- Groq free tier (secundárias): https://openrouter.ai/blog/tutorials/free-llm-apis-compared/ , https://benchlm.ai/free-tier/groq
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- LLM em CPU: https://itsfoss.com/testing-local-llms-without-gpu , https://insiderllm.com/guides/cpu-only-llms-what-actually-works/
- Twitch IRC anônimo: https://discuss.dev.twitch.com/t/anonymous-connection-to-twitch-chat/20392 ; doc oficial: https://dev.twitch.tv/docs/irc/authenticate-bot
- TwitchDownloader: https://github.com/lay295/TwitchDownloader ; chat-downloader issue #283: https://github.com/xenova/chat-downloader/issues/283

## Não verificado (registrar no benchmark)

- Velocidade real do faster-whisper e do Ollama no 5500U.
- Se o `justinfan` ainda funciona em nov/2026.
- Atraso real do chat em relação ao vídeo.
- Limites atuais do Gemini e do Groq (só fontes secundárias).
- Ganho real do YAMNet sobre loudness simples.

## Consolidação com a pesquisa do agy (sinais e avaliação)

Relatórios do agy: `docs/agy/sinais.md`, `docs/agy/avaliacao.md` e `docs/agy/resumo.md` na worktree `agy-argon-pesquisa`. As velocidades que ele cita (CED-mini 150–300× tempo real, YAMNet 50–100×, RMS < 5 s/h) **não foram medidas** por nenhum de nós; vão para o benchmark T4.

| Tema | Eu | agy | Consolidado |
|---|---|---|---|
| Transcrição | Passe inteiro com `small` (base das palavras-chave e das bordas) | Estágio 1 sem ASR; transcrever só janelas de 90 s nos top 15–25 picos | **Decidir pelo benchmark T4.** Se o `small` fizer 1 h em ≤ 15 min, passe inteiro, porque o hype de GTA VI pode ser só fala, sem grito nem chat (ex.: "olha a física da água no trailer"). Senão, modo cirúrgico do agy. As duas usam a mesma `transcribe(start, end)`. |
| Papel do LLM | Refina bordas (índices de frase) + título, com fallback | Fora do caminho crítico; só título/gancho, ou citação com alinhamento reverso | **Bordas determinísticas por padrão.** O LLM dá título e veredito "autocontido" (×0,85 se não, como o sharkyclips). Refinar bordas pelo LLM fica atrás de flag e só é ligado se o `eval` mostrar ganho. |
| Setup antes do pico | Início entre t_p−45 e t_p−5 s | 6–15 s de setup; pico entre 35% e 75% do clipe | **Mínimo de 6 s de setup**; `PeakPositionRatio` vira métrica. |
| Chat | Chatters únicos/s, tokens de reação | + filtro de Poisson para chat pequeno, teto por autor, **lista de bots** (`nightbot`, `streamelements`, `moobot`, `fossabot`, `wizebot`, `botrix`), mediana **causal** na live | Adotar os três. |
| Loudness | EBU R128 + silêncio→explosão | RMS dB 2 Hz contra mediana de 60 s, ignorando silêncio < −55 dB, + salto sobre os 3 s anteriores | Equivalentes; usar o **salto sobre os 3 s anteriores** (pega jumpscare sem depender de silêncio antes). |
| Pesos iniciais | chat_unicos 1,0, reação 0,8, heatmap 0,8, loudness 0,7, silêncio→explosão 0,5, keywords 0,4 | chat 0,30, eventos vocais 0,25, loudness 0,20, externo 0,10 (+ fala) | São só pontos de partida; a calibração (T30) decide. O agy dá menos peso ao heatmap; concordo para o **score**, porque ele é grosso. |
| Ground truth | Clipes de viewers + heatmap | + timestamps `MM:SS` em comentários do YouTube com ≥ 5 curtidas; agrupar clipes da Twitch a < 30 s | Adotar os dois. |
| Métricas | Recall@N, precisão@N, checklist com nota manual | Recall@5/@10, **MidWordCutRate = 0%**, **SentenceEdgeRate ≥ 85%**, **PeakPositionRatio 0,35–0,75**, Precision@5 humana; checklist G1–G5 com G1 e G3 eliminatórios | **Adotar as do agy** (mais objetivas) + recall/precisão. |
| Comparar configs | `clipes eval` reroda score/bounds sem render | Harness sobre `signals.npz` + `words.json` cacheados, < 1 s por config, diff de texto dos candidatos que mudaram | Igual; adotar o **diff de texto**. |
| Live | Gravar e rodar o pipeline de VOD a cada 5 min | Ring buffer de 90 s com streamlink | **Manter a janela deslizante** (um código só, sem publicação automática). O ring buffer só se o usuário pedir clipe em < 1 min. |
