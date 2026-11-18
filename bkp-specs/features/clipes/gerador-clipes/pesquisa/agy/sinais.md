# Detecção de Picos e Ajuste de Bordas em CPU (100% Gratuito e Sem Login)

> **Data de referência:** 2026-11-16.
> **Escopo:** Pipeline 100% local em CPU (sem GPU), sem contas/logins e sem APIs pagas, voltado a lives e VODs (YouTube e Twitch) de *hype* GTA VI, reações, sustos e comédia em pt-BR.

---

## 1. Análise Detalhada por Sinal

### 1.1. Chat (YouTube Live Chat & Twitch Chat)

O chat é o sensor externo mais forte de humor, susto e *hype* coletivo, mas reage com **atraso de 3 a 8 segundos** (latência da transmissão + tempo de digitação). Por isso, toda série temporal de chat deve sofrer um **deslocamento temporal (*shift*) de `-5 s`** antes de ser combinada com o áudio ([`utkualsir/sharkyclips/backend/sharkyclips/highlights/signals/chat.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/highlights/signals/chat.py)).

- **O que medir (em *bins* de 1 s e janelas móveis de 5 s):**
  1. **Densidade relativa + Filtro de Poisson:** Taxa de mensagens em 5 s (`rate5`) dividida pela mediana móvel de 121 s (`baseline`). Para evitar falsos positivos em lives pequenas (onde passar de 0,2 msg/s para 2 msg/s daria "10x"), aplica-se um teto via *z-score* de Poisson em janela de 10 s: $z = \frac{C_{10} - E_{10}}{\sqrt{E_{10} + 1}}$, limitando o *score* a $\min(\text{clip}_{0,1}(\log_2(\text{ratio})/\log_2(6)), \text{clip}_{0,1}(z/5))$.
  2. **Teto por autor único (*anti-flood*) e filtro de bots:** Ignorar lista fixa de bots (`nightbot`, `streamelements`, `moobot`, `fossabot`, `wizebot`, `botrix`) e limitar a contribuição de um mesmo `author_id` a no máximo 1 mensagem a cada 3 s (inspirado em [`xixihhhh/hotclip`](https://github.com/xixihhhh/hotclip) e [`ColinGPT9/clips-studio`](https://github.com/ColinGPT9/clips-studio)).
  3. **Explosão de risadas, emotes e *copy-paste waves* (pt-BR + global):**
     - Normalizar caracteres repetidos (`re.sub(r"(.)\1{2,}", r"\1\1", texto.casefold())` transforma `"kkkkkkkk"` em `"kk"` e `"caaaralho"` em `"caaralho"`).
     - Regex de reação/comédia/susto pt-BR e Twitch: `r"(?:k{3,}|haha+|hehe+|rsrs+|ahuahua+|lulw?|kekw|omegalul|pog(?:gers|champ)?|omg|wtf|carai|caralho|mano+|meu deus|mds|pqp|nem fudendo|clipa|clip it|\?{3,}|!{3,})"`
     - Ondas de *copy-paste*: mesma mensagem curta ($\le 40$ chars, não numérica de 1–2 dígitos para ignorar enquetes `1`/`2`) enviada por $\ge 3$ autores distintos em $\le 5\text{ s}$.
- **Precisão esperada:** **Alta (75%–90%)** em canais médios/grandes ($\ge 30$ msgs/min); **Baixa (< 30%)** em canais pequenos (< 5 msgs/min).
- **Custo de CPU:** **Desprezível (< 1% de 1 core)** — processar 50.000 mensagens em NumPy/SciPy leva $< 0,2\text{ s}$.
- **Como obter sem login:**
  - **YouTube VOD:** `yt-dlp --skip-download --write-subs --sub-langs live_chat -o "chat.%(ext)s" <URL>` (gera `.live_chat.json` linha a linha com `replayChatItemAction.videoOffsetTimeMsec`).
  - **Twitch VOD:** Endpoint GraphQL público `https://gql.twitch.tv/gql` usando o `Client-Id` web público da própria Twitch (`kimne78kx3ncx6brgo4mv6wki5h1ko`) e a *persisted query* `VideoCommentsByOffsetOrCursor` (hash `b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a`), paginando por `contentOffsetSeconds` em até 8 threads paralelas (baixa 5 h de VOD em ~30 s; código verificado em [`sharkyclips/chat.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/chat.py)), ou via CLI [`lay295/TwitchDownloader`](https://github.com/lay295/TwitchDownloader) (`TwitchDownloaderCLI chatdownload -u <VOD_ID> -o chat.json`). *(Nota: descartar [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader) para Twitch em 2026 devido à issue [#290](https://github.com/xenova/chat-downloader/issues/290)).*
  - **Twitch LIVE (tempo real):** Conexão WebSocket IRC anônima nativa da Twitch em `wss://irc-ws.chat.twitch.tv:443` enviando `PASS schmoopiie`, `NICK justinfan12345` (qualquer sufixo numérico após `justinfan` é aceito sem conta/OAuth em modo leitura), `CAP REQ :twitch.tv/tags twitch.tv/commands` e `JOIN #<canal>`.
  - **YouTube LIVE (tempo real):** Polling do continuation token da página ao vivo via `yt-dlp --live-from-start --write-subs --sub-langs live_chat` ou biblioteca Python `pytchat` (sem API key). *[Não verificado em execução ao vivo hoje; documentado via especificação do `yt-dlp`/`pytchat`].*
- **Limitações LIVE vs VOD:** Em **VOD**, temos o histórico completo para calcular a mediana móvel centrada de 121 s imediatamente. Em **LIVE**, a mediana precisa ser causal (calculada apenas sobre os últimos 120 s já ocorridos) e o pico de chat só se confirma **5 a 8 s após o evento** (exigindo um *ring buffer* de vídeo/áudio de pelo menos 60–90 s no disco/RAM).

---

### 1.2. Áudio: RMS/Loudness, Gritos e Risadas (Classificadores Leves em CPU)

Essencial para **sustos, gritos de reação, explosões no GTA VI e risadas** do streamer, funcionando mesmo quando o chat é pequeno ou lento.

#### A) Energia Acústica (RMS dB e Salto Abrupto)
- **Como funciona:** Extrair o áudio em PCM 16 kHz mono (`ffmpeg -i input -vn -ac 1 -ar 16000 audio.wav`) e calcular o RMS em dB a cada 0,5 s (2 Hz). Para que pausas silenciosas não puxem a linha de base para baixo, mascarar frames com `db <= -55 dB` antes de calcular a mediana móvel de 60 s (`baseline`). Combinar dois sub-sinais ([`sharkyclips/signals/loudness.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/highlights/signals/loudness.py)):
  - **Excesso sobre a linha de base (60% do peso):** $\text{clip}_{0,1}((\text{db} - \text{baseline} - 3,0) / 12,0)$.
  - **Salto súbito (*jump*) sobre os últimos 3 s (40% do peso):** $\text{clip}_{0,1}((\text{db} - \text{media}_{3\text{s}} - 4,0) / 10,0)$ — captura **sustos (*jumpscares*)** onde há silêncio tenso seguido de grito.
- **Custo de CPU:** **~200x–500x mais rápido que o tempo real** em NumPy (1 hora de áudio processada em $< 5\text{ s}$).

#### B) Classificadores Leves de Eventos Sonoros (Risada, Grito, Comemoração)
Um pico de volume sozinho pode ser apenas um tiro/explosão de gameplay ou música alta. Um classificador pré-treinado no **AudioSet** (521–527 classes) separa voz humana excitada (risada/grito) de ruído de jogo:

| Modelo / Runtime | Tamanho / Parâmetros | Velocidade em CPU | Classes-Alvo (AudioSet) | Recomendação |
|---|---|---|---|---|
| **CED-mini INT8** via [`k2-fsa/sherpa-onnx`](https://github.com/k2-fsa/sherpa-onnx) | **~10 MB** (`model.int8.onnx`) | **150x–300x tempo real** (janelas de 2,0 s, sem PyTorch) | `Laughter`, `Giggle`, `Snicker`, `Belly laugh`, `Chuckle`, `Screaming`, `Shout`, `Yell`, `Whoop`, `Cheering` | **🥇 Melhor opção para CPU** (usado em `sharkyclips/signals/laughter.py`, Apache-2.0) |
| **Google YAMNet** ([TF-Hub](https://tfhub.dev/google/yamnet/1) / TFLite / ONNX) | ~14 MB FP32 / **~4 MB INT8** (3,7M params, MobileNetV1) | **50x–100x tempo real** (janelas de 0,96 s, salto de 0,48 s, 16 kHz) | Índices `13..18` (*Laughter/Giggle/Chuckle*), `11` (*Screaming*), `6` (*Shout*), `59` (*Cheering*) | **🥈 Excelente alternativa** se usar `onnxruntime` ou `tflite-runtime` |
| **PANNs** (`Cnn14` / `MobileNetV2` via [`panns-inference`](https://github.com/qiuqiangkong/panns_inference)) | `Cnn14`: ~327 MB (81M params) / `MobileNetV2`: ~18 MB | `Cnn14`: ~5x–12x tempo real / `MobileNetV2`: ~40x | 527 classes AudioSet (mAP 0,439 no `Cnn14`) | Evitar `Cnn14` em CPU puro (pesado e puxa PyTorch); preferir `CED-mini` ou `YAMNet` |

- **Precisão esperada (RMS + CED-mini/YAMNet):** **Alta (80%–88%)** para sustos e gargalhadas no microfone; **Média (60%)** quando a música de fundo ou explosões do jogo sobrepõem a voz (mitigável aplicando o classificador apenas onde o Silero VAD ou bandas de frequência vocais $300\text{ Hz}–3400\text{ Hz}$ estão ativas).
- **Como obter sem login:** Download direto dos pesos abertos (`https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/sherpa-onnx-ced-mini-audio-tagging-2024-04-19.tar.bz2` ou `tfhub.dev/google/yamnet/1`).
- **Limitações LIVE vs VOD:** Funciona **identicamente em LIVE e VOD** (latência de apenas 2 s para fechar a janela do classificador + 60 s de histórico para a mediana do RMS).

---

### 1.3. Transcrição e Dinâmica de Fala (`faster-whisper` vs `whisper.cpp` em CPU, pt-BR)

A transcrição cumpre **três papéis**: (1) detectar palavras-gatilho ("clipa isso", "GTA 6", "Rockstar", "caralho", "mentira", "olha isso"), (2) medir a **dinâmica de fala** (*words-per-second* e o padrão cômico *pause-then-burst* de 1,2–8 s de pausa seguido de fala rápida $\ge 1,3\times$ a mediana em [`sharkyclips/signals/speech.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/highlights/signals/speech.py)), e (3) fornecer **timestamps palavra a palavra** para cortar em limites naturais de frase e gerar as legendas 9:16.

- **Comparativo `faster-whisper` vs `whisper.cpp` em CPU (x86_64 Linux, quantização INT8):**

| Critério | [`SYSTRAN/faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (CTranslate2) | [`ggml-org/whisper.cpp`](https://github.com/ggml-org/whisper.cpp) (GGML) |
|---|---|---|
| **Engine em CPU x86_64** | CTranslate2 com `oneDNN` / `oneMKL` (`compute_type="int8"`) | C/C++ puro com AVX2 / OpenBLAS (`q5_0` ou `q8_0`) |
| **Velocidade em CPU (x86 6–8 cores)** | **Ligeiramente mais rápido em batch x86** (~3x–5x tempo real no `small`, ~1,2x–2x no `medium` / `large-v3-turbo` com `vad_filter=True`) | Equivalente em `q8_0`, ou **20%–35% mais rápido se usar quantização `q5_0` / `q4_k`** |
| **Uso de RAM (INT8 / Q5)** | `small`: ~1,2 GB \| `medium`: ~3,0 GB \| `large-v3-turbo`: ~2,2 GB | `small`: ~0,6 GB \| `medium`: ~1,8 GB (`q5_0`) \| `large-v3-turbo`: ~1,3 GB (`q5_0`) |
| **Qualidade em pt-BR (gírias/lives)** | `base`/`tiny` erram muita gíria; **`small` (INT8)** é o mínimo aceitável para triagem; **`large-v3-turbo` (INT8)** (809M params, 4 camadas decoder vs 32 do `large-v3`) entrega qualidade próxima ao `large-v3` com velocidade de `small`/`medium` | Mesmos pesos e qualidade; suporta prompt inicial (`--prompt "Live de GTA VI, gameplay, gírias em português do Brasil."`) |
| **Integração no Pipeline Python** | **Nativa** (`word_timestamps=True` + Silero VAD embutido via `vad_filter=True`) | Via CLI ou `pywhispercpp` |

- **Estratégia recomendada para CPU sem GPU (Abordagem em 2 Estágios):**
  - **Nunca transcrever 4 horas de VOD inteiro com modelo grande em CPU!** Uma live de 4 h levaria 2 a 3 horas de CPU.
  - **Estágio 1 (Triagem Ultrarrápida sem ASR):** Rodar apenas Chat + Áudio (RMS + CED-mini/YAMNet) + Silero VAD no VOD inteiro (leva **menos de 2 minutos** para 4 h de live em CPU) e selecionar as **Top 15–25 janelas candidatas de 90 s** ao redor dos picos.
  - **Estágio 2 (Transcrição Cirúrgica de Alta Qualidade):** Rodar `faster-whisper` com modelo **`large-v3-turbo` (`compute_type="int8"`, `language="pt"`, `word_timestamps=True`, `vad_filter=True`)** **apenas nas janelas candidatas** (20 janelas $\times$ 90 s = 30 min de áudio $\rightarrow$ processado em **~10–15 min** na CPU com qualidade de legenda quase perfeita em pt-BR!).
  - Em **modo LIVE**, manter um buffer circular e só disparar o `faster-whisper` (`small` ou `large-v3-turbo` INT8) sobre os últimos 75 s quando o gatilho de Áudio+Chat disparar.

---

### 1.4. "Most Replayed" (Heatmap) do YouTube

- **Como funciona:** O YouTube divide o vídeo em ~100 segmentos e expõe a intensidade de *replay* normalizada em `[0.0, 1.0]`.
- **Como obter sem login:**
  ```bash
  yt-dlp --skip-download --dump-json "https://www.youtube.com/watch?v=VIDEO_ID" | jq '.heatmap'
  ```
  Retorna `[{"start_time": 120.0, "end_time": 135.0, "value": 0.94}, ...]`.
- **Precisão esperada:** **Altíssima (85%–95%)** quando disponível — reflete onde milhares de espectadores reais voltaram o vídeo para rever um susto, piada ou *easter egg* de GTA VI.
- **Custo de CPU:** **Zero** (vem pronto no JSON de metadados do `yt-dlp` em ~2 s).
- **Limitações LIVE vs VOD:** **Indisponível em LIVE** e **indisponível em VODs recém-encerrados** (o YouTube leva de 24 h a 72 h e exige volume mínimo de visualizações para gerar o `heatmap`, retornando `null` antes disso). Portanto, deve ser usado como **bônus oportunista** quando presente, nunca como dependência obrigatória.

---

### 1.5. Clips de Viewers da Twitch

- **Como funciona:** Espectadores clicam em "Clip" durante a live; vários clipes sobrepostos no mesmo minuto do VOD indicam um momento comprovadamente relevante.
- **Como obter sem login:** Consultation à API GraphQL pública da Twitch (`https://gql.twitch.tv/gql`, header `Client-Id: kimne78kx3ncx6brgo4mv6wki5h1ko`) buscando os clipes do canal/broadcaster filtrados pelo `video.id` do VOD e lendo `videoOffsetSeconds`, `durationSeconds` e `viewCount` (mesma infraestrutura de [`lay295/TwitchDownloader`](https://github.com/lay295/TwitchDownloader)). *[Nota: a paginação de comentários de VOD via GQL foi verificada no código de `sharkyclips`; a query GQL de listagem de clipes por VOD não foi executada contra a Twitch hoje, 2026-11-16].*
- **Precisão esperada:** **Muito Alta (80%–90%)** para localização do evento, mas **bordas ruins** (viewers frequentemente cortam o início da piada ou deixam 10 s de sobra no final — sempre recalibrar as bordas pelo nosso *word-snapping*).
- **Custo de CPU:** **Zero** (requisição HTTP JSON leve).
- **Limitações LIVE vs VOD:** Em **VOD** consolidado, os clipes já têm `viewCount` para ranqueamento. Em **LIVE**, um clipe leva de 30 s a 2 min após o momento para ser criado e salvo pelo viewer, e `videoOffsetSeconds` pode demorar a indexar até o VOD ser fechado.

---

### 1.6. Mudança de Cena e Movimento Visual (CPU)

- **Como funciona:** Detecta cortes bruscos (mudança de câmera/tela cheia para *facecam*, explosão de tela, tela de *Wasted/Busted* no GTA, abertura de trailer do GTA VI).
- **Como obter em CPU sem pesar:**
  - **Evitar `TransNetV2` em CPU:** É uma 3D-CNN que fica lenta em CPU puro.
  - **Abordagem recomendada (inspirada em [`mehbul/chopify`](https://github.com/mehbul/chopify) e [`Breakthrough/PySceneDetect`](https://github.com/Breakthrough/PySceneDetect)):** Decodificar com `ffmpeg` a **2 fps em resolução minúscula (`160x90`, escala de cinza/HSV)** apenas nas janelas candidatas (ou usando `-skip_frame noref` para ler apenas *keyframes*), medindo diferença de histograma (`ContentDetector` do `PySceneDetect` ou filtro nativo `ffmpeg -vf "select='gt(scene,0.35)',metadata=print"`).
- **Precisão esperada:** **Baixa isoladamente (20%–30%)** — um corte de cena sozinho não significa que o momento é engraçado ou *hype*, mas **Alta (85%+)** para **alinhar cortes visuais** e confirmar que houve reação/mudança de tela.
- **Custo de CPU:** Em `160x90` @ 2 fps, roda a **~80x–150x tempo real** em CPU.
- **Limitações LIVE vs VOD:** Funciona igual em LIVE e VOD.

---

## 2. Como Combinar os Sinais (Pontuação Ponderada + Bônus de Concordância)

Em vez de depender de um único sinal, discretizamos a live/VOD em **segundos ($t = 0, 1, \dots, N$)**, onde cada sinal ativo $s \in \mathcal{S}_{\text{ativos}}$ produz uma curva normalizada $S_s(t) \in [0, 1]$.

### 2.1. Pesos Base Recomendados (Foco: GTA VI, Reações, Sustos e Comédia)

Se um sinal opcional não existir na fonte (ex.: YouTube sem `heatmap`, ou live sem chat suficiente), ele é ignorado e os pesos restantes são renormalizados ($\hat{w}_s = \frac{w_s}{\sum_{k \in \mathcal{S}_{\text{ativos}}} w_k}$):

| Sinal ($s$) | Peso Base ($w_s$) | Papel Principal no Caso de Uso (GTA VI / Reações / Sustos / Comédia) |
|---|---:|---|
| **`chat`** (densidade + risadas/emotes em `-5 s`) | **0,30** | Validação social imediata de piadas (`KKKK`, `LUL`), sustos e *hype* |
| **`audio_events`** (`CED-mini` / `YAMNet`: risada + grito) | **0,25** | Captura gargalhadas do streamer e gritos de susto/reação mesmo sem chat |
| **`loudness`** (excesso RMS 60s + salto de 3s) | **0,20** | Captura *jumpscares*, explosões e picos vocais súbitos |
| **`speech_keywords`** (WPS, *pause-then-burst* + termos GTA/reação) | **0,15** | Captura *setup → punchline* e menções diretas ("Rockstar", "trailer", "clipa") |
| **`external_crowd`** (`Most Replayed` YT / Clips Twitch) | **0,10** | Bônus forte quando disponível em VODs maduros |

*(Nota: **`scene_change`** não entra na soma para não inflar gameplays frenéticos; é usado no pós-processamento de bordas e como desempate visual).*

### 2.2. Fórmula de Fusão com Bônus de Ressonância Multimodal

Adotando a validação cruzada de [`sharkyclips/scorer.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/highlights/scorer.py) e [`hotclip`](https://github.com/xixihhhh/hotclip): um pico isolado de volume pode ser só um tiro no jogo, e um pico isolado de chat pode ser uma enquete; mas **quando $\ge 2$ sinais independentes disparam juntos ($S_s(t) \ge 0,5$), o momento é quase certamente real**:

$$C(t) = \left( \sum_{s \in \mathcal{S}_{\text{ativos}}} \hat{w}_s \cdot S_s(t) \right) \times \Big( 1 + 0,15 \cdot \max\big(0,\; A(t) - 1\big) \Big)$$

onde $A(t) = \sum_{s: \hat{w}_s \ge 0,08} \mathbb{I}[S_s(t) \ge 0,5]$ é o número de sinais relevantes concordando no segundo $t$. Em seguida:
1. Suavizar $C(t)$ com filtro Gaussiano 1D ($\sigma = 2,0\text{ s}$): $H(t) = \text{GaussianFilter}_{1\text{D}}(\min(1, C(t)), \sigma=2)$.
2. Extrair picos locais via `scipy.signal.find_peaks(H, height=mediana + 0.1*(max-mediana), distance=15, prominence=0.01)` e aplicar supressão não-máxima (NMS) para que nenhum clipe se sobreponha.

---

## 3. Como Ajustar as Bordas para Ter SENTIDO (Início/Fim Naturais de 20–60 s)

O maior erro de clipadores automáticos é cortar exatamente no segundo do grito/risada (perdendo o *setup* que explica **por que** aquilo aconteceu) ou truncar palavras ao meio. Para garantir clipes de **20 a 60 s** com contexto completo:

### Passo 1: Janela Assimétrica de *Setup → Clímax → Reação*
Dado um pico em $t_{\text{peak}}$ e a largura do pulso onde $H(t) \ge 0,5 \cdot H(t_{\text{peak}})$ (intervalo $[t_{\text{left}}, t_{\text{right}}]$):
- **Pre-roll obrigatório (Contexto/Setup):** Recuar o início bruto para $t_{\text{start\_raw}} = \min(t_{\text{left}} - 4\text{ s},\; t_{\text{peak}} - 12\text{ s})$. Em reações, sustos e comédia, os **8 a 15 segundos anteriores ao pico** contêm a frase ou ação que prepara a piada/susto.
- **Post-roll (Resolução/Risada):** Avançar o fim bruto para $t_{\text{end\_raw}} = t_{\text{right}} + 4\text{ s}$, garantindo que a reação/risada termine antes do corte.
- Se $t_{\text{end\_raw}} - t_{\text{start\_raw}} < 20\text{ s}$, expandir 35% para trás e 65% para frente até atingir pelo menos 24 s.

### Passo 2: Configuração Correta do Silero VAD + `faster-whisper`
Para que o Whisper não quebre frases ao meio em micro-pausas de respiração, configurar o `vad_options` do `faster-whisper`:
- `min_silence_duration_ms = 600` (padrão é 100–300 ms; subir para 600 ms impede cortes no meio de orações).
- `speech_pad_ms = 400` (protege ataques e finais de fonemas).
- `word_timestamps = True` (obrigatório para obter `word.start`, `word.end` e pontuação por palavra).

### Passo 3: Algoritmo Determinístico de *Word & Silence Snapping*
Adaptado diretamente de [`sharkyclips/highlights/candidates.py`](https://github.com/utkualsir/sharkyclips/blob/HEAD/backend/sharkyclips/highlights/candidates.py#L131-L180):

1. **Ajuste do Início (`_snap_start`):**
   Buscar todas as palavras $w_i$ iniciando na janela $[t_{\text{start\_raw}} - 6\text{ s},\; \min(t_{\text{start\_raw}} + 3\text{ s},\; t_{\text{peak}} - 6\text{ s})]$ e escolher o índice $i$ que maximiza a função de qualidade de borda:
   $$Q_{\text{start}}(i) = 2,0 \cdot \mathbb{I}[w_{i-1} \text{ termina em } \text{.?!}] + \begin{cases} 1,5 & \text{se } \text{gap}_{i-1,i} \ge 0,8\text{ s} \\ 0,7 & \text{se } 0,3\text{ s} \le \text{gap}_{i-1,i} < 0,8\text{ s} \\ 0,0 & \text{caso contrário} \end{cases} - 1,5 \cdot \frac{|w_i.\text{start} - t_{\text{start\_raw}}|}{9,0}$$
   Definir o corte inicial com margem de proteção de fonema de 250 ms:
   $$t_{\text{start}} = \max\big(0,\; w_{i^*}.\text{start} - 0,25\text{ s},\; w_{i^*-1}.\text{end} + 0,02\text{ s}\big)$$

2. **Ajuste do Fim (`_snap_end`):**
   Buscar palavras $w_j$ terminando em $[\max(t_{\text{end\_raw}} - 3\text{ s},\; t_{\text{peak}} + 3\text{ s}),\; t_{\text{end\_raw}} + 6\text{ s}]$ respeitando $20\text{ s} \le t_{\text{end}} - t_{\text{start}} \le 60\text{ s}$, maximizando:
   $$Q_{\text{end}}(j) = 2,0 \cdot \mathbb{I}[w_j \text{ termina em } \text{.?!}] + \begin{cases} 1,5 & \text{se } \text{gap}_{j,j+1} \ge 0,8\text{ s} \\ 0,7 & \text{se } 0,3\text{ s} \le \text{gap}_{j,j+1} < 0,8\text{ s} \\ 0,0 & \text{caso contrário} \end{cases} - 1,5 \cdot \frac{|w_j.\text{end} - t_{\text{end\_raw}}|}{9,0}$$
   **Regra de proteção de risada pós-fala:** Se o classificador `CED-mini`/`YAMNet` detectar risada ativa (`score >= 0.5`) logo após $w_{j^*}.\text{end}$, estender $t_{\text{end}}$ até o fim do bloco de risada $+ 0,35\text{ s}$ (desde que não ultrapasse 60 s nem invada a primeira palavra da próxima frase). Caso contrário:
   $$t_{\text{end}} = \min\big(\text{duração},\; w_{j^*}.\text{end} + 0,35\text{ s},\; w_{j^*+1}.\text{start} - 0,02\text{ s}\big)$$
