# deteccao-por-cota Specification

## Problem Statement

O seletor atual (`pick_candidate_peaks` em `candidates.py`) depende exclusivamente de máximos pontuais (`scipy.signal.find_peaks`) sobre a curva fundida de `loudness`, `chat` e `heatmap`. Em podcasts conversados com compressor de áudio, lives calmas ou gravações da Twitch sem `heatmap`, a curva fica achatada (zona morta de `3–4 dB` e `min_scale=8.0` em `signals.py`), limitando drasticamente a quantidade de clipes ou retornando zero candidatos; por outro lado, transcrever um VOD de 3h inteiro com `small beam=5` custaria ~88 min no Ryzen 5 5500U. Precisamos implementar o modo cota/piso da frente D: pré-triagem prosódica em blocos, ASR em duas passadas (`beam_size=1` sem timestamps por palavra na Passada 1 + `word_timestamps=True` nos escolhidos na Passada 2), segmentação e pontuação de todos os segmentos por percentil do vídeo, cota por hora com piso de qualidade anti-lixo, diversificação temporal e deduplicação textual.

## Goals

- [ ] Garantir uma cota estável de candidatos (`--min-per-hour 3`, limitada a `--max-clips`) mesmo em vídeos conversados sem picos de grito e sem `heatmap`/`chat`, mantendo `0` clipes em vídeos sem fala (`speech_density < 0.30`).
- [ ] Manter o tempo total de ASR em duas passadas dentro do orçamento de `<= 12 min` de CPU por hora de vídeo no Ryzen 5 5500U (`beam_size=1, word_timestamps=False` em até `30%` da duração na Passada 1 + `word_timestamps=True` apenas nos top candidatos na Passada 2).
- [ ] Pontuar cada segmento por features baratas (`CheapScore`: gancho `?`/`!`, marcadores de história/risada, ritmo de fala, prosódia acústica e sinais externos) normalizadas por percentil do próprio vídeo, com diversificação temporal (`min_sep >= 120s`, máx `50%` no mesmo terço no 1º passe) e deduplicação textual (Jaccard de bigramas / TF-IDF `<= 0.60`).
- [ ] Calibrar os pesos com `precision@K` sobre `tests/fixtures/calib_labels.json` e executar as medições M2 e M7 no container no piso 5500U.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Modelos Whisper `tiny` ou `base` para pré-transcrição | Rejeitado na pesquisa (cursor-D, CONSOLIDADO §5): `tiny` tem WER ~24% em pt-BR e não está assado no `Dockerfile` |
| Uso do LLM local como juiz principal de todos os segmentos | Inviável em tempo de CPU para dezenas de segmentos e fraco em zero-shot segundo o benchmark Rhapsody (CONSOLIDADO §3 e §5) |
| Dependência de legendas automáticas `json3` do YouTube como caminho único | Não existe em lives da Twitch nem em gravações locais; pode entrar apenas como atalho opcional futuro |
| Treinamento de redes neurais / fine-tuning em GPU | Fora do piso CPU-only; calibração usa busca em grade determinística sobre `precision@K` |

---

## Implicit-Requirement Dimensions Sweep

| Dimension | Resolution |
| --------- | ---------- |
| Input validation & bounds | `--mode` em `{peaks, quota, auto}` (padrão `auto`); `--min-per-hour >= 0.0` (padrão `3.0`); `--asr-budget-ratio` em `(0.05, 1.0]` (padrão `0.30`); piso anti-lixo exige `speech_density >= 0.30`, `word_count >= 30` e `score >= S_min_abs` (`0.25`) |
| Failure / partial-failure states | Se um vídeo tiver menos candidatos acima do piso absoluto `S_min_abs = 0.25` do que a cota `K_min`, o sistema entrega apenas os que passarem do piso (ou `[]` em live de música) e registra o motivo em `clips.json["note"]` |
| Idempotency / retry / duplicate handling | Transcrição da Passada 1 é cacheada em `<cache_dir>/pass1_segments.json` e Passada 2 em `<cache_dir>/words.json`; deduplicação elimina candidatos com sobreposição temporal `> 30%` ou similaridade textual `> 0.60` |
| Auth boundaries & rate limits | N/A because todo o cálculo prosódico, ASR em duas passadas e pontuação ocorrem localmente sobre `audio.wav` |
| Concurrency / ordering | Estritamente sequencial em CPU (`6` threads); na série do projeto, executa após `partes-por-assunto` e `creditos-encerramento` reutilizando `clipes/topics.py` |
| Data lifecycle / expiry | Cache `pass1_segments.json` reside em `<out_dir>/.cache/` junto com `signals.npz` e `words.json` |
| Observability | `clips.json` registra `detection_mode` (`"peaks"`, `"quota"` ou `"auto"`), `floor_mode` (`bool`), `quota_target` (`int`), `timings["asr_pass1_s"]` e `timings["asr_pass2_s"]` |
| External-dependency failure | Zero dependências externas ou modelos novos: usa `numpy`, `scipy` e `faster-whisper small` já presentes na imagem |
| State-transition integrity | Candidatos vindos de cota são convertidos em `CandidatePeak` com `[topic_start, topic_end]` preenchidos, alimentando `snap_topic_parts` (`partes-por-assunto`) sem quebrar o contrato de `snap.py` |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Modelo e parâmetros da Passada 1 de ASR | `faster-whisper small` (`int8`, `beam_size=1`, `word_timestamps=False`, `vad_filter=True`) sobre até `30%` da duração do vídeo | `small beam=1` sem word timestamps mediu `0,29 xRT` (vs `0,49 xRT` em `beam=5`), já está assado no `Dockerfile` e evita o WER de 24% do `tiny` em pt-BR (CONSOLIDADO §3 e §5) | yes |
| Cota padrão e espaçamento mínimo | `--min-per-hour = 3.0`, `min_sep = 120.0s`, máximo de `50%` dos clipes no mesmo terço do vídeo no 1º passe | Equilibra volume (3–4 clipes/h) e cobertura ao longo de toda a live sem aglomerar cortes nos primeiros 15 minutos (CONSOLIDADO §5, relatórios D) | yes |
| Modo padrão na CLI | `--mode auto` (usa picos acima do percentil 90 e, se gerarem `< ceil(min_per_hour * dur_h)` candidatos distribuídos, aciona a Passada 1 + cota por segmento) | Preserva o caminho rápido quando já há picos multimodais claros e garante a cota quando a curva é plana ou sem heatmap | yes |
| Piso de qualidade anti-lixo | `speech_density >= 0.30`, `word_count >= 30`, `score >= percentil 75` (relaxando até `percentil 55` no modo piso, nunca abaixo de `S_min_abs = 0.25`) | Impede que lives de música 24/7 ou telas de espera gerem clipes artificiais (`test_zero_clips_flat_signal_degrades_cleanly` continua passando) | yes |

**Open questions:** none - todas as decisões foram resolvidas no CONSOLIDADO e nos relatórios D ou registradas como premissas explícitas acima.

---

## User Stories

### P1: Pré-Triagem Prosódica (Passada 0) e ASR em Duas Passadas com Orçamento ⭐ MVP

**User Story**: Como operador no piso Ryzen 5 5500U, quero que o pipeline identifique onde há fala dinâmica no vídeo inteiro em menos de 1 segundo e transcreva esses blocos rapidamente (`beam_size=1` sem timestamps de palavra) dentro de um orçamento fixo, refinando com timestamps por palavra apenas nos candidatos escolhidos.

**Why P1**: Permite descobrir histórias e conversas sem pico de grito em VODs longos sem gastar 88 minutos de ASR.

**Acceptance Criteria**:

1. WHEN `compute_signals` processar `rms_db` THEN o sistema SHALL calcular o sinal `prosody` (`1 Hz`) combinando desvio-padrão de energia nos frames de voz ativa e modulação silábica (`|Δ rms_db|`) normalizados por percentil com escala adaptativa por IQR.
2. WHEN `triage_audio_blocks` analisar um vídeo de duração `D` com `--asr-budget-ratio R` (padrão `0.30`) THEN o sistema SHALL dividir o áudio em blocos de `30.0s` (passo `15.0s`), descartar blocos com `speech_density < 0.30` e selecionar blocos ordenados por score acústico-prosódico estratificados pelos 3 terços do vídeo até somar no máximo `R * D` segundos de áudio.
3. WHEN `transcribe_fast_blocks` (Passada 1) executar sobre os blocos selecionados THEN o sistema SHALL transcrever usando `faster-whisper` com `beam_size=1` e `word_timestamps=False`, salvando o cache em `pass1_segments.json`.
4. WHEN os candidatos finais forem escolhidos após a pontuação de segmentos THEN o sistema SHALL executar `transcribe_candidates` (Passada 2, com `word_timestamps=True`) exclusivamente nas janelas dos candidatos aprovados para o `snap.py`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_signals_candidates.py` verificando que um áudio conversado com volume comprimido (`±1.5 dB`) gera blocos prosódicos nos 3 terços do vídeo, respeita o teto `0.30 * D` e descarta blocos silenciosos/musicais.

---

### P1: Segmentação, Pontuação `CheapScore`, Modo Cota/Piso e Diversificação ⭐ MVP

**User Story**: Como criador de cortes, quero que o sistema pontue todos os segmentos transcritos por gancho, narrativa, ritmo e prosódia (normalizados por percentil do próprio vídeo) e entregue uma cota diversificada e sem duplicatas por hora de vídeo.

**Why P1**: Resolve a limitação de quantidade de clipes apontada pelo usuário quando só picos e heatmap eram usados.

**Acceptance Criteria**:

1. WHEN os segmentos de assunto da Passada 1 forem avaliados por `score_topic_segments` THEN o sistema SHALL calcular `CheapScore` em `[0.0, 1.0]` combinando gancho inicial (`?`/`!`), marcadores de história/reação (com penalidade para leitura de sub/donate), cadência (`palavras/s`), prosódia acústica e sinais externos (`chat`/`heatmap` quando presentes), normalizados por percentil dentro do próprio vídeo.
2. WHEN `pick_quota_candidates` selecionar candidatos para um vídeo de `H` horas com `--min-per-hour M` (padrão `3.0`) THEN o sistema SHALL buscar atingir `K = clamp(ceil(M * H), 1, max_clips)` candidatos válidos com `word_count >= 30` e `score >= P75` do vídeo.
3. IF o número de candidatos aprovados em `P75` for menor que `K` THEN o sistema SHALL ativar `floor_mode = True` e relaxar o limiar em passos até `P55` respeitando o piso absoluto `S_min_abs = 0.25`.
4. The sistema SHALL aplicar espaçamento mínimo `min_sep >= 120.0s` entre centros de candidatos independentes, limitar cada terço do vídeo a no máximo `ceil(K / 2)` candidatos no 1º passe de seleção e descartar qualquer candidato cuja similaridade textual (Jaccard de bigramas / TF-IDF) com um candidato já escolhido de maior score seja `> 0.60`.

**Independent Test**: Executar `pytest` sobre um vídeo sintético de 2 horas sem `heatmap` e sem picos de grito (apenas segmentos de conversa) e verificar que `pick_quota_candidates` seleciona `6` candidatos (`3/h`), distribuídos pelos 3 terços do vídeo, sem duplicatas textuais.

---

### P2: Calibração `precision@K` sem GPU e Medições M2 / M7 no Ryzen 5 5500U

**User Story**: Como engenheiro mantendo a precisão do detector, quero um conjunto rotulado leve (`calib_labels.json`) e testes de benchmark em container no piso 5500U para medir o ganho de `beam_size=1` (M2), o custo do ASR em duas passadas (M7) e a `precision@8` sem depender do heatmap.

**Why P2**: Fecha as medições M2 e M7 do CONSOLIDADO §7 e a calibração offline da frente D (§5).

**Acceptance Criteria**:

1. WHEN `evaluate_quota_calibration` for executada sobre `tests/fixtures/calib_labels.json` (contendo os momentos positivos de `docs/teste-real.md` e janelas negativas do mesmo VOD) com o sinal `heatmap` ocultado THEN o sistema SHALL calcular `precision_at_k` e `recall_at_k` atingindo `precision_at_8 >= 0.625`.
2. WHEN o benchmark M2/M7 (`tests/test_bench_m2_m7_quota.py`) rodar no container no Ryzen 5 5500U THEN o sistema SHALL comprovar `xRT_pass1 <= 0.38` (`small`, `beam_size=1`, `word_timestamps=False`), tempo total de ASR (Passada 1 + Passada 2) `<= 12.0 min` por hora de VOD e `mid_word_cut_rate == 0.0` após a Passada 2.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_bench_m2_m7_quota.py -v -s`.

---

## Edge Cases

- IF o áudio inteiro do vídeo consistir em música contínua ou ruído constante sem fala (`speech_density < 0.30` ou `< 30` palavras reconhecidas) THEN o sistema SHALL retornar `0` clipes, gravar `clips.json` vazio com `note` explicativa e encerrar com código `0`.
- IF um VOD curto tiver menos de `20` minutos (`H < 0.33`) THEN o sistema SHALL calcular `K = clamp(ceil(min_per_hour * H), 1, max_clips)` permitindo ao menos `1` a `2` clipes se ultrapassarem `S_min_abs = 0.25`.
- WHEN `--mode peaks` for especificado explicitamente na CLI THEN o sistema SHALL executar apenas o caminho clássico de picos (`pick_candidate_peaks` + passe único de ASR) preservando 100% de retrocompatibilidade.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| COTA-01 | P1: Sinal `prosody` 1 Hz e pré-triagem de blocos (`speech_density >= 0.30`) | Design | Pending |
| COTA-02 | P1: Seleção estratificada por terços do vídeo até `--asr-budget-ratio` | Design | Pending |
| COTA-03 | P1: ASR em duas passadas (`beam=1` sem word ts -> Pass 2 com word ts) | Design | Pending |
| COTA-04 | P1: Pontuação `CheapScore` por percentil intravídeo e dedup textual | Design | Pending |
| COTA-05 | P1: Seleção por cota/piso (`--min-per-hour`), diversificação e `--mode` | Design | Pending |
| COTA-06 | P2: Calibração `precision@K` sem GPU com `calib_labels.json` | Design | Pending |
| COTA-07 | P2: Medições M2 e M7 no Ryzen 5 5500U (tempo e qualidade de 2 passadas) | Design | Pending |

**Coverage:** 7 total, 7 mapped to tasks, 0 unmapped

---

## Success Criteria

- [ ] Vídeos conversados sem `heatmap` e sem gritos geram `>= min_per_hour` clipes válidos por hora distribuídos nos 3 terços do vídeo, enquanto lives de música contínua continuam gerando `0` clipes.
- [ ] Custo de CPU do ASR em duas passadas no Ryzen 5 5500U fica `<= 12 min` por hora de VOD (`xRT <= 0.38` na Passada 1).
- [ ] `precision_at_8 >= 0.625` no dataset semente `calib_labels.json` mesmo com o `heatmap` desabilitado na detecção.
