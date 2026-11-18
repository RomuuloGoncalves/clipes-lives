# Pesquisa A: o que faz clipes de live performarem (TikTok, Reels, Shorts)

Data da pesquisa: 2026-11-17. Somente leitura + web. Nenhum código alterado.
**Aviso geral:** quase tudo abaixo vem de blogs/fornecedores de ferramentas (OpusClip, AutoClip, Adaptly etc.), não de documentação oficial. Só duas coisas têm fonte oficial/primária: limite de 3 min do Shorts (blog.youtube) e, via imprensa, anúncio do Instagram. Números de "retenção/engajamento" são indicativos, sem metodologia publicada. Onde não achei: "não verificado".

## 1. Duração e séries "Parte 1/2/3"

| Plataforma | Máximo hoje | Faixa recomendada (fontes de terceiros) |
|---|---|---|
| YouTube Shorts | 3 min, só vertical/quadrado (oficial) | 15-60 s p/ feed; ver nota |
| TikTok | máx. 10 min nas contas em 2026 (1 fonte, não verificado oficialmente); Creator Rewards exige >60 s (fontes secundárias) | 21-34 s = maior completion (62% vs 48% em >60 s, dados próprios do OpusClip, sem metodologia); 31-60 s costuma dar mais watch time total |
| Instagram Reels | 3 min (anúncio jan/2025) | 90 s recomendado por parte das fontes; oficial: não verificado |

Fontes:
- Shorts 3 min, 15/10/2024, "square or taller": https://blog.youtube/news-and-events/tall-updates-coming-to-shorts/ (lido 2026-11-17).
- Reels 3 min (Mosseri, jan/2025): https://macmagazine.com.br/post/2025/01/20/instagram-ganha-reels-de-ate-3-minutos-e-grade-de-posts-retangulares/ (2025-01-20); Metricool https://metricool.com/instagram-reels-length/ (não aberto, só resultado de busca).
- TikTok length/completion: https://www.opus.pro/blog/tiktok-length-format-retention-data (publicado 2025-11-11). "TikTok não recompensa duração, recompensa retenção": resumo de busca em https://www.jeffbullas.com/thread/whats-the-ideal-tiktok-video-length-for-maximum-reach-now/ (não aberto).
- TikTok 10 min e Creator Rewards >1 min: só resultados de busca de blogs (metricool.com/tiktok-video-length, ttcalculator.net); não achei página oficial TikTok: **não verificado**.

Implicação para decisão 3 (partes de 45-60 s): cai na faixa "31-60 s" (mais watch time) e abaixo do corte de 60 s do Creator Rewards só se for <=60 s; para elegibilidade a Rewards seria preciso >60 s (TikTok, fonte secundária). Para Shorts/Reels 45-60 s é seguro. Assunto de ~3 min em uma peça única também é permitido nas 3 plataformas.

**Séries "Parte N":** não achei dado quantitativo (OpusClip diz explicitamente que não cobre; "cada clipe deve funcionar sozinho, com seu próprio gancho e payoff" — mesma fonte). Efeito de "Parte 1/2" na distribuição: **não verificado**. Boa prática (opinião, não dado): cada parte autossuficiente, rótulo "Parte 1/N" visível no título/legenda, gancho de fim = pergunta aberta ou consequência anunciada ("o que ele fez depois..."), e a Parte 1 como a mais forte.

## 2. Hook dos primeiros 3 s (clipe de live)

Fonte principal: AutoClip, publicado 2026-04-26, atualizado 2026-11-16: https://autoclip.dev/blog/viral-clip-anatomy-3-second-hook-data
- Maioria das saídas ocorre antes do 3º segundo; o gancho é "um filtro". Cortar 2 s de diferença pode mudar mais o resultado que trocar de momento.
- Tipos: **mid-sentence entry** (começar no meio da fala; menor variância, bom p/ podcast/live), **consequência visível primeiro** (gaming/esporte), **tensão declarada** (afirmação discutível), "cold weirdness" (alto teto, erra muito).
- Aberturas fracas: title card, logo, zoom lento em quem vai falar, linha de "setup" que apresenta a história em vez de começar nela.
- Legenda palavra-a-palavra no 1º segundo segura mais que bloco de frase; evitar emoji/efeito/texto gigante no 1º segundo; estilo consistente entre clipes.
- OpusClip (2025-11-11): pergunta sobre dor específica +23% retenção (dado próprio, sem método); entregar "quick win" em 10 s; texto na tela >=2 s e que agregue algo além da fala.
- Retenção "80%+ nos 3 s": benchmarks conflitantes; nenhuma afirmação oficial YouTube/TikTok de que os 3 s são "o" sinal dominante: **não verificado**.
- Busca em PT-BR (Flow/Podpah): nada específico achado; consenso geral de blogs: começar sem introdução, afirmação/contradição/"não esperava isso" (resumo de busca, sem URL de página aberta).

## 3. Legenda embutida e safe zones

**Não existe safe zone oficial publicada** por TikTok/Instagram/YouTube que eu tenha achado. Fontes de terceiros divergem muito (bottom 250 a 480 px em 1080x1920):
- TikTok (Adaptly, 2026): área central 900x1492; bloco inferior 320 px, direito 120 px: https://adaptlypost.com/blog/social-media-safe-zones-2026-complete-guide
- TikTok (ScreenSnap): topo 120, direita 150, base 250. (Moda): topo 130, base 480, direita 150. (PostPlanify): ~960x1386. Todos via resumo de busca de 2026-11-17.
- Reels: "topo 15%, base 10%" (um guia, sem fonte oficial): **não verificado**.
- Shorts: base ~300 px, área ~984x1500 (um guia); kapwing tem overlay: https://www.kapwing.com/video-editor/youtube-shorts/safe-zone
- Reap.video (artigo de 2026-09-07) admite que não há template permanente e propõe margens conservadoras para as 3 plataformas: laterais 108-162 px, topo ~288 px, base 480-672 px (derivado por mim dos %: 25-35% da altura): https://reap.video/blog/short-form-video-safe-zones

**Leitura para o nosso ASS:** nosso estilo é DejaVu Sans 72, Bold, outline 5, shadow 2, alinhamento 2 (centro-baixo), MarginV=430, margens L/R 70 (clipes/render.py:118-119), 3 palavras por cue, destaque amarelo palavra a palavra. 430 px de base cobre TikTok mais permissivo (250-320) e Shorts (~300), mas fica **abaixo** do conservador do Reap (480-672) e do Moda (480). Margem L/R 70 é menor que os 96-190 px de botões à direita citados pelo BlitzCut. Texto centralizado costuma ficar longe do rail direito, mas linhas largas podem invadir.
- BlitzCut (publicado 2026-06-06, atualizado 2026-11-11), https://blitzcutai.com/blog/best-caption-size-youtube-shorts-2026: corpo 60-75 px (65 sugerido), karaokê 70-85 px, entretenimento peso Bold 700, 1-3 palavras por vez no word-by-word, largura <=~850 px, posição ~700-1000 px a partir da base (isto é, mais alto que o nosso 430; terço central-inferior), longe da direita (96-190 px) e do topo (120-290 px). Nosso 72 px e 3 palavras estão dentro da faixa.
- AutoClip: se a legenda esconde o payoff visual (ex.: jogada), subir/remover. Relevante para live de jogo com webcam.
- Fonte oficial/estudo comparando DejaVu Sans vs Montserrat/Poppins: **não verificado**.

## 4. Texto do post (caption/descrição/hashtags)

- **TikTok:** limite de descrição 2.200 caracteres (docs da Content Posting API, citado por blogs; fontes que dizem 4.000 não citam doc oficial: contestado). Hashtags contam no limite; sem teto oficial achado; recomendação comum 3-5 (não verificado oficialmente). Fonte: resultado de busca incl. https://blog.hootsuite.com/social-media-updates/tiktok/tiktoks-video-descriptions-can-now-be-up-to-2200-characters-long/ (2026-11-17, não aberto).
- **Instagram:** teto de **5 hashtags** por post/Reel (reportado em 2025-12-19, atribuído ao @Creators; matéria diz que alguns viram 3): https://www.businesstoday.in/technology/news/story/no-more-hashtag-stuffing-instagram-limits-hashtag-usage-to-just-5-per-reel-and-post-507444-2025-12-19. Fontes conflitam (3 vs 5); página oficial do Instagram: **não verificado**. Mosseri diz que hashtags "não aumentam alcance"; descoberta vem de palavras-chave na legenda, texto na tela e conteúdo (a mesma matéria; a parte de "palavras-chave" é interpretação do artigo).
- **YouTube Shorts:** hashtags 3-5 no fim da descrição; #Shorts opcional hoje (blogs, sem fonte oficial: https://subscribr.ai/youtube-strategy/youtube-shorts-hashtags-virality e https://www.opus.pro/blog/youtube-shorts-caption-subtitle-best-practices, resultados de busca). Título é o campo com mais peso para busca; limite oficial de 100 caracteres do título do YouTube **não verificado nesta pesquisa**. Metadados são secundários ao conteúdo no feed.
- **#fyp:** nenhuma evidência achada de que #fyp/#foryou melhore distribuição: **não verificado**.
- Estrutura sugerida (minha, sem fonte): 1ª linha = gancho/promessa com palavra-chave (aparece truncada no feed), 2ª = crédito (canal + link da live, decisão 4), no fim 3-5 hashtags: 1-2 de nicho/streamer/jogo, 1 de formato (#cortes), no máx. 1 genérica.

### Exemplos reais de legendas de clipes de sucesso (pedido: 8-12)
**Não verificado: 0 exemplos reais coletados.** Tentativas: busca web por títulos/legendas de cortes de Flow/Podpah/streamers pt-BR não retornou nenhuma página com legendas reais; YouTube/TikTok/Instagram exigem JS/login e `WebFetch` em `youtube.com/@Cortes_do_Flow/shorts` deu 404. Para não fabricar, não incluo exemplos inventados. Próximo passo: usar a extensão Claude in Chrome nos canais de cortes (ver pergunta em aberto no resumo) para copiar 10 títulos/legendas reais com hashtags e views.
Único padrão de nomenclatura achado (TheTVDB, títulos de episódio Flow): "NOME DO CONVIDADO + Flow #NN" (https://cavac.at/cavacopedia/Flow%20Podcast aparece em busca, não validado). Serve só como indício de que convidado/assunto vai no título.

## 5. Formato visual

- Nenhum teste controlado achado comparando fundo borrado vs. layout dividido. Fornecedores (Eklipse, fluxnote, OpenClip) dizem que facecam em cima + jogo embaixo é o mais comum em clipes de streamer; "fluxnote: facecam visível = +45% engajamento", sem método: não verificado (busca 2026-11-17).
- Crítica à letterbox/blur puro: sub-usa a tela; a alternativa é preencher a faixa com webcam/legenda. Reddit r/Twitch: zoom forte em webcam pequena fica borrado por baixo bitrate/resolução (https://reddit.birdcat.cafe/r/Twitch/comments/1il9byn/why_is_my_webcam_blurry_in_vertical_clips, 2025-02-09).
- Ritmo de cortes: nenhum número confiável achado: **não verificado**. Regra qualitativa: reenquadrar/zoom leve quando muda o falante; legenda como principal "movimento".
- Consequência técnica p/ nós: só funciona recortar webcam do 16:9 se a posição for estável (detecção de região, custo CPU baixo, ex.: recorte fixo configurável por canal) ou se o video 16:9 inteiro permanecer (nosso estado atual).

## 6. Recomendações acionáveis para o nosso pipeline (impacto/esforço, melhor primeiro)

1. **Começar o clipe em "mid-sentence"/consequência**: ajustar início para a 1ª palavra da frase-gancho (já temos palavras do Whisper), sem intro de silêncio >0,3 s. Esforço baixo, impacto alto (AutoClip).
2. **Encerramento/gancho por parte**: terminar na borda de frase + 0,5-1 s de respiro; em partes 1..N-1, sobrepor texto "Parte 2 ▶" nos últimos 2 s. Baixo esforço, atende decisão 4.
3. **Subir a safe zone**: MarginV 430 → ~520 e MarginL/R 70 → ~130 (conservador entre fontes); configurável por constante. Esforço trivial. Validar visualmente em preview, pois não há spec oficial.
4. **Texto na tela "Parte N/M" + título curto nos 3 primeiros segundos** (ASS, camada separada, na zona superior segura ~300-420 px do topo). Baixo esforço. Cuidado: AutoClip desaconselha title card longo; manter <=1 linha.
5. **Descrição com crédito + 3-5 hashtags por plataforma** gerada por template (sem LLM no início): linha-gancho, "Fonte: <canal> <link>", hashtags (IG <=5; TikTok/Shorts 3-5). Baixo esforço; LLM local só para a linha-gancho depois.
6. **Duração por parte 45-60 s** com alvo ~40-55 s (OpusClip: 31-60 s dá mais watch time), mas mantendo assunto completo; para Shorts/Reels aceitar até ~3 min quando indivisível. Já alinhado à decisão 3.
7. **Legenda: manter word-highlight, 2-3 palavras, fonte mais pesada/larga (ex.: Montserrat/Poppins Bold, empacotada na imagem) e tamanho 65-80**; evitar efeitos fortes no 1º segundo. Esforço baixo (instalar fonte no Dockerfile).
8. **Layout: A/B de 2 variantes** (A: atual borrado+16:9; B: 16:9 maior + faixa de legenda/webcam recortada) em canais reais e comparar retenção, já que não há evidência pública do vencedor. Esforço médio, impacto incerto.
9. **Coletar 10-20 exemplos reais de legendas/hashtags** de canais de cortes via Chrome (tarefa de pesquisa, não do pipeline) para calibrar o template do item 5. Esforço baixo; preenche a lacuna da seção 4.
10. **Evitar depender de #fyp/#shorts e de hashtags genéricas**; priorizar palavras-chave do assunto no título/1ª linha (Mosseri: hashtags não aumentam alcance; Shorts: #Shorts opcional). Esforço zero, economiza caracteres.

## Lacunas / não verificado (resumo)
Safe zones oficiais; limites oficiais TikTok (10 min, 2.200 vs 4.000 chars, Rewards >1 min); número oficial de hashtags do Instagram (3 vs 5); efeito de séries "Parte N"; ritmo de cortes; exemplos reais de legendas de canais BR; evidência de que #fyp ajuda.
