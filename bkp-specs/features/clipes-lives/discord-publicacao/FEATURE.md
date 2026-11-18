---
produto: clipes-lives
feature: discord-publicacao
repos: [clipes-lives]
issues: [onda-c-discord-publicar]
status: planejada
tags: [discord, bot, tiktok, youtube-shorts, oauth, publicacao]
relacionadas: [ia-nuvem, fila-distribuida]
---

# discord-publicacao

## Resumo
Cada job concluído vira uma thread no canal do Discord com 1 mensagem por clipe (prévia recomprimida se passar do limite de upload). `publicar <id>` ou `/publicar` (só admins listados) publica no YouTube Shorts (`private`) e no TikTok (`SELF_ONLY`, conta privada) até a auditoria dos apps; respeita `policy.json`, idempotência por clipe/plataforma e cotas diárias.

## Onde vive
- `clipes-lives`: `clipes/bot.py`, `clipes/discord_rest.py`, `clipes/previa.py`, `clipes/oauth.py`, `clipes/publicar_youtube.py`, `clipes/publicar_tiktok.py`, `clipes/publicacao.py` (novos), `clipes/monitor.py`, `clipes/__main__.py`, `Dockerfile`, `docker-compose.yml`.

## Decisões que valem
- AD-009 (publicação só por comando, privada até auditoria; reverte AD-006) e AD-012 (100% gratuito).

## Issues
- `issues/onda-c-discord-publicar/` (spec, design, tasks): Onda C.
