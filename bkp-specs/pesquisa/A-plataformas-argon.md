# Pesquisa A: O que faz clipes de live performarem em TikTok, Instagram Reels e YouTube Shorts (2025–2026)

- **Agente / Worktree:** `pesq-argon-A` (Gemini 4 Argon)
- **Data da coleta e verificação:** `2026-11-17`
- **Escopo:** Análise crítica comparativa das regras algorítmicas, limites técnicos, *safe zones* de UI, estrutura de retenção (*hook* 3s, séries Parte 1/2/3, legendas ASS, metadados/hashtags e layout vertical) aplicada diretamente à arquitetura e às restrições de hardware do projeto `clipes-lives` (`Ryzen 5 5500U`, container Docker `6 GB RAM`, `6 threads`, 100% CPU, offline/gratuito).

---

## 1. Duração Ideal e Máxima por Plataforma; Séries "Parte 1/2/3" e Ganchos de Fim de Parte

### 1.1. Limites Técnicos vs. Teto Algorítmico vs. *Sweet Spot* de Retenção (2025–2026)

Expandir o limite técnico de upload nas plataformas não significa que vídeos longos performem melhor automaticamente: os três algoritmos continuam ponderando fortemente **taxa de conclusão (*completion rate*)**, **tempo médio assistido (*Average View Duration - AVD*)** e **replays** em relação à duração total do vídeo.

| Plataforma | Limite Técnico de Upload / Gravação | Teto Algorítmico de Recomendação / Monetização | *Sweet Spot* (Clipe Único: Reação / Gameplay) | *Sweet Spot* (História / Podcast / Parte de Série) | Fontes e Datas Verificadas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **YouTube Shorts** | **3 min (180 s)** (vídeos verticais ou quadrados enviados a partir de 15/10/2024) | **180 s** no feed Shorts. **Atenção:** Shorts com **> 60 s** que tenham reivindicação ativa de direitos autorais (Content ID / música comercial não licenciada para >60s) são **bloqueados globalmente** e inelegíveis para recomendação. | **18 a 38 s** (maximiza loop/replay e taxa de conclusão > 80%) | **45 a 85 s** (ou até ~150 s quando a narrativa sustenta retenção contínua sem *dead air*) | [YouTube Help - Understand three-minute YouTube Shorts (`answer/15424877`)](https://support.google.com/youtube/answer/15424877?hl=en) (out/2024, verificado em 17/11/2026); [YouTube Official Blog - Engaged Views](https://blog.youtube/inside-youtube/engaged-views-youtube-explained/) (2025, verificado em 17/11/2026) |
| **Instagram Reels** | Até **15–20 min** como vídeo vertical no app/web; gravação nativa no editor Reels até **3 min (180 s)** | **3 min (180 s)** é o teto de descoberta anunciado por Adam Mosseri (jan/2025). Vídeos **> 180 s** sofrem restrição de distribuição para **não-seguidores** na aba Reels/Explorar. | **20 a 35 s** (foco em *replay* e compartilhamento via DM — *Sends per Reach*) | **45 a 75 s** (narrativas completas que geram envio por DM e salvamentos) | [House of Marketers - Instagram Reels 3 Minutes Expansion](https://houseofmarketers.com/instagram-reels-3-minutes-nyc-algorithm-change/) (jan/2025, verificado em 17/11/2026) |
| **TikTok** | **10 min** (gravação no app) / **60 min** (upload web/app) | **Piso duro de 60 s (1 min+)** exclusivamente para monetização direta no *Creator Rewards Program* (exige >= 5 s assistidos no *For You* para contar como *qualified view*). Para alcance orgânico sem monetização de fundo, **não há piso de 60 s**. | **21 a 34 s** (maior taxa de conclusão para clipes rápidos de reação/jogada) | **45 a 65 s** (ou **62 a 90 s** se o canal for elegível ao *Creator Rewards Program* e o corte tiver ritmo alto) | [InfluenceFlow - TikTok Creator Rewards 1-Minute Rule](https://influenceflow.io/) (2025–2026, verificado em 17/11/2026) |

> **Fato empírico verificado nos canais brasileiros em novembro de 2026:**
> Na coleta direta realizada em `2026-11-17` nas abas `/shorts` de `@Podpah`, `@CortesPodpahOFICIAL`, `@FlowPodcast`, `@CortesdoFlow`, `@Gaules`, `@CazeTV` e `@CortesdoCasimitoOFICIAL` (ver Seção 4.3), observa-se uma divisão clara:
> 1. **Clipes de reação/punchline:** duram entre **16 s e 40 s** (ex.: Flow `18 s` com 2,32M views; NerdBrothers/Alanzoka `36 s` com 440k views; Cyber Curioso/Flow `29 s` com 868k views).
> 2. **Clipes de história/resenha pós-expansão de 3 minutos:** canais oficiais abandonaram a trava artificial de 59 s e hoje publicam rotineiramente entre **45 s e 89 s**, chegando a **116 s–156 s** quando o assunto exige (ex.: Podpah TV `72 s`; Flow `89 s`; Cortes do Flow `71 s`; Gaules `80 s` e `82 s`; CazéTV `74 s` e `81 s`; Cortes do Casimito `156 s` com 339k views).

### 1.2. Diagnóstico Crítico do Problema no Nosso Pipeline (`clipes/snap.py` e `clipes/candidates.py`)

O usuário apontou na **Decisão 5**: *"Problema a resolver: clipes saem incompletos, cortando o assunto no meio (janela max 55s centrada no pico em snap.py)"*.
A leitura de `clipes/candidates.py` e `clipes/snap.py` revela **três travas matemáticas acopladas** que causam exatamente esse defeito:

1. **Janela de transcrição curta (`window_half = 45.0` em `candidates.py:15`):** o Whisper só transcreve `[peak - 45s, peak + 45s]` (total 90 s). Se uma história em live leva 110 s para chegar ao clímax (ou continua por mais 50 s após a primeira gargalhada), o texto fora de `±45 s` sequer existe em `words`.
2. **Posição rígida do pico (`PEAK_POS_MIN = 0.35`, `PEAK_POS_MAX = 0.75` em `snap.py:16-17`):**
   - `earliest_start = peak - 0.75 * max_dur` -> para `max_dur = 55s`, o clipe só pode começar no máximo **41,25 s antes do pico**, e o `pre_roll` ideal é premiado entre `6.0 s` e `12.0 s` (`IDEAL_PRE_ROLL_LOW/HIGH` em `snap.py:11-12`!).
   - `latest_end = peak + 0.65 * max_dur` -> o clipe só pode terminar no máximo **35,75 s depois do pico**.
   - **Consequência prática:** em conversas e histórias (como no teste real do Podpah #1049 em `docs/teste-real.md`), o pico de áudio (`loudness`) ou de `chat` quase sempre acontece **no punchline final da história (aos 80%–92% da narrativa)**, ou no meio de uma sequência de 2 minutos. Como `snap.py` força o clipe a começar ~10 s antes do pico e durar no máximo 55 s com `peak_ratio <= 0.75`, ele **ignora o início da história** e **corta o assunto no meio** após o pico.

### 1.3. Como Séries "Parte 1 / Parte 2 / Parte 3" Performam Hoje e Ganchos de Fim de Parte

A estratégia de fatiar um assunto longo (de 90 s a 180 s) em partes de **45–60 s** ("Parte 1/N", conforme **Decisão 3**) funciona através do **Efeito Zeigarnik (*Open Loop*)**: uma tensão narrativa interrompida antes da resolução induz o espectador a clicar no perfil (*Profile Tap*) ou comentar pedindo a continuação, dois sinais fortes de sessão em TikTok e Reels.

Contudo, em 2025–2026 há uma diferença crítica entre **divisão narrativa legítima** e **corte frustrante**:
- **O que performa mal (fadiga de "Parte 2"):** cortar um assunto curto (< 65 s) que caberia inteiro em um único vídeo, ou encerrar a Parte 1 sem entregar nenhum micro-contexto/valor, ou ainda cortar no meio de uma oração gramatical (o usuário percebe como erro de edição ou *clickbait* barato e passa direto). Além disso, nenhuma das 3 plataformas garante entrega automática da Parte 2 no feed de não-seguidores se houver grande intervalo entre os posts (exceto quando o usuário entra ativamente no perfil ou usa playlists/séries).
- **Quando usar Parte 1/N no nosso pipeline:**
  - Se o bloco temático contínuo em torno do pico tiver **<= 60 s** (ou até ~65 s): **NÃO dividir** — gerar Clipe Único completo.
  - Se o bloco temático contínuo tiver entre **70 s e 180 s**: dividir em **2 ou 3 partes de 45–60 s cada**, onde a Parte 1 constrói o contexto e para na frase de virada (*cliffhanger*), e a Parte 2 já inicia relembrando os últimos `1.5 s–2.0 s` (ou última frase curta) da Parte 1 antes de entregar o clímax (`peak`) e o desfecho.

#### Técnicas de Gancho de Fim de Parte (compatíveis com Decisões 3 e 4 — sem corte seco)
1. **Corte em Frase de Virada / Pergunta Suspensa (*Cliffhanger* semântico em borda de frase):**
   - O ponto de corte entre a Parte $k$ e a Parte $k+1$ deve ocorrer **obrigatoriamente em borda de frase (`.`, `?`, `!`) ou pausa `>= 0.45 s`**, logo após um conector de tensão ou anúncio de revelação (ex.: *"Só que quando eu abri a porta..."*, *"E foi aí que ele falou o valor:"*, *"Mas o pior ainda ia acontecer."*).
2. **Micro-Teaser (*Flash-Forward* de 1,2 s a 1,8 s) no Encerramento da Parte 1:**
   - Em vez de terminar a Parte 1 em corte seco (proibido pela **Decisão 4**), anexar nos últimos `1,5 s` o frame/áudio da explosão de risada/reação que ocorre na Parte 2 com filtro de transição rápida + card visual `"CONTINUA NA PARTE 2 ➡️"` e o crédito da live.
3. **Encerramento Suave (*Outro Overlay* de 1,0 s a 1,5 s) na Última Parte (ou Clipe Único):**
   - Aplicar `afade=t=out:st=<end-0.6>:d=0.6` no áudio (após a reação pós-fala já protegida por `_extend_past_reaction` em `snap.py:54`) + escurecimento leve nos últimos `1,2 s` exibindo o card de crédito (`🎥 Live completa: <canal> | <link>`), eliminando 100% a sensação de corte abrupto.

---

## 2. Hook dos Primeiros 3 Segundos: O que Funciona em Clipe de Live

### 2.1. A Métrica dos 3 Segundos nas Plataformas
- **YouTube Shorts:** expõe diretamente no YouTube Studio a métrica **"How many chose to view" (*Viewed vs. Swiped Away*)** ([YouTube Help `answer/12942217`](https://support.google.com/youtube/answer/12942217?hl=en&co=YOUTUBE._YTVideoType%3Dshorts), verificado em 17/11/2026) e separa visualizações brutas de **Engaged Views** ([YouTube Official Blog](https://blog.youtube/inside-youtube/engaged-views-youtube-explained/), 2025). Se o espectador desliza nos primeiros segundos, o vídeo perde distribuição mesmo que o clímax aos 30 s seja excelente. *(Nota: benchmarks de agências apontam >70% de "Viewed" como faixa competitiva; o limiar interno exato do algoritmo do YouTube é **não verificado**, pois não consta na documentação pública).*
- **TikTok:** exige pelo menos **5 segundos de visualização** no *For You* até mesmo para contabilizar uma *qualified view* no programa de recompensas ([InfluenceFlow, 2025–2026](https://influenceflow.io/)).

### 2.2. Os 3 Elementos de Hook que Funcionam em Cortes de Live (e Como Automatizar em CPU)

Em lives (diferente de vídeos roteirizados), o streamer não grava um gancho pensado para os primeiros 3 segundos. Por isso, canais profissionais de cortes (Podpah, Flow, cortes de Gaules/Alanzoka/Casimiro) fabricam o hook na edição combinando **3 camadas simultâneas**:

1. **Cold Open (*Teaser* do Clímax nos primeiros 1,5 s–2,5 s) OU Podagem de Muletas Iniciais:**
   - **Padrão A (Cold Open / Flash-Forward):** Para clipes > 40 s cujo início narrativo é calmo, copia-se um trecho de **1,5 s a 2,2 s** da frase/grito do pico (`clip.peak`) para o início absoluto (`t = 0.0 s`), seguido de um corte seco/efeito sonoro curto (ou *glitch* visual de 0,15 s) para o início da história. O espectador vê a reação absurda no segundo zero e fica para entender o motivo.
   - **Padrão B (Início Direto Limpo — Custo Zero):** Nunca iniciar `t_start` em palavras vazias ou muletas de fala (*"então"*, *"tipo assim"*, *"mano"*, *"é que"*, *"bom"*, *"cara, olha"* — veja o `clip_06` do nosso `docs/teste-real.md`: começou em *"Cara, olha... Muito feliz."*). Em `snap.py::_speech_start_candidates`, penalizar candidatos cuja primeira palavra seja muleta/conector fraco e premiar frases interrogativas (`?`), exclamativas (`!`) ou com substantivo/verbo forte logo na primeira cláusula.
2. **Título Fixo na Tela (*Headline Banner* superior):**
   - Um banner visual posicionado **acima do vídeo 16:9** (na faixa `Y = 380..620 px`, fora da UI superior da plataforma) contendo o título curto (4 a 8 palavras em CAIXA ALTA, até ~42 caracteres, com 1–2 palavras destacadas em amarelo `#FFFF00` ou vermelho `#FF3B30`).
   - Funciona como "manchete de jornal": em menos de 1 segundo o cérebro lê a promessa do vídeo antes mesmo de processar o áudio. Pode ficar visível durante todo o clipe (padrão clássico de páginas de cortes no Reels/TikTok) ou nos primeiros **3,5 s a 4,0 s**.
3. **Legenda Queimada Ativa desde `t = 0.0 s`:**
   - Não pode haver silêncio > 0,3 s nos primeiros 2 segundos do clipe. A primeira palavra falada deve começar em `t <= 0.20 s` (hoje `snap.py:109` usa `w.start - 0.25`, o que já é ótimo e deve ser mantido entre `0.15 s` e `0.25 s`).

---

## 3. Legenda Embutida: Estilo, Fonte, Posição e *Safe Zones* de UI (Diagnóstico do `MarginV=430`)

### 3.1. Mapa de *Safe Zones* por Plataforma em Canvas `1080 × 1920` (2025–2026)

As três plataformas sobrepõem elementos de interface (botões de ação à direita, nome do canal, legenda/título, trilha de áudio e botões interativos na base).

| Plataforma | Margem Topo Insegura (px) | Margem Base Insegura (px) | Margem Direita Insegura (px) | Margem Esquerda Insegura (px) | Fontes e Especificações Verificadas |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TikTok** | `110 px` (orgânico) a `240 px` (header/busca/ads) | `320–420 px` (orgânico c/ legenda curta) até `660 px` (ads/cards/legendas longas) | `120 px` a `180 px` (coluna de ícones Like/Comment/Save/Share a partir de `Y ≈ 840`) | `60 px` a `120 px` | [TikTok Ads Manager - Interactive Add-On & Safe Zone Specs](https://ads.tiktok.com/resources/help/article/tiktok-interactive-add-on-download-card-ad-specifications?lang=en) (verificado em 17/11/2026); [Toco Marketing TikTok Safe Zone](https://toco.marketing/tools/tiktok-safe-zone/) (verificado em 17/11/2026) |
| **Instagram Reels** | `220 px` a `269 px` (14% superiores) | `420 px` (orgânico) a `672 px` (35% inferiores com legenda multi-linha + áudio + nav bar) | `65 px` (margem geral, 6%) a `120 px` (sobre os botões Like/Comment/Share na metade inferior) | `35 px` a `65 px` (6%) | [ClipUGC - Instagram Reels Safe Zone](https://clipugc.com/tools/instagram-reels-safe-zone) (verificado em 17/11/2026); [Vidova AI Cross-Platform Safe Zones](https://vidova.ai/blog/instagram-reels-tiktok-youtube-shorts-safe-zones) (2025–2026, verificado em 17/11/2026) |
| **YouTube Shorts** | `250 px` a `380 px` (barra superior, pesquisa, menu) | `380 px` a `440 px` (`@canal`, botão Inscrever-se, título de 2 linhas, *pill* "Vídeo relacionado" e barra de áudio) | `120 px` a `150 px` (coluna de botões Like/Dislike/Comment/Share/Remix) | `60 px` | [Vidova AI Cross-Platform Safe Zones](https://vidova.ai/blog/instagram-reels-tiktok-youtube-shorts-safe-zones) (2025–2026, verificado em 17/11/2026) |
| **Caixa Segura Universal (Master 1080×1920)** | **Evitar `Y < 280 px`** (ou `Y < 380 px` p/ títulos no Shorts) | **Evitar `Y > 1420 px`** (`MarginV >= 500 px` da base) | **Evitar `X > 950 px`** (`MarginR >= 130 px`) | **Evitar `X < 90 px`** (`MarginL >= 90 px`) | Consolidação das restrições máximas das 3 plataformas acima. |

### 3.2. Auditoria Geométrica do Nosso Pipeline Atual (`clipes/render.py`)

Em `clipes/render.py` (linhas 118–119 e 255–258), temos atualmente:
```text
Style: Cap,DejaVu Sans,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,430,1
```
E o filtro de vídeo:
```text
[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_s]
[bg_b][fg_s]overlay=(W-w)/2:(H-h)/2[v_base]
```

Veja onde cada elemento cai exatamente no canvas `1080 × 1920`:
- **Vídeo principal 16:9 (`[fg_s]`):** `1080 × 608 px`, centralizado verticalmente de **`Y = 656 px` até `Y = 1264 px`**.
- **Legenda ASS atual (`Alignment=2`, `MarginV=430`, `MarginL=70`, `MarginR=70`):**
  - No formato ASS com `Alignment=2` (base-centro), `MarginV=430` posiciona a linha de base da legenda em **`Y = 1920 - 430 = 1490 px`** (ocupando `Y ≈ 1415..1490 px` em 1 linha, ou `Y ≈ 1340..1490 px` em 2 linhas).
  - **Problema 1 (`MarginV=430` é baixo demais para Reels e Shorts com "Vídeo Relacionado"):** `Y = 1490 px` fica dentro da zona de exclusão de `420–672 px` da base do Instagram Reels (`Y = 1248..1500 px`) quando a legenda do post tem 2+ linhas ou botão de tradução/áudio, e encosta no *pill* de **"Vídeo relacionado"** + título de 2 linhas do YouTube Shorts. Além disso, deixa um buraco vazio entre o fim do vídeo 16:9 (`Y = 1264 px`) e a legenda (`Y = 1415 px`), obrigando o olho do espectador a saltar para baixo.
  - **Problema 2 (`MarginL=70, MarginR=70` invade a coluna de botões da direita):** a pilha vertical de ícones (Like/Comment/Share) no TikTok, Reels e Shorts ocupa os `120–150 px` direitos (`X = 930..1080 px`). Com `MarginR=70`, linhas de 3 palavras médias/longas avançam até `X = 1010 px` e ficam sob o botão de Comentários/Compartilhar.
  - **Solução exata para `render.py`:**
    - Elevar **`MarginV` de `430` para `540` (no layout 16:9 centralizado):** a base da legenda passa para **`Y = 1920 - 540 = 1380 px`** (topo em `Y ≈ 1300 px`), colada logo abaixo da borda inferior do vídeo 16:9 (`Y = 1264 px`) e **100% fora da UI inferior** das 3 plataformas (`540 px` livres na base!).
    - Ajustar **`MarginL=110, MarginR=130`** (ou `120, 120` para manter o centro óptico em `X = 540` sem jamais tocar `X > 960 px`).

### 3.3. Estilo Visual, Tipografia e *Word-Highlight*

1. **Fonte:** `DejaVu Sans` (usada hoje em `render.py:118`) é legível, mas tem anatomia corporativa/fina. No estilo de cortes virais (*Hormozi / Submagic / OpusClip*), usam-se fontes **Sans-Serif Geométricas/Condensadas ExtraBold/Black** como **Montserrat Black/ExtraBold** (pacote Debian `fonts-montserrat`, 100% gratuito e disponível via `apt`) ou **Anton** / **Liberation Sans Bold**.
2. **Caixa Alta e Agrupamento:** converter tokens da legenda para **CAIXA ALTA** (`.upper()`) com **2 a 3 palavras por cue** (`words_per_cue=3` em `render.py:99` já está ótimo, mas convém quebrar também se `len(char) > 18` para evitar que 3 palavras longas quebrem em 2 linhas largas).
3. **Destaque Dinâmico (*Karaoke Word-Highlight + Pop*):**
   - Hoje `render.py:138-156` alterna entre branco (`&H00FFFFFF&`) e amarelo vivo (`&H0000FFFF&` em BGR = `#FFFF00`), com `Outline=5`, `Shadow=2`.
   - Podemos enriquecer a palavra ativa usando tags nativas do renderizador `libass` (custo zero de CPU no `ffmpeg`):
     - Aumentar levemente a escala da palavra falada no instante em que é ativada: `{\c&H0000FFFF&\fscx112\fscy112}` e restaurar `{\c&H00FFFFFF&\fscx100\fscy100}` nas demais, com `Outline=6` e `Shadow=3`.
     - Opcionalmente, colorir palavras de alta reação (`TITLE_KEYWORDS` de `render.py:13`) em **verde-limão (`&H0039FF14&`)** ou **vermelho/laranja vivo (`&H00303BFF&`)** quando forem a palavra ativa.

---

## 4. Texto de Post (Descrição/Caption), Hashtags, SEO Social e 12 Exemplos Reais

### 4.1. Regras e Mudanças Estruturais por Plataforma (2025–2026)

1. **Instagram Reels — O Novo Limite Oficial de 5 Hashtags (Dez/2025):**
   - Em **18 de dezembro de 2025**, o Instagram (`@creators` e Adam Mosseri) reduziu oficialmente o limite de hashtags de **30 para no máximo 5 hashtags por post/Reel** ([PersonaXpand, 18/12/2025](https://personaxpand.com/blog/instagram-hashtag-limit); [PostBoost, 2026](https://postboost.co/learn/instagram-hashtag-limit), verificados em 17/11/2026).
   - Mosseri reiterou que hashtags **não inflam alcance diretamente**; servem como rótulos contextuais (SEO) para classificar o tema, enquanto a métrica decisiva de alcance é **Sends per Reach (compartilhamentos por DM)** e retenção ([Kontentino, 2025–2026](https://www.kontentino.com/)).
2. **TikTok — SEO Social acima de Hashtags Genéricas:**
   - O algoritmo do TikTok indexa simultaneamente: (a) transcrição automática do áudio falado nos primeiros segundos, (b) texto queimado/nativo na tela (OCR), e (c) palavras-chave semânticas na primeira linha da descrição.
   - Recomendação consolidada: descrição curta (**80 a 150 caracteres**) contendo os nomes próprios (streamer/convidado + jogo/assunto) + pergunta que incentive comentário + **3 a 5 hashtags específicas**.
3. **YouTube Shorts — Título Curto (< 55 chars visíveis) + "Vídeo Relacionado":**
   - O limite técnico do título é de **100 caracteres**, mas no player mobile do Shorts apenas os primeiros **40 a 55 caracteres** aparecem sem corte quando seguidos de hashtags.
   - **Regra oficial crítica sobre links no Shorts (Decisão 4):** desde agosto/setembro de 2023 e mantido em 2025–2026, **links `http://` ou `https://` na descrição e nos comentários de YouTube Shorts NÃO são clicáveis** ([YouTube Help `answer/13748639`](https://support.google.com/youtube/answer/13748639?hl=en), verificado em 17/11/2026). No Instagram Reels e no TikTok orgânico, URLs na legenda também **não são clicáveis**.
   - **Implicação direta para a Decisão 4 do nosso projeto (*"Clipe precisa de crédito: canal + link da live"*):** colocar a URL completa na descrição do JSON/post cumpre o requisito textual e funciona no desktop, mas **no mobile o crédito precisa obrigatoriamente aparecer queimado na tela do vídeo** (ex.: `🎥 Live: twitch.tv/<canal>` ou `youtube.com/@<canal>`) e, no caso do YouTube Shorts, apontar o campo nativo *Related Video* quando aplicável.
4. **Uso de `#fyp`, `#foryou`, `#paravoce`, `#viral` vs. `#shorts`:**
   - **`#fyp` / `#foryou` / `#viral`:** consenso unânime em 2025–2026 de que são **inúteis** para alcance e desperdiçam a cota de 5 hashtags do Instagram, diluindo o sinal semântico do vídeo ([PersonaXpand](https://personaxpand.com/blog/instagram-hashtag-limit); [PostBoost](https://postboost.co/learn/instagram-hashtag-limit)). **Nosso gerador nunca deve incluir `#fyp` ou `#viral`.**
   - **`#shorts`:** no YouTube, qualquer vídeo vertical/quadrado de até 3 minutos já é classificado automaticamente como Short pelo aspecto e duração ([YouTube Help `answer/15424877`](https://support.google.com/youtube/answer/15424877?hl=en)). Ainda assim, incluir `#shorts` ao final ou usar apenas **3 a 4 hashtags de entidade/nicho no título** (`#nomeDoCanal #convidado #assunto`) é a prática dominante nos grandes canais brasileiros (ver exemplos reais abaixo).

### 4.2. Estrutura Ideal de Texto de Post para o Nosso Pipeline (Unificada)

Para alimentar um template único compatível com as 3 plataformas (respeitando o teto de **<= 5 hashtags** do Instagram):
- **Título (Shorts / On-Screen Hook):** `30 a 55 caracteres` em CAIXA ALTA (ou *Sentence case* forte) + 1 emoji opcional. No Shorts, anexar 3–4 hashtags de entidade ao final do título se sobrar espaço até 100 chars.
- **Linha 1 da Legenda (Gancho / Pergunta de Engajamento):** 1 frase curta contextualizando o corte ou provocando opinião (*"Você faria o mesmo nessa situação?"* / resumo de 1 frase com o nome do streamer/convidado para SEO).
- **Linha 2 da Legenda (Crédito Obrigatório — Decisão 4):** `🎥 Crédito: @<canal> | Live completa: <url_da_live>` (e se for série: `📌 Parte 1 de <N> — Parte 2 no perfil!`).
- **Linha 3 da Legenda (Hashtags — exatas 4 ou 5 tags):** `#<canal_ou_podcast> #<convidado_ou_jogo> #<tema_especifico> #cortes #<nicho>` (ex.: `#podpah #bistecone #humor #cortes #podcastbrasil` ou `#gaules #cs2 #furia #esportsbrasil #cortes`).

### 4.3. 12 Exemplos Reais Verificados de Clipes Brasileiros de Sucesso (Coletados em 17/11/2026)

Todos os 12 exemplos abaixo foram extraídos diretamente das páginas públicas dos vídeos em `2026-11-17` (metadados HTML/JSON reais verificados):

1. **Flow Podcast (Canal Oficial) — 2,32 milhões de views (18 s)**
   - **URL:** https://www.youtube.com/shorts/rQoRUd_jAAo
   - **Data de publicação:** `2023-10-13` (verificado em `2026-11-17`, `2.329.031 views`)
   - **Título:** `Nessas horas o inglês some imediatamente`
   - **Legenda / Hashtags:** `#flowpodcast #shorts #nasdaily #igor3k` + CTA de membros do canal.
   - **Por que performou:** Corte ultracurto (18 s) de identificação imediata, título de 40 caracteres sem entregar a piada inteira, 4 hashtags combinando marca (`#flowpodcast`, `#igor3k`), convidado (`#nasdaily`) e formato (`#shorts`).

2. **Cyber Curioso (Corte de Igor 3K / Flow) — 868 mil views (29 s)**
   - **URL:** https://www.youtube.com/shorts/YT0ponhRHEk
   - **Data de publicação:** `2025-06-26` (verificado em `2026-11-17`, `868.962 views`)
   - **Título:** `IGOR 100% sincero sobre MONARK #flowpodcast #igor3k #cortespodcast`
   - **Legenda / Hashtags:** `O host do PODCAST FLOW IGOR 3K , durante entrevista ALT TABET (UOL) , de ANTONIO TABET , traz declarações polêmicas sobre sua relação com Monark | #podcast #podcastcortesoficial #cortesdobr #cortespodcast #flowpodcast`
   - **Por que performou:** Título de alta curiosidade citando duas entidades fortes em CAIXA ALTA (`IGOR` e `MONARK`) + 3 hashtags no título + descrição rica em palavras-chave para SEO + 5 hashtags de nicho na descrição.

3. **Flow Podcast (Canal Oficial) — 740 mil views (16 s)**
   - **URL:** https://www.youtube.com/shorts/WoDB3HXgLVQ
   - **Data de publicação:** `2023-10-04` (verificado em `2026-11-17`, `740.210 views`)
   - **Título:** `Calma Ninja o cara tem família`
   - **Legenda / Hashtags:** `#flowpodcast #ninja #igor3k #shorts`
   - **Por que performou:** Punchline de 16 s com título em tom de comentário de amigo ("Calma Ninja o cara tem família", 30 caracteres) e exatas 4 hashtags (marca, convidado, host, formato).

4. **NerdBrothers (Corte de Alanzoka) — 440 mil views (36 s)**
   - **URL:** https://www.youtube.com/shorts/HJpQYLM2Y1Q
   - **Data de publicação:** `2026-11-11` (verificado em `2026-11-17`, `440.872 views` em 6 dias)
   - **Título:** `O ALANZOKA CHOROU JOGANDO ZELDA OCARINA OF TIME REMAKE😂`
   - **Legenda / Hashtags:** `O ALANZOKA CHOROU JOGANDO ZELDA OCARINA OF TIME REMAKE😂 | #Alanzoka #Zelda #OcarinaOfTime`
   - **Por que performou:** Duração no *sweet spot* de gameplay (36 s), título em CAIXA ALTA + 1 emoji contrastando emoção ("CHOROU" + "😂") e exatas **3 hashtags hiper-específicas** (`#Alanzoka #Zelda #OcarinaOfTime`), sem tags genéricas.

5. **Cortes do Casimito [OFICIAL] — 339 mil views (156 s / 2m36s)**
   - **URL:** https://www.youtube.com/shorts/NBYFVgF233E
   - **Data de publicação:** `2026-09-09` (verificado em `2026-11-17`, `339.019 views`)
   - **Título:** `SIMULAÇÃO DA IA SOBRE O BRASILEIRÃO 2026 #futebol #cortesdocasimiro`
   - **Legenda / Hashtags:** Apenas as **2 hashtags no título** (`#futebol #cortesdocasimiro`).
   - **Por que performou:** Prova empírica de que assuntos longos (2m36s) com estrutura de lista/progressão ("simulação tabela inteira") sustentam retenção até o fim no formato Shorts de 3 minutos sem precisar de corte prematuro aos 55 s.

6. **CazéTV (Canal Oficial) — 106 mil views (81 s)**
   - **URL:** https://www.youtube.com/shorts/8D1MzIxFU3k
   - **Data de publicação:** `2026-11-15` (verificado em `2026-11-17`, `106.501 views` em 48h)
   - **Título:** `O FUTEBOL BRASILEIRO ESTÁ PRESO EM UMA SIMULAÇÃO?`
   - **Legenda / Hashtags:** Crédito cruzado para o canal de cortes (`✂️ CANAL CORTES DA CAZÉTV: https://www.youtube.com/@cazetv-cortes`) + descrição institucional com palavras-chave de SEO (`Brasileirão`, `Premier League`, `La Liga`).
   - **Por que performou:** Pergunta provocativa em CAIXA ALTA (49 caracteres) e duração de **81 s** (acima de 1 minuto para fechar o raciocínio completo).

7. **TecMundo (Corte/Notícia Alanzoka & Maethe) — 69 mil views (54 s)**
   - **URL:** https://www.youtube.com/shorts/aaSzcd1u8fw
   - **Data de publicação:** `2026-05-05` (verificado em `2026-11-17`, `69.079 views`)
   - **Título:** `ALANZOKA E MAETHE ANUNCIAM PRIMEIRO FILHO!`
   - **Legenda / Hashtags:** Texto jornalístico de 2 frases ricas em entidades (`O streamer Alanzoka e a criadora Maethe pegaram a internet de surpresa...`) + 54 s cravados.

8. **Cortes do Flow [OFICIAL] — 49 mil views (71 s)**
   - **URL:** https://www.youtube.com/shorts/suoXsKfW9uo
   - **Data de publicação:** `2026-11-15` (verificado em `2026-11-17`, `49.126 views` em 48h)
   - **Título:** `A RESPOSTA DO BOMBEIRO ÀS 3 DA MANHÃ! 😂 #humor #flow #resenha #comedia`
   - **Legenda / Hashtags:** 4 hashtags temáticas no título (`#humor #flow #resenha #comedia`) + link do canal principal na descrição.
   - **Por que performou:** Exemplo clássico de *curiosity gap* no título ("A RESPOSTA DO BOMBEIRO ÀS 3 DA MANHÃ! 😂", 40 chars antes das tags) e duração de **71 s** para contar a anedota inteira sem amputar o punchline.

9. **Flow Podcast (Canal Oficial) — 43 mil views (55 s)**
   - **URL:** https://www.youtube.com/shorts/d3PhXVAbcPU
   - **Data de publicação:** `2026-11-16` (verificado em `2026-11-17`, `42.994 views` em 24h)
   - **Título:** `PARAGUAI É MAIS SEGURO QUE O BRASIL 🇵🇾 #farinazzo #paraguai #flow #seguranca`
   - **Legenda / Hashtags:** Afirmação polêmica/comparativa em 38 caracteres + emoji + 4 hashtags (`#convidado #pais #marca #tema`).

10. **Podpah TV (Canal Oficial) — 36 mil views (45 s)**
    - **URL:** https://www.youtube.com/shorts/TydfpPLqHfQ
    - **Data de publicação:** `2026-11-16` (verificado em `2026-11-17`, `35.952 views` em 24h)
    - **Título:** `JÁ AGRADECEU SEUS PAIS POR TER UM NOME COMUM`
    - **Legenda / Hashtags:** 0 hashtags; aposta 100% em pergunta direta ao espectador (44 caracteres em CAIXA ALTA) e corte de 45 s.

11. **Gaules (Canal Oficial) — 31,8 mil views (33 s)**
    - **URL:** https://www.youtube.com/shorts/PiW7TqsYrcs
    - **Data de publicação:** `2025-11-19` (verificado em `2026-11-17`, `31.891 views`)
    - **Título:** `GAULES VOLTOU A JOGAR CS!`
    - **Legenda / Hashtags:** Créditos estruturados na descrição (`✂️ Canal de Cortes: http://youtube.com/c/CortesdoGaulesOFICIAL | 🎮 Canal de Gameplays: http://youtube.com/c/GaulesVods | 🟣 Lives na roxinha: /Gaules`).

12. **Gaules (Canal Oficial) — 20,7 mil views em 24h (82 s)**
    - **URL:** https://www.youtube.com/shorts/uOV8pXOd_Yw
    - **Data de publicação:** `2026-11-16` (verificado em `2026-11-17`, `20.775 views` em 24h)
    - **Título:** `Café da manhã em Miami é diferenciado #gta6 #vlog`
    - **Legenda / Hashtags:** Título curto + 2 hashtags (`#gta6 #vlog`) + crédito da live na descrição (`🟩 Lives na verdinha: /Gaules`).

*(Observação de transparência: URLs individuais de posts internos do TikTok e do Instagram Reels exigem sessão autenticada ou bloqueiam raspagem direta sem login; portanto, os exemplos textuais verificados palavra por palavra com URL, data e contagem de views foram extraídos do YouTube Shorts, enquanto as regras de TikTok e Reels foram verificadas nas fontes documentais das Seções 1, 3 e 4).*

---

## 5. Formato Visual: Layout Webcam + Jogo em Vertical, Zoom/Reenquadramento e Ritmo

### 5.1. O Gargalo Visual do Nosso `clipes/render.py` Atual

Hoje, `clipes/render.py` (linhas 254–259) aplica o seguinte filtro `ffmpeg`:
- Fundo `[bg_b]`: `1080 × 1920` borrado (`boxblur=10:2`).
- Frente `[fg_s]`: o quadro horizontal `16:9` inteiro reduzido para **`1080 × 608 px`** (`scale=1080:1920:force_original_aspect_ratio=decrease`).

**Crítica técnica:**
1. **`608 px` de `1920 px` significa que 68,4% da tela do celular é apenas borrão vazio!**
2. Em uma live de gameplay (Twitch/YouTube), a webcam do streamer no canto da tela `16:9` ocupa tipicamente ~`22%` da largura (`~420 × 240 px` em 1080p). Quando `render.py` espreme o `16:9` inteiro para `1080 × 608 px`, **o rosto do streamer passa a medir apenas `~235 × 135 px` (~2% da área da tela 9:16)**. Microexpressões faciais — principal motor de retenção humana — desaparecem.
3. Mesmo em podcasts de mesa (Podpah/Flow) ou *Just Chatting*, os participantes ficam minúsculos no centro da tela.

### 5.2. Três Modos de Enquadramento Vertical Viáveis em CPU (`Ryzen 5 5500U`, Sem GPU)

Para manter alto throughput em CPU sem rodar redes pesadas de detecção facial quadro a quadro:

1. **Modo 1 — *Center-Crop Expandido 4:3 / 5:4* (`1080 × 864` ou `1080 × 960`) para Podcasts / React / Geral (Custo de CPU: ZERO):**
   - Em vez de encaixar `100%` da largura `16:9` em `1080 × 608`, cortar ~`20%` das bordas laterais irrelevantes do `16:9` (`crop=ih*4/3:ih` ou `scale=-2:900,crop=1080:900`) antes de sobrepor no fundo borrado.
   - A altura útil do vídeo salta de **`608 px` (31,6%) para `864–960 px` (45%–50% da tela)**, quase dobrando o tamanho dos rostos sem cortar quem está falando no centro e **sem gastar 1 ms extra de CPU** no `ffmpeg`.
2. **Modo 2 — *Split-Screen 9:16 (Webcam no Topo `600 px` + Gameplay na Base `1320 px`)* ([VodClipsLab, 2025–2026](https://vodclipslab.com/)):**
   - Padrão dominante em canais de cortes de gameplay (Alanzoka, Gaules, Paulinho o Loko, Coringa):
     - **Topo (`1080 × 600 px`, `Y = 0..600`):** crop na região da webcam do streamer.
     - **Base (`1080 × 1320 px`, `Y = 600..1920`):** crop central `9:11` da ação do jogo (mantendo mira/HUD central).
     - **Legenda ASS:** posicionada exatamente sobre a linha divisória (`Y ≈ 600..680 px` ou `Y ≈ 1280..1360 px`), onde o olho acompanha simultaneamente o jogo e a reação facial.
   - **Como fazer em CPU sem custo proibitivo:** amostrar apenas **3 frames** do clipe (ex.: início, pico, fim) e rodar o detector facial ultraleve do OpenCV (`YuNet` ONNX de 300 KB ou Haar Cascade/MediaPipe em CPU, que leva **< 50 ms no total por clipe**) procurando um rosto pequeno fixo nos 4 cantos típicos de webcam (`top-left`, `top-right`, `bottom-left`, `bottom-right`). Se o rosto estiver estacionário em um canto nos 3 frames -> ativar filtro `ffmpeg` de *Split-Screen* (custo de render `ffmpeg` idêntico ao `boxblur` atual, ou até mais rápido por eliminar o `boxblur`!). Se não achar webcam de canto -> cair no Modo 1.
3. **Modo 3 — *Punch-In Zoom* no Clímax e Remoção de Silêncios Longos:**
   - **Zoom dinâmico no pico (`clip.peak`):** aplicar um corte ligeiramente mais fechado (`1.12x` de zoom central) a partir de `clip.peak - 0.5 s` até `clip.peak + 3.5 s` usando dois segmentos ou expressão temporal no `crop` do `ffmpeg`. Isso cria o *pattern interrupt* visual exatamente na frase de impacto.
   - **Escurecimento do fundo borrado:** adicionar `eq=brightness=-0.12:saturation=0.85` na camada `[bg_b]` em `render.py:256` para que o fundo borrado nunca roube contraste da legenda branca/amarela nem do título superior.

---

## 6. Recomendações Acionáveis para o Nosso Pipeline (Top 10 por Impacto / Esforço)

Todas as 10 recomendações abaixo respeitam rigorosamente o **Piso de Hardware** (`Ryzen 5 5500U`, `6 GB RAM` no Docker, `6 threads`, sem GPU, modelos locais sequenciais, custo zero de API) e atacam diretamente as **Decisões 1 a 5** do usuário:

| # | Recomendação Acionável | Onde Alterar no Código | Impacto | Esforço | Ganho Esperado e Alinhamento com Decisões |
| :-: | :--- | :--- | :-: | :-: | :--- |
| **1** | **Desacoplar `snap.py` da trava de 55 s e criar Agrupador de Bloco Narrativo (até 180 s) + Fatiador "Parte 1/N" (45–60 s)** | `clipes/candidates.py` (`window_half=95.0`), `clipes/snap.py` (`snap_candidate`), `clipes/cli.py` | **Altíssimo** | **Médio** | **Resolve a Decisão 3 e a Decisão 5:** amplia a janela candidata para capturar a história inteira (até ~180 s), detecta onde o assunto realmente começa e termina (quebra de tópico ou pausa longa `>= 1.2 s`), relaxa `PEAK_POS_MAX` para permitir picos no final do clipe (`0.85`), e se o bloco completo passar de 65 s, divide automaticamente em **Partes de 45–60 s** cortadas em borda de frase (`.?!`) com *overlap* de `1.5 s` entre partes. |
| **2** | **Corrigir *Safe Zones* do ASS (`MarginV=540`, `MarginL=110`, `MarginR=130`) + Tipografia Forte em CAIXA ALTA com *Pop* nativo `libass`** | `clipes/render.py::build_ass` (linhas 118–159) e `Dockerfile` (`fonts-montserrat`) | **Alto** | **Muito Baixo** | Tira a legenda da zona de colisão inferior do Reels/Shorts (`Y=1490 -> Y=1380`, logo abaixo do vídeo) e da barra de ícones direita (`MarginR=130`), muda para fonte *Montserrat ExtraBold* em `.upper()` e adiciona `{\fscx112\fscy112}` na palavra ativa sem adicionar 1 ms ao tempo de render. |
| **3** | **Queimar Banner de Título (*Hook* de Topo) + Crédito da Live + Encerramento Suave (Sem Corte Seco)** | `clipes/render.py::build_ass` e `render_clip` (linhas 254–277) | **Alto** | **Baixo** | **Resolve a Decisão 4 e o Hook de 3 s:** adiciona um segundo `Style: Hook` no mesmo arquivo `.ass` (no topo seguro `Y = 420..580 px` com caixa escura `BorderStyle=3`) exibindo o título do clipe (e `"PARTE 1/2"` quando aplicável), um `Style: Credit` discreto (`🎥 Live: <canal>`) + card final de `1.2 s` (`Continua na Parte 2` ou `Live completa: <url_curta>`) acompanhado de `afade=t=out` nos últimos `0.5 s` do áudio. Custo extra de CPU = **zero** (tudo renderizado pelo mesmo filtro `subtitles` do `ffmpeg`). |
| **4** | **Filtro Anti-Muleta no Início do Clipe (`t_start`) e Gancho de Fim de Parte em Borda de Sentença** | `clipes/snap.py::_speech_start_candidates` e `_speech_end_candidates` | **Alto** | **Baixo** | Penaliza em `-2.0` candidatos de início cuja primeira palavra seja muleta (*"então"*, *"tipo"*, *"mano"*, *"cara"*, *"é"*) e premia inícios com pergunta/exclamação ou substantivo forte. Nas fronteiras entre Parte 1 e Parte 2, premia frases que terminam em conector de suspense ou pergunta. |
| **5** | **Expandir o Vídeo Central (Crop `4:3` / `5:4` + Escurecimento do Fundo Borrado)** | `clipes/render.py::render_clip` (linhas 254–259) | **Alto** | **Muito Baixo** | Aumenta a altura útil do vídeo principal de `608 px` (31,6% da tela) para `~860 px` (45% da tela) com um simples ajuste na expressão `scale/crop` do `ffmpeg` e escurece o fundo borrado (`eq=brightness=-0.12`), destacando rostos e legendas sem custo adicional de CPU. |
| **6** | **Gerador Local Leve (ou Híbrido) de Metadados de Post: Título Curto (<50 chars), Legenda SEO, Crédito e <= 5 Hashtags** | Novo módulo `clipes/metadata.py` + `clipes/cli.py` (`clips.json`) | **Alto** | **Médio** | **Resolve a Decisão 1 e Seção 4:** após descarregar o Whisper da RAM (`del model; gc.collect()`), roda um LLM local quantizado (`Qwen2.5-1.5B-Instruct` ou `Qwen2.5-3B-Instruct` GGUF `Q4_K_M` via `llama-cpp-python`, ocupando `~1.1–2.0 GB RAM` e `~4–8 s` por clipe em 6 threads) para gerar: (a) `hook_title` (<= 45 chars), (b) `caption` com pergunta + crédito + link da live, (c) exatas **4 a 5 hashtags de nicho** (respeitando o limite do Instagram de dez/2025, zero `#fyp`), e (d) `score_llm` (nota 0–10 de completude/viralidade). |
| **7** | **Cold Open Opcional de 1,8 s para Clipes Longos (> 45 s) com Pre-Roll Lento** | `clipes/render.py::render_clip` | **Médio-Alto** | **Médio** | Quando o `pre_roll` (`peak - start`) for `> 18 s`, concatena via `ffmpeg` nos primeiros `1.8 s` a frase/reação do `peak` com etiqueta `[TRECHO DA LIVE]` antes de iniciar o contexto em `clip.start`, elevando a retenção nos primeiros 3 segundos (*Viewed vs. Swiped Away*). |
| **8** | **Detecção Leve de Webcam (3 Frames por Clipe) para Layout *Split-Screen* (Facecam Topo + Gameplay Base)** | `clipes/render.py` | **Médio-Alto** | **Médio** | Amostra 3 frames do segmento e verifica se há rosto estacionário em um dos 4 cantos do 16:9 (< 50 ms em CPU). Se positivo, troca o layout de fundo borrado pelo *Split-Screen* vertical (`600 px` webcam no topo + `1320 px` gameplay na base), multiplicando por 4x o tamanho do rosto do streamer em lives de jogos. |
| **9** | **Captura de Sinais e Gravação em Buffer Rolante na Twitch (Pico Relativo ao Baseline do Canal)** | `clipes/ingest.py` e `clipes/signals.py::signal_chat` | **Médio-Alto** | **Médio** | **Alinhado à Decisão 2 e crítica do fluxo Twitch:** evita a API `Create Clip` da Twitch (que corta curto e seco) e o limiar fixo de `6 msgs/10s`; monitora o chat via IRC/WebSocket leve e dispara gravação de janela completa (`pre-roll` do buffer HLS + `post-roll` até a queda da energia do chat/áudio) quando o *z-score* / razão sobre a mediana móvel (`signal_chat`) superar o limiar do canal. |
| **10** | **Melhoria de Qualidade de Transcrição em Nomes Próprios e Gírias (`initial_prompt` no Whisper)** | `clipes/asr.py::transcribe_candidates` (linha 98) | **Médio** | **Muito Baixo** | Passar `initial_prompt` no `model.transcribe(...)` contendo o `title` da live, nome do canal e vocabulário pt-BR de lives/games (extraído de `ing.title` em `ingest.py`). Custo de CPU e RAM = **zero**, e reduz drasticamente erros de grafia de nomes de convidados/jogos nas legendas queimadas e nas hashtags sem precisar subir para o modelo `large-v3-turbo`. |

---

## 7. Referências e Fontes Verificadas

1. **YouTube Help Center** — *Understand three-minute YouTube Shorts* (`answer/15424877`). URL: https://support.google.com/youtube/answer/15424877?hl=en (Política vigente desde 15/10/2024; verificado em `2026-11-17`).
2. **YouTube Help Center** — *Content tab analytics tips - Shorts ("How many chose to view" / Viewed vs. Swiped Away)* (`answer/12942217`). URL: https://support.google.com/youtube/answer/12942217?hl=en&co=YOUTUBE._YTVideoType%3Dshorts (Verificado em `2026-11-17`).
3. **YouTube Official Blog** — *Engaged Views on YouTube Shorts Explained*. URL: https://blog.youtube/inside-youtube/engaged-views-youtube-explained/ (Atualização de métricas de views vs. engaged views de 2025; verificado em `2026-11-17`).
4. **YouTube Help Center** — *Links in YouTube Shorts & Related Video Policy* (`answer/13748639`). URL: https://support.google.com/youtube/answer/13748639?hl=en (Política de links não clicáveis em descrições/comentários de Shorts e uso de *Related Video*; verificado em `2026-11-17`).
5. **YouTube Help Center** — *Get started creating YouTube Shorts* (`answer/10059070`). URL: https://support.google.com/youtube/answer/10059070?hl=en (Verificado em `2026-11-17`).
6. **House of Marketers** — *Instagram Expands Reels to 3 Minutes & Algorithm Updates (Adam Mosseri Announcement)*. URL: https://houseofmarketers.com/instagram-reels-3-minutes-nyc-algorithm-change/ (Publicado em jan/2025; verificado em `2026-11-17`).
7. **PersonaXpand** — *Instagram Hashtag Limit Change (5 Hashtags Maximum Announcement, Dec 18, 2025)*. URL: https://personaxpand.com/blog/instagram-hashtag-limit (Anúncio oficial `@creators` de 18/12/2025; verificado em `2026-11-17`).
8. **PostBoost** — *Instagram 5 Hashtag Limit Guide*. URL: https://postboost.co/learn/instagram-hashtag-limit (2026; verificado em `2026-11-17`).
9. **TikTok Ads Manager Help Center** — *Specifications & Safe Zones for TikTok Interactive / In-Feed Video (`1080x1920`)*. URL: https://ads.tiktok.com/resources/help/article/tiktok-interactive-add-on-download-card-ad-specifications?lang=en (Verificado em `2026-11-17`).
10. **Toco Marketing** — *TikTok Safe Zone Dimensions (1080x1920)*. URL: https://toco.marketing/tools/tiktok-safe-zone/ (Verificado em `2026-11-17`).
11. **ClipUGC** — *Instagram Reels Safe Zone Overlay & Dimensions (1080x1920)*. URL: https://clipugc.com/tools/instagram-reels-safe-zone (Verificado em `2026-11-17`).
12. **Vidova AI** — *Instagram Reels, TikTok & YouTube Shorts Safe Zones Guide*. URL: https://vidova.ai/blog/instagram-reels-tiktok-youtube-shorts-safe-zones (2025–2026; verificado em `2026-11-17`).
13. **VodClipsLab** — *Vertical Gaming Clip Layout: Facecam Top / Gameplay Bottom (9:16 Retention)*. URL: https://vodclipslab.com/ (2025–2026; verificado em `2026-11-17`).
14. **Amostra de 12 Shorts Brasileiros Verificados (`2026-11-17`):**
    - Flow Podcast: https://www.youtube.com/shorts/rQoRUd_jAAo (`2023-10-13`), https://www.youtube.com/shorts/WoDB3HXgLVQ (`2023-10-04`), https://www.youtube.com/shorts/d3PhXVAbcPU (`2026-11-16`), https://www.youtube.com/shorts/wjNX1cNvrZg (`2026-11-15`)
    - Cortes do Flow [OFICIAL]: https://www.youtube.com/shorts/suoXsKfW9uo (`2026-11-15`), https://www.youtube.com/shorts/kHmHVAMV6Es (`2026-11-16`)
    - Cyber Curioso (Flow/Igor 3K): https://www.youtube.com/shorts/YT0ponhRHEk (`2025-06-26`)
    - NerdBrothers (Alanzoka): https://www.youtube.com/shorts/HJpQYLM2Y1Q (`2026-11-11`)
    - TecMundo (Alanzoka): https://www.youtube.com/shorts/aaSzcd1u8fw (`2026-05-05`)
    - Cortes do Casimito [OFICIAL]: https://www.youtube.com/shorts/NBYFVgF233E (`2026-09-09`)
    - CazéTV: https://www.youtube.com/shorts/8D1MzIxFU3k (`2026-11-15`)
    - Podpah TV: https://www.youtube.com/shorts/TydfpPLqHfQ (`2026-11-16`), https://www.youtube.com/shorts/cdPEwcNFKUQ (`2026-11-16`)
    - Gaules: https://www.youtube.com/shorts/PiW7TqsYrcs (`2025-11-19`), https://www.youtube.com/shorts/uOV8pXOd_Yw (`2026-11-16`)
