# legenda-hashtags Specification

## Problem Statement

Atualmente o pipeline gera apenas um título heurístico simples por regex (`generate_title` em `render.py`), não produz texto de legenda estruturado para postagem nem hashtags específicas do nicho/streamer, e não dispõe de avaliação semântica para desempatar candidatos próximos. Precisamos integrar o modelo local `Qwen3-1.7B-Q4_K_M` via `llama.cpp` (`/opt/llama/llama-server`) de forma estritamente sequencial após o Whisper, com saída JSON forçada por gramática, banco de hashtags por nicho/jogo/streamer e validação determinística contra alucinações e hashtags proibidas (`#fyp`, `#viral`, `#reels`, hífens).

## Goals

- [ ] Executar `Qwen3-1.7B-Q4_K_M` (e opcionalmente `Qwen3-4B-Instruct-2507-Q4_K_M` via `CLIPES_LLM_GGUF`) em subprocesso `llama-server` uma única vez por lote após a liberação do Whisper da RAM (`RSS pico <= 2.5 GB` com 1.7B).
- [ ] Gerar para cada clipe em `clips.json`: `title` (`<= 60` chars, `?` ou `!`, `<= 1` emoji, sem nomes próprios inventados), `caption` (3 linhas padronizadas com gancho, crédito/link/`Parte k/N` e tags) e `hashtags` (`3 a 5` tags `^#[a-zA-Z0-9_À-ÿ]+$` sem `#fyp/#foryou/#viral/#reels`).
- [ ] Calcular `llm_score` (`0–10`) com rubrica ancorada e temperatura `0`, usando-a apenas como desempate/ranking relativo sem descartar clipes aprovados pelos sinais.
- [ ] Medir no piso Ryzen 5 5500U (`M3`) `tok/s`, RSS e `s/clipe` do Qwen3-1.7B (e 4B) sobre os 8 clipes reais de `UBv07VGeD24`.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
| ------- | ------ |
| Descarte de clipes por `llm_score < 6` | Rejeitado no CONSOLIDADO §3: os 5 LLMs testados deram notas 7–9 para o mesmo trecho e LLMs zero-shot quase empatam com o acaso (paper Rhapsody) |
| Compilação ou uso de `llama-cpp-python` | Falhou ao compilar wheel na imagem slim e é desnecessário pois o binário `/opt/llama/llama-server` já está assado no `Dockerfile` (CONSOLIDADO §3) |
| Classes de abstração (`LLMProvider`, `ModelRegistry`, factories) | Proibido pelo CONSOLIDADO §3 e §8 (estilo ponytail: apenas `complete(prompt, schema) -> dict` lendo `CLIPES_LLM_GGUF`) |
| APIs externas pagas (OpenAI, Anthropic, Gemini API) | Viola a restrição dura de execução 100% gratuita, offline e sem chaves de API |

---

## Implicit-Requirement Dimensions Sweep

| Dimension | Resolution |
| --------- | ---------- |
| Input validation & bounds | Transcrição enviada ao prompt truncada em `<= 350` palavras (`~500` tokens); `titulo <= 60` caracteres; `3 <= len(hashtags) <= 5`; cada hashtag casa com `^#[a-zA-Z0-9_À-ÿ]+$`; `0.0 <= llm_score <= 10.0` |
| Failure / partial-failure states | Se o `llama-server` falhar ao subir, der timeout (`> 45s` por clipe) ou o título reprovar no filtro anti-alucinação, o clipe usa `generate_title(clip, words)` como fallback e monta a legenda/hashtags a partir do banco determinístico sem abortar o pipeline |
| Idempotency / retry / duplicate handling | Temperatura `0` (`--temp 0`) e `--seed 42` garantem saída determinística; hashtags são deduplicadas case-insensitive preservando a ordem |
| Auth boundaries & rate limits | N/A because o `llama-server` escuta apenas em `127.0.0.1` em porta efêmera local dentro do container |
| Concurrency / ordering | Estritamente sequencial: `asr.py` executa `del model; gc.collect()` antes de retornar; `llama-server` sobe depois do `snap` e morre (`terminate/kill`) no bloco `finally` antes do `render` |
| Data lifecycle / expiry | Nenhum estado persiste fora de `clips.json`; o processo `llama-server` é encerrado ao fim do lote liberando 100% do RSS antes do `ffmpeg` |
| Observability | `timings["llm_s"]` registrado no manifesto; cada clipe em `clips.json` expõe `title`, `caption`, `hashtags`, `llm_score`, `llm_reason` e `title_source` (`"llm"` ou `"heuristic_fallback"`) |
| External-dependency failure | Flag `--no-llm` (ou ausência do binário/GGUF em ambiente de teste rápido) contorna o subprocesso e usa o gerador heurístico + banco de hashtags |
| State-transition integrity | A nota do LLM (`llm_score`) altera apenas a chave secundária de ordenação (`round(clip.score + 0.02 * (llm_score / 10.0), 4)` ou desempate) e nunca reduz `len(clips)` |

---

## Assumptions & Open Questions

Every ambiguity is resolved or recorded here - nothing is left silently unclear.

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Modelo padrão vs opcional | `Qwen3-1.7B-Q4_K_M.gguf` padrão (`CLIPES_LLM_GGUF`); `Qwen3-4B-Instruct-2507-Q4_K_M.gguf` opcional via `WITH_LLM_4B=1` | Já configurado no `Dockerfile` do projeto; 1.7B gasta ~2 GB RSS e ~8–11s/clipe (CONSOLIDADO §3) | yes |
| Desativação de raciocínio (`<think>`) no Qwen3 | Passar `--reasoning-budget 0` ao `llama-server` e sufixo `/no_think` no system prompt ChatML | Sem desligar `<think>`, o Qwen3-1.7B esgota tokens pensando ou fecha JSON vazio sob gramática estrita (`C-credito-modelos-argon.md` §2a) | yes |
| Regra anti-alucinação para títulos | Rejeitar `titulo` se contiver palavra capitalizada fora do início da frase que não apareça (case-insensitive, sem acento) em `clip.text`, `ing.title` ou `ing.channel` | Os modelos 1.5B/1.7B inventaram fatos/nomes em testes reais (`CONSOLIDADO §3`); fallback imediato para `generate_title` | yes |
| Formato da legenda e hashtags | Legenda de 3 linhas (`gancho` / `Crédito: <canal> · Live completa: <url>[ · Parte k/N]` / `3–5 hashtags`); bloqueio de `#fyp`, `#foryou`, `#viral`, `#reels` e hífens | Teto de 5 hashtags no Instagram/TikTok e contraexemplo medido de `#fyp` (CONSOLIDADO §2.4) | yes |

**Open questions:** none - todas as decisões foram resolvidas no CONSOLIDADO ou registradas como premissas explícitas acima.

---

## User Stories

### P1: Execução Sequencial do LLM Local via llama-server com JSON Schema ⭐ MVP

**User Story**: Como operador no piso Ryzen 5 5500U, quero que o LLM local rode via `llama-server` somente após o Whisper liberar a RAM, gerando JSON válido em uma única subida por lote de clipes.

**Why P1**: Garante o cumprimento da restrição dura de memória (Whisper e LLM nunca juntos na RAM) e evita a falha de compilação do `llama-cpp-python`.

**Acceptance Criteria**:

1. WHEN `transcribe_candidates` concluir a transcrição em `clipes/asr.py` THEN o sistema SHALL remover a referência ao `WhisperModel` e invocar `gc.collect()` antes de retornar.
2. WHEN `enrich_clips_with_llm` for chamado para um lote de clipes com `--no-llm` inativo THEN o sistema SHALL iniciar um único subprocesso `llama-server` com `-m $CLIPES_LLM_GGUF -t $CLIPES_CPU_THREADS -c 2048 --reasoning-budget 0`, enviar uma requisição HTTP local por clipe com `response_format={"type": "json_object", "schema": ...}` e `temperature=0`, e encerrar o subprocesso no bloco `finally`.
3. IF o subprocesso `llama-server` falhar ao iniciar ou exceder o timeout por clipe THEN o sistema SHALL registrar aviso em log e preencher `title`, `caption` e `hashtags` via fallback determinístico sem interromper a execução.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_llm.py` verificando o ciclo de vida do subprocesso, o contrato de `complete(prompt, schema) -> dict` e o fallback quando o binário ou modelo falha.

---

### P1: Validação Anti-Alucinação, Legenda de 3 Linhas e Banco de Hashtags ⭐ MVP

**User Story**: Como criador de cortes, quero que cada clipe receba título curto e chamativo sem fatos inventados, legenda de 3 linhas com crédito/parte e 3 a 5 hashtags limpas do nicho/streamer sem `#fyp` ou hífens.

**Why P1**: Implementa as regras de produto §2.4 e §3 do CONSOLIDADO que protegem a qualidade do texto publicado.

**Acceptance Criteria**:

1. WHEN o LLM retornar `titulo` THEN o sistema SHALL validar que `len(titulo) <= 60`, que contém `?` ou `!`, que possui no máximo `1` emoji, e que nenhuma palavra capitalizada após a primeira palavra está ausente de `clip.text + ing.title + ing.channel`; IF qualquer condição falhar THEN o sistema SHALL substituir `titulo` por `generate_title(clip, words)`.
2. WHEN o sistema processar a lista de `hashtags` do LLM THEN o sistema SHALL remover hífens internos, descartar tags na blocklist (`#fyp`, `#foryou`, `#foryoupage`, `#viral`, `#viralvideo`, `#reels`, `#shorts`, `#explore`), validar o padrão `^#[a-zA-Z0-9_À-ÿ]+$` e completar com o banco `NICHE_HASHTAGS` (canal + jogo/tema + `#cortes`) até garantir entre `3` e `5` hashtags únicas.
3. The sistema SHALL formatar `caption` com exatamente 3 linhas: linha 1 com o gancho do clipe, linha 2 com `Crédito: <canal> · Live completa: <url>` (seguido de ` · Parte k/N` quando `part_total > 1`), e linha 3 com as `3` a `5` hashtags separadas por espaço.
4. WHEN o LLM retornar `nota` (`0–10`) THEN o sistema SHALL gravar `llm_score` e `llm_reason` em `clips.json` e usar `llm_score` exclusivamente como critério de desempate relativo sem descartar nenhum clipe.

**Independent Test**: Executar `pytest tests/test_llm.py` testando rejeição de títulos com nomes próprios inventados ("Professor"), limpeza de hashtags com hífen (`#gta-rp` -> `#gtarp`), remoção de `#fyp`/`#viral` e formatação exata das 3 linhas da legenda com e sem `Parte k/N`.

---

### P2: Medição M3 no Piso Ryzen 5 5500U (Qwen3-1.7B e 4B em Clipes Reais)

**User Story**: Como engenheiro validando o orçamento do piso 5500U, quero medir em container a velocidade (`tok/s` e `s/clipe`), o pico de RSS e a qualidade de `titulo`/`hashtags` do `Qwen3-1.7B-Q4_K_M` (e `4B` se presente) nos 8 clipes reais do teste.

**Why P2**: Fecha a medição obrigatória M3 do CONSOLIDADO §7 diretamente no Ryzen 5 5500U.

**Acceptance Criteria**:

1. WHEN a suíte de medição M3 (`tests/test_bench_m3_llm.py`) executa no container sobre os 8 clipes reais de `UBv07VGeD24` THEN o sistema SHALL verificar para o `Qwen3-1.7B-Q4_K_M`: geração `tg >= 10.0 tok/s`, pico de RSS `<= 2.5 GB`, tempo médio `<= 20.0s` por clipe, `100%` de saídas JSON válidas, `3..5` hashtags válidas por clipe sem `#fyp` e `0` nomes próprios alucinados.

**Independent Test**: Rodar `docker compose run --rm clipes pytest tests/test_bench_m3_llm.py -v -s`.

---

## Edge Cases

- IF a transcrição do clipe estiver vazia (`clip.text == ""`, clipe selecionado por silêncio/grito) THEN o sistema SHALL pular a chamada ao LLM para esse clipe, manter `generate_title(clip, words)` e preencher `caption` e `hashtags` com o banco determinístico do canal.
- IF o LLM devolver menos de 3 hashtags válidas após a remoção de `#fyp`/`#viral` THEN o sistema SHALL preencher as vagas faltantes usando `NICHE_HASHTAGS` (`#<canal>`, `#<tema_ou_jogo>`, `#cortes`, `#livesbr`).
- WHERE a flag `--no-llm` for passada na CLI o sistema SHALL não iniciar `llama-server`, definir `llm_score = None` e gerar `caption` e `hashtags` via template determinístico + `NICHE_HASHTAGS`.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| LLM-01 | P1: Liberação do Whisper e subprocesso `llama-server` por lote | Design | Pending |
| LLM-02 | P1: Geração de título `<= 60` chars e filtro anti-alucinação | Design | Pending |
| LLM-03 | P1: Validação de 3–5 hashtags sem `#fyp`/hífen + banco por nicho | Design | Pending |
| LLM-04 | P1: Legenda de 3 linhas e `llm_score` 0–10 como desempate | Design | Pending |
| LLM-05 | P2: Medição M3 (`Qwen3-1.7B`/`4B` no Ryzen 5 5500U em 8 clipes reais) | Design | Pending |

**Coverage:** 5 total, 5 mapped to tasks, 0 unmapped

---

## Success Criteria

- [ ] Whisper e LLM nunca coexistem na RAM; RSS máximo durante a etapa `llm` fica `<= 2.5 GB` com `Qwen3-1.7B-Q4_K_M`.
- [ ] `100%` dos clipes em `clips.json` possuem `title <= 60` chars, `caption` de 3 linhas e `3..5` hashtags casando com `^#[a-zA-Z0-9_À-ÿ]+$` sem `#fyp`/`#viral`/`#reels`.
- [ ] Medição M3 executada no container no Ryzen 5 5500U com todos os critérios numéricos aprovados.
