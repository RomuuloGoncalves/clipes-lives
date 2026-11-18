# Contexto: decisões do usuário e pontos em aberto

## Já decidido (não reabrir)

- Projeto pessoal, 100% local, CPU sem GPU, tudo grátis (free tier só sem cartão e com limite documentado).
- Entrada: link de live do YouTube ou da Twitch (Kick se der), ao vivo ou VOD, sem login quando possível.
- Saída: clipes 9:16 1080×1920 legendados com título sugerido. Sem publicação automática. Painel opcional, última fase.
- Conteúdo: hype GTA VI, reações fortes, sustos, comédia, momentos marcantes.
- Requisito central: clipe com sentido (começo e fim naturais, contexto completo).
- A spec original (streamlink + Whisper API + LLM + FFmpeg + React + publisher) era só conceito.

## Mudanças propostas em relação à spec original

| Original | Proposta | Motivo |
|---|---|---|
| Whisper API | faster-whisper local int8 | API é paga |
| LLM detecta os momentos | **Sinais (chat, áudio, heatmap, palavras) detectam; LLM só ajusta borda e título** | Transcrição sozinha não vê grito, susto nem chat explodindo; LLM pequeno em CPU é lento e alucina timestamps |
| streamlink para tudo | yt-dlp para VOD, streamlink só para live | yt-dlp traz heatmap, chat do YouTube e download de trechos |
| Baixar o vídeo inteiro | Áudio + chat inteiros, vídeo só dos trechos | 90%+ menos banda e disco |
| Pipeline de live separado | Live = gravação + pipeline de VOD em janela deslizante | Um código só |
| React | Pasta + `index.html` estático; Gradio na última fase | Um usuário local |
| Publisher | Removido | Fora de escopo |

## Pontos em aberto (perguntar ao usuário)

Cada um tem um default que vale até a resposta.

1. **Streamers e VODs de referência.** Quais canais você quer clipar? Preciso de 5 VODs (≥ 2 da Twitch com clipes de viewers) para medir a qualidade e calibrar emotes e palavras-chave. *Default:* escolho VODs públicas de GTA VI em pt-BR.
2. **Quantidade de clipes.** Até quantos clipes por VOD? *Default:* até 8, só os que passam o limiar.
3. **LLM remoto.** Aceita Gemini ou Groq free tier como opção (mais rápido e melhor em pt-BR, mas a transcrição sai da máquina e o free tier pode usar os dados para treino)? *Default:* só Ollama local, remoto desligado.
4. **Layout.** Os streamers alvo usam facecam? Prefere facecam em cima e jogo embaixo (`split`) ou o vídeo inteiro com fundo borrado (`blur`)? *Default:* automático, `split` se achar rosto fixo.
5. **Idioma.** Só pt-BR ou também streamers gringos? Título sempre em pt-BR? *Default:* detecção automática, legenda no idioma falado, título em pt-BR.
6. **Tempo de processamento.** Aceita ~30–60 min de CPU por 1 h de VOD, com o notebook ocupado? *Default:* sim; o benchmark (T3) dá o número real.
7. **RAM.** A máquina tem ~9 GB, com ~3 GB livres agora. Dá para fechar o navegador e outros apps durante o processamento? *Default:* Whisper e LLM rodam em sequência, `batch_size` pequeno.
8. **Live.** Um atraso de 5–10 min entre o momento e o clipe é aceitável? *Default:* sim, janela deslizante de 5 min.
9. **Kick.** O chat da Kick tem endpoint público (achado do agy). Kick é prioridade ou fica no fim? *Default:* suportar, com chat se o endpoint funcionar.
10. **Conta da Twitch.** Se o chat anônimo (`justinfan`) parar de funcionar, aceita criar um app gratuito na Twitch (conta, sem cartão)? *Default:* não; a live da Twitch segue sem chat.
11. **Base de código.** O sharkyclips (MIT, 7★, 3 semanas de vida, app grande com React e publicação, sem pt-BR na UI) já faz quase tudo para VOD. Se o spike T0 for bom, prefere usar ele como motor e só acrescentar pt-BR + live (menos código, mas depende de um projeto novo de um autor só), ou um pipeline próprio enxuto copiando os módulos MIT dele? *Default:* decidir pelo resultado do T0, preferindo o wrapper.
