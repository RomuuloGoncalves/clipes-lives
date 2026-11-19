# 00 - Revisão crítica da síntese (pesquisa TikTok / anti-flop)

Data: 2026-11-18. Objeto: `00-sintese.md`, conferida contra `01-algoritmo.md`, `02-hook-retencao.md`, `03-metricas-diagnostico.md` e `04-clipes-lives-politicas.md`, com checagem independente na web dos pontos críticos.

## 1. Veredito

**Aprovada com correções.** A síntese é fiel aos relatórios na grande maioria dos pontos, separa bem oficial, vendor e folclore, e não promove nenhum número de folclore a regra do TikTok (os limiares da seção 5 estão rotulados como regras internas a calibrar). Os problemas foram:

1. **Confiança subestimada** (não superestimada) no ponto mais importante: a regra de conteúdo não original foi tratada como "provável (MÉDIA-ALTA)", com datas e texto "não resolvidos". A regra é **oficial e está em vigor**, só que num capítulo das Community Guidelines que os relatórios não leram.
2. **Mistura de fontes em O10:** texto do guideline geral, trechos do Creator Academy e páginas da TikTok Shop University (contexto de vendedores) apareciam como um único fato OFICIAL.
3. **C5 (replays) dado como não resolvido**, quando a própria fonte citada resolve a dúvida.
4. **Lacuna operacional grave:** a síntese não considera que o pipeline publica no TikTok como `SELF_ONLY` (conta privada) até a auditoria do app. Posts privados não têm distribuição, então medi-los como "flop" ou usá-los para calibrar o revisor seria um erro.
5. Lacunas menores: YouTube Shorts fora do escopo, teto de 5 hashtags, Creator Rewards.

Não achei afirmação da síntese que contradiga os relatórios, nem número de vendor/folclore apresentado como oficial.

## 2. O que foi verificado de forma independente

| # | Ponto | Fonte checada | Resultado |
|---|-------|---------------|-----------|
| V1 | Regra de conteúdo não original / reaproveitado | [Community Guidelines - Integrity and Authenticity](https://www.tiktok.com/community-guidelines/en/integrity-authenticity/) (lançado 25/08/2026, vigente 24/09/2026) | **Confirmado, OFICIAL.** Texto: "Content is also ineligible for the FYF if it includes unoriginal or reused material without anything new." Mesma página: "You should only post content you created or have the right to share" e proibição de violar IP de terceiros. **Não** menciona marca d'água nem define quanto de edição basta. Busca indica que versões anteriores já tinham regra equivalente ("unoriginal or reproduced content without new or creative changes"), ou seja, a regra não é nova. |
| V2 | FYF Standards: inelegível sem ser violação, visível nas análises | [FYF Standards](https://www.tiktok.com/community-guidelines/en/fyf-standards) | **Confirmado.** Lançada 25/08/2026, vigente 24/09/2026. Diz que poucas views não significam necessariamente violação, que o vídeo segue acessível por busca/perfil e que o criador vê nas análises se ficou inelegível. Confirmado também que a página **não** contém a regra de não original (remete às seções "FYF Ineligible" de cada capítulo); isso explica a divergência de [01]. |
| V3 | Sinais oficiais de ranking | [Transparência - recommendation system](https://www.tiktok.com/transparency/en/recommendation-system) | **Confirmado.** Interações previstas: curtir, compartilhar, comentar, "Não tenho interesse"; seguir o autor ou interagir com o perfil; terminar, pular ou favoritar; passar certo tempo vendo; tocar no som. Duração do vídeo é sinal. Similarity check entre vídeos consecutivos (ex.: mesmo som). Vídeo novo passa por moderação antes de poder ser recomendado. **Replay não aparece.** Pesos: não publicados. |
| V4 | Seguidores não são fator direto; sinal forte; duplicação; replay | [Newsroom, 18/06/2020](https://newsroom.tiktok.com/en-us/how-tiktok-recommends-videos-for-you) | **Confirmado:** "neither follower count nor whether the account has had previous high-performing videos are direct factors"; terminar um vídeo mais longo é exemplo de indicador forte; não recomenda conteúdo duplicado, já visto ou spam; vídeos recém-enviados/em revisão podem ficar de fora. **Replay:** citado só no onboarding de usuário novo ("your first set of likes, comments, and replays will initiate an early round of recommendations"). |
| V5 | Creator Academy (originality policy) | [Creator Academy](https://www.tiktok.com/creator-academy/article/tiktok-originality-policy) | **Não verificável:** o fetch voltou só com o menu, como em [04]. Os detalhes atribuídos a ele (Creator Rewards, marca d'água) continuam dependendo de trechos de busca e da Shop University. |
| V6 | Creator Rewards exige vídeo original > 1 min | Busca (só fontes secundárias: Nuelink, Toptal, Metricool, ttcalculator etc.) | **Consistente em fontes secundárias, sem página oficial lida**; há guias com limiares diferentes. Mantido como não confirmado. |
| V7 | Publicação do próprio projeto | `README.md` do repositório (Content Posting API) | **Confirmado no código/documentação:** TikTok `privacy_level=SELF_ONLY` em conta privada e YouTube `private` até a auditoria. |

Não verificados (ficam como estavam): limiares de retenção/conclusão, tamanho de lote, zona segura em px, -14 LUFS, números Metricool/Buffer (só via relatórios), data de enforcement "13-15/set/2025".

## 3. Erros e correções

| # | Local na síntese | Problema | Tipo | Correção |
|---|------------------|----------|------|----------|
| E1 | O10, C6, L1 | Regra de não original tratada como "provável (MÉDIA-ALTA)", com "datas e texto exato não resolvidos". | Confiança subestimada / lacuna resolvível | Regra é OFICIAL (V1). Texto e data fixados. Em aberto só: quanto de edição basta, data de enforcement de 2025 e o conteúdo do Creator Academy. |
| E2 | O10 | Juntava num fato OFICIAL o guideline geral, trechos do Creator Academy e a TikTok Shop University (vendedores). "Marca d'água é indicador de não original" não está no guideline geral. | Fontes misturadas | Separado em O10 (guideline, verificado) e O10b (Creator Academy/Shop University, indício de aplicação). Referência do item de marca d'água no P0 passou a apontar O10b. |
| E3 | C5 | "Não resolvido". A fonte resolve: replay é citado em 2020 só para usuário novo; não está na lista atual. | Contradição mal resolvida | Resolvido: replay como sinal de ranking do vídeo = MÉDIA/não confirmado. A frase de [02] "replays contam" (ALTA) está superestimada no relatório 02. |
| E4 | Seção 1.5, P3, L | Não considera que posts via API saem privados (`SELF_ONLY`/`private`). | Lacuna importante | Adicionada causa 5 em 1.5, item no P3 e L10: medir só posts públicos, registrar o modo de publicação, excluir privados das medianas e da calibração. |
| E5 | — | YouTube Shorts (o projeto publica nos dois) não é coberto; risco de aplicar regras do TikTok ao Shorts. | Lacuna | Adicionada L11. |
| E6 | C2 | 2-5 hashtags sem mencionar o teto de 5 hashtags por post (ago/2025, não oficial) já registrado em pesquisa anterior do projeto. | Lacuna menor | Nota em C2: 5 é teto, não alvo. |
| E7 | O12 (novo) | P0 "permissão do streamer" só tinha base em blogs [04]. | Sustentação fraca | Adicionado O12 (guideline oficial: postar só o que criou ou tem direito de compartilhar) e citado no P0. |
| E8 | L2 / [02] | "Creator Rewards exige >1 min" só em L2. | Lacuna menor | Adicionada L12 com o resultado de V6. |

Observações que **não** exigiram alteração:
- O3 cita [02] para "sinais de dispositivo/conta pesam menos"; quem traz isso é [01] (Newsroom 2020). Imprecisão de citação sem efeito.
- D3 (tempo morto > ~1 s) diverge de [02] (silêncios > ~0,5 s). É regra interna rotulada como tal; aceitável, mas calibrar.
- O1 [E] "3 de 5 elementos de valor agregado" é critério interno, não do TikTok (o texto oficial só exige "algo novo"). Está rotulado como regra interna na seção 5; manter, sabendo que é mais rígido que o texto oficial de propósito.
- A afirmação de [03] "seguidores não são fator: não verifiquei" (C4) agora está verificada (V4).

## 4. Versão corrigida dos pontos críticos

**Conteúdo não original (OFICIAL, verificado).** Community Guidelines, *Integrity and Authenticity*, vigente desde 24/09/2026: conteúdo com material não original ou reaproveitado **sem nada novo** é inelegível para a For You Feed. O vídeo não é removido por isso e segue acessível por busca/perfil; o Studio mostra se ficou inelegível. O TikTok **não define** quanto de edição basta. Marca d'água de terceiros e "reação só com cara" como indicadores vêm de páginas de apoio (Shop University/Creator Academy), não do guideline. Ação: clipe cru nunca; exigir valor agregado visível (hook próprio, legenda, layout 9:16 próprio, comentário); exportar sem marca d'água.

**Direitos (OFICIAL, verificado).** A mesma seção manda postar só o que você criou ou tem direito de compartilhar. Clipe de canal de terceiro exige permissão; crédito não substitui licença.

**Sinais de ranking (OFICIAL, verificado; pesos desconhecidos).** O sistema prevê se o usuário vai curtir, compartilhar, comentar, marcar "Não tenho interesse", seguir/abrir o perfil, terminar, pular, favoritar, assistir por certo tempo e tocar no som. Duração, caption, som e hashtags são informação do vídeo. Terminar um vídeo mais longo é o exemplo oficial (2020) de sinal forte. Seguidores e sucesso anterior não são fatores diretos (2020). Similarity check evita vídeos parecidos em sequência; conteúdo duplicado não é recomendado. **Replay não é sinal oficial confirmado** para ranking do vídeo. Ordem/pesos ("share vale 5x like" etc.) e lotes de teste: folclore.

**Diagnóstico de flop.** Antes de qualquer análise: o post é **público**? Posts `SELF_ONLY`/`private` publicados pela API não têm distribuição e ficam fora das medianas. Depois: elegibilidade no Studio, curva de retenção, sinais negativos/duplicação, moderação pendente e ruído (24-72 h).

## 5. Alterações feitas em `00-sintese.md`

1. Nota de revisão abaixo do cabeçalho.
2. O5: marcado [verificado], data exata (18/06/2020) e observação sobre a página atual.
3. O9: marcado [verificado], citação de "poucas views não significam violação", nota sobre seções "FYF Ineligible".
4. O10: reescrito com o texto oficial verificado do capítulo Integrity and Authenticity (25/08/2026, vigente 24/09/2026).
5. O10b: novo, separando Creator Academy/Shop University (Creator Rewards, marca d'água, reação só facial).
6. O12: novo, regra oficial de só postar o que criou ou tem direito de compartilhar.
7. Seção 1.5: nova causa 5 (publicado como privado via API).
8. C2: nota sobre teto de 5 hashtags (não oficial) e 2-3 recomendado.
9. C5: resolvido (replay só no onboarding de usuário novo, ausente da lista atual).
10. C6: resolvido (regra oficial no capítulo Integrity and Authenticity; enforcement 2025 segue não verificado).
11. P0: referência da marca d'água trocada para O10b; permissão do streamer passou a citar O12.
12. P3: novo item "medir só posts públicos".
13. L1: reescrita como parcialmente resolvida, com o que segue em aberto.
14. L10, L11, L12: novas (publicação privada, YouTube Shorts, Creator Rewards).

## 6. Pendências que continuam

- Ler o Creator Academy num navegador logado (fetch só traz o menu).
- Pesquisar as regras equivalentes do YouTube Shorts (conteúdo reutilizado, recomendação) antes de aplicar o revisor aos dois destinos.
- Definições oficiais das métricas do Studio (support.tiktok.com) continuam não lidas.
- Os limiares da seção 5 seguem sem base externa; calibrar com 20-30 clipes **públicos**.
