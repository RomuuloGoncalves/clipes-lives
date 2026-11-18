# creditos-encerramento Specification

## Problem Statement

Atualmente `render.py` posiciona a legenda com `MarginV=430` (`y=1490`), invadindo a zona ocupada pela descrição/música do TikTok, Reels e Shorts, não exibe crédito nem link do canal na imagem (em Shorts, links na descrição não são clicáveis), e corta o vídeo abruptamente no último milissegundo da fala (*corte seco*). Precisamos reposicionar a legenda na safe zone (`MarginV=560`, `MarginL/R=120/200`), queimar o crédito `twitch.tv/<canal>` (ou `@<canal>`) como destaque inicial (`4s`) + marca d'água permanente (`40%`), adicionar encerramento de `+1.2s` com quadro congelado (`tpad`), fade de áudio (`loudnorm -> afade -> apad`) e cartão final ASS (`"Parte k+1 →"` ou `"Live completa: ..."`), além de oferecer reenquadramento vertical opcional com o modelo YuNet já incluso na imagem.

## Goals

- [ ] Posicionar legendas dentro da safe zone vertical e lateral (`MarginV=560`, `MarginL=120`, `MarginR=200`) em `build_ass`.
- [ ] Exibir crédito do canal (`twitch.tv/<canal>` ou `@<canal>`) em `y=600, x>=120` com opacidade total nos primeiros `4.0s` (`\fad(300,400)`) e como marca d'água a `~40%` de opacidade (`\alpha&H99&`) até o fim do conteúdo (`D`).
- [ ] Eliminar 100% dos cortes secos adicionando `1.2s` de encerramento (`tpad=stop_mode=clone:stop_duration=1.2`, áudio `loudnorm` seguido de `afade` + `apad`, `-t D+1.2`, sem fade para preto) e cartão ASS centrado (`"Parte k+1 →"` nas partes `1..N-1` ou `"Live completa: <credito>"` na última/única), mantendo todo arquivo final `<= 60.0s` para partes de até `58.0s`.
- [ ] Oferecer reenquadramento vertical opcional (`--reframe`, P2) detectando rosto/webcam a `1 fps` (`640x360`) com YuNet ONNX (`/opt/models/yunet/face_detection_yunet_2023mar.onnx`).

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Fade-to-black no encerramento do vídeo | Rejeitado no CONSOLIDADO §2.3 porque quebra a transição de loop automático no TikTok/Reels/Shorts |
| Animação pop por palavra ativa ou troca de fonte customizada | Rejeitado no CONSOLIDADO §2.2 por ausência de evidência medida de retenção |
| Rastreamento de rosto quadro a quadro em 30/60 fps | Desnecessário (webcam de streamer é estática) e caro em CPU; amostragem a `1 fps` com mediana da caixa resolve em `< 1.5s` |

---

## Implicit-Requirement Dimensions Sweep

| Dimension | Resolution |
| --------- | ---------- |
| Input validation & bounds | `MarginV=560`, `MarginL=120`, `MarginR=200`; `OUTRO_DURATION = 1.2s`; duração do MP4 renderizado deve ser `clip.duration + 1.2s` com tolerância `±0.05s` medida via `ffprobe`; caracteres especiais no nome do canal são sanitizados por `_ass_escape` |
| Failure / partial-failure states | Se o metadado do vídeo não trouxer `channel`/`uploader`, infere da URL (`twitch.tv/<canal>`) ou usa o `video_id`; se `--reframe` não detectar rosto em `>= 30%` dos quadros amostrados ou falhar no ONNX, cai automaticamente para o layout 16:9 centralizado com fundo borrado |
| Idempotency / retry / duplicate handling | Renderização continua escrevendo em `.tmp_clip_XX.mp4` e aplicando `replace(out_file)` somente após `returncode == 0` do `ffmpeg` |
| Auth boundaries & rate limits | N/A because toda a renderização e detecção YuNet ocorrem offline sobre o segmento local |
| Concurrency / ordering | Esta feature modifica `clipes/render.py` e `clipes/cli.py` em **série** imediatamente após `partes-por-assunto` |
| Data lifecycle / expiry | Arquivos `.ass` ficam em `<out_dir>/.cache/clip_XX.ass` e segmentos temporários `seg_XX.*` continuam removidos no bloco `finally` |
| Observability | `clips.json` registra `channel_credit`, `rendered_duration` (`D + 1.2`) e `layout` (`"blurred_16x9"` ou `"reframed_face"`) |
| External-dependency failure | YuNet ONNX usa `onnxruntime` já instalado na imagem; ausência do arquivo `.onnx` desativa `--reframe` com aviso sem abortar o render |
| State-transition integrity | Mesmo quando um clipe não possui palavras transcritas (`n_subs == 0`), `build_ass` gera os eventos `Credit`, `Watermark` e `Card`, garantindo que o filtro `subtitles` sempre queime o crédito e o cartão final |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Valor de `MarginV` e margens laterais no ASS | `MarginV = 560` (`y = 1360`), `MarginL = 120`, `MarginR = 200` | Posiciona a legenda logo abaixo do quadro 16:9 (`y = 656..1264`) sem cobrir o vídeo e fora da zona crítica inferior e dos botões à direita (CONSOLIDADO §2.2) | yes |
| Duração e estilo do encerramento | `1.2s` de quadro congelado (`tpad=stop_mode=clone:stop_duration=1.2`), sem fade para preto, com `-t D+1.2` | `58.0s` (teto da parte) + `1.2s` = `59.2s <= 60.0s` (evita bloqueio de Content ID em Shorts `> 1 min`, CONSOLIDADO §2.1 e §2.3) | yes |
| Ordem da cadeia de filtros de áudio | `loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={max(0,D-0.5):.3f}:d=0.5,apad=pad_dur=1.2` | Pôr `apad` antes do `loudnorm` alteraria a medição de loudness integrado por conta do silêncio adicionado (CONSOLIDADO §2.3) | yes |
| Posição e opacidade do crédito/marca d'água | `\pos(120,600)\fad(300,400)` nos primeiros `4.0s` e `\pos(120,600)\alpha&H99&` (`~40%` opaco) de `4.0s` até `D` | Fica na faixa borrada logo acima do vídeo 16:9 (`y=656`), dentro da safe zone do topo (`y >= 288`, `x >= 120`, CONSOLIDADO §2.3) | yes |

**Open questions:** none - todas as decisões foram resolvidas no CONSOLIDADO ou registradas como premissas explícitas acima.

---

## User Stories

### P1: Safe Zones, Crédito do Canal e Marca d'Água em ASS ⭐ MVP

**User Story**: Como criador de cortes, quero que a legenda fique fora da área coberta pela interface do TikTok/Reels/Shorts e que o nome/link curto do canal apareça visível dentro do vídeo desde o início.

**Why P1**: Em Shorts os links na descrição não são clicáveis (YouTube Help 13748639) e `MarginV=430` colide com a UI das plataformas (CONSOLIDADO §2.2 e §2.3).

**Acceptance Criteria**:

1. WHEN `ingest` (ou `ingest_local`) processar os metadados da mídia THEN o sistema SHALL extrair o identificador do canal e formatar `channel_credit` como `twitch.tv/<canal>` para fontes Twitch ou `@<canal>` para fontes YouTube.
2. WHEN `build_ass` gerar o arquivo `.ass` THEN o sistema SHALL definir `Style: Cap` com `MarginL=120, MarginR=200, MarginV=560`.
3. WHEN `build_ass` gerar o arquivo `.ass` THEN o sistema SHALL incluir `Style: Credit` e emitir dois eventos `Dialogue`: o primeiro de `0.0s` a `min(4.0, D)` com `{\pos(120,600)\fad(300,400)}<channel_credit>` e o segundo de `min(4.0, D)` até `D` com `{\pos(120,600)\alpha&H99&}<channel_credit>`.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_render_eval.py` inspecionando o arquivo `.ass` gerado para confirmar `MarginV=560`, `120,200,560`, `\pos(120,600)` e `\alpha&H99&`.

---

### P1: Encerramento de 1,2s (Quadro Congelado, Fade de Áudio e Cartão Final) ⭐ MVP

**User Story**: Como espectador do clipe, quero que o vídeo nunca termine em corte seco na última sílaba e exiba um cartão final claro indicando a próxima parte (`Parte k+1 →`) ou onde assistir à live completa (`Live completa: twitch.tv/<canal>`).

**Why P1**: Atende à decisão 4 do usuário e §2.3 do CONSOLIDADO sem estourar o teto de 60s.

**Acceptance Criteria**:

1. WHEN `build_ass` gerar o arquivo `.ass` para um clipe de duração de conteúdo `D` THEN o sistema SHALL incluir `Style: Card` e um evento `Dialogue` no intervalo `[D, D + 1.2]` em `\pos(540,960)\fad(250,0)` contendo `"Parte {part_index + 1} →"` quando `part_index < part_total`, ou `"Live completa: <channel_credit>"` quando `part_index == part_total`.
2. WHEN `render_clip` executar o `ffmpeg` THEN o sistema SHALL aplicar `tpad=stop_mode=clone:stop_duration=1.2` antes do filtro `subtitles` (sem filtro `fade` de vídeo para preto), aplicar `-af "loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=out:st={max(0.0, D-0.5):.3f}:d=0.5,apad=pad_dur=1.2"` e limitar a saída com `-t {D + 1.2:.3f}`.
3. WHEN o arquivo MP4 final for inspecionado via `ffprobe` THEN o sistema SHALL produzir duração total igual a `D + 1.2s` (`±0.05s`) em resolução `1080x1920`.

**Independent Test**: Renderizar um clipe sintético de `10.0s` dentro do container via `pytest` e verificar com `ffprobe` que o MP4 resultante tem `11.20s ± 0.05s`, stream `h264` `1080x1920` e stream `aac`.

---

### P2: Reenquadramento Vertical Opcional com YuNet ONNX (`--reframe`)

**User Story**: Como criador de cortes de podcasts/conversas, quero poder ativar opcionalmente (`--reframe`) o reenquadramento vertical automático focado no rosto de quem fala usando o detector YuNet já assado na imagem.

**Why P2**: Previsto como tarefa opcional P2 no CONSOLIDADO §2.2 e §3 após o crédito e encerramento estarem prontos.

**Acceptance Criteria**:

1. WHERE a flag `--reframe` for informada na CLI o sistema SHALL amostrar o segmento de vídeo a `1 fps` em `640x360`, executar `/opt/models/yunet/face_detection_yunet_2023mar.onnx` via `onnxruntime` e calcular a mediana da caixa de rosto se houver detecção válida em `>= 30%` dos quadros.
2. IF `--reframe` não estiver ativo OR se menos de `30%` dos quadros tiverem rosto detectado THEN o sistema SHALL manter o layout padrão 16:9 centralizado sobre o fundo borrado.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_render_eval.py` testando `detect_speaker_crop` com quadros sintéticos e verificando o fallback para o layout 16:9 borrado quando nenhum rosto é encontrado.

---

## Edge Cases

- IF um clipe não tiver nenhuma palavra transcrita (`words == []`, clipe de reação/música) THEN o sistema SHALL ainda assim escrever o `.ass` com `Credit`, `Watermark` e `Card` e aplicar o filtro `subtitles` no `ffmpeg`.
- IF a duração do conteúdo `D` for menor que `4.0s` (em testes unitários curtos) THEN o sistema SHALL limitar o primeiro evento `Credit` a `[0.0, D]` sem gerar intervalo negativo no segundo evento.
- IF o vídeo de origem não possuir trilha de áudio (`0:a?` ausente) THEN o sistema SHALL aplicar `tpad` e `-t D+1.2` normalmente sem falhar no filtro `-af`.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| CRED-01 | P1: Safe Zones no ASS (`MarginV=560`, `MarginL=120`, `MarginR=200`) | Design | Pending |
| CRED-02 | P1: Extração de canal e crédito/marca d'água (`\pos(120,600)`, `4s` + `40%`) | Design | Pending |
| CRED-03 | P1: Encerramento `ffmpeg` (`tpad 1.2s`, `loudnorm -> afade -> apad`, `-t D+1.2`) | Design | Pending |
| CRED-04 | P1: Cartão final ASS (`"Parte k+1 →"` vs `"Live completa: <credito>"`) | Design | Pending |
| CRED-05 | P2: Reenquadramento vertical opcional a 1 fps com YuNet ONNX (`--reframe`) | Design | Pending |

**Coverage:** 5 total, 5 mapped to tasks, 0 unmapped

---

## Success Criteria

- [ ] `MarginV=560, MarginL=120, MarginR=200` aplicados em 100% dos arquivos `.ass`.
- [ ] Todos os MP4s renderizados incluem crédito visível em `y=600`, cartão final nos últimos `1.2s` e têm duração exata `D + 1.2s ± 0.05s` medida por `ffprobe`.
- [ ] Ordem dos filtros de áudio preserva `loudnorm` antes de `afade` e `apad`.
