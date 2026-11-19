# 01 - Como o algoritmo de recomendacao do TikTok analisa um video

Pesquisa feita em 2026-11-18. Foco: clipes de lives em corte vertical 9:16.

## 1. Resumo

- O TikTok **nao publica pesos nem o mecanismo de "lotes de teste"**. O que e oficial: a For You Feed (FYF) calcula um *prediction score* por video para cada usuario (probabilidade de ver, terminar, curtir, compartilhar, comentar, seguir, marcar "Nao tenho interesse") e ordena por ele.
- O "funil em lotes" (teste com ~200-500 pessoas, depois expansao) e **consenso de criadores/blogs**, coerente com a logica de recomendacao, mas **nao documentado oficialmente**. Numeros de corte (40%, 50%, 70% de conclusao) sao contraditorios entre fontes: tratar como folclore.
- Oficial e importante: **seguidores e historico de videos bons nao sao fatores diretos** (Newsroom 2020), e videos recem-enviados/em revisao podem nao ser recomendados ainda.
- "Flopar em ~200 views" e explicavel sem teoria de shadowban: o video foi mostrado a um grupo pequeno, a retencao/engajamento previsto-real foi fraco, ou o video e **inelegivel para a FYF** (conteudo nao original/baixa qualidade/reaproveitado, regras de 2025-2026).
- Para clipes de lives, o maior risco e **originalidade/qualidade** (conteudo reaproveitado sem edicao criativa, marca d'agua de outro app) somado a **gancho fraco nos primeiros segundos**.

## 2. Achados com fonte e nivel de confianca

Legenda: **[OFICIAL]** = TikTok diz; **[PESQUISA]** = paper/estudo identificavel; **[FOLCLORE]** = blogs/criadores sem metodologia.

### 2.1 Sinais e ranking

| # | Achado | Fonte | Confianca |
|---|--------|-------|-----------|
| 1 | O sistema gera um *prediction score* por video; videos com maior score vao para a FYF; a lista ainda passa por um "similarity check" final. | https://www.tiktok.com/transparency/en/recommendation-system | OFICIAL |
| 2 | Interacoes previstas: curtir, compartilhar, comentar, marcar "Nao tenho interesse", seguir o autor, tocar no som. Tambem preve se o usuario vai gastar certo tempo vendo, terminar, pular ou favoritar. | idem | OFICIAL |
| 3 | Sinais de video: hora da postagem, regiao, idioma do autor, som, **duracao do video**, hashtags. Sinais de dispositivo/conta (idioma, pais, tipo de aparelho) pesam menos. | idem; https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you (jun/2020) | OFICIAL |
| 4 | Sinais fortes valem mais que fracos. Exemplo oficial de sinal forte: **terminar de assistir um video mais longo**. | Newsroom 2020 (link acima) | OFICIAL (dado antigo, mas nunca contradito) |
| 5 | "Nao tenho interesse" faz o TikTok **limitar aquele tipo de conteudo** no feed do usuario. | pagina de transparencia | OFICIAL |
| 6 | Diversidade: o similarity check substitui videos muito parecidos (ex.: mesmo som); evita dois videos seguidos do mesmo som/criador e conteudo duplicado/ja visto. | transparencia + Newsroom 2020 | OFICIAL |
| 7 | **Numero de seguidores e historico de videos de sucesso nao sao fatores diretos.** | Newsroom 2020 | OFICIAL |
| 8 | Videos recem-enviados ou em revisao de moderacao podem nao ser recomendados ainda; so videos que passaram a moderacao inicial entram no pool. | Newsroom 2020; transparencia | OFICIAL |
| 9 | Ordem/peso exato (watch time > completion > shares > saves > comentarios > likes) e multiplicadores (ex.: "share vale 5x like"). Fontes discordam entre si (watch time primeiro vs. completion primeiro; 40-50% de peso etc.). | https://femfounded.org/reference/tiktok-algorithm/ ; https://www.go-viral.app/blog/tiktok-algorithm-2026/ | FOLCLORE |
| 10 | Rewatch/loop, saves e follow pos-video como sinais fortes: plausivel (follow e "like/share/comment/favorite" aparecem entre as previsoes oficiais; replay nao e listado explicitamente pelo TikTok). | inferencia | MEDIA/BAIXA |
| 11 | O sistema de ranking industrial da ByteDance (paper Monolith, RecSys 2022) usa treino online com feedback do usuario em tempo real; ByteDance diz que roda em produtos seus, sem confirmar que seja exatamente a FYF. Sustenta a ideia de que o sinal chega rapido, nao os detalhes. | arXiv 2209.07663; https://www.nextplatform.com/2022/09/26/chinas-bytedance-intros-different-approach-to-recommendation-at-scale/ | PESQUISA (indireta) |

### 2.2 Funil em lotes (test batches)

| Achado | Fonte | Confianca |
|--------|-------|-----------|
| Video e mostrado a um grupo inicial pequeno (~200-500 contas, as vezes priorizando seguidores); se retencao/engajamento forem bons, passa para lotes maiores em ondas. | varios blogs: https://medium.com/@GTRsolutions/why-your-best-tiktok-videos-die-at-200-views-and-how-the-algorithm-actually-works-4e905e73bd38 ; https://vidgrow.co/blog/tiktok-algorithm-3-rules-2026/index.html ; https://contentmatch.io/how-to-break-out-of-tiktok-200-view-jail-2026/ | FOLCLORE (consistente, nao oficial) |
| Janela de avaliacao do primeiro lote: 30-60 min (uma fonte). | https://femfounded.org/reference/tiktok-algorithm/ | FOLCLORE |
| Limiares numericos: conclusao 25% / 40% / 50% / 70%; "taxa de 3s > 60%". Contraditorios entre si. | vidgrow, istantcalc.com, contentmatch.io | FOLCLORE, nao usar como meta dura |
| Videos antigos podem ser re-testados semanas depois ("cauda longa"). | blogs diversos | FOLCLORE |

O que o TikTok diz apenas indiretamente: a pagina oficial nao descreve distribuicao de videos novos. A existencia de um funil por estagios e **inferencia razoavel** (um sistema de predicao precisa de dados reais para refinar o score de um video sem historico), nao fato documentado.

### 2.3 Por que um video "flopa" (~200 views)

Causas, separadas por nivel de evidencia:

1. **Inelegivel para a FYF (OFICIAL, parcial).** O TikTok confirma que conteudo "nao adequado para audiencia ampla" e tornado inelegivel e que baixa visualizacao **nao significa necessariamente violacao**; o criador pode checar nas analises se o video foi inelegivel. Videos inelegiveis continuam acessiveis por busca/perfil. Pagina de padroes FYF: https://www.tiktok.com/community-guidelines/en/fyf-standards (lancada 25/08/2026, em vigor 24/09/2026). Fontes secundarias dizem que conteudo **reaproveitado/nao original, sem edicao criativa, com marca d'agua de outro app ou de baixa qualidade** tambem e excluido da FYF, e que contas reincidentes podem ficar temporariamente inelegiveis (https://www.relevantaudience.com/tiktok-ads/tiktok-algorithm-for-you-feed/ ; https://openclip.app/guides/tiktok-slideshow-unoriginal-content-flag). **Nao consegui confirmar o texto exato dessa regra em fonte oficial** (a pagina fetchada nao a continha): confianca MEDIA.
2. **Retencao fraca no lote inicial (FOLCLORE coerente com o oficial).** Como tempo assistido e conclusao sao sinais previstos oficialmente, um gancho que perde as pessoas nos 2-3 primeiros segundos reduz o score. Estudo do Buffer (via netinfluencer) alerta que duracao sozinha nao determina sucesso, mas ressalta gancho nos 2 primeiros segundos: https://www.netinfluencer.com/tiktok-longer-videos-outperform-short-form-content-study-reveals/. Dados como "70-85% de retencao em 3s = 2.2x views" (insights.ttsvibes.com) nao tem metodologia: descartar.
3. **Sinais negativos (OFICIAL):** "Nao tenho interesse" e pular rapido; limitam distribuicao daquele tipo de conteudo.
4. **Similaridade/duplicacao (OFICIAL):** conteudo duplicado ou muito parecido (mesmo som, repeticao) e filtrado pelo similarity check.
5. **Moderacao pendente (OFICIAL):** video recem-enviado/em revisao nao e recomendado ainda; atraso inicial nao e flop.
6. **Fatores de conta/horario/hashtag (FOLCLORE):** "shadowban", horario ideal, numero de hashtags. Sem evidencia oficial; hashtags, som e hora aparecem como sinais de video, mas sem peso conhecido.

## 3. Implicacoes praticas para clipes de lives (9:16)

Marque o que e baseado em oficial (O) ou heuristica (H).

1. **Originalidade visivel (O/H):** nao postar o corte cru da live. Adicionar edicao criativa (enquadramento 9:16 proprio, legendas, zoom, titulo/gancho, overlay do jogo/webcam). Evitar marca d'agua de outros apps; exportar limpo, sem logo da plataforma de origem.
2. **Gancho nos primeiros 1-3 s (H, apoiado pelo sinal oficial de tempo assistido/conclusao):** abrir no momento de pico (reacao, jogada, punchline), nao no contexto. Legenda grande no frame 0. Cortar silencio/preambulo.
3. **Duracao curta e fechada (O parcial):** duracao e sinal oficial, e "terminar de assistir" e o exemplo oficial de sinal forte. Priorizar clipes que **terminam no pico** (15-35 s tipico) para maximizar conclusao; para loop, fazer o final emendar no inicio (H, rewatch nao e confirmado oficialmente).
4. **Gerar sinais de intencao, nao so likes (O/H):** CTA que provoque comentario/compartilhamento natural (pergunta, polemica leve); compartilhamento e comentario sao interacoes previstas oficialmente. Evitar bait que induza "Nao tenho interesse".
5. **Variedade entre posts (O):** nao postar varios clipes quase iguais com o mesmo som/mesmo trecho; o similarity check e o filtro de duplicados penalizam. Variar sons, ganchos e momentos.
6. **Nao esperar historico para furar (O):** seguidores nao sao fator direto; cada clipe e julgado pela propria retencao. Vale testar varios clipes curtos em vez de apostar em um.
7. **Medir no painel do TikTok (O):** checar nas analises se o video esta inelegivel para recomendacao (diferenciar flop por retencao de flop por elegibilidade). Acompanhar retencao media, % assistido completo e taxa de follow pos-video.
8. **Paciencia e re-teste (H):** nao apagar/repostar imediatamente; repost identico cai em duplicacao. Se flopou, mudar gancho/enquadramento e postar como clipe novo.
9. **Para o pipeline do projeto (H):** priorizar na selecao de clipes os trechos com pico emocional nos primeiros 2 s, cortar o inicio morto, legendar automaticamente, manter qualidade de encode (1080x1920) e nao inserir marca d'agua externa.
10. **Metas numericas:** usar 40/50/70% de conclusao apenas como referencia interna a ser calibrada com os proprios dados, nao como regra do TikTok.

## 4. Lacunas e cuidados

- Nao encontrei documento oficial do TikTok sobre lotes de teste, pesos dos sinais, nem sobre "200 views". Qualquer numero especifico e estimativa de terceiros.
- Newsroom "How TikTok recommends videos #ForYou" e de **junho/2020**; a pagina de transparencia atual e mais recente mas generica.
- A pagina fetchada de padroes FYF (ago-set/2026) nao listou "nao original/reaproveitado"; essa regra veio de fontes secundarias com datas conflitantes. Verificar na secao "FYF Ineligible" das Diretrizes da Comunidade e no Creator Academy antes de assumir.
- A pagina de suporte "How TikTok recommends content" retornou so o titulo no fetch; nao foi usada.

## 5. Fontes

- https://www.tiktok.com/transparency/en/recommendation-system (oficial)
- https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you (oficial, 2020)
- https://www.tiktok.com/community-guidelines/en/fyf-standards (oficial, 2026)
- https://arxiv.org/abs/2209.07663 e https://www.nextplatform.com/2022/09/26/chinas-bytedance-intros-different-approach-to-recommendation-at-scale/ (Monolith)
- https://www.netinfluencer.com/tiktok-longer-videos-outperform-short-form-content-study-reveals/ (estudo Buffer, secundario)
- Folclore/blogs: https://medium.com/@GTRsolutions/why-your-best-tiktok-videos-die-at-200-views-and-how-the-algorithm-actually-works-4e905e73bd38 ; https://femfounded.org/reference/tiktok-algorithm/ ; https://www.go-viral.app/blog/tiktok-algorithm-2026/ ; https://vidgrow.co/blog/tiktok-algorithm-3-rules-2026/index.html ; https://contentmatch.io/how-to-break-out-of-tiktok-200-view-jail-2026/ ; https://www.relevantaudience.com/tiktok-ads/tiktok-algorithm-for-you-feed/ ; https://openclip.app/guides/tiktok-slideshow-unoriginal-content-flag
