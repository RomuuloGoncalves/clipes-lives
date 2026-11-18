---
produto: clipes-lives
feature: ia-nuvem
repos: [clipes-lives]
issues: [onda-a-ia-nuvem-gancho]
status: planejada
tags: [llm, gemini, free-tier, titulo, slug, descricao, gancho, ass]
relacionadas: [legenda-hashtags, creditos-encerramento, discord-publicacao]
---

# ia-nuvem

## Resumo
Título, gancho de topo, descrição longa, hashtags e nota passam a vir do Gemini free tier (cascata modelo x chave, respeitando cotas), com o Qwen3-1.7B local como fallback offline e a mesma validação anti-alucinação. O clipe ganha nome `clip_NN_<slug>.mp4`, `clip_id` estável, `.descricao.txt` e um gancho de até 35 caracteres no topo nos primeiros 5 s (ASS `TopHook`, y=365).

## Onde vive
- `clipes-lives`: `clipes/cloud_llm.py` (novo), `clipes/llm.py`, `clipes/render.py`, `clipes/cli.py`, `clipes/models.py`, `docker-compose.yml`, `clipar`.

## Decisões que valem
- AD-008 (Gemini free tier + Qwen local; texto da transcrição sai da máquina, aceito) e AD-012 (100% gratuito).
- AD-001 continua: a IA só escreve texto e nota; picos vêm de sinais medidos.

## Issues
- `issues/onda-a-ia-nuvem-gancho/` (spec, design, tasks): Onda A.
