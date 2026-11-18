# Pesquisa A: o que faz clipe de live performar (TikTok, Reels, Shorts)

Pesquisador: agente Cursor (worktree `pesq-cursor`). Coleta em 2026-11-17, com acesso à web (busca + fetch + `yt-dlp` só leitura). "Não verificado" = não achei fonte primária.

## 1. Duração

| Plataforma | Máximo hoje | O que a fonte diz |
|---|---|---|
| YouTube Shorts | 3 min (vertical/quadrado, desde 15/10/2024) | [YouTube Help 15424877](https://support.google.com/youtube/answer/15424877) (lido em 2026-11-17). **Short > 1 min com qualquer claim de Content ID fica bloqueado no mundo todo.** |
| Instagram Reels | 20 min gravando; **> 3 min não é recomendado a novos públicos** | [about.instagram.com/features/reels](https://about.instagram.com/features/reels) (2026-11-17) |
| TikTok | 10 min gravado no app, 60 min por upload; Content Posting API: 3 min para todos | Help Center via [deepege, conferido em 2026-11-08](https://deepege.com/en/guides/tiktok-video-length/). Creator Rewards exige vídeo > 1 min (mesma fonte, não reverificado na Creator Academy). |

O que foi medido: a conclusão cai com o tamanho (Kuaishou: mediana de 48,5% em 5–10 s, 27,2% em 45–60 s). Já o alcance sobe: Buffer, com 1,1 mi de TikToks, achou +43% de alcance para vídeos > 1 min em relação a 30–60 s ([ReelsBot](https://reelsbot.co/blog/how-long-should-a-short-be), 2026). A mediana publicada fica em 16–19 s ([viral.app, 3,3 mi de vídeos](https://viral.app/blog/insights/short-form-video-trends)).

Minha leitura: **45–60 s por parte é razoável**, e a decisão 3 está alinhada. No YouTube, quando houver risco de música/claim, mantenha a parte em **≤ 60 s**.

Séries "Parte 1/2/3": não achei estudo com amostra. O padrão real que observei foi "parte dois no comentário fixado" (canal MINI CLIPS gta, `TegKcRnS0rs`). Gancho de fim de parte: terminar numa frase de setup sem o desfecho, com um cartão "Parte 2 →". **Não verificado** como efeito medido.

## 2. Hook dos primeiros 3 s

- Os dados mostram que a saída mais comum acontece em ~1,2–1,3 s, e a curva continua caindo até 7 s ([ReelsBot](https://reelsbot.co/blog/first-three-seconds)). Não existe A/B público de tipos de hook.
- Na prática, nos exemplos reais (Flow e Podpah): **começam no meio da fala**, sem cartão de título e sem silêncio. O título fica no texto do post, não no vídeo.
- Recomendação: cortar o silêncio antes da primeira palavra (≤ 0,2 s). Opcionalmente, um cold open de 1–2 s com a frase do pico antes do contexto. Título na tela só se for curto e dentro da safe zone.

## 3. Legenda embutida e SAFE ZONES (1080x1920)

Nenhuma plataforma publica margens para post orgânico, só para anúncio ([Woven](https://www.woven.video/guides/safe-zone-checker), [Shotstack](https://shotstack.io/learn/vertical-video-safe-zones/), conferidos em nov/2026):

| | Topo | Base | Esq. | Dir. |
|---|---|---|---|---|
| TikTok (template In-Feed) | 240 | 660 | 120 | 120, e 300 a partir de y=840 (botões) |
| Reels (Meta 14%/35%/6%) | 269 | 672 | 65 | 65 |
| Shorts (template Google) | 288 | 672 | 48 | 192 |
| **União das três** | **288** | **672** | **120** | **192–300** |

**Problema no nosso ASS:** `MarginV=430` põe a base da legenda em y≈1490, ou seja, **dentro dos 672 px de baixo**, onde ficam o @, a descrição e a música. Proposta: `MarginV≈700` (base em y≈1220) e `MarginL=120,MarginR=200`.

Estilo observado nos Shorts do Flow (quadros que extraí de `suoXsKfW9uo` e `erKItlSoAOA`): sans-serif branca pequena com contorno, 3–5 palavras, **sem destaque por palavra**, centrada em y≈1250. O nosso word-highlight amarelo é o padrão "Hormozi/CapCut". Não achei evidência medida de que um seja melhor que o outro.

## 4. Texto do post

- **TikTok:** caption de 4.000 caracteres no app, 2.200 via API. **Máximo de 5 hashtags** desde ago/2025, um limite não anunciado oficialmente ([Search Engine Land, 2025-08-18](https://searchengineland.com/tiktok-limits-hashtags-460894)). A Creator Academy recomenda 2–3 ([omnisocials, conferido 2026-10-17](https://omnisocials.com/tiktok-hashtag-limits)).
- **Instagram:** **máximo de 5 hashtags** por post/Reel (Mosseri, [The Verge 2025-12-18](https://www.theverge.com/news/847739/instagram-hashtag-spam-limit)). Tags genéricas como #reels e #explore "podem prejudicar" ([Indian Express](https://indianexpress.com/article/technology/social/posting-on-instagram-you-can-now-use-only-five-hashtags-per-post-and-reel-10429929/)).
- **YouTube:** mais de 60 hashtags faz todas serem ignoradas. As 3 primeiras aparecem junto do título ([YouTube Help 6390658](https://support.google.com/youtube/answer/6390658)). `#shorts` não é necessário para a classificação, que é automática por formato e duração ([15424877](https://support.google.com/youtube/answer/15424877)). Que a tag ajude no alcance é **não verificado**.
- **#fyp/#viral:** não achei fonte oficial que confirme ganho. Contraexemplo observado: o perfil TikTok @cortesdoflow usa só `#viral #viralvideo #fyp #cortes #podcast` e tem 500–750 views por vídeo, contra 5–30 mil do @podpah, que tem caption curta e sem tag.

Exemplos reais coletados em 2026-11-17 (id | duração | views):
1. Shorts Cortes do Flow `suoXsKfW9uo` | 71 s | 49 mil: "A RESPOSTA DO BOMBEIRO ÀS 3 DA MANHÃ! 😂 #humor #flow #resenha #comedia"
2. `erKItlSoAOA` | 41 s | 33 mil: "EDUCAÇÃO pro FILHO ou DINHEIRO na MÃO? 🚨 #flow #política #brasil". Descrição: "ASSISTA COMPLETO: link"
3. `xaswF6ddYac` | 22 mil: "O SEGREDO POR TRÁS DOS SHOWS GIGANTES! 🚢 #ironmaiden #rock #flow"
4. `4Qs0pfd8DuA` | 21 mil: "VOTAÇÃO APERTADA NO SENADO AGITA BRASÍLIA! 🏛️ #flownews #senado #politica #lula"
5. `kHmHVAMV6Es` | 7,3 mil: "NEM TODA GUERRA É ERRADA? ⚔️ #professorhoc #geopolitica #historia #flow"
6. TikTok @podpah `7708917812412353812` | 35 s | 29,8 mil: "isso aqui é REALEZA, blz?"
7. TikTok @podpah `7708786502750211349` | 18 s | 10,4 mil: "acabou o episódio e eu ainda não entendi"
8. TikTok @gaules `7669925115383008530` | 44 s | 402,6 mil: "POV: Presidente Campeão do mundo! A Kings virou Brasil, cada jogo, ca…" (truncado pela API)
9. TikTok @gaules `7663133607778766087` | 86 s | 19,3 mil: "Enganei todo mundo KKKKK"
10. YouTube GAME PLAY CORTES `FGE7BExxyUQ`: "Paulinho o Loko se desculpa de forma genial 😧". Descrição com `#gtarp #paulinhooloko #capitalcity #modderclips #gameplaycortes` + "Este canal NÃO É OFICIAL…" + incentivo a assistir a live original
11. TikTok @cortesdoflow `7637141091598470420` | 110 s | 750: "#viral #viralvideo #fyp #cortes #podcast" (contraexemplo)

Instagram: **não verificado**, porque não há acesso sem login.

Padrão: título curto (≤ 60 caracteres) em pergunta ou exclamação, CAIXA ALTA parcial, 1 emoji, 2–4 tags = **nome do canal/pessoa + tema + formato**.

## 5. Formato visual

- Podcast/estúdio (Flow): **crop vertical em tela cheia no rosto de quem fala**, trocando de câmera a cada fala, sem fundo borrado.
- Live de jogo com webcam: o layout comum é webcam em cima (~1/3) e jogo embaixo (~2/3). O próprio editor de clipes da Twitch gera versão retrato ([Twitch Help](https://help.twitch.tv/articles/en_US/Knowledge/how-to-use-clips)). Que isso performa melhor que o 16:9 centralizado é **não verificado** em números, mas é o padrão dominante nos exemplos.
- Ritmo: troca de plano ou zoom a cada fala/pausa. A recomendação genérica é "pattern change" ([Clyper](https://www.clyper.io/guides/what-is-a-good-retention-rate)), sem número medido.

## 6. Recomendações acionáveis (impacto/esforço)

1. **MarginV 430 → ~700** e margens laterais 120/200 no `build_ass` (uma linha, alto impacto).
2. Cortar o silêncio inicial: começar ≤ 0,2 s antes da primeira palavra (mexe em `snap.py`).
3. Hashtags: **3–5 específicas** (canal/pessoa, jogo/tema, "cortes"). Sem #fyp/#viral/#reels.
4. Título do post ≤ 60 caracteres, pergunta ou exclamação, com 1 emoji. É o que o LLM deve gerar (ver C).
5. Partes de 45–60 s. Para YouTube com música de fundo, ≤ 60 s por causa do bloqueio de claim.
6. Cartão "Parte 2 →" nos últimos ~1,5 s da parte N quando houver continuação (ver C).
7. Crédito discreto do canal fora da UI (ver C) + "live completa: link" na descrição.
8. Reenquadramento por rosto/webcam em vez de 16:9 centralizado, para podcast e webcam (YuNet, ver C).
9. Legenda: manter 3 palavras por cue. Testar sem highlight por amostragem própria, porque não há evidência externa.
10. Medir: guardar `views@24h` por clipe para calibrar a nota do LLM, já que nenhuma fonte dá número universal de retenção.
