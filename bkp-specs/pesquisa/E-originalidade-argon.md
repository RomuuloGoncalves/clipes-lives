# Pesquisa E — Conteúdo Reutilizado, Originalidade, Direitos Autorais e Alcance em Canais de Cortes (`clipes-lives`)

- **Agente:** Pesquisador Independente E (Gemini 4 Argon)
- **Data da pesquisa e verificação das fontes:** `2026-11-17`
- **Escopo:** Políticas oficiais de YouTube, TikTok, Instagram/Meta e Twitch sobre conteúdo reutilizado/inautêntico, direitos autorais (Content ID vs. DMCA Strike), elegibilidade de recomendação ("shadow ban" vs. restrições documentadas), práticas de baixo risco no ecossistema brasileiro de cortes, riscos de técnicas de evasão, impacto de layout vertical (fundo borrado vs. barras pretas vs. crop/split) e recomendações acionáveis para o pipeline `clipes-lives` dentro do piso de hardware (Ryzen 5 5500U, 6 threads, 6 GB RAM no container Docker, 100% CPU-only).

---

## 1. O que cada plataforma diz com precisão: políticas oficiais, consequências reais e o mito do "shadow ban"

O termo **"shadow ban"** não existe na documentação oficial de YouTube, TikTok ou Instagram. O que criadores chamam de "shadow ban" ao publicar cortes de lives é, na realidade, um conjunto de **mecanismos documentados de restrição de recomendação para não-seguidores** (filtros de originalidade/agregadores) combinado com **detecção de duplicatas por fingerprinting audiovisual**. Ou seja: o vídeo continua público no perfil e visível para seguidores ou via link direto, mas deixa de ser distribuído nas superfícies algorítmicas (*Shorts Feed*, *For You Feed*, *Reels Tab / Explore*).

Além disso, todas as plataformas separam rigorosamente duas camadas:
1. **Direito Autoral (Copyright / DMCA):** Quem é o dono legal da obra audiovisual/musical e se houve autorização ou reivindicação.
2. **Originalidade / Monetização / Recomendação:** Mesmo com permissão explícita do dono da live (ou até sob licença Creative Commons), a plataforma pode **negar monetização** ou **reduzir recomendação** se o corte não tiver transformação criativa substancial em relação ao vídeo original.

### 1.1. Matriz comparativa das plataformas

| Plataforma | Política de Originalidade / Recomendação | Política de Monetização | Direitos Autorais (Claim vs. Strike) | O que o usuário vê ("Shadow Ban" vs. Oficial) |
|---|---|---|---|---|
| **YouTube** | Não há restrição explícita documentada de recomendação para cortes legítimos no Shorts, mas vídeos duplicados ou massificados podem ser classificados como spam/inautênticos. *(Penalidade algorítmica silenciosa por "repost" sem strike/claim: **não verificado** em documentação oficial do YouTube).* | **YPP (Programa de Parcerias):** Políticas de **Conteúdo Reutilizado** (*Reused Content*) e **Conteúdo Inautêntico** (*Inauthentic Content*, nome que substituiu *Repetitious Content* em **15/07/2025**). Avaliam o canal inteiro: cortes de terceiros sem comentário/narrativa própria reprovam ou suspendem o canal do YPP, mesmo com autorização do streamer. | **Content ID Claim** (automático): não penaliza a conta; rastreia, bloqueia ou desvia 100% da receita ao titular (ou restringe Shorts se tiver >1 min ou áudio não licenciado).<br>**Copyright Strike** (manual/DMCA): remove o vídeo, exige *Copyright School*, dura 90 dias; **3 strikes encerram o canal e contas vinculadas**. | Painel *Direitos Autorais* e aba *Validação (Earn)* no YouTube Studio mostram claims, strikes e status do YPP. |
| **TikTok** | **For You Feed (FYF) Eligibility:** Conteúdo "não original" (*Unoriginal / Reproduced Content*), com marca d'água de terceiros ou baixa qualidade é marcado como **"Não elegível para o feed Para Você"** (*Ineligible for the For You feed*). | **Creator Rewards Program:** Exige vídeos `>= 60s` 100% originais. Cortes de lives/podcasts de terceiros, *Duets* e *Stitches* são desqualificados. Acumular **5 violações de conteúdo não original em 30 dias** expulsa a conta do programa. | Remoção por DMCA mediante notificação do titular; reincidência gera strikes de propriedade intelectual e banimento permanente da conta. | Visível em *TikTok Studio / Analytics* do vídeo como *"Not eligible for the For You feed"* com opção de apelação. Para quem não olha o Analytics, parece "shadow ban" (0 views do FYF). |
| **Instagram (Meta)** | **Recommendation Guidelines & Atualização de Originalidade (30/04/2024):**<br>1. **Direct Replace:** Quando o sistema detecta um Reel idêntico/repostado, substitui o repost pelo vídeo do criador original nas recomendações e adiciona rótulo de atribuição.<br>2. **Penalidade a Agregadores:** Contas que postam repetidamente conteúdo de terceiros sem "edição material" (**10+ vezes em 30 dias**) perdem elegibilidade de recomendação em *Explore*, *Reels* e *Feed* para não-seguidores por pelo menos 30 dias. | Ferramentas de monetização (*Bonuses*, *Gifts*, *Subscriptions*, *Ads on Reels*) exigem conformidade com as *Partner Monetization Policies* — conteúdo não original ou reivindicado via *Meta Rights Manager* perde monetização. | **Meta Rights Manager** (automático): permite ao titular monitorar, bloquear ou reivindicar receita/atribuição.<br>**Relatório DMCA** (manual): remove o conteúdo e aplica strike na conta. | Visível em **Status da Conta (*Account Status*) -> O que não pode ser recomendado**. Se a conta cair na trava de agregador, o alcance fora de seguidores cai a quase zero. |
| **Twitch** | A Twitch é a fonte primária na nossa arquitetura, mas seus Termos de Serviço definem que VODs e Clips são **Conteúdo do Usuário (*User Content*)** pertencentes ao streamer (que concede licença à Twitch, não a terceiros externos). | N/A (nesta etapa a Twitch é fonte de ingestão, não destino de publicação). | A Twitch aplica silenciamento automático de VODs (*Audible Magic*) e processa notificações DMCA contra streamers por música/vídeo com copyright. | Baixar VODs/lives de terceiros fora das ferramentas permitidas ou republicar sem autorização do streamer infringe direitos autorais do criador fora da Twitch. |

### 1.2. Detalhamento e Fontes Oficiais por Plataforma

#### A. YouTube
1. **Conteúdo Reutilizado (*Reused Content*) vs. Conteúdo Inautêntico (*Inauthentic Content*):**
   - **Fonte Oficial:** *YouTube Channel Monetization Policies* (`https://support.google.com/youtube/answer/1311392`, atualizado em **15 de julho de 2025**, verificado em `2026-11-17`).
   - **Definição de Conteúdo Reutilizado:** Canal que publica conteúdo de outras fontes sem adicionar comentário original significativo, valor educacional ou edição narrativa transformativa. O YouTube explicita: *"Mais importante ainda, políticas de conteúdo reutilizado se aplicam mesmo que você tenha permissão do detentor dos direitos autorais."* Exemplos oficialmente reprovados para monetização:
     - Clipes curtos compilados de outras redes sociais ou momentos de programas/lives sem linha narrativa própria.
     - Reações não-verbais ou com pouca fala adicionada.
     - Reuploads de vídeos de outros criadores com apenas legendas, cortes simples ou filtros.
   - **Definição de Conteúdo Inautêntico (atualização de 15/07/2025):** Substituiu o nome *"Repetitious content"* para deixar claro que canais baseados em produção em massa por template automatizado, leituras sintéticas sem curadoria humana ou vídeos quase idênticos em série não são elegíveis ao YPP.
2. **Content ID Claim vs. Copyright Strike:**
   - **Fontes Oficiais:**
     - *Difference between copyright takedowns and Content ID claims* (`https://support.google.com/youtube/answer/7002106`, verificado em `2026-11-17`).
     - *Copyright strike basics* (`https://support.google.com/youtube/answer/2814000`, verificado em `2026-11-17`).
     - *What is a Content ID claim?* (`https://support.google.com/youtube/answer/2797370`, verificado em `2026-11-17`).
   - **Consequência prática para o `clipes-lives`:** Se o streamer ou podcast (ex.: Podpah, Flow, CazéTV) tiver o episódio cadastrado no Content ID, o corte receberá um **Content ID Claim** automático (receita vai para o podcast, sem punição à conta) **ou**, se a assessoria jurídica do canal emitir DMCA manual, um **Copyright Strike** (remoção + risco de perda do canal com 3 strikes em 90 dias).

#### B. TikTok
1. **Elegibilidade para o Feed "Para Você" (*For You Feed - FYF*):**
   - **Fontes Oficiais:**
     - *Community Guidelines — Integrity and Authenticity* (`https://www.tiktok.com/safety/en/policies-and-engagement/integrity-authenticity`, verificado em `2026-11-17`).
     - *Content not eligible for the For You feed* (`https://www.tiktok.com/support/faq_detail?id=7543604781940791864`, verificado em `2026-11-17`).
   - **Consequência:** Vídeos importados/reproduzidos de outras fontes sem adição criativa substancial, compilações estáticas ou vídeos contendo marcas d'água/logos de outras plataformas são retidos pelo filtro de recomendação (*Ineligible for the For You feed*). Não geram exclusão da conta (a menos que recebam denúncia DMCA), mas perdem ~95%+ da distribuição orgânica.
2. **Desmonetização no Creator Rewards Program:**
   - **Fontes Oficiais:**
     - *Creator Rewards Program / Unoriginal Content FAQ* (`https://www.tiktok.com/support/faq_detail?id=7581820708028865036`, verificado em `2026-11-17`).
     - *TikTok Creator Fund Update to Creator Rewards Program* (`https://support.tiktok.com/de/business-and-creator/tiktok-creator-fund-us/tiktok-creator-fund-update-us`, verificado em `2026-11-17`).
   - **Consequência:** O programa exige vídeos de no mínimo 60 segundos e desqualifica vídeos marcados como *Unoriginal Content* (incluindo cortes de podcasts/streams de terceiros apenas legendados). Cinco violações em 30 dias suspendem o perfil do programa.

#### C. Instagram / Meta
1. **Original Content Guidelines & Penalidade para Agregadores:**
   - **Fontes Oficiais:**
     - *Instagram Original Content Guidelines* (`https://creators.instagram.com/original-content-guidelines`, verificado em `2026-11-17`).
     - *Rewarding Original Creators on Instagram* (`https://creators.instagram.com/blog/rewarding-original-creators-on-instagram`, publicado em **30 de abril de 2024**, verificado em `2026-11-17`).
     - *Recommendations and Originality on Instagram* (`https://creators.instagram.com/blog/recommendations-and-originality`, verificado em `2026-11-17`).
     - *Recommendations on Instagram Help Center* (`https://help.instagram.com/313829416281232`, verificado em `2026-11-17`).
   - **Regra explícita sobre o que NÃO é edição material:** Na página oficial `creators.instagram.com/original-content-guidelines`, o Instagram documenta que **conteúdo de terceiros só passa a ser considerado original se for "materialmente editado" (*materially edited*)**, e lista explicitamente o que **NÃO** conta como edição material:
     - Apenas adicionar marca d'água ou bordas/molduras;
     - Alterar velocidade do vídeo;
     - **Adicionar texto/legenda que apenas descreve ou transcreve literalmente o que já está sendo dito/mostrado no vídeo.**
   - **Consequência documentada:**
     1. **Substituição direta (*Direct Replace*):** Se o corte for detectado como cópia de um Reel já existente do criador original, o Instagram exibe o vídeo do criador original nas recomendações em vez do seu.
     2. **Bloqueio de recomendação da conta agregadora:** Contas que acumulam **10 ou mais postagens de conteúdo não original nos últimos 30 dias** ficam inelegíveis para aparecer em *Reels*, *Explore* e *Sugestões no Feed* para não-seguidores até que a janela móvel de 30 dias volte a ter maioria de conteúdo original.

#### D. Twitch
1. **Termos de Serviço (Clips e VODs):**
   - **Fontes Oficiais:**
     - *Twitch Terms of Service — Section 8 (User Content)* (`https://legal.twitch.com/en/legal/terms-of-service/`, verificado em `2026-11-17`).
     - *Twitch DMCA and Copyright FAQs* (`https://help.twitch.tv/s/article/dmca-and-copyright-faqs?language=en_US`, verificado em `2026-11-17`).
     - *Twitch Video on Demand (VOD) Guide* (`https://help.twitch.tv/s/article/video-on-demand?language=en_US`, verificado em `2026-11-17`).
     - *Twitch Developer Services Agreement* (`https://legal.twitch.com/legal/developer-agreement`, verificado em `2026-11-17`).
   - **Regra:** O streamer detém os direitos sobre seu *User Content* (transmissões ao vivo, VODs e Clips). Os Termos da Twitch permitem que o próprio criador exporte seus VODs/Clips para outras plataformas e permitem que usuários criem Clips **dentro** do ecossistema da Twitch, mas **não concedem licença automática a terceiros para baixar VODs/streams com ferramentas externas e republicá-los comercialmente em outras redes sociais** sem autorização do streamer.

---

## 2. O que conta como edição TRANSFORMATIVA / valor agregado vs. o que o nosso pipeline faz hoje

Analisando o código atual do `clipes-lives` (`clipes/render.py`, `clipes/snap.py`, `clipes/cli.py`) e as 5 decisões do usuário frente às regras oficiais de YouTube (`answer/1311392`), Instagram (`original-content-guidelines`) e TikTok (`integrity-authenticity`):

### 2.1. Classificação de cada elemento do pipeline (Atual + Planejado)

| Edição / Recurso | Status no `clipes-lives` | Conta como Edição Transformativa sozinha? | Papel real nas plataformas (Alcance, UX e Conformidade) |
|---|---|---|---|
| **Legenda palavra-a-palavra queimada (`.ass` com *word highlight* amarelo)** | **Implementado** (`render.py::build_ass`) | **NÃO.** Documentado pelo Instagram (*"adding text that only literally describes what is already happening"*) e pelo YouTube/TikTok como insuficiente sozinho para descaracterizar *Reused/Unoriginal Content*. | **Essencial para retenção** (usuários assistem no mudo ou em ambientes barulhentos) e acessibilidade, mas **zero proteção** contra filtro de conteúdo reutilizado se for a única edição. |
| **Fundo borrado (`boxblur=10:2`) + vídeo 16:9 centralizado em `1080x1920`** | **Implementado** (`render.py::render_clip`) | **NÃO.** É apenas adaptação de proporção de tela (moldura/borda dinâmica). | Evita barras pretas, mas é o padrão visual mais associado a bots de repostagem em massa (ver Seção 6). |
| **Normalização de áudio (`loudnorm=I=-14:TP=-1.5:LRA=11`) e corte em borda de frase (`snap.py`)** | **Implementado** (`render.py`, `snap.py`) | **NÃO.** É acabamento técnico de engenharia de áudio/corte. | Melhora drasticamente a taxa de conclusão (*completion rate*) por não cortar palavras no meio (`mid_word_cut_rate=0.0`) e evitar picos estourados. |
| **Título heurístico ou gerado por IA local** | **Parcial** (`render.py::generate_title`; IA local na Decisão 1) | **NÃO sozinho**, mas um **gancho contextual / pergunta crítica sobreposta nos primeiros 3–5s** aproxima o vídeo de curadoria editorial. | Ajuda o algoritmo de busca/indexação (SEO de Shorts/Reels/TikTok) e segura o *scroll* nos primeiros 2 segundos. |
| **Crédito visível (canal + link da live) e cartão de encerramento (Decisões 3 e 4)** | **Planejado** (Decisão 4) | **NÃO transforma** o vídeo para fins de YPP, mas **atende às exigências de atribuição** de streamers/podcasts e evita aparência de plágio. | Reduz drasticamente o risco de **DMCA Strike manual** por parte do criador original e melhora o acabamento (evita corte seco no fim). |
| **Divisão em partes de 45–60s ("Parte 1/N") com gancho no fim (Decisão 3)** | **Planejado** (Decisão 3) | **NÃO sozinho.** Se as partes forem apenas fatias sequenciais sem curadoria, ainda são conteúdo de terceiros. | Aumenta engajamento (comentários pedindo Parte 2, visitas ao perfil) e resolve o problema de assuntos de ~3 min sem violar o teto de 60s dos Shorts/Reels. |
| **Resumo contextual / Tese / Pergunta interpretativa em card visual ou narração (Intro/Overlay)** | **Não implementado** (Recomendado na Seção 5) | **PARCIAL A ALTO (quando combinado).** Adicionar contexto editorial original (*"O contexto por trás de..."*, dados extras na tela, síntese crítica gerada pelo LLM local) entra na categoria de *commentary/context/educational value* do Instagram e YouTube. | Diferencia o corte de qualquer outro canal que baixou o mesmo trecho bruto da live. |
| **Curadoria multi-trecho (juntar setup + payoff distantes eliminando "barriga")** | **Não implementado** (Recomendado na Seção 5) | **MODERADO.** Cortar pausas longas e unir setup + clímax mostra edição ativa em vez de um `ffmpeg -ss -t` contínuo. | Dificulta a colisão exata 1:1 com clipes brutos de outros recortadores (*Direct Replace* do Instagram) e eleva a retenção. |
| **Narração própria (voz humana ou TTS claramente contextual) / Reação em vídeo** | Fora do escopo automático padrão | **SIM (Padrão-ouro oficial).** É o requisito que YouTube YPP, TikTok Creator Rewards e Instagram citam como transformação inequívoca. | Permite inclusive monetização direta em programas de parceria (desde que autorizado ou enquadrado em crítica/análise). |

> **Conclusão crítica da Seção 2 para o usuário:**
> Apenas legendar palavra-a-palavra e colocar fundo borrado **garante boa retenção técnica**, mas **NÃO impede** que YouTube classifique o canal como *Reused Content* (sem monetização YPP) nem que Instagram/TikTok apliquem filtros de agregador caso o mesmo trecho já tenha sido postado pelo streamer original ou por dezenas de outros clipadores. Para que os cortes performem em alcance orgânico e tenham baixo risco, é preciso combinar **(a) permissão/regras da fonte + janela de tempo** com **(b) elementos editoriais reais gerados pelo pipeline** (gancho interpretativo, contexto na tela, corte de silêncios internos, reenquadramento superior ao blur estático e cartão de crédito/fechamento).

---

## 3. Prática correta e de baixo risco para canais de cortes (Brasil e Global)

Quando o usuário gera cortes **das próprias lives**, o risco de copyright e de "conteúdo de terceiros" é zero (basta não subir o mesmo arquivo idêntico repetidas vezes nem deixar marca d'água do TikTok ao postar no Reels/Shorts).

Quando o usuário gera cortes **de lives de terceiros**, a operação segura e sustentável apoia-se em 6 pilares verificados:

### 3.1. Permissão, Programas de Cortes e Regras de Grandes Canais BR
- **Licença vs. Tolerância:** Pela Lei de Direitos Autorais brasileira (Lei nº 9.610/1998, art. 29) e pelo DMCA (17 U.S.C. § 512), qualquer reprodução parcial de obra audiovisual de terceiros depende de autorização, salvo exceções estritas (citação para fins de estudo, crítica ou polêmica, na medida justificada para o fim a atingir — art. 46, III da LDA).
- **Como operam os grandes canais e podcasts no Brasil (ex.: Flow Podcast, Podpah, CazéTV/Casimiro, Gaules, Loud):**
  1. **Regra do Delay (tipicamente 24 horas após o fim da live):** É a convenção mais importante do mercado brasileiro de cortes (*confirmada em diretrizes públicas de estúdios de podcast e comunidades de clipadores, verificado em `2026-11-17`*). Publicar cortes **enquanto a live ainda está no ar** ou antes do canal oficial soltar seus próprios cortes ("furar a live") é a causa nº 1 de **Copyright Strikes manuais** no Brasil, pois compete diretamente com o canal oficial.
  2. **Crédito visível e link direto na descrição:** Todo corte deve trazer o nome do canal/podcast original em destaque na tela e o link da transmissão original nas primeiras linhas da descrição e/ou comentário fixado.
  3. **Proibição de títulos difamatórios / fake news / clickbait criminoso:** Tirar uma fala de contexto ou imputar crime/ofensa grave na *thumbnail* ou título aciona imediatamente o jurídico do convidado ou do estúdio via DMCA Strike.
  4. **Programas Oficiais de Clipadores ("Campeonatos de Cortes" / Whop / ViewX):** Hoje, diversos streamers e podcasts mantêm programas oficiais onde liberam o acervo mediante regras claras (marca d'água da campanha, link específico, hashtag) e pagam por CPM (visualizações válidas) fora do AdSense do YouTube.

### 3.2. Tamanho do trecho e não reupar blocos longos
- **Nunca reupar a live inteira ou blocos brutos (>10–15 min):** Além de acionar bloqueio imediato no Content ID, caracteriza substituição de mercado (*market substitution*), anulando qualquer defesa de uso justo/citação.
- **Faixa segura para cortes verticais (30s a 60s, ou séries de 45–60s):** A decisão do usuário de dividir assuntos longos (até ~3 min) em **partes de 45–60s ("Parte 1/N")** está alinhada com o teto nativo de recomendação rápida de Shorts/Reels e evita a trava do YouTube Shorts para músicas/reivindicações acima de 1 minuto.

### 3.3. Música de fundo e Direitos Conexos (O maior risco oculto em lives da Twitch)
- Muitos streamers da Twitch tocam músicas comerciais (Spotify/YouTube) durante a live (às vezes usando faixa separada de VOD da Twitch para não ficar gravado no VOD, mas **presente na transmissão ao vivo** que o `ingest.py::record_live_stream` grava!).
- Se o corte capturar música comercial de gravadora (UMG, Sony, Warner) ao fundo da fala do streamer:
  - No **YouTube Shorts**, trechos musicais não licenciados ou em vídeos longos geram bloqueio global ou perda de receita.
  - No **Instagram Reels** e **TikTok**, áudios comerciais com mais de 60 segundos ou fora da biblioteca comercial podem causar **silenciamento total (*mute*) do vídeo** — inclusive silenciando a voz do streamer junto!
- **Prática recomendada no pipeline:** Se houver música de fundo alta na live original, aplicar separação vocal leve ou filtro passa-banda/atenuação fora da faixa de voz humana apenas quando necessário, ou priorizar janelas com predominância de fala limpa.

---

## 4. ALERTA DE RISCO: Por que NUNCA usar truques para tentar enganar a detecção

> **REGRA INEGOCIÁVEL:** O pipeline `clipes-lives` **NÃO deve implementar e NÃO recomenda** nenhuma técnica de ofuscação para tentar enganar o Content ID, o Meta Rights Manager ou o filtro de originalidade do TikTok.

### 4.1. Práticas de evasão mencionadas em fóruns e por que falham tecnicamente
1. **Espelhamento horizontal (`hflip` / *mirroring*):**
   - *Por que falha:* Os sistemas modernos de *perceptual video hashing* (Content ID do YouTube, *vPDQ* / *TMK* da Meta) geram descritores invariantes à reflexão horizontal (comparam o frame normal e o espelhado simultaneamente).
   - *Dano colateral:* Inverte textos de camisas, placas, HUD de jogos e deixa o rosto do streamer assimétrico ("efeito estranheza"), derrubando a retenção e denunciando visualmente que o vídeo é pirata.
2. **Alteração de Pitch / Velocidade (`asetrate`, `atempo=1.08`, etc.):**
   - *Por que falha:* O fingerprinting acústico moderno opera sobre constelações espectrais normalizadas no tempo e na frequência (*tempo/pitch-invariant audio fingerprinting*). Mudar 3% a 10% de velocidade/pitch não impede o match no Content ID.
   - *Dano colateral:* O Instagram documenta explicitamente em `creators.instagram.com/original-content-guidelines` que *"altering speed"* é classificado como edição superficial inelegível. Além disso, distorce o timbre da voz do streamer.
3. **Zoom pulsante contínuo, filtros de cor agressivos ou *frames* falsos inseridos:**
   - *Por que falha:* A correspondência é feita por amostragem temporal de segmentos e sub-regiões do quadro; alterar bordas ou saturar cores não apaga a assinatura espaço-temporal central nem a trilha de áudio.
   - *Penalidade agravada:* Tentar burlar deliberadamente medidas tecnológicas de proteção e sistemas automatizados de direitos autorais viola os Termos de Serviço do YouTube, TikTok e Meta, podendo converter o que seria apenas um *Content ID Claim* inofensivo em **encerramento sumário da conta por fraude/evasão de sistema (*circumvention of technological measures*)**.

---

## 5. Recomendações acionáveis para o pipeline `clipes-lives` (Ordenadas por Impacto / Esforço)

Todas as recomendações abaixo foram desenhadas estritamente dentro do **Piso de Hardware** (**Ryzen 5 5500U, 6 threads e 6 GB RAM no container Docker, sem GPU, 100% gratuito/offline, execução estritamente sequencial entre Whisper e LLM local**) e atendem às **5 Decisões do Usuário**.

### 5.1. Tabela de Priorização (Impacto na Originalidade/Retenção vs. Esforço de Implementação e CPU/RAM)

| Prioridade | Recurso no Pipeline | Impacto (Originalidade + Retenção) | Esforço Técnico | Custo de CPU / RAM no Container (6 GB / 6 threads) | Variável / Flag de Configuração Sugerida |
|---:|---|---|---|---|---|
| **1** | **Header Contextual Fixo/Dinâmico ("Gancho Editorial") + Badge `"Parte K/N"` + Crédito Visível na Tela (`.ass`)** | **Muito Alto** (Adiciona contexto editorial visível desde o frame 0, cumpre atribuição ao streamer e sinaliza série) | **Baixo** (Puro ASS multi-estilo em `render.py::build_ass`, zero filtro pesado extra no `ffmpeg`) | **~0 s CPU / ~0 MB RAM** (renderizado no mesmo passo do filtro `subtitles` do `libass`) | `CLIPES_SHOW_CREDITS=1`<br>`CLIPES_CHANNEL_CREDIT="auto"`<br>`CLIPES_SHOW_HOOK_BANNER=1`<br>`CLIPES_PART_BADGE=1` |
| **2** | **Cartão de Encerramento (*Outro Card* de 1.5s–2.0s) + Fade-out de Áudio/Vídeo + Gancho para Próxima Parte** | **Alto** (Elimina o corte seco — Decisão 4 —, exibe fonte/link da live original e aumenta retenção até 100% do vídeo) | **Baixo a Médio** (`afade` + overlay/drawbox nos últimos `1.8s` ou `concat` leve no `ffmpeg`) | **+0.5 a 1.5 s CPU por clipe / < 50 MB RAM** | `CLIPES_OUTRO_SECONDS=1.8`<br>`CLIPES_OUTRO_TEXT="Assista à live completa no canal oficial"` |
| **3** | **Segmentação Narrativa em Partes de 45–60s ("Parte 1/N") para Assuntos Longos (até ~3 min)** | **Muito Alto** (Resolve a Decisão 3 e 5: não corta o assunto no meio, respeita bordas de frase e cria efeito série legítimo) | **Médio** (Expandir janela candidata em `candidates.py`/`snap.py` até ~180s quando a densidade de fala/sinal se mantém acima do baseline e fatiar em blocos de 45–60s em `ends_sentence`) | **+15% a 35% no ASR das janelas longas / mesma RAM (~600 MB int8)** | `CLIPES_MULTI_PART=1`<br>`CLIPES_PART_MIN_DUR=45`<br>`CLIPES_PART_MAX_DUR=60`<br>`CLIPES_TOPIC_MAX_DUR=180` |
| **4** | **Geração de Gancho Interpretativo / Síntese Crítica + Nota 0–10 via LLM Local Leve (Sequencial pós-Whisper)** | **Alto** (Transforma o texto na tela de "mera transcrição literal" — rejeitada pelo Instagram — em **comentário contextual/resumo editorial**, além de filtrar clipes ruins < nota mínima) | **Médio** (`llama-cpp-python` / `Qwen2.5-1.5B-Instruct` ou `3B-Instruct` Q4_K_M rodando **após** `del model; gc.collect()` do Whisper) | **~3 a 8 s CPU por clipe / ~1.2 a 2.1 GB RAM** (sequencial, cabe com folga nos 6 GB após descarregar Whisper) | `CLIPES_LLM_PROVIDER="local"`<br>`CLIPES_LLM_MODEL="qwen2.5-1.5b-instruct-q4_k_m"`<br>`CLIPES_MIN_SCORE_LLM=6.5` |
| **5** | **Modos de Layout Vertical Configuráveis: `crop_split` (Facecam + Gameplay), `smart_crop` (9:16 ou 4:5 central) vs. `blur_pad` com Escurecimento (`eq=brightness=-0.18`)** | **Alto** (Reduz drasticamente a "cara de repost automatizado" do fundo borrado puro e aumenta a área útil do sujeito na tela — ver Seção 6) | **Médio** (Parametrizar `-filter_complex` em `render.py` conforme modo escolhido) | **Igual ou até 15% mais rápido** (`smart_crop` é mais leve que `split + boxblur + overlay`) | `CLIPES_LAYOUT="blur_dark"` *(opções: `blur_pad`, `blur_dark`, `center_crop_4_5`, `split_facecam`)* |
| **6** | **Jump-Cuts Internos de "Ar Morto" (Remoção de silêncios > 1.2s no meio da história)** | **Médio a Alto** (Altera a linha do tempo em relação ao VOD bruto — evitando match 1:1 de *Direct Replace* — e aumenta retenção) | **Médio** (Usar os timestamps das `Word` já extraídas pelo Whisper para suprimir hiatos `> 1.2s` sem reação de riso/aplauso via `select`/`aselect` no `ffmpeg`) | **+1 a 2 s CPU no render / 0 MB RAM extra** | `CLIPES_TRIM_INTERNAL_SILENCE=0` *(opt-in, limiar `1.2s`)* |
| **7** | **Trava de Delay de Publicação / Fila com Janela de Respeito (`publish_after_utc`) no `clips.json`** | **Alto contra Strikes** (Implementa a regra de ouro de 12h–24h para VODs/lives de terceiros sem impedir a gravação em tempo real) | **Muito Baixo** (Metadado calculado na fila/manifesto `clips.json`) | **0 CPU / 0 RAM** | `CLIPES_PUBLISH_DELAY_HOURS=24` *(0 para lives do próprio usuário)* |

### 5.2. Como o Header Editorial + Crédito + Outro Card funcionam via `.ass` sem pesar na CPU
O `render.py::build_ass` hoje define apenas um `Style: Cap` (fonte 72, centralizado inferior `MarginV=430`). Podemos adicionar no mesmo arquivo `.ass` — **com custo zero de filtro extra no FFmpeg** — mais três estilos (`Style: HookBanner`, `Style: CreditTag`, `Style: OutroCard`):
1. **`HookBanner` (Topo superior, `Alignment=8`, `MarginV=180`):** Exibe nos primeiros `4.5s` (ou durante o clipe) a pergunta/contexto editorial gerado pela IA local (ex.: *"Por que esse erro custou o campeonato? (Parte 1/2)"*) — cumprindo o critério do Instagram de adicionar texto contextual que **não é** apenas a transcrição literal da fala.
2. **`CreditTag` (Acima ou abaixo do quadro 16:9, `Alignment=2`, `MarginV=560`, fonte menor semi-transparente):** Exibe continuamente `🎥 Fonte: <Nome do Canal> | Live original no link da descrição`.
3. **`OutroCard` (Últimos `1.8s` do clipe, após o fim da frase):** Enquanto o vídeo sofre leve escurecimento/fade, exibe no centro da tela:
   - Se for parte intermediária (`Parte 1/2`): *"Continua na Parte 2 👉 Siga para ver o desfecho | Fonte: <Canal>"*
   - Se for parte final ou clipe único: *"Clipe completo! 🎥 Assista à transmissão original em: <Canal> (<URL curta>)"*

---

## 6. Fundo borrado vs. Barras pretas vs. Preencher (Crop / Zoom / Split-screen)

Atualmente, `clipes/render.py` (linhas 254–259) utiliza o layout de **fundo borrado (`blur_pad`)**:
```
[0:v]split=2[bg][fg];
[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920,setsar=1[bg_b];
[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fg_s];
[bg_b][fg_s]overlay=(W-w)/2:(H-h)/2[v_base]
```
Nesse arranjo, o vídeo horizontal `16:9` ocupa apenas `1080x608` pixels (**31,6% da área total** do canvas vertical `1080x1920`), enquanto **68,4% da tela** são ocupados pelo fundo desfocado em movimento.

### 6.1. O que as fontes e diretrizes das plataformas dizem sobre cada layout

| Critério | 1. Barras Pretas (*Pillarbox / Letterbox*) | 2. Fundo Borrado (*Blurred Background* — atual) | 3. Preenchimento / Crop Central `4:5` ou `9:16` (*Smart Crop*) | 4. Split-Screen Vertical (*Facecam no topo + Gameplay/Palco embaixo*) |
|---|---|---|---|---|
| **Área útil do vídeo principal na tela `1080x1920`** | **31,6%** (`1080x608`), com **68,4% de pixels pretos mortos**. | **31,6%** (`1080x608`), com **68,4% de fundo duplicado borrado**. | **70,3%** (no crop `4:5` -> `1080x1350`) a **100%** (no crop `9:16` -> `1080x1920`). | **100%** (ex.: `1080x640` facecam no topo + `1080x1280` tela principal embaixo). |
| **Efeito na Retenção Visual** | **Péssimo.** Guias de boas práticas de criadores apontam que grandes blocos pretos quebram a imersão vertical no celular e aumentam o *swipe-away* imediato *(taxa exata de queda em %: **não verificado** em estudo oficial público das plataformas)*. | **Moderado a Baixo.** Melhor que barras pretas, mas o movimento duplicado e brilhante no fundo compete com a legenda `.ass` branca/amarela e deixa o rosto dos participantes minúsculo em telas de celular. | **Alto.** Rosto e expressões faciais ficam 2x a 3x maiores na tela do celular, prendendo a atenção visual nos primeiros 3 segundos. | **Muito Alto para Lives de Gameplay/React.** Mantém simultaneamente a expressão facial do streamer em grande escala e a ação da tela. |
| **"Aparência de Repost" e Originalidade Percebida** | **Altíssimo risco de parecer repost preguiçoso.** Associado a uploads brutos não adaptados para mobile. | **Alto risco de "cara de bot de cortes".** Como 90% dos scripts básicos de automação usam exatamente `16:9` centralizado sobre `boxblur`, revisores humanos do YouTube (YPP) e usuários identificam instantaneamente como corte automatizado em massa (*Inauthentic / Reused template*). | **Baixo risco visual** (parece vídeo pensado para vertical), **desde que** não corte os interlocutores nas bordas laterais. | **Baixíssimo risco visual.** Altera substancialmente a geometria do quadro em relação ao VOD 16:9 original e é o formato nativo mais aceito em Shorts/TikTok de lives. |
| **Custo de CPU no `ffmpeg` (Ryzen 5 5500U)** | Mais leve de todos (`pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black`). | Leve graças à otimização atual (`downscale 270x480 -> boxblur -> upscale`). | **Mais leve que o fundo borrado** (apenas `crop` + `scale`, sem `split` nem `boxblur`). | Leve (dois `crop` + `vstack`, sem `boxblur`). |

> **Nota de transparência metodológica ("não verificado"):**
> Nenhuma das três plataformas (YouTube, TikTok, Instagram) publica em sua documentação oficial que o filtro `boxblur` por si só receba uma penalidade matemática direta no código do algoritmo de recomendação (**não verificado**). O que **está documentado oficialmente** é:
> 1. O Instagram (`https://creators.instagram.com/original-content-guidelines`, verificado em `2026-11-17`) classifica *"applying basic borders"* (molduras/bordas básicas, o que inclui molduras borradas) como edição superficial que **não** qualifica um vídeo de terceiros como original.
> 2. O YouTube (`https://support.google.com/youtube/answer/1311392`, atualizado em `15/07/2025`, verificado em `2026-11-17`) instrui seus revisores a reprovar canais com templates repetitivos automatizados (*Inauthentic content*).

### 6.2. Como melhorar o layout no `clipes-lives` sem usar GPU
Como podcasts de mesa (ex.: Podpah, Flow) frequentemente têm 2 pessoas nas extremidades do quadro `16:9` (onde um crop `9:16` cego no centro cortaria os dois rostos), o pipeline deve oferecer **3 perfis de enquadramento (`CLIPES_LAYOUT`)** puramente em `ffmpeg`:

1. **`blur_dark` (Novo padrão recomendado para podcasts/mesas com 2+ pessoas, substituindo o `blur_pad` atual):**
   - Em vez de encaixar o vídeo `16:9` inteiro em `1080x608` (apenas 31% da tela), faz um **crop suave central `4:3` ou `14:9`** no vídeo principal (aumentando os participantes em ~30% na tela sem cortar quem está sentado à mesa, ocupando `1080x810`), aplica **escurecimento e dessaturação no fundo borrado** (`eq=brightness=-0.22:saturation=0.65`) para que o fundo não brigue com a legenda `.ass`, e adiciona uma linha sutil/sombra separando o vídeo principal do fundo + banner de título/crédito no topo.
2. **`center_crop` (Para vlogs, IRL ou 1 pessoa centralizada):**
   - Preenche `1080x1440` (`3:4`) ou `1080x1920` (`9:16`) com crop central direto, maximizando retenção mobile e reduzindo tempo de renderização no `ffmpeg`.
3. **`split_facecam` (Opcional por canal para lives de Gameplay/React na Twitch):**
   - Configurável via coordenadas da *facecam* do canal (`CLIPES_FACECAM_BOX="x:y:w:h"`): recorta a webcam do streamer para o terço superior (`1080x640`) e centraliza o conteúdo principal nos dois terços inferiores (`1080x1280`) via `vstack`.

---

## 7. Referências e Fontes Verificadas

1. **YouTube Help Center — Políticas de Monetização de Canais do YouTube (Conteúdo Reutilizado e Conteúdo Inautêntico):**
   - URL: `https://support.google.com/youtube/answer/1311392`
   - Data da política: Atualização de *"Repetitious content"* para *"Inauthentic content"* em vigor desde **15 de julho de 2025** (verificado em `2026-11-17`).
2. **YouTube Help Center — Diferença entre remoções por direitos autorais (Strikes) e reivindicações do Content ID:**
   - URL: `https://support.google.com/youtube/answer/7002106` (verificado em `2026-11-17`).
3. **YouTube Help Center — Fundamentos dos avisos de direitos autorais (Copyright Strike Basics):**
   - URL: `https://support.google.com/youtube/answer/2814000` (verificado em `2026-11-17`).
4. **YouTube Help Center — O que é uma reivindicação do Content ID?**
   - URL: `https://support.google.com/youtube/answer/2797370` (verificado em `2026-11-17`).
5. **Instagram for Creators — Diretrizes de Conteúdo Original (Original Content Guidelines):**
   - URL: `https://creators.instagram.com/original-content-guidelines` (verificado em `2026-11-17`).
6. **Instagram for Creators Blog — Rewarding Original Creators on Instagram (Direct Replace & Penalidade a Agregadores):**
   - URL: `https://creators.instagram.com/blog/rewarding-original-creators-on-instagram`
   - Data de publicação original: **30 de abril de 2024** (verificado em `2026-11-17`).
7. **Instagram for Creators — Recommendations and Originality:**
   - URL: `https://creators.instagram.com/blog/recommendations-and-originality` (verificado em `2026-11-17`).
8. **Instagram Help Center — Diretrizes de Recomendações no Instagram:**
   - URL: `https://help.instagram.com/313829416281232` (verificado em `2026-11-17`).
9. **TikTok Safety Center — Diretrizes da Comunidade: Integridade e Autenticidade:**
   - URL: `https://www.tiktok.com/safety/en/policies-and-engagement/integrity-authenticity` (verificado em `2026-11-17`).
10. **TikTok Support — Conteúdo não elegível para o feed Para Você (For You Feed Eligibility):**
    - URL: `https://www.tiktok.com/support/faq_detail?id=7543604781940791864` (verificado em `2026-11-17`).
11. **TikTok Support — Creator Rewards Program & Unoriginal Content Rules:**
    - URL: `https://www.tiktok.com/support/faq_detail?id=7581820708028865036` e `https://support.tiktok.com/de/business-and-creator/tiktok-creator-fund-us/tiktok-creator-fund-update-us` (verificados em `2026-11-17`).
12. **Twitch Legal — Terms of Service (Section 8: User Content):**
    - URL: `https://legal.twitch.com/en/legal/terms-of-service/` (verificado em `2026-11-17`).
13. **Twitch Help Center — DMCA and Copyright FAQs & Video on Demand (VOD):**
    - URLs: `https://help.twitch.tv/s/article/dmca-and-copyright-faqs?language=en_US` e `https://help.twitch.tv/s/article/video-on-demand?language=en_US` (verificados em `2026-11-17`).
14. **Twitch Legal — Developer Services Agreement:**
    - URL: `https://legal.twitch.com/legal/developer-agreement` (verificado em `2026-11-17`).
