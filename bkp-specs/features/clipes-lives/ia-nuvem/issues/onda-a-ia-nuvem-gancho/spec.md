# Onda A: IA em nuvem, nome do clipe, descrição longa e gancho no topo

## Problem Statement

Os títulos do Qwen3-1.7B local saem fracos (eco do título da live, nota sempre 8.0) e o nome do arquivo (`clip_01.mp4`) não diz nada. Falta uma descrição longa para YouTube/TikTok e falta o gancho curto no topo do vídeo, que o LiveCliper usa para segurar os primeiros segundos. Esta onda troca a fonte principal de texto por o Gemini no free tier (cascata de modelos e chaves, respeitando cotas), mantendo o Qwen local como fallback offline e a mesma validação anti-alucinação. Tudo gratuito: nenhuma API paga, nenhum Cloud Billing (decisão do usuário, rodada 3).

## Goals

- [ ] Título com fonte `cloud` em >= 80% dos clipes de uma rodada com chaves válidas e rede (medido no `clips.json`).
- [ ] Nenhum título, gancho ou descrição com nome próprio ou número ausente da transcrição (validadores existentes passam em 100% dos aceitos).
- [ ] Todo clipe tem `clip_NN_<slug>.mp4`, descrição longa e gancho de topo nos primeiros 5 s.
- [ ] Custo zero: nenhuma chamada a API paga; par (modelo, chave) com cota diária esgotada não é chamado de novo no mesmo dia.

## Out of Scope

| Feature | Reason |
| ------- | ------ |
| Publicar em rede social | Onda C (publicar-redes) |
| Escolher picos/bordas com a nuvem | AD-001 continua: picos vêm de sinais medidos; IA só escreve texto e nota |
| SDKs `google-genai` / `anthropic` | REST com `urllib` basta (2 endpoints); evita dependência e rebuild |
| Tradução / outros idiomas | Público PT-BR |
| Faixa borrada atrás do gancho (`boxblur` extra, como no LiveCliper) | Topo já é preto ou borrado; texto com contorno basta e custa zero CPU |
| Código do LiveCliper | Repositório sem licença: só inspiração de design, nenhuma cópia |
| APIs pagas (Claude, Gemini com billing) | Rodada 3: 100% gratuito |
| Enviar áudio ou vídeo à nuvem | Só texto (transcrição do clipe, título da live, canal) sai da máquina |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --------------------- | -------------- | --------- | ---------- |
| Privacidade: transcrição sai da máquina | Nuvem só liga com chave no env e `CLIPES_LLM_BACKEND` != `local`; o README e o log da rodada avisam que o texto foi enviado | Reverte "100% local" (AD-008); usuário pediu nuvem | n |
| Ordem da cascata | Gemini free tier (para cada modelo de `CLIPES_GEMINI_MODELS`, todas as chaves de `GEMINI_API_KEYS`) → Qwen local (sessão lazy) → heurística | Rodada 3: 100% gratuito, sem Claude | y |
| Gemini Free Tier e privacidade | Só Free Tier, sem Cloud Billing; no Free Tier o Google pode usar prompts e respostas para treino (aceito pelo usuário) | Rodada 3 | y |
| Nomes padrão dos modelos | `CLIPES_GEMINI_MODELS=gemini-2.5-flash,gemini-2.5-flash-lite` | IDs mudam; ficam só em env com default; 404 conta como falha do par | n |
| Cotas do free tier | Par que responder 429 `RESOURCE_EXHAUSTED` fica esgotado até a virada do dia em horário do Pacífico (reset de cota diária do Gemini; a confirmar na doc) em `$OUT_DIR/.gemini_cota.json`; 429 de limite por minuto só tira o par da rodada atual; pausa mínima `CLIPES_GEMINI_MIN_INTERVAL_S=4` entre chamadas da mesma chave | Free tier tem RPM e RPD baixos (relatório A 2.2); não martelar a API | n |
| Formato do gancho | <= 35 caracteres, sem emoji, pergunta ou promessa sem entregar o desfecho, 0 a 5 s, `\an5\pos(540,365)`, até 2 linhas de <= 18 caracteres (linha 1 branca, linha 2 amarela), fonte 64, em caixa alta | Relatório A seção 4: faixa livre Y=260..475 (UI do TikTok ocupa 0..240; selo em 490; crédito em 600) | n |
| Nome do arquivo | `clip_NN_<slug>.mp4`; slug ASCII minúsculo, `[a-z0-9-]`, <= 40 chars, derivado em Python do título final (nunca pedido ao LLM); multipartes `clip_NN_partKofN_<slug>.mp4` | Pedido item 6; mantém o prefixo `partKofN` atual | n |
| ID estável do clipe | `clip_id = <video_id>_c<NN>` (sanitizado) no `clips.json` | Usado pelo Discord e pelo `publicar <id>` (Onda C) | n |
| Descrição longa | LLM gera `descricao_longa`: 2 a 4 frases, 150 a 500 caracteres, sem URL; o sistema compõe o texto final: título, parágrafo, `Crédito: <canal>`, `Live completa: <url>` (+ `Parte k/N`), hashtags | Relatório A seção 3.2; texto final cabe no YouTube (5000) e no TikTok (2200) | n |
| Fallback da descrição longa | Parágrafo determinístico = primeiras frases completas da transcrição do clipe (<= 320 chars), mesma composição final | Clipe nunca sai sem descrição | n |
| Arquivos de chave | Chaves só por env vindo de arquivo fora do repo (`~/.config/clipes-lives/segredos.env`, modo 600, carregado pelo `clipar` com `set -a`) | Regra: segredos fora do git | n |

**Open questions:** none - all resolved or logged above (required before the spec is confirmed).

---

## User Stories

### P1: Cascata de IA em nuvem com fallback local ⭐ MVP

**User Story**: Como dono do canal de cortes, quero que título, gancho, descrição e nota venham do Gemini free tier, com o Qwen local como reserva, para ter textos melhores sem perder a execução offline.

**Why P1**: Todos os outros itens desta onda consomem o texto gerado aqui.

**Acceptance Criteria**:

1. WHEN `enrich_clips_with_llm` rodar com `CLIPES_LLM_BACKEND=auto` e `GEMINI_API_KEYS` definido THEN o sistema SHALL tentar os pares (modelo, chave) na ordem de `CLIPES_GEMINI_MODELS` x `GEMINI_API_KEYS` antes de qualquer outro provedor.
2. IF uma chamada ao Gemini responder HTTP 429, 500, 503, timeout ou JSON fora do schema THEN o sistema SHALL passar ao próximo par (modelo, chave) sem repetir o par que falhou na mesma rodada.
3. IF um par responder 429 com `RESOURCE_EXHAUSTED` de cota diária THEN o sistema SHALL gravar o par como esgotado em `$OUT_DIR/.gemini_cota.json` e não chamá-lo de novo até a virada do dia da cota.
4. IF todos os pares estiverem esgotados THEN o sistema SHALL registrar `[llm] cota gratuita do Gemini esgotada` uma vez por rodada e seguir para o Qwen local sem chamar a rede.
5. IF nenhum provedor de nuvem responder com JSON válido THEN o sistema SHALL usar o Qwen local (`llama_server_session`) e, se ele falhar, o fallback determinístico atual.
6. WHERE `CLIPES_LLM_BACKEND=local` ou nenhuma chave de nuvem estiver definida the system SHALL fazer zero requisições de rede para provedores de IA.
7. The system SHALL aplicar `validate_title`, `is_title_echo` e `validate_hook` à saída de qualquer provedor, e gravar `title_source` como `cloud:gemini:<modelo>`, `llm` (local) ou `heuristic_fallback`.
10. The system SHALL não ter código nem variável para API paga (sem Claude, sem Cloud Billing).
8. The system SHALL nunca gravar chave de API em log, `clips.json`, `README.md` ou mensagem de exceção (chaves mascaradas como `***`).
9. WHEN a rodada usar um provedor de nuvem THEN o sistema SHALL registrar no `README.md` da rodada a linha `Texto da transcrição enviado a: <provedores>`.

**Independent Test**: Com `urlopen` falso que devolve 429 no 1º par e JSON válido no 2º, `enrich_clips_with_llm` grava `title_source="cloud:gemini:<modelo2>"`; sem chaves, o fake registra zero chamadas.

---

### P1: Título e nome de arquivo com qualidade ⭐ MVP

**User Story**: Como dono do canal, quero títulos que falem do momento (não da live) e arquivos `clip_NN_<slug>.mp4` para achar o clipe pelo nome.

**Why P1**: Pedido item 6; o nome do arquivo é usado pelo Discord e pela publicação (Onda C).

**Acceptance Criteria**:

1. WHEN o título final do clipe for definido THEN o sistema SHALL gerar `slug` = título sem acento, minúsculo, com não alfanuméricos trocados por `-`, sem `-` repetido ou nas pontas, cortado em <= 40 caracteres na última fronteira de palavra.
2. WHEN o render gravar o clipe THEN o sistema SHALL nomear o arquivo `clip_NN_<slug>.mp4` (ou `clip_NN_partKofN_<slug>.mp4` com `part_total > 1`) e usar o mesmo `stem` para `.txt`, `.descricao.txt` e `.ass`.
3. IF o slug resultar vazio THEN o sistema SHALL usar `clip_NN.mp4` (comportamento atual).
4. IF já existir arquivo com o mesmo nome na pasta da rodada THEN o sistema SHALL sobrescrever apenas quando `NN` for o mesmo índice (re-render da mesma rodada).
5. The system SHALL pedir ao provedor um título de <= 60 caracteres sobre o conflito, revelação, reação ou pergunta do trecho, rejeitado por `is_title_echo` quando repetir o título da live.
6. WHEN o manifesto for gravado THEN o sistema SHALL incluir em cada clipe `clip_id = <video_id>_c<NN>`, com `video_id` passado por `sanitize_id`.

**Independent Test**: `slugify("Ele NÃO acreditou no que viu?!")` == `"ele-nao-acreditou-no-que-viu"`; render com fake ffmpeg grava `clip_03_ele-nao-acreditou-no-que-viu.mp4`.

---

### P2: Descrição longa

**User Story**: Como dono do canal, quero uma descrição longa pronta para colar no YouTube/TikTok.

**Why P2**: Útil na publicação (Onda C), mas o clipe existe sem ela.

**Acceptance Criteria**:

1. WHEN o provedor devolver `descricao_longa` com 150 a 500 caracteres, sem URL, que passe em `validate_no_hallucinated_entities` THEN o sistema SHALL usá-la como parágrafo e marcar `description_source="llm"`.
2. IF `descricao_longa` faltar, sair do intervalo, tiver URL ou falhar na validação THEN o sistema SHALL usar como parágrafo as primeiras frases completas da transcrição do clipe (<= 320 caracteres) e marcar `description_source="fallback"`.
3. The system SHALL compor a descrição final como título, linha vazia, parágrafo, linha vazia, `Crédito: <canal>`, `Live completa: <url>` (com ` · Parte k/N` quando `part_total > 1`), linha vazia, hashtags, e gravá-la em `<stem>.descricao.txt` e em `clips.json` (`description_long`).

**Independent Test**: Fake com `descricao_longa` de 120 caracteres gera `description_source="fallback"`; com 300 caracteres válidos gera `llm` e o arquivo termina pelas hashtags.

---

### P2: Gancho no topo do clipe

**User Story**: Como espectador, quero ler nos primeiros segundos uma frase curta que diga por que assistir.

**Why P2**: Melhora retenção; o clipe funciona sem ele.

**Acceptance Criteria**:

1. WHEN o provedor devolver `gancho_topo` com 1 a 35 caracteres, sem emoji, que passe em `validate_no_hallucinated_entities` THEN o sistema SHALL usá-lo como gancho do clipe (`hook_top`).
2. IF `gancho_topo` for inválido THEN o sistema SHALL usar o título final cortado em <= 35 caracteres na fronteira de palavra, ou nenhum gancho se o título também tiver mais de 35 caracteres sem fronteira útil.
3. WHEN `build_ass` receber `hook_top` não vazio THEN o sistema SHALL emitir um evento de estilo `TopHook` de 0,00 s até `min(5,0, duração do conteúdo)` com `\an5\pos(540,365)` e `\fad(120,350)`.
4. The system SHALL quebrar o gancho em caixa alta em no máximo 2 linhas de <= 18 caracteres separadas por `\N`, com a linha 1 em `&H00FFFFFF&` e a linha 2 em `&H0000FFFF&`, fonte 64.
5. WHERE `CLIPES_HOOK_TOP=0` the system SHALL renderizar sem o evento `Hook`.
6. WHILE o gancho estiver na tela the system SHALL mantê-lo entre Y=260 e Y=475, sem sobrepor o selo `Parte k/N` (Y=490) nem o crédito (Y=600).

**Independent Test**: `build_ass(..., hook_top="Ele perdeu tudo?")` escreve uma linha `Dialogue: 3,0:00:00.00,0:00:05.00,TopHook,...` com `\pos(540,365)` e `ELE PERDEU TUDO?`.

---

## Edge Cases

- IF o clipe tiver menos de 5 s de conteúdo THEN o sistema SHALL encerrar o gancho no fim do conteúdo.
- IF a resposta da nuvem vier com `<think>` ou bloco markdown ```json THEN o sistema SHALL removê-los antes do `json.loads`.
- IF `.gemini_cota.json` estiver corrompido THEN o sistema SHALL tratá-lo como vazio, reescrevê-lo e registrar aviso.
- WHEN dois clipes gerarem o mesmo slug THEN o sistema SHALL manter os dois arquivos distintos pelo `NN`.
- IF o texto do provedor trouxer `{`, `}` ou `\` THEN o sistema SHALL escapá-los com `_ass_escape` antes de ir para o ASS.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| -------------- | ----- | ----- | ------ |
| NUV-01 | P1: Cascata (AC 1, 2, 6) | Tasks | Pending |
| NUV-02 | P1: Cascata (AC 3, 4, 10) cotas do free tier e custo zero | Tasks | Pending |
| NUV-03 | P1: Cascata (AC 5, 7) fallback + validação | Tasks | Pending |
| NUV-04 | P1: Cascata (AC 8, 9) segredos e aviso de privacidade | Tasks | Pending |
| NOME-01 | P1: Título e arquivo (AC 1, 3) slug | Tasks | Pending |
| NOME-02 | P1: Título e arquivo (AC 2, 4) nome no render | Tasks | Pending |
| NOME-03 | P1: Título e arquivo (AC 5) prompt de título | Tasks | Pending |
| NOME-04 | P1: Título e arquivo (AC 6) `clip_id` estável | Tasks | Pending |
| DESC-01 | P2: Descrição longa | Tasks | Pending |
| GANCHO-01 | P2: Gancho (AC 1, 2) escolha do texto | Tasks | Pending |
| GANCHO-02 | P2: Gancho (AC 3 a 6) render ASS | Tasks | Pending |

**Coverage:** 11 total, 11 mapped to tasks, 0 unmapped.

---

## Success Criteria

- [ ] Rodada real no Podpah (ASR em cache) com chaves válidas: >= 80% `title_source` começando por `cloud:`, 0 títulos ecoando a live.
- [ ] Mesma rodada com `CLIPES_LLM_BACKEND=local`: zero requisições externas (log) e mesmos arquivos gerados com fonte `llm`/`heuristic_fallback`.
- [ ] Todo `.mp4` da rodada segue `clip_NN_<slug>.mp4` e tem `.descricao.txt`.
