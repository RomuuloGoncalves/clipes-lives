# Pesquisa B: Twitch (API, monitor de categoria, gravacao, direitos)
Use busca na web (docs oficiais dev.twitch.tv primeiro). Entregue em pesquisa/B-twitch.md:
1. Como listar lives de uma categoria (Helix Get Streams por game_id), autenticacao necessaria (app token, gratis?), rate limits.
2. Chat: IRC anonimo (justinfan) vs EventSub; quantos canais simultaneos cabem sem problema; limites de join; como medir pico relativo (taxa por 10 s vs baseline por canal) e como evitar bots/flood.
3. Gravacao: streamlink vs yt-dlp vs ffmpeg para live Twitch; buffer rolante em segmentos (ffmpeg segment) com pre-roll; consumo de CPU/disco/banda no piso 6 GB/6 threads (quantas gravacoes simultaneas realistas?); como evitar anuncios/pre-roll injetados.
4. Create Clip API: limites (duracao, escopo, token de usuario) e por que/ quando usar como complemento (link oficial, credito).
5. Direitos e ToS: regras da Twitch para republicar clipes de terceiros em outras plataformas, DMCA/musica, o que exige credito/permissao; riscos praticos para esse tipo de bot. Nao e aconselhamento juridico, so o que as fontes dizem.
6. Termine com desenho sugerido do "monitor" (processos, filas, estado em disco, limites de concorrencia) pequeno e simples para o piso de hardware.
