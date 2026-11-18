---
produto: clipes-lives
feature: fila-distribuida
repos: [clipes-lives]
issues: [onda-b-fila-mongo-dashboard]
status: planejada
tags: [mongodb, atlas, lease, painel, vercel, capacidade, vigia-temp, live-continua]
relacionadas: [monitor-twitch, discord-publicacao]
---

# fila-distribuida

## Resumo
Vários PCs coordenados pelo MongoDB Atlas M0: um dono por live (lease de 90 s, heartbeat de 20 s), jobs com `_id` determinístico por janela, janelas `lost` reatribuídas sem regerar clipes. Cada PC detecta capacidade (cota de CPU, RAM, disco, temperatura, GPU) e ajusta limites; o limite de lives contínuas vem de medição (`bench-live`). Painel na Vercel (Hobby) com Basic Auth e botão "liberar recursos" (coleção `commands`). Vigia térmico corrigido (hwmon `k10temp`). `clipar --ate-acabar` clipa a live em janelas de 10 min a cada 5 min até ela acabar.

## Onde vive
- `clipes-lives`: `clipes/coord.py`, `clipes/capacidade.py`, `clipes/janelas.py`, `clipes/perfil.py` (novos), `clipes/monitor.py`, `clipes/cli.py`, `clipes/asr.py`, `clipes/topics.py`, `clipes/llm.py`, `painel/` (novo), `vigia-temp`, `clipar`, `entrypoint.sh`, `Dockerfile`, `docker-compose.yml`.

## Decisões que valem
- AD-010 (Mongo só coordena; vídeo e `DiskQueue` locais; agentes nunca acessam o banco), AD-011 (painel na Vercel, usuário de banco mínimo, comando liberar), AD-013 (GPU na Onda D), AD-014 (vigia por hwmon, sem sensor só avisa).

## Issues
- `issues/onda-b-fila-mongo-dashboard/` (spec, design com desenho da Onda D, tasks): Onda B.
