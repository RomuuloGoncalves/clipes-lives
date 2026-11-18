# Como Medir se um Clipe Tem Sentido e é Bom (Avaliação em CPU)

> **Data de referência:** 2026-11-16.
> **Objetivo:** Avaliar objetivamente a qualidade narrativa (início/fim naturais, contexto completo) e a relevância dos clipes gerados (20–60 s), permitindo comparar configurações em segundos na CPU sem reprocessar vídeo ou áudio.

---

## 1. Ground Truth Parcial (Fontes Gratuitas e Sem Rotulagem Manual)

Como não existe um dataset rotulado perfeito para cada live de GTA VI ou reação, combinamos três fontes gratuitas de **consenso de audiência** como *ground truth* parcial:

1. **YouTube "Most Replayed" (`heatmap` via `yt-dlp`):**
   - Extrair o vetor `heatmap` (`yt-dlp --skip-download --dump-json <URL> | jq '.heatmap'`).
   - Considerar como *regiões de pico real* ($G_{\text{YT}}$) os máximos locais onde `value >= 0.70` (ou o percentil 90 da live).
2. **Clips de Viewers da Twitch (Agrupados por Densidade):**
   - Coletar os clipes públicos associados ao `video_id` do VOD (via [`lay295/TwitchDownloader`](https://github.com/lay295/TwitchDownloader) ou GraphQL público da Twitch).
   - Filtrar clipes com `view_count >= 20` (ou os Top 15 mais vistos do VOD) e fundir clipes cujos `video_offset_seconds` distem menos de 30 s entre si. Quanto mais viewers diferentes cliparam o mesmo minuto, maior o peso daquele evento no *ground truth* ($G_{\text{Twitch}}$).
3. **Timestamps de Comentários e Capítulos:**
   - Extrair comentários do VOD no YouTube (`yt-dlp --write-comments --skip-download`) que contenham marcações `MM:SS` ou `HH:MM:SS` com $\ge 5$ curtidas.

> **⚠️ Por que o Ground Truth é "Parcial" (e como lidar com isso):**
> 1. **Incompletude:** Nosso detector pode achar uma piada excelente que nenhum viewer clipou na hora (falso negativo do *ground truth*).
> 2. **Bordas ruins dos viewers:** Clipes da Twitch têm duração padrão de 30 s até o momento do clique e frequentemente cortam o *setup* da piada ou palavras ao meio.
> **Consequência prática:** Usamos $G_{\text{YT}} \cup G_{\text{Twitch}}$ **apenas para medir a localização do evento (`Recall@K`)**, e usamos **métricas estruturais + checklist objetivo** para medir se as bordas têm sentido.

---

## 2. Checklist Objetivo de Qualidade e Sentido (5 Portões Binários)

Para avaliar um clipe candidato sem subjetividade vaga ("achei legal"), aplicamos 5 portões objetivos (0 ou 1 ponto cada):

- **[ ] G1 — Zero Corte *Mid-Word* (Integridade Fonética — ELIMINATÓRIO):**
  O início ($t_{\text{start}}$) e o fim ($t_{\text{end}}$) caem estritamente fora de qualquer palavra pronunciada (`word.start <= t <= word.end`) e mantêm margem mínima de $\ge 150\text{ ms}$ de silêncio ou pausa antes da primeira sílaba e após a última sílaba.
- **[ ] G2 — Início Contextualizado (*Setup* Completo):**
  A primeira frase do clipe começa após pontuação forte anterior (`.?!`) ou pausa vocal $\ge 0,8\text{ s}$, não começa com conectivos órfãos de meio de frase (*"mas então..."*, *"porque tipo assim..."*, *"como eu falei..."*) e inclui **entre 6 s e 15 s de preparação (*setup*)** antes do instante do pico ($t_{\text{peak}}$).
- **[ ] G3 — Clímax Contido e Corroborado (*Payoff* — ELIMINATÓRIO):**
  O pico do evento ($t_{\text{peak}}$: grito de susto, explosão de risada ou pico de chat) está totalmente dentro do clipe com margem de segurança ($t_{\text{start}} + 5\text{ s} \le t_{\text{peak}} \le t_{\text{end}} - 3\text{ s}$) e teve concordância de pelo menos 2 sinais independentes (ou 1 sinal com *score* $\ge 0,85$).
- **[ ] G4 — Fechamento Natural (Resolução Sem Assunto Novo):**
  O corte final ocorre após a conclusão da frase de reação **e** após a curva de risada/grito cair abaixo de $0,30$, sem incluir a primeira frase de um assunto seguinte ("Bom, agora vamos para outra missão...").
- **[ ] G5 — Conformidade Técnica (Duração 20–60 s e Legenda Limpa):**
  Duração total entre **20,0 s e 60,0 s**, áudio inteligível sem estouro digital e legendas 9:16 sincronizadas (máximo de 2–4 palavras por bloco, sem sobreposição temporal).

**Critério de aprovação de um clipe:** Passar obrigatoriamente em **G1 e G3** e obter nota **$\ge 4/5$**.

---

## 3. Métricas Simples e Automatizáveis

Inspirado no comando `pnpm quality:eval` do [`xixihhhh/hotclip`](https://github.com/xixihhhh/hotclip) e nos relatórios `selection.json` do [`mehbul/chopify`](https://github.com/mehbul/chopify), definimos 5 métricas que um script Python calcula em milissegundos:

| Métrica | Fórmula / Definição | Meta Ideal | O que diagnostica |
|---|---|---|---|
| **1. `Recall@K` de Eventos** ($K \in \{5, 10\}$) | $\%$ dos momentos do *ground truth* parcial ($G_{\text{YT}} \cup G_{\text{Twitch}}$) cujo centro $t_{\text{GT}}$ cai dentro de algum dos Top-$K$ clipes gerados ($t_{\text{start}} \le t_{\text{GT}} \le t_{\text{end}}$) | `Recall@5 >= 0.50`<br>`Recall@10 >= 0.75` | Se a combinação de pesos está achando os momentos certos da live |
| **2. `MidWordCutRate`** | $\%$ de clipes gerados em que $t_{\text{start}}$ ou $t_{\text{end}}$ corta uma palavra do Whisper ou fica a $< 50\text{ ms}$ da palavra vizinha | **0,0%** | Falha no algoritmo de *word-snapping* (`_snap_start`/`_snap_end`) |
| **3. `SentenceEdgeRate`** | $\%$ de clipes cujo $t_{\text{start}}$ ocorre após `.?!` ou pausa $\ge 0,5\text{ s}$ **e** cujo $t_{\text{end}}$ ocorre em `.?!` ou pausa $\ge 0,5\text{ s}$ | **$\ge 85\%$** | Se os clipes começam e terminam em limites naturais de frase |
| **4. `PeakPositionRatio` ($r$)** | Posição relativa do pico dentro do clipe: $r = \frac{t_{\text{peak}} - t_{\text{start}}}{t_{\text{end}} - t_{\text{start}}}$ | **$0,35 \le r \le 0,75$** em $\ge 80\%$ dos clipes | Se $r < 0,20$, faltou *setup* (cortou em cima do susto); se $r > 0,85$, cortou a risada/reação no final |
| **5. `Precision@5` Humana** | Fração dos Top-5 clipes gerados que aprovam no Checklist (G1+G3 e $\ge 4/5$) em inspeção rápida | **$\ge 3/5$ (60%+)** | Qualidade real percebida pelo usuário final |

---

## 4. Jeito Barato de Comparar Configurações em CPU (Sem Re-Transcrever nem Renderizar)

Testar pesos de sinais ou parâmetros de corte rodando download + Whisper + FFmpeg 9:16 a cada mudança levaria **horas de CPU**. O jeito barato e determinístico é **separar a extração de sinais (feita 1 vez) da simulação de cortes (feita em 50 ms)**:

### Passo 1: Montar um *Golden Set* Leve de 4 VODs (Cache de Sinais)
Escolher 4 VODs representativos de ~1–2 h (1 react de GTA VI com `heatmap` no YouTube, 1 gameplay de terror/sustos na Twitch, 1 gameplay de comédia/GTA RP na Twitch, 1 live de conversa/hype) e salvar em `tests/fixtures/<vod_id>/` apenas 3 arquivos pequenos (~1–2 MB no total por VOD, sem salvar o `.mp4` no Git):
- `signals.npz`: vetores NumPy de 1 Hz já extraídos (`rms_db`, `laughter`, `shouting`, `chat_rate`, `chat_react`, `scene_cuts`).
- `words.json`: lista de palavras com timestamps (`[{"start": 12.10, "end": 12.45, "word": "Olha", "ends_sentence": false}, ...]`) gerada uma única vez pelo `faster-whisper`.
- `ground_truth.json`: lista dos top picos do `heatmap` / Twitch Clips + 5 intervalos "ouro" revisados manualmente uma única vez.

### Passo 2: Rodar o *Harness* de Comparação (`eval_configs.py`) em < 1 Segundo
Para comparar uma Configuração A (ex.: sem compensação de *delay* de chat, `pre_roll=4s`) contra uma Configuração B (ex.: `chat_delay=5s`, `pre_roll=10s`, bônus multimodal `0.15`):
1. O script lê `signals.npz` e `words.json` dos 4 VODs e roda apenas a matemática de `compute_hype()` + `pick_candidates()` (leva **~0,05 s por VOD em 1 core de CPU**).
2. Calcula automaticamente `Recall@5`, `Recall@10`, `MidWordCutRate`, `SentenceEdgeRate` e `PeakPositionRatio` para A e B.
3. Imprime um *diff* de texto mostrando apenas os candidatos que mudaram:
   ```text
   [VOD gta6_react] Candidato #1:
     Config A: 1032.4s -> 1054.1s (21.7s, r=0.12 [FALTOU SETUP], mid_word=True)
               "...caralho vocês viram a física da água no trailer..."
     Config B: 1022.1s -> 1056.8s (34.7s, r=0.41 [OK], sentence_edge=True)
               "Calma, volta dez segundos nessa cena da praia. Caralho vocês viram a física da água no trailer..."
   ```

### Passo 3: Auditar Apenas o Delta (Sem Renderizar 9:16)
Quando uma configuração B vence nas métricas automáticas, validamos o sentido humano em **2 minutos** lendo o texto expandido do *diff* acima ou gerando apenas o corte de áudio `.mp3` (`ffmpeg -ss <start> -to <end> -i audio.wav -c copy`) dos Top 5 clipes — deixando a renderização pesada de vídeo 9:16 com legendas `.ass` exclusivamente para o pipeline final de produção.
