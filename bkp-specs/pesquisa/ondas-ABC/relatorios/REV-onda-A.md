# REV-onda-A: revisão independente da Onda A (branch onda-a, `git diff master...HEAD`)

**Veredito: FAIL** (1 teste vermelho no gate quick; 0 achados de segurança; ressalvas de spec/teste abaixo).

## Evidência de gate
`docker compose run --rm -v clipes -v tests test pytest -q tests/test_cloud_llm.py tests/test_models.py tests/test_llm.py tests/test_render_eval.py tests/test_cli_llm.py` → **69 passed, 1 failed** (87 s).
Falha: `tests/test_render_eval.py::test_cli_skip_render_writes_part_metadata_in_manifest_and_readme` (`assert 'clip_01_part1of2_parte-da-historia-completa-aqui0.mp4' == 'clip_01_part1of2.mp4'`).
Suíte global não rodada (instrução). Sem acesso a banco, sem push, sem edição de código.

## Achados

### 1. [teste/regressão] BLOQUEANTE: teste pré-existente do `--skip-render` ficou com o nome antigo
- `tests/test_render_eval.py:253`: `assert manifest["clips"][0]["file"] == "clip_01_part1of2.mp4"`.
- A spec (Título e arquivo AC2) manda `clip_NN_partKofN_<slug>.mp4`; o código (`clipes/render.py:105-111`, `cli.py` skip-render) já gera o slug. O T10 só atualizou o caminho de `tests/test_cli_llm.py`; este segundo caminho do `--skip-render` ficou vermelho. Os testes em `test_cli_llm.py` foram atualizados corretamente (nome novo, não afrouxado).
- Sugestão: trocar a asserção por `"clip_01_part1of2_parte-da-historia-completa-aqui0.mp4"` (valor derivado da spec: slug do título), não por `startswith`. Rodar de novo o gate.

### 2. [spec] `wrap_top_hook` descarta palavras em silêncio (`clipes/render.py:~138-156`)
- Gancho válido de até 35 chars pode não caber em 2 linhas de 18 (ex.: 3 palavras longas); o ramo `lines.append(cur); if len(lines)>=max_lines: break` joga fora a palavra que estourou, e palavra > 18 é cortada (`word[:max_chars]`). O espectador vê gancho truncado ("ELE PERDEU\nTUDO"). Spec GANCHO-02 AC4 só limita linhas, mas AC1 aceita 35 chars.
- Sugestão: em `pick_hook_top`, rejeitar/encurtar o gancho que não cabe em 2x18 pelo mesmo wrap (ou cair no título cortado), e testar um caso de 35 chars com palavras longas.

### 3. [spec] Timeout e nome da variável divergem do design (`clipes/cloud_llm.py:57-62`)
- Design: `CLIPES_CLOUD_TIMEOUT=20`; código usa `CLIPES_LLM_TIMEOUT` com padrão 60 s. Sem rede, cada par custa até 60 s (2 modelos x N chaves x clipes) antes do fallback local; "rodada mais lenta, sem erro" do design vira minutos.
- Sugestão: variável própria com padrão 20 s, ou registrar a decisão no design.

### 4. [bug leve] Arquivo de cota ignora `--out-dir` (`clipes/cloud_llm.py:72-74`)
- Usa só `OUT_DIR` do ambiente (no Docker é `/out`, ok via `clipar`). Com `--out-dir` diferente e sem `OUT_DIR`, grava em `./out/.gemini_cota.json` relativo ao cwd e a cota diária não persiste entre rodadas do mesmo diretório de saída.
- Sugestão: o CLI passa `out_root` ao módulo (ou define `CLIPES_GEMINI_COTA_FILE` a partir dele).

### 5. [bug leve] `description_source="fallback"` sem descrição (`clipes/cli.py`, bloco de `description_long`)
- Se a transcrição do clipe for vazia, `description_long` fica `""` mas `description_source` vira `"fallback"`; o manifesto indica uma descrição que não existe. (`llm._apply_fallback` já trata: fonte só com parágrafo? não: também marca `fallback` com texto vazio.)
- Sugestão: deixar `description_source=""` quando não há parágrafo.

### 6. [teste] Lacunas de cobertura (não bloqueiam)
- NOME-02 AC4 ("sobrescrever só com o mesmo NN"): há teste de re-render do mesmo índice; título diferente no re-render deixa o `.mp4` antigo órfão (comportamento aceito pela spec, só vale registrar).
- NUV-04 AC8: chave fora de log/`clips.json`/README está testada (`test_cli_llm.py:429,438`, `test_cloud_llm.py::test_log_nao_vaza_chave`); a asserção contra `.gemini_cota.json` existe (`test_valor_da_chave_nunca_no_arquivo`). OK.
- `_is_daily_quota` depende de "PerDay"/"per day" no corpo do 429; o teste usa `GenerateRequestsPerDayPerProjectPerModel` (formato real do `quotaId`, conforme a doc). Se o Google mudar o texto, cota diária vira 429 por minuto (par só sai da rodada, volta a chamar na próxima): degrada para custo de chamadas, não para erro. Registrar no design.

### 7. [nota] Design usa `responseSchema`, código usa `responseJsonSchema` (`cloud_llm.py:134`)
- Campo válido no REST atual do Gemini (aceita JSON Schema com `maxLength`). Atualizar o design para refletir; sem ação no código.

## Verificação por critério (spec → teste → resultado esperado pela spec)
| Req | Resultado |
|---|---|
| NUV-01 (ordem modelo x chave, backend local = 0 chamadas) | `test_ordem_modelo_x_chave`, `test_backend_local_zero_chamadas`, `test_sem_chave_zero_chamadas`: asserções sobre `fake.calls`, independentes da implementação. OK |
| NUV-02 (429 diário, por minuto, todos esgotados, JSON corrompido, virada do dia) | 6 testes de cota com tempo injetado e `.gemini_cota.json` real. OK. Reset à meia-noite do Pacífico (`cloud_llm.py:77-79`) coerente com a spec (a confirmar na doc, como a spec diz) |
| NUV-03 (nuvem→local→heurística, validação do título da nuvem) | `test_enrich_cloud_ok_never_opens_local_session` e cascata com contadores. OK |
| NUV-04 (segredos, README) | chave só no header `x-goog-api-key`, nunca na URL (`cloud_llm.py:140`, `test_chave_so_no_header`); `mask()` em todo log de exceção; nenhum `print` de chave; `.gitignore` ganhou `*.env`; `clipar` carrega `segredos.env` e avisa se modo != 600; `docker-compose.yml` sem valores. OK |
| NOME-01/02/04 | `slugify`, `clip_stem`, render com fake. OK (ver achado 1 para o caminho `--skip-render` restante) |
| NOME-03 | prompt testado por palavras-chave (`conflito`, `sem emoji`...) e `is_title_echo` aplicado à saída da nuvem. OK |
| DESC-01 | llm/fallback, 150-500, URL, entidade inventada, composição final exata (`test_long_description_validation_fallback_and_format`). OK |
| GANCHO-01/02 | `pick_hook_top`, evento `TopHook` com `\pos(540,365)`, 5 s / 3 s, opt-out `CLIPES_HOOK_TOP=0`, 2 linhas branca/amarela. OK, ressalva do achado 2 |

## Alterações em testes pré-existentes (item 3)
- `tests/test_llm.py`, fixture `complete()` (servidor fake): só acrescentou `gancho_topo` e `descricao_longa` à resposta; necessário porque `complete()` agora exige as chaves `required` (`llm.py:355-358`). Não afrouxou asserção. OK.
- `tests/test_cli_llm.py`: `clip_01.txt` → `clip_01_<slug>.txt` em 2 testes, com asserções novas a mais (clip_id, descricao, hook). Não afrouxou. OK.
- `tests/test_render_eval.py:253`: NÃO atualizado (achado 1).
- Commit T18: não pertence a esta onda e não está no diff (`git log master..HEAD` lista só T1-T10).
- `max_tokens` do `complete()` local subiu 300→600 (`llm.py:315`): justificado pelo schema maior; nenhum teste fixava 300.

## Privacidade e custo zero
Só texto da transcrição, título da live e canal saem (nenhum áudio/vídeo); nuvem só com chave e backend != `local`; `README.md` ganha "Texto da transcrição enviado a: gemini" só quando houve resposta da nuvem (testado nos dois sentidos); nenhuma variável/código de API paga.

## O que falta para PASS
Corrigir o achado 1 (obrigatório) e rodar de novo o gate dos 5 arquivos. Achados 2-5 recomendados antes do merge; 6-7 informativos.

FIM
