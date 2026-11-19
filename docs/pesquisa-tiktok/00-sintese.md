# 00 - Síntese: como o TikTok analisa vídeos e como evitar que clipes de lives flopem

Data: 2026-11-18. Consolida os relatórios 01 (algoritmo), 02 (hook/retenção), 03 (métricas/diagnóstico) e 04 (clipes de lives/políticas) desta pasta. Nenhum fato novo foi adicionado; nada foi verificado externamente nesta síntese (todas as classificações vêm dos relatórios de origem, citados como [01]..[04]).

> **Revisado em 2026-11-18** (ver `00-revisao.md`): O5, O9, O10, C5, C6 e L1 foram conferidos direto nas páginas oficiais; foram acrescentados O12, L10 e L11 e uma nota em C2. Itens marcados **[verificado]** foram checados de forma independente na revisão.

Legenda usada aqui:
- **OFICIAL**: o TikTok diz em página própria (transparência, Newsroom, Community Guidelines, Creator Academy, Shop University).
- **PESQUISA**: paper identificável.
- **VENDOR**: estudo de empresa com amostra declarada (Buffer, Metricool, SociaVault etc.). Útil para direção, não para limiar.
- **FOLCLORE**: blogs/criadores sem metodologia; números a tratar como hipótese.
- **INFERÊNCIA**: dedução dos próprios relatórios.

---

## 1. Como a análise do TikTok funciona

### 1.1 O que é OFICIAL

| # | Fato | Fonte | Observação |
|---|------|-------|------------|
| O1 | A For You Feed calcula um *prediction score* por vídeo e por usuário e ordena por ele; a lista final passa por um *similarity check*. | Página de transparência [01] | Atual, mas genérica. |
| O2 | Interações previstas: curtir, compartilhar, comentar, seguir, tocar no som, marcar "Não tenho interesse"; também se o usuário vai assistir por certo tempo, terminar, pular ou favoritar. | Transparência [01] | |
| O3 | Sinais do vídeo: caption, som, hashtags, duração, hora, região, idioma. Sinais de dispositivo/conta pesam menos. | Transparência + Newsroom 2020 [01][02] | Pesos não publicados. |
| O4 | Terminar um vídeo mais longo é o exemplo oficial de sinal forte. | Newsroom jun/2020 [01][02] | Dado antigo, nunca contradito. |
| O5 | Número de seguidores e histórico de vídeos de sucesso **não são fatores diretos**. | Newsroom 18/06/2020 [01][02] **[verificado]** | Texto de 2020; a página de transparência atual não repete a frase (nem a contradiz). |
| O6 | "Não tenho interesse" limita aquele tipo de conteúdo para o usuário. | Transparência [01] | |
| O7 | Similarity check evita vídeos muito parecidos/mesmo som/mesmo criador em sequência e conteúdo duplicado. | Transparência + Newsroom [01] | Reposts idênticos são penalizados. |
| O8 | Vídeos recém-enviados ou em revisão de moderação podem ainda não ser recomendados. | Newsroom + transparência [01] | Atraso inicial não é flop. |
| O9 | Conteúdo pode ser tornado inelegível para a FYF sem ser violação ("poucas views não significam necessariamente que quebrou uma regra"); segue acessível por busca/perfil; o criador consegue ver nas análises se o vídeo ficou inelegível. | FYF Standards (lançada 25/08/2026, vigente 24/09/2026) [01] **[verificado]** | A página remete às seções "FYF Ineligible" de cada capítulo das Community Guidelines. |
| O10 | "Content is also ineligible for the FYF if it includes unoriginal or reused material without anything new" (conteúdo com material não original ou reaproveitado **sem nada novo** é inelegível para a FYF). | Community Guidelines, capítulo **Integrity and Authenticity** (lançado 25/08/2026, vigente 24/09/2026) **[verificado]** | Regra oficial. O texto oficial **não** define quanto de edição basta nem menciona marca d'água. |
| O10b | Não original fica fora do Creator Rewards; marca d'água/logo de terceiros é indicador de não original; reação só com expressão facial não conta como contribuição. | Creator Academy (só trechos de busca; fetch vazio de novo na revisão) e **TikTok Shop University** (contexto de vendedores) [04] | OFICIAL, mas de páginas secundárias/de outro contexto; tratar como forte indício de como a regra é aplicada, não como texto do guideline. |
| O11 | Contas Business só têm a Commercial Music Library. | TikTok Ads [04] | Contas pessoais: fontes divergem. |
| O12 | "You should only post content you created or have the right to share" e IP de terceiros não é permitido. | Community Guidelines, Integrity and Authenticity (2026) **[verificado]** | Base oficial para exigir permissão do streamer (P0/O3). |

### 1.2 PESQUISA (indireta)
- Paper Monolith (ByteDance, RecSys 2022): treino online com feedback em tempo real. Sustenta "o sinal chega rápido", mas não confirma que seja exatamente a FYF [01].

### 1.3 VENDOR (direção confiável, número não)
- Metricool 2026 (2,31 mi posts): ~73% das views vêm da FYP; views médias caíram ~31% com mais volume; hashtags +5% views; pergunta no vídeo/legenda +26% comentários; ~96% das views nos primeiros 10 dias [03].
- Buffer: frequência 2-5/semana = +17% views/post vs 1/semana, com retorno decrescente até 11+ [04]; conclusão maior em clipes muito curtos [03].
- Taxa de engajamento "boa": 1,5% a 4,25% conforme fórmula e amostra; não comparável sem checar a fórmula [03].
- Métricas de retenção/tempo assistido/fontes de tráfego não estão na API pública; só no TikTok Studio, com dados por post às vezes disponíveis por ~7 dias [03].

### 1.4 FOLCLORE (não usar como regra)
- **Funil em lotes** (teste com ~200-500 contas, depois ondas maiores; janela de 30-60 min) [01]. Plausível por inferência (um sistema preditivo precisa de dados reais de um vídeo novo), mas **não documentado**.
- **Ordem/pesos dos sinais** ("watch time > completion > shares > saves > comentários > likes", "share vale 5x like", "40-60% do peso") [01][03].
- **Limiares**: conclusão 25/35/40/50/70%; retenção 3 s ≥ 60-70%; 15 s ≥ 60%; 30 s ≥ 50%; "lote fixo de 1.000 views" [01][02][03].
- **Loop "completion > 100%"** [02].
- **Shadowban**, horário mágico, número exato de hashtags [01].
- **Ferramentas de "virality score"** (Higgsfield, AI Virality Predictor etc.): sem validação independente; servem no máximo como lint [03].

### 1.5 Modelo mental resultante
Cada clipe é julgado pelo próprio desempenho previsto/real (seguidores não ajudam diretamente). Um flop de ~200 views tem quatro explicações compatíveis com o oficial, que devem ser diferenciadas no diagnóstico:
1. **Inelegível para FYF** (não original, marca d'água, baixa qualidade, questão de direitos) - checar no Studio.
2. **Retenção/engajamento fraco** na exposição inicial (gancho ruim, tempo morto, clipe longo demais).
3. **Sinais negativos/duplicação** ("Não tenho interesse", repost parecido demais).
4. **Ainda em moderação** ou ruído normal (não julgar antes de 24-72 h).
5. **Publicado como privado** (adicionado na revisão): enquanto o app da Content Posting API não for auditado, o pipeline publica com `privacy_level=SELF_ONLY` em conta privada (README do projeto). Nesse modo o vídeo **não tem distribuição pública**; views baixas não são flop e não devem alimentar diagnóstico nem calibração (ver L10).

---

## 2. Contradições entre relatórios e como foram resolvidas

| # | Tema | O que cada relatório diz | Resolução |
|---|------|--------------------------|-----------|
| C1 | Duração-alvo | [01] 15-35 s; [02] 10-30 s (guias de gaming: 7-15 s para highlight simples); [04] checklist 15-45 s, guias 15-60 s; [03] "menor duração que contém o payoff". | Não há fonte oficial de duração ideal; o único fato oficial é que **terminar** é sinal forte. Regra adotada: **a menor duração que entrega o payoff**, faixa de referência **10-35 s** para momento único; 35-60 s exige justificativa (narrativa/escalada); >60 s só com narrativa. Calibrar com dados próprios. |
| C2 | Quantidade de hashtags | [02] 1-3; [04] 3-5 (convenção de vendor); Metricool: hashtags +5% views. | Nenhum número é oficial; hashtags são sinal oficial mas sem peso conhecido. Adotado: **2-5 hashtags relevantes** (jogo + nicho + no máx. 1 geral), sem stuffing. Nota da revisão: pesquisa anterior do projeto (`bkp-specs/pesquisa/A-plataformas-cursor.md`) registra um **limite de 5 hashtags por post desde ago/2025** (Search Engine Land, não anunciado oficialmente) e recomendação de 2-3 atribuída ao Creator Academy (via terceiro); 5 é, portanto, teto duro, não alvo. |
| C3 | Zona segura da base | [02]: 660 px (manypixels), 480 px (poster.ly), pior caso 1014 px com caption longa. | Adotado o mais conservador comum: **texto crítico entre Y 240 e Y 1260 px** e **X 120 a 900 px** (em 1080x1920). Valores de agências, não oficiais; confirmar no preview do app. |
| C4 | "Seguidores não são fator direto" | [01][02]: OFICIAL (Newsroom 2020); [03]: "não verifiquei", Baixa-Média. | Mantido como **OFICIAL (2020)**, pois [01] e [02] citam a página do Newsroom diretamente. Ressalva: texto de 2020. |
| C5 | Replays como sinal | [02]: "replays contam" (oficial); [01]: replay não aparece explicitamente na lista oficial. | **Resolvido na revisão [verificado]:** os dois estão certos em parte. O Newsroom 2020 cita replays só no contexto de um **usuário novo** treinando o feed ("your first set of likes, comments, and replays will initiate an early round of recommendations"); a página de transparência atual **não** lista replay entre as interações previstas. Replay como sinal de ranking do vídeo = **MÉDIA/não confirmado**. Loop continua recomendado por ser barato, não por ser oficial. |
| C6 | Política de conteúdo não original | [01]: FYF Standards (lançada 25/08/2026, vigente 24/09/2026) não continha a regra na versão lida; fontes secundárias sim. [02]: guidelines atualizadas set/2025 (TechCrunch ago/2025), BAIXA-MÉDIA. [04]: ALTA via trechos do Creator Academy/Shop University; enforcement ~13-15/set/2025, outras fontes citam 2026. | **Resolvido na revisão [verificado]:** a regra é **OFICIAL** e está no capítulo *Integrity and Authenticity* das Community Guidelines (versão lançada 25/08/2026, vigente 24/09/2026), não na página FYF Standards; por isso [01] não a achou. Versões anteriores das guidelines já tinham regra equivalente ("unoriginal or reproduced content without new or creative changes"), então a regra **não é nova**; a data "13-15/set/2025" de enforcement segue **não verificada** e é irrelevante para a ação. O que segue sem fonte oficial é **quanto** de edição basta. |
| C7 | Legenda queimada indexada (OCR/ASR) | [02]: não confirmado, BAIXA; [04]: TikTok indexa texto na tela e fala, MÉDIA (vendors); uma fonte diz que só texto do editor nativo é lido. | **BAIXA-MÉDIA**. Não muda a ação: a legenda queimada já se justifica por retenção (gente vendo sem som). Repetir a keyword também na caption (que é oficialmente informação do vídeo). |
| C8 | Tamanho da caption | [02]: curta; [04]: 150-300 caracteres (BAIXA, fonte única). | Adotado **caption curta** (aprox. 1-2 linhas visíveis), keyword no início, porque caption longa invade a zona segura e o número de 150-300 é de fonte única. |
| C9 | Efeito de pergunta nos comentários | Metricool +26% [03]; ttsvibes +44% [04] (sem metodologia). | Usar só a **direção** (pergunta ajuda comentários), citando Metricool. |
| C10 | Tamanho do lote de teste | [01]: 200-500; [03]: não há fonte para lote fixo de 1.000. | Coerentes: **não existe número oficial**. Nenhuma decisão do pipeline deve depender disso. |
| C11 | Posição da webcam | [02]: webcam no topo, jogo embaixo (mais citado); Eklipse varia por tipo (tutorial = base). | Padrão do projeto (split-barra, webcam em cima) mantido; exceção quando o HUD crítico do jogo cairia na zona da base. |

---

## 3. Checklist anti-flop unificado e priorizado

Prioridade: **P0** = eliminatório (risco de inelegibilidade, mute ou remoção); **P1** = alto impacto em retenção (sinais oficiais de tempo/conclusão); **P2** = descoberta e engajamento; **P3** = processo e medição.

### P0 - Elegibilidade e direitos (bloqueia publicação)
- [ ] Clipe tem **valor agregado visível** (não é corte cru): no mínimo hook/título próprio + legenda + layout 9:16 próprio (webcam+jogo) ou comentário/narração. [O10, 01, 04]
- [ ] **Sem marca d'água/logo de terceiros** (TikTok, Twitch, Kick, CapCut, outras contas). Logo próprio discreto é aceitável. Exportar do pipeline, nunca baixar de outra rede. [O10b, 04]
- [ ] **Sem música comercial reconhecível** (trilha/DJ da live, música do jogo licenciada) ou trecho cortado/substituído por áudio liberado. [02, 04]
- [ ] **Permissão do streamer** registrada (por escrito, cobrindo TikTok e monetização) quando o canal não é o do próprio dono. Crédito ou "no copyright intended" não substituem licença. [O12, 04]
- [ ] **Não é repost idêntico/quase idêntico** de clipe já publicado (inclusive em outra conta própria). [O7]

### P1 - Retenção
- [ ] **Frame 0 com ação, reação ou payoff** (ou teaser do pico); sem menu, loading, caminhada, "oi pessoal". [01, 02, 04]
- [ ] **No máximo ~1-2 s de contexto** antes do momento; setup cortado. [02]
- [ ] **Áudio presente desde o frame 0**; sem silêncio inicial. [02]
- [ ] **Texto-hook de 1 linha nos 0-3 s**, dentro da zona segura, sem depender do som. [02]
- [ ] **Duração = menor que entrega o payoff** (referência 10-35 s; ver C1). [01, 02, 03]
- [ ] **Sem tempo morto** (respawn, espera, silêncios longos). [02]
- [ ] **Termina logo após o payoff**, sem tela de "fim"; de preferência final que emenda no início (loop). [01, 02]
- [ ] **Legendas queimadas** legíveis, revisadas (nomes de streamer/jogo, gírias PT-BR). [02, 04]
- [ ] **Voz inteligível** acima do jogo. [02]
- [ ] **Saída 1080x1920**, nada crítico fora da zona segura. [02]

### P2 - Descoberta e engajamento
- [ ] **Keyword** (streamer + jogo + momento) no início da caption e, se possível, no texto em tela/fala. [04]
- [ ] **Caption curta**, com contexto sem spoiler do final. [02, 04]
- [ ] **2-5 hashtags** relevantes. [C2]
- [ ] **Pergunta/CTA genuíno** de comentário; nada de bait que provoque "Não tenho interesse". [01, 03, 04]
- [ ] **Variedade entre posts**: não publicar em sequência clipes com mesmo som/mesmo trecho. [O7]

### P3 - Processo e medição
- [ ] Cadência sustentável (~1/dia, 3-5+/semana) em janelas de horário em teste (fuso de Brasília). [04]
- [ ] Medir só posts **públicos**; posts `SELF_ONLY`/`private` da API não entram nas medianas (L10).
- [ ] Após postar: checar se áudio foi mutado e se o vídeo está elegível para recomendação no Studio. [O9, 04]
- [ ] Registrar por clipe: duração, tipo de hook, layout, legenda, horário, views 24 h/72 h/7 d, FYP%, tempo médio, % assistido completo, shares, saves, seguidores novos (exportar antes da janela de ~7 dias). [03]
- [ ] Não julgar antes de 24-72 h; comparar com a **mediana própria** de clipes de duração similar; mudar **uma variável** por vez; concluir só após ≥3 repetições por variante. [03]
- [ ] Se flopou: não apagar e repostar igual; reeditar (gancho/enquadramento) e publicar como clipe novo. [01]

### Árvore de diagnóstico resumida (de [03])
1. Inelegível no Studio ou FYP% baixo em vários vídeos → originalidade/direitos/restrição de conta.
2. Queda brusca 0-3 s → hook.
3. Queda gradual → ritmo/duração.
4. Queda num ponto → ver o que acontece naquele segundo.
5. Conclusão boa, views baixas → distribuição (caption/hashtags/horário/tema).
6. Views altas, poucos seguidores → falta motivo para seguir (identidade/série/CTA).
7. Variação dentro do normal → não reagir a um vídeo isolado.

---

## 4. Lacunas e ressalvas

- **L1 - Política de conteúdo não original (parcialmente resolvida na revisão).** Texto oficial fixado: Community Guidelines, *Integrity and Authenticity*, lançado 25/08/2026, vigente 24/09/2026: "Content is also ineligible for the FYF if it includes unoriginal or reused material without anything new" **[verificado]**. Continua em aberto: (a) o Creator Academy segue ilegível por fetch (só menu); (b) a data de enforcement "13-15/set/2025" [04] não foi verificada; (c) os detalhes "reação só com expressão facial não conta" e "filtros não tornam original" vêm da **TikTok Shop University** (contexto de vendedores), não do guideline geral [04]; (d) não existe critério oficial de "quanto de edição basta".
- **L2 - Benchmarks sem fonte primária.** Todos os limiares de retenção/conclusão (3 s ≥ 60-70%, 40/50/70% de conclusão, FYP% ≥ 70%), "+28% watch time com legendas", "40-92% assistem sem som", "15-20% menos watch time em 16:9", "8x impressões de busca", "+44% comentários", -14 LUFS (padrão da indústria, não confirmado pelo TikTok), Creator Rewards exigir >1 min (secundário) e o tamanho do lote de teste **não têm fonte primária**. Usar só como referência interna a calibrar com os dados do canal.
- **L3 - Aviso jurídico sem fonte.** A afirmação de que o Brasil não tem "fair use" e que a Lei 9.610/98 tem exceções limitadas é **inferência do autor do relatório 04, sem fonte nesta pesquisa**. Nada aqui é aconselhamento jurídico; clipes de terceiros (sobretudo monetizados) exigem consulta a advogado e permissão expressa.
- **L4 - Fontes enviesadas.** As fontes de gaming/clipping (Eklipse, Opus, Insights.gg, ClipSpeed, StreamLadder) vendem ferramentas de clipe; números delas são marketing até prova em contrário.
- **L5 - Zona segura.** Só é oficial no preview do Ads Manager; os px usados vêm de agências.
- **L6 - Dados oficiais antigos.** A explicação mais detalhada (Newsroom) é de jun/2020; a página de transparência atual é genérica.
- **L7 - Métricas.** Definições oficiais do Studio (support.tiktok.com) não foram lidas; não há A/B orgânico nativo confirmado; métricas de retenção não saem pela API pública (registro manual/CSV).
- **L8 - Música em gameplay.** Não há regra específica para música tocando dentro do jogo/live; assumido como risco.
- **L9 - Brasil.** Nenhum dado de horário/comportamento específico do Brasil.
- **L10 - Publicação privada (adicionado na revisão).** O pipeline publica no TikTok com `SELF_ONLY` (conta privada) até a auditoria do app na Content Posting API; no YouTube, `private`. Todo o checklist P3, a árvore de diagnóstico e a calibração dos critérios da seção 5 **só fazem sentido para posts públicos** (feitos manualmente no app ou após a auditoria). Registrar no CSV como o clipe foi publicado (público manual / privado via API) e excluir os privados das medianas.
- **L11 - YouTube Shorts fora do escopo (adicionado na revisão).** O projeto publica também no YouTube Shorts (botões YouTube/TikTok/Ambos), mas esta pesquisa cobre só o TikTok. A política de "conteúdo reutilizado" do YouTube (monetização) e o sistema de recomendação do Shorts não foram pesquisados; não aplicar os critérios desta síntese ao Shorts sem checar.
- **L12 - Creator Rewards (adicionado na revisão).** "Vídeo original > 1 min" para o Creator Rewards aparece de forma consistente em fontes secundárias (busca da revisão), mas não foi confirmado em página oficial; há guias que citam limiares diferentes. Só importa se o canal for monetizar; clipes de 10-35 s não seriam elegíveis de qualquer forma.

---

## 5. Critérios objetivos para o revisor de clipes

Cada critério tem resultado **PASSA / FALHA** (ou nota 0-2 quando indicado). Critérios marcados **[E]** são eliminatórios: uma falha bloqueia o botão Aprovar. Os limiares numéricos são **regras internas** derivadas da síntese (não regras do TikTok) e devem ser recalibrados após 20-30 clipes medidos [03].

### 5.1 Hook (0-3 s)
| ID | Critério | Como verificar | Regra |
|----|----------|----------------|-------|
| H1 [E] | Frame 0 não é tela parada/menu/loading/intro | Ver o primeiro frame | Falha se for menu, loading, tela preta, cena sem ação ou saudação |
| H2 | Ação, reação ou fala relevante começa até 1 s | Assistir 0-1 s; ou pico de áudio/transcrição | Passa se o evento começa em ≤1,0 s; ≤2,0 s = parcial (1 ponto) |
| H3 | Payoff ou teaser do pico aparece até 3 s, ou o hook textual promete o payoff | Assistir 0-3 s | Passa/Falha |
| H4 | Texto-hook na tela entre 0 e 3 s | Ver 0-3 s | Passa se há 1 linha de hook legível; falha se não há texto |
| H5 | Áudio desde o frame 0 | Forma de onda / ouvir | Falha se houver silêncio inicial perceptível (> ~0,5 s) |
| H6 | Entendível sem a live | Pergunta: "um estranho entende o que acontece?" | Passa/Falha (julgamento binário com rubrica fixa) |

### 5.2 Duração e ritmo
| ID | Critério | Regra |
|----|----------|-------|
| D1 | Duração total | 10-35 s = 2; 35-60 s = 1 (exige justificativa de narrativa); <10 s ou >60 s sem narrativa = 0 |
| D2 | Contexto antes do momento | ≤2 s = passa |
| D3 | Tempo morto | Falha se houver trecho > ~1 s sem ação/fala relevante (respawn, espera, silêncio) que poderia ser cortado |
| D4 | Final | Passa se termina em até ~1-2 s após o payoff, sem tela de "fim"; bônus se o último frame emenda no primeiro (loop) |

### 5.3 Legenda (queimada)
| ID | Critério | Regra |
|----|----------|-------|
| L1 | Presença | Falha se há fala relevante sem legenda |
| L2 | Legibilidade | Altura de letra ≥ 1/20 da largura (≥ 54 px em 1080 px), alto contraste (contorno/sombra), máx. 2 linhas, 1-3 palavras destacadas por vez |
| L3 | Precisão | Falha se houver erro em nome de streamer, jogo, personagem ou palavra que muda o sentido |
| L4 | Posição | Dentro da zona segura (5.4) |

### 5.4 Zona segura (1080x1920)
| ID | Critério | Regra |
|----|----------|-------|
| Z1 [E] | Resolução/proporção | 1080x1920, 9:16, sem barras pretas laterais de vídeo 16:9 |
| Z2 | Texto crítico (hook, legendas) | Inteiro dentro de Y 240-1260 px e X 120-900 px |
| Z3 | Informação crítica do jogo | HUD essencial (kill feed, placar, minimapa quando relevantes) fora dos 660 px da base e da coluna direita de 180 px |
| Z4 | Conferência no app | Preview com UI sobreposta checado em pelo menos 1 clipe por template de layout |

### 5.5 Áudio e música
| ID | Critério | Regra |
|----|----------|-------|
| A1 [E] | Música protegida | Falha se houver música comercial reconhecível (trilha da live, DJ, música licenciada do jogo) |
| A2 | Inteligibilidade da voz | Voz audível sobre o jogo em alto-falante de celular (referência: voz +3-5 dB sobre o jogo) |
| A3 | Nível | Sem clipping/distorção; loudness próximo de -14 LUFS (referência da indústria, não do TikTok) |
| A4 | Pós-publicação | Áudio não foi mutado pelo TikTok (checar após postar) |

### 5.6 Layout webcam + jogo
| ID | Critério | Regra |
|----|----------|-------|
| W1 | Preenche 9:16 | Sem webcam de canto copiada do 16:9; layout empilhado (split-barra) ou reframe do jogo |
| W2 | Webcam | Rosto visível e reagindo; webcam não cobre HUD essencial |
| W3 | Jogo | Recorte acompanha a ação (não só crop central que perde o evento) |
| W4 | Sem webcam detectada | Aceitável se o reframe do jogo + legenda + hook entregam valor agregado; registrar como variável de teste |

### 5.7 Caption e hashtags
| ID | Critério | Regra |
|----|----------|-------|
| C1 | Keyword | Streamer e/ou jogo + momento no início da caption |
| C2 | Tamanho | Curta (1-2 linhas visíveis), contexto sem spoiler |
| C3 | Hashtags | 2-5 relevantes (jogo, nicho, até 1 geral); falha com stuffing ou hashtag irrelevante |
| C4 | Engajamento | Termina com pergunta/opinião genuína; falha se for bait enganoso |
| C5 | Não duplicar | Caption não repete literalmente o texto-hook da tela; complementa |

### 5.8 Originalidade
| ID | Critério | Regra |
|----|----------|-------|
| O1 [E] | Valor agregado | Pelo menos 3 de: hook/título próprio, legenda queimada, layout/reframe 9:16 próprio, zoom/punch-in editorial, comentário/narração. Corte cru = falha |
| O2 [E] | Marca d'água | Nenhum logo/marca d'água de terceiros (TikTok, Twitch, Kick, CapCut, outra conta) |
| O3 [E] | Direitos | Permissão do streamer registrada quando o canal não é o próprio |
| O4 [E] | Duplicação | Não é igual/quase igual a clipe já publicado (mesmo trecho + mesmo hook) |
| O5 | Variedade | Não usa o mesmo som/trecho de um clipe postado nas últimas publicações |

### 5.9 Decisão
- Qualquer **[E]** falho → **reprovar** (reeditar).
- Sem falhas [E]: somar pontos dos demais critérios (PASSA = 1, FALHA = 0; D1 e H2 com 0-2). Registrar a nota junto com o clipe para calibrar contra métricas reais (tempo médio/duração, % assistido completo, FYP%), conforme [03]. O corte de aprovação deve ser definido após os primeiros 20-30 clipes, não antes.
