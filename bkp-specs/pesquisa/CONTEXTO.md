# Contexto comum (clipes-lives)

Projeto: ~/Projetos/clipes-lives (Python, Docker). Pipeline atual: ingest (yt-dlp) -> sinais 1Hz (loudness, chat, heatmap) -> picos -> faster-whisper small int8 so nas janelas candidatas -> snap de bordas -> render ffmpeg 1080x1920 (fundo borrado + video 16:9 centralizado + legenda ASS word-highlight). Leia README.md, docs/teste-real.md e clipes/*.py antes de qualquer coisa.

## PISO DE HARDWARE (restricao dura, vale para tudo)
Ryzen 5 5500U, 12 threads, 9 GB RAM, SEM GPU. Container Docker: 6 GB RAM, 6 threads. Tudo gratuito, sem chave de API, offline depois de baixar modelos. Alto volume de videos: throughput e RAM importam. Modelos pesados rodam SEQUENCIAIS (nunca Whisper e LLM juntos na RAM). ASR atual: ~703s para 24 min de audio (small int8).

## Decisoes do usuario
1. IA de texto (titulo, legenda, hashtags, nota 0-10): modelo LOCAL leve que caiba no piso acima. Pluggable (interface simples) so se custar quase nada; padrao local.
2. Twitch nesta rodada: detectar, gravar e enfileirar. SEM publicar (Instagram/Discord ficam para depois).
3. Assunto longo: dividir em partes de 45-60s ("Parte 1/N"), cortes em borda de frase, gancho no fim de cada parte. Assunto ate ~3 min.
4. Clipe precisa de credito (canal + link da live) e encerramento (nao pode terminar em corte seco).
5. Problema a resolver: clipes saem incompletos, cortando o assunto no meio (janela max 55s centrada no pico em snap.py).

## Ideia de fluxo Twitch (do usuario)
Pico no chat (6+ msgs em 10s) -> clipe -> processamento (legenda + vertical) -> IA avalia nota 0-10 -> fila (1 post a cada 45 min) -> publicacao. Monitorar todas as lives de uma categoria pela API da Twitch.
Nossa critica: o Create Clip da Twitch gera clipe curto ja cortado (conflita com decisao 5); preferimos gravar nos canais com pico (buffer rolante para pre-roll) e cortar nos nossos limites. Limiar fixo 6/10s nao escala entre streamers pequenos e grandes: usar pico relativo ao baseline do canal (ja existe logica em clipes/signals.py::signal_chat).

## Regras
- Nunca acessar banco de dados. Sem push, sem deploy, sem publicar nada. Nada de segredos em arquivos.
- NAO fabricar: tudo que for fato de plataforma (limites, APIs, ToS) precisa de fonte (URL) e data; o que nao achar, marque "nao verificado".
- Responder em pt-BR.
