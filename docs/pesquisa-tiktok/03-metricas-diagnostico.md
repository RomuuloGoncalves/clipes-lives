# 03 - Métricas, diagnóstico e pré-avaliação de vídeos no TikTok

Data da pesquisa: 2026-11-18. Escopo: clipes curtos (cortes de lives) para TikTok.

**Aviso de honestidade:** quase todas as fontes abaixo são blogs de marketing/vendors. Não consegui acessar a Central de Ajuda oficial do TikTok (support.tiktok.com) pelas buscas; as definições vêm de guias de terceiros. Limiares numéricos (ex.: "70% de conclusão") são **heurísticas não oficiais**. Confiança indicada em cada achado: Alta / Média / Baixa.

---

## 1. Métricas do TikTok Studio (por vídeo)

| Métrica | O que significa | Como ler | Confiança |
|---|---|---|---|
| Tempo médio assistido (average watch time) | Média de segundos assistidos por view | Sempre dividir pela duração do vídeo. Referência informal: > 50% da duração. Baixo = hook/intro falhou | Média |
| Assistiu ao vídeo todo (watched full video / finish rate) | % de viewers que chegaram ao fim | Depende da duração: só comparar vídeos de tamanho parecido | Média |
| Retenção (gráfico) | Curva de % de audiência por segundo | Ler a FORMA e onde cai, e o que está na tela nesse instante. Pode exigir conta Business/Studio | Média |
| Fontes de tráfego | Para Você (FYP), Perfil, Seguindo, Som, Busca, Hashtag, Outros | FYP alto = distribuição algorítmica; Perfil/Seguindo alto = só base atual | Média |
| Novos seguidores | Seguidores ganhos pelo vídeo | Views altas + poucos seguidores = falta motivo para seguir | Média |
| Compartilhamentos, salvamentos, comentários, curtidas | Sinais de engajamento | Shares/saves indicam valor; comentários podem ser provocados com pergunta | Média |

Notas práticas:
- Dados por post às vezes só ficam disponíveis por ~7 dias (Buffer); esperar pelo menos 24 h antes de julgar, pois há atraso de atualização. Exportar/registrar números cedo.
- Retenção, tempo assistido, fontes de tráfego e buscas NÃO estão na API pública de desenvolvedores do TikTok; só no Studio (portanto automação do projeto precisaria de entrada manual/CSV ou scraping do Studio). Fonte: Hootsuite (abaixo). Confiança: Média.

Fontes:
- https://blog.hootsuite.com/tiktok-analytics/
- https://buffer.com/resources/tiktok-analytics
- https://www.sirency.com/blog/tiktok-analytics-explained
- https://sproutsocial.com/insights/tiktok-metrics/
- https://www.dashsocial.com/blog/tiktok-analytics

## 2. O que o algoritmo parece pesar

- Consenso direcional entre blogs: tempo assistido relativo (% do vídeo) e taxa de conclusão são os sinais mais fortes, seguidos de rewatch, shares e saves. Confiança: Média (consenso, mas sem fonte primária).
- Afirmação de que seguidores/histórico não são fator direto de recomendação aparece citada como declaração da empresa; não verifiquei no texto oficial. Confiança: Baixa-Média.
- Limiares circulando (todos NÃO verificados e conflitantes): "70%+ de conclusão para viralizar" (antes 50% em 2024); "35% de conclusão + ~1,5% engajamento libera para pool de 5-10 mil"; "conclusão+watch time = 40-60% do peso". **Não existe fonte confiável para um lote de teste fixo de 1.000 views.** Confiança: Baixa. Usar apenas como ordem de grandeza e calibrar com os próprios dados.
- A maior parte da vida do vídeo é curta: ~96% das views e 98% das interações chegam nos primeiros 10 dias (Metricool). Confiança: Média.

Fontes:
- https://meikuio.com/tiktok-algorithm-2026/ (separa confirmado/contestado/mito)
- https://www.go-viral.app/blog/tiktok-algorithm-2026/
- https://www.socialync.io/blog/tiktok-algorithm-2026-what-works-now
- https://metricool.com/press-release-tiktok-study-2026/

## 3. Benchmarks

**Estudo Metricool 2026** (2,31 mi de posts, 92 mil contas, jan-fev 2025 vs 2026; divulgado 12/05/2026): views médias caíram ~31% com mais volume; FYP = ~73% das views; vídeo tem 5,6x views e 7,8x interações vs imagem/carrossel; hashtags +5% views e ~+10% interações; pergunta no vídeo/legenda +26% comentários; média de 2,54 vídeos/semana por conta. Confiança: Média (dados de vendor, acessados via press release e cobertura secundária).
- https://metricool.com/press-release-tiktok-study-2026/
- https://www.socialmediatoday.com/press-release/20260511-metricools-2026-tiktok-study-reveals-31-drop-in-video-views-amid-content-1/
- https://www.netinfluencer.com/tiktok-content-volume-up-nearly-80-percent-in-early-2026-as-per-post-performance-declined-report-finds/

**Taxa de engajamento** varia enormemente por fórmula (sobre seguidores vs sobre views) e amostra: Rival IQ mediana 2,01%; Hootsuite 1,5%; Socialinsider 2,60%; SociaVault (150 mil contas) mediana 4,25%, de 7,84% (nano, 1-10k) a 1,84% (mega, 500k+). Conclusão: **não comparar com benchmark sem checar a fórmula**; melhor comparar com seu próprio histórico. Confiança: Média para a dispersão, Baixa para qualquer valor isolado.
- https://sociavault.com/blog/good-engagement-rate-tiktok
- https://owlstat.io/blog/what-is-a-good-tiktok-engagement-rate/
- https://improvado.io/blog/social-media-benchmarking

**Benchmarks de FYP%:** guias sugerem meta >=70% e <50% = vídeo não "quebrou" a bolha (não oficial); coerente com os ~73% da Metricool. Confiança: Média-Baixa.

**Duração:** Buffer indica conclusão maior em clipes muito curtos e vídeos de 3-10 min com mais engajamento/watch time em seu dataset. Para clipes de live, comparar conclusão só entre vídeos de duração similar (faixas: <15s, 15-30s, 30-60s, 60s+). Confiança: Média.

## 4. Testes A/B de hooks e títulos

- **Não encontrei recurso nativo de A/B de hook/título para posts orgânicos no TikTok.** Existe Split Test no TikTok Ads Manager (divide audiência sem sobreposição; guias citam 90% de confiança, 7 dias para conversão, "Estimated Testing Power" > 80%). YouTube tem teste nativo de título/thumbnail (até 3 variantes, só desktop, não para Shorts). Confiança: Média.
  - https://coinis.com/how-to/ab-test-tiktok-ads
  - https://support.google.com/youtube/answer/16391400?hl=en
- **Método orgânico caseiro (inferência, não validado):** como não há controle de audiência, o teste é sequencial e ruidoso. Recomendações dos guias: mudar UMA variável; testar hooks realmente diferentes (pergunta, visual inesperado, pattern interrupt, texto na tela), não retoques; repetir cada variante várias vezes (um guia diz mínimo 3 por variante; Shopify fala em centenas de impressões). Sem consenso numérico. Confiança: Baixa-Média.
  - https://ttcalculator.net/guides/growth/tiktok-ab-testing-content/
  - https://www.shopify.com/blog/ab-testing-social-media
- **Alternativa mais limpa para o projeto:** Spark Ads/pago pequeno com 2 hooks do mesmo clipe (audiência dividida), ou dois clipes do mesmo streamer/mesma faixa de horário. Comparar por métricas de retenção (conclusão, tempo médio), não por views.
- Regras de higiene: mesmo horário/dia da semana, mesma duração, mesma conta; comparar a janela 24-72 h; registrar em planilha (id do clipe, hook, tipo, duração, game, streamer, métricas).

## 5. Pré-avaliar um vídeo antes de postar

**Ferramentas/IA (todas vendor, sem validação independente encontrada):**
- Higgsfield Virality Predictor (lançado ~09/05/2026, preview experimental; clipes até 15 s; devolve virality score, hook score, hold rate e mapa de calor cerebral). Citado também o código aberto tribeV2_ViralAnalyser (local, sem limite de 15 s). 
  - https://pasqualepillitteri.it/en/news/2273/higgsfield-virality-predictor-hook-score-hold-rate-2026
  - https://nahornyi.ai/en/news/higgsfield-predicts-video-virality-before-posting
- AI Virality Predictor: score 0-100 por HistGradientBoosting treinado em ~10 mil linhas de engajamento (Kaggle/HuggingFace); sem métricas de acerto divulgadas. https://ai-virality-predictor.vercel.app/
- Viral-V, ViralScore AI, Viral Hook Analyzer, Inflowave: análise de hook de 3 s e curva de retenção prevista. Inflowave admite que nenhum score garante viralidade: nota alta = "bloqueadores óbvios removidos". https://inflowave.io/tools/video-predictor
- **Conclusão:** nenhum estudo independente/peer-reviewed achado mostrando que esses scores predizem desempenho real. Tratá-los como lint de hook/ritmo, não como previsão. Confiança: Alta nessa cautela.

**Heurísticas próprias (inferência baseada nos achados; testáveis):** como o projeto já gera clipes automaticamente, construir um score interno em vez de depender de vendor:
1. Hook (0-3 s): há ação/fala/pico de áudio/reação nos primeiros 3 s? Legenda/texto em tela ligado? Sem intro, sem tela parada. (Fonte da ideia: Buffer, hooks em 2-3 s.)
2. Densidade de eventos: picos de volume, risadas, kills, mudança de cena por segundo.
3. Clareza sem contexto: um estranho entende o que está acontecendo sem a live?
4. Duração: preferir a menor duração que contém o payoff (conclusão cai com a duração).
5. Fim/loop: payoff perto do final e corte que convida a rewatch.
6. Legibilidade: layout 9:16, legenda grande, webcam visível (o projeto já tem layout split webcam+jogo).
7. Pergunta/CTA de comentário (Metricool: +26% comentários com pergunta).
8. Áudio: fala audível, sem copyright de música (ver outros docs da série).
- **Calibração:** registrar o score previsto e comparar com conclusão/tempo médio reais em 20-30 clipes; ajustar pesos (regressão simples). Esse ciclo é o único jeito de saber se a heurística presta. Confiança do método: Média (prática padrão de ML, mas não validada neste contexto).
- IA multimodal (ex.: modelo de visão/áudio com transcrição) pode pontuar hook e clareza com rubrica fixa; usar rubrica binária e consistente, e validar contra métricas reais.

## 6. Framework de diagnóstico passo a passo (vídeo que flopou)

Pré-condições: esperar >=24 h (idealmente 48-72 h); comparar com a MEDIANA dos seus últimos 10-20 vídeos de duração similar, não com benchmark externo; ter exportado os números (janela de 7 dias do Studio).

**Passo 0 - Confirmar que é flop.** Views < ~50% da mediana própria após 48-72 h? Se sim, seguir. (Metricool: ~96% das views chegam em 10 dias; depois de 3 dias o vídeo raramente reacende.)

**Passo 1 - Distribuição (fontes de tráfego).**
- FYP% baixo (<50%) e views vêm de Perfil/Seguindo: o vídeo não passou do teste inicial. Ir ao passo 2.
- FYP alto mas views baixas: teste inicial passou pouco; ir ao passo 2 também.
- Pouca mudança de fonte, mas queda geral de todas as contas: pode ser sazonal/saturação (Metricool: -31% views médias). Comparar com outros vídeos recentes.
- FYP caiu de forma sustentada em vários vídeos: suspeitar de elegibilidade/restrição (direitos autorais, violação de diretrizes, conteúdo não original/reupload); checar Status da conta e notificações. (Fonte: Sociality/blogs; Confiança: Baixa-Média.)

**Passo 2 - Retenção (curva).**
- Queda brusca nos 0-3 s: problema de HOOK (abertura). Ação: cortar o início, começar no pico, texto de gancho.
- Queda gradual/constante: ritmo/valor insuficiente. Ação: encurtar, cortar tempo morto.
- Queda num ponto específico: ver o que ocorre naquele segundo (cena confusa, silêncio, spoiler antecipado do payoff).
- Pico de rewatch no fim: bom sinal (loop).

**Passo 3 - Conclusão e tempo médio vs duração.**
- Tempo médio < ~50% da duração e conclusão baixa: abertura/ritmo (passo 2).
- Conclusão boa mas views baixas: conteúdo funciona, falta alcance inicial: revisar horário, título/legenda/hashtags/som, tema/nicho (Buffer: "good completion but low views -> investigate distribution").

**Passo 4 - Engajamento e conversão.**
- Poucos shares/saves com boa conclusão: sem valor compartilhável/identificação.
- Views altas, poucos seguidores novos: o clipe não dá razão para seguir (perfil sem identidade; falta CTA/continuidade da série).

**Passo 5 - Contexto de publicação.** Horário, dia, duração, jogo/streamer, legenda, hashtags, som. Comparar com seus melhores clipes.

**Passo 6 - Classificar a causa (árvore):**
1. Hook fraco (queda 0-3 s) -> reeditar abertura.
2. Ritmo/duração (queda gradual) -> encurtar.
3. Tema/escolha de momento fraco (retenção boa só nos engajados, baixo alcance) -> melhorar seleção de clipes.
4. Distribuição/conta (FYP% baixo em muitos vídeos) -> verificar restrições e originalidade.
5. Ruído/sazonalidade (variação normal) -> não reagir a um único vídeo.

**Passo 7 - Registrar e testar.** Anotar a hipótese, mudar UMA variável no próximo clipe similar, medir de novo. Só concluir após >=3 repetições por variante (heurística).

**Regra anti-ruído:** nunca tirar conclusão de um único vídeo; usar mediana de 10+ clipes e variação típica (ex.: considerar flop se estiver abaixo do 25º percentil próprio).

## 7. Sugestões de implementação no projeto (inferência)

- Tabela/CSV de acompanhamento por clipe: id, data, streamer, jogo, duração, score_prévio, hook_tipo, destino (TikTok/YouTube), views_24h/72h/7d, FYP%, tempo_médio, conclusão, shares, saves, novos_seguidores.
- Score pré-postagem automático a partir do que o pipeline já tem (transcrição, picos de áudio, duração, presença de webcam) + checklist no Discord antes do botão Aprovar.
- Alerta de diagnóstico: comparar métricas inseridas com a mediana e sugerir a causa mais provável via a árvore do passo 6.

## 8. Lacunas

- Definições oficiais do Studio (support.tiktok.com) não verificadas diretamente.
- Nenhuma validação independente de acerto de ferramentas de predição de viralidade.
- Limiares de conclusão/FYP por faixa de duração: sem fonte primária; precisam ser calibrados com os dados do canal.
- Ausência de A/B nativo orgânico: confirmar na versão atual do app/Studio.
