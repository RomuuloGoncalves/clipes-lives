# 05 - Análise dos clipes gerados e melhorias priorizadas

Data: 2026-11-18. Base: critérios da seção 5 de `00-sintese.md` e ressalvas de `00-revisao.md` (limiares são regras internas a calibrar; posts `SELF_ONLY`/`private` não medem flop; YouTube Shorts fora do escopo da pesquisa). Nenhum código foi alterado. Nenhum segredo foi lido para este documento além do uso do token do bot para leitura (não gravado em lugar nenhum).

## 1. Inventário

### 1.1 Locais (`out/`)
- 44 clipes `.mp4` em 9 pastas `cellbit - live [cellbit_1795005000..1795007400]` (cada pasta tem `clips.json`, `README.md`, `.txt` caption e `.descricao.txt`). **Não são lives inteiras**: são os clipes já renderizados (26-66 s). A pasta `cellbit_1795007700` está só em `queue/processing` (`source.mkv`, sem clipe renderizado) e `queue/done/*` guarda só `job.json`/`meta.json` (1,9 GB em `queue`, sem vídeos de clipe).
- Todos: 1080x1920, ~29,85 fps, trilha de áudio AAC presente (checado por atom `mp4a`), 3,6-23,6 MB.
- Em todos o `layout` do `clips.json` é `black_bars_16x9` (nenhum `split_webcam`), `detection_mode=auto`, nota LLM 7-9.

### 1.2 Discord (#general, servidor 1572417049932599479)
- Autenticação: o bot do projeto usa `DISCORD_BOT_TOKEN` no `.env` (`clipes/discord_rest.py`, `clipes/bot.py`). Usei esse token só em GET (canais, mensagens, threads). Credencial funcionou.
- #general (id final ...386) tem 280 mensagens (18-19/11/2026): só o bot posta "cartões" de live (`**canal** - live / Clipes: N / url`, embed do Twitch) e avisos "janela perdida X (fila cheia)" (74) e "[acompanhar] recusada ... lease com outro PC" (3); algumas mensagens de pessoas (suporte/inglês de PC, sem relação com clipes). O canal configurado no `.env` (`DISCORD_CHANNEL_ID`) **não é o #general**, mas os clipes estão em **190 threads criadas a partir do #general**.
- Ignorados conforme instrução: 65 cartões/threads de cellbit. Analisados: 125 threads de **alanzoka (77), ishowspeed (28), paulinholokobr (20)** = **609 clipes mp4** anexados (386 / 124 / 99). Cada thread tem uma mensagem por clipe com título, `Gancho`, `Nota/IA (llm|heuristic_fallback)`, `Legenda`, crédito, hashtags, descrição longa, `Policy: liberado` e botões **YouTube / TikTok / Ambos** (`publicar:<destino>:<clip_id>`). Não há botão "Aprovar" nem "Rejeitar" visível nessas mensagens (só 3 botões de publicação); 0 mensagens com status de publicação.
- Baixados 7 clipes (3 streamers, 1 por faixa de data/layout) no scratchpad e extraídos frames 0 / 1 s / meio / fim.

### 1.3 Estatísticas dos 609 clipes do Discord (metadados do anexo)
| Item | Resultado |
|---|---|
| Resolução | 441 em 1080x1920; **168 em 720x1280** (todos criados até 19:01 UTC, antes do commit de qualidade) |
| Duração | mín 26,2 s, mediana 37,0 s, máx 66,2 s; 10-35 s: 249 (41%); 35-60 s: 308 (51%); **>60 s: 52 (8,5%)**; <10 s: 0 |
| Tamanho | mediana 8,4 MB, máx 19 MB |
| Hashtags | **590/609 com exatamente 5** (teto), 17 com 3, 2 com 4 |
| Origem da nota | 265+83+33 `llm`; **228 `heuristic_fallback`** (37%) |
| Nota | 7-9 (llm); heurística 0,0-0,8 (escala diferente) |
| Policy | 609/609 `liberado` |
| Títulos com artefato ` ?`/`!?` | 123 |
| Nomes de arquivo repetidos entre jobs | 11 |
| Hashtags mais usadas | `#twitchtv<canal>` 312+, `#twitchbr` 211, `#conflito` 184+74, `#live` 168, `#reacao` 130 |

## 2. Avaliação por vídeo (amostra visual)
Escala: P = passa, F = falha, ~ = parcial, ? = não verificável sem ffmpeg/ASR (áudio não analisado). [E] = eliminatório. O1/O3 dependem de decisão humana (permissão do streamer): marcado "?".

| Clipe (origem) | Layout | Z1 [E] | H1 [E] | H2/H3 | H4 | L2 legenda | Z2 zona segura | W1/W2 | D1 | D4 final | C3 tags | O1 [E] valor | O3 [E] direitos | Resultado |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| out: cellbit c5000 #1 "como é que eu levo o carro" (39 s) | barras 16:9 | P (1080x1920) mas ~75% preto | P | ~ (fala) | P (fade 120 ms) | P, mas ~1/3 do tamanho do 54 px? ~ | ~ | F (jogo 16:9 pequeno, webcam de canto copiada) | 1 | F (cartão "Live completa" 1,2 s) | ~ (5, genéricas) | P (hook+legenda+cartão) | ? | Reprovar por W1/D4 (melhorar) |
| out: cellbit c5300 #2 "onde está o valor" (26 s) | barras | P | P | ~ | P | ~ | ~ | F | 2 | F | ~ | P | ? | idem |
| out: cellbit c5300 #3 "Shirley é câncer?" (66 s) | barras | P | P | ~ | P | ~ | ~ | F | **0 (>60 s)** | F | ~ | P | ? | Reprovar D1; tema sensível ("câncer") |
| out: cellbit c7400 #3 "legenda não funciona" (32 s) | barras | P | P | ~ | P | ~ | ~ | F | 2 | F | ~ | P | ? | hook = assunto de legenda, pouco valor externo (H6 F) |
| Discord: alanzoka 19/11 00:39 "gente ?" (33 s) | **split-barra** | P | P | ~ | P ("vou trocar arma ?") | P | P (barra 340 px; legenda no jogo) | **P** | 2 | F (cartão final) | ~ | P | ? | Melhor padrão; falta H6 (hook genérico) |
| Discord: alanzoka 18/11 16:25 "nem sei que que é Gabriela" (30 s) | black_bars | P | P | ~ | P ("vídeo boat charters tocando...", truncado) | ~ | ~ | F (jogo ocupa ~28% da altura) | 2 | F | ~ | P | ? | Reprovar W1; hook truncado |
| Discord: alanzoka 18/11 12:22 "será que tem passando..." (28 s) | black_bars 720p? (batch antigo) | F se 720x1280 | P | ~ | P | ~ | ~ | F | 2 | F | ~ | P | ? | Reprovar se confirmado 720p |
| Discord: ishowspeed 19/11 00:17 "like what is going to happen" (52 s) | **barra branca + black_bars** (webcam no canto do jogo) | P | P | ~ | P (PT "Como nossa vida vai começar agora?") vs fala/legenda em inglês | ~ ("Oh my gosh" legenda no meio, sem tradução) | ~ | F | 1 | F | ~ | P | ? | Mismatch de idioma hook PT x fala EN |
| Discord: ishowspeed 18/11 22:33 "Lori rescatada" (33 s) | idem | P | P | ~ | P | ~ ("go.") | ~ | F | 2 | F | ~ | P | ? | W1 F |
| Discord: paulinholokobr 18/11 15:57 "you you you you" (35 s) | idem | P | **? (menu do jogo no meio, frame pouco claro)** | F? | P ("Você acha que ele vai se afastar?") | ~ | ~ | F | 2 | F | **F** (`#twitchtvpaulinholokobr #twitchbr #livesbr`, nada de tema) | ~ | ? | Título "you you you you" = clipe sem payoff; reprovar |
| Discord: paulinholokobr 18/11 14:19 "provavelmente eu vou ter" (30 s, 720p) | barras | F (720p) | P | ~ | P | ~ | ~ | F | 2 | F | ~ | P | ? | Reprovar Z1 |

Notas do método: 11 clipes visualmente inspecionados com 4 frames cada; os demais 640+ só por metadados (duração, resolução, texto da mensagem). H2/H3 (início do evento) não foram medidos em áudio por falta de ffmpeg; "~" significa que o frame 0 já mostra imagem em movimento e texto-hook (com fade de 120 ms), sem confirmação de fala.

### 2.1 Placar por critério (amostra, não censo)
- **Z1 [E]**: passa em 1080x1920, mas 168 dos 609 (27,6%) do Discord estão em 720x1280 (falha de resolução alvo) e todo layout `black_bars_16x9` fica com ~75% de preto (vídeo 16:9 centralizado), o que contraria W1/"sem barras pretas laterais" (a síntese cita barras laterais; aqui são barras de cima/baixo, o mesmo problema de aproveitamento do 9:16).
- **H1 [E]**: passa em todos os frames 0 vistos (há movimento e hook em fade).
- **H4**: passa (hook em texto, 1ª linha) em todos; porém o hook é **truncado com "..."** em vários (ex.: "VÍDEO BOAT CHARTERS TOCANDO...", "QUER VER UM FORGETTABLE COM D...", "Vai usar outro carro para evitar a臂" = caractere corrompido).
- **D1**: 41% em 10-35 s (nota 2), 51% em 35-60 s (nota 1, mas sem justificativa de narrativa registrada), 8,5% >60 s (nota 0). Nada <26 s.
- **D4**: falha em 100% da amostra: sempre há cartão final "Live completa: twitch.tv/..." sobre o último frame congelado por 1,2 s (`OUTRO_DURATION`, `render.py:20`), contra a regra "sem tela de fim" e com o loop impossível.
- **C3**: 97% dos posts com 5 hashtags (teto, não alvo), e boa parte genérica ou espúria (`#conflito`, `#reacao`, `#live` em clipes sem conflito; `#twitchtv<canal>` é quase uma tag de marca; hashtags de erro como `#Cinzia`, `#Verde`, `#Vermelho`).
- **C5**: o hook da tela e a caption da primeira linha costumam ser **idênticos** ("Onde está o valor? !", "Corno? !") - falha C5.
- **O1 [E]**: passa (barra de marca + legenda + hook + reframe/layout), mas sem comentário/narração; clipes em `black_bars` têm valor agregado mais fraco.
- **O2 [E]**: atenção. Há marca "twitch.tv/<canal>" fixa na parte superior do jogo + cartão "Live completa: twitch.tv/<canal>". É logo/handle de **terceiro** (o streamer) aplicado como crédito, mesmo risco de O10b ("marca d'água de terceiros indica não original") se interpretado pelo TikTok; não é uma marca de plataforma, mas é um indicador de "não original". Decisão do dono do projeto.
- **O3 [E]**: o pipeline emite `Policy: liberado` em 609/609; não há registro de permissão por streamer no artefato (não consegui verificar `perfil.py`/política em profundidade).
- **A1/A2/A3**: **não analisados** (sem ffmpeg/ASR). Pelo código, o áudio passa por `loudnorm=I=-14:TP=-1.5:LRA=11` + fade out 0,5 s (`render.py:863-872`), coerente com A3; música da live (A1) não é filtrada.

## 3. Achados

1. **Qualidade do layout é inconsistente.** O `split-barra` (padrão desde o commit 12d7cba) só apareceu no clipe mais novo do alanzoka. Em 100% dos clipes de `out/` e em ishowspeed/paulinholokobr/boa parte do alanzoka o resultado é `black_bars_16x9`: faixa de jogo 16:9 no meio (~560 px de altura em 1920) com webcam de canto copiada do 16:9 e ~1000+ px de preto. Isso é a pior combinação para retenção e para "valor agregado" (W1). Causa provável: `fc_tipo == "nenhuma"` (webcam não detectada) -> fallback `black_bars_16x9` (`render.py:1081`, `_resolve_split_for_clip` em `render.py:975`); em `cli.py:425` o padrão também é `black_bars_16x9`/`blurred_16x9`. Para ishowspeed/paulinholokobr o clipe tem barra branca (a marca) mas **sem** `split` (webcam continua no canto do jogo), ou seja, o layout é híbrido e desperdiça a tela.
2. **Resolução**: 27,6% dos clipes no Discord estão em 720x1280 (modo "compacto" para caber no Discord). Se o TikTok receber esses arquivos, Z1 falha. Os botões "TikTok/Ambos" usam o mesmo anexo do Discord? (não verifiquei se publicam o arquivo original em 1080p; isso precisa ser confirmado em `publicacao.py`/`publicar_tiktok.py`).
3. **Hook**: sempre presente (bom), mas (a) truncado com "..." e cortado no meio da palavra; (b) corrompido em pelo menos 1 caso (`臂`, provável mistura de encoding/ASR/LLM); (c) frequentemente é só a fala transcrita com "?"/"!" colado ("Joey? !", "Corno? !") e não uma promessa de payoff (H3/H6); (d) em ishowspeed o hook é PT mas fala/legenda em inglês, sem tradução; (e) no `split-barra` o título fica **fixo durante todo o clipe** na barra (bom para zona segura, mas não é hook dinâmico).
4. **Duração**: 51% em 35-60 s e 8,5% >60 s; piso de 25 s (`snap.py:293`, `min_dur=25`) impede clipes de 10-25 s, que a síntese diz ser a faixa preferencial (referência 10-35 s). Cortes tópicos de 60-66 s em "conflito"/"câncer" tendem a ter tempo morto (D3), embora eu não tenha medido.
5. **Cartão final** de 1,2 s com "Live completa: twitch.tv/..." em 100% dos clipes: corta loop, atrapalha D4 e pode reduzir a conclusão (o espectador vê uma tela parada nos últimos 1,2 s).
6. **Metadados/caption**: 5 hashtags sempre, genéricas, repetidas (`#conflito/#reacao/#live` em ~40% dos clipes, independente do conteúdo); caption inicia com a própria legenda e depois credita + link da live (URL num post TikTok não é clicável; só ocupa linhas); 37% da nota vem de `heuristic_fallback` com notas 0-0,8 que **ainda assim recebem `Policy: liberado`** (a nota não gateia nada); 123 títulos com artefato ` ?`/`!?` e 11 nomes de arquivo repetidos entre jobs (risco de colisão/duplicata O4/O7 ao publicar).
7. **Legendas**: palavra-chave destacada em amarelo (bom), posição na zona segura no `split-barra`, mas em `black_bars` a legenda cai na base preta (Y~1000+ em 1920), perto/dentro da faixa de UI do TikTok (Z2 depende do layout). Há legendas em inglês em clipes PT-BR e vice-versa; precisão (L3) nunca é revisada automaticamente (ex.: "Cuga perfeita", "luces caralho", "Cinzia").
8. **Registro Discord**: informativo (nota, hook, legenda, hashtags, policy, botões por destino), mas: não mostra duração/resolução/layout/nota por critério, não mostra eliminatórios, não registra a nota do revisor da síntese seção 5, e não sinaliza clipes 720p/>60 s/heurística. Também não há marcação de "público manual vs privado via API" (L10). #general mistura cartões, avisos de fila e conversas de pessoas; os clipes ficam em threads fora do `DISCORD_CHANNEL_ID`.
9. **Fila perdendo trabalho**: 74 "janela perdida ... (fila cheia)" (53 alanzoka, 14 ishowspeed, 7 cellbit) e 3 "lease com outro PC" indicam janelas de live descartadas, ou seja, momentos que nunca viraram clipe.
10. **Agendador**: não há horário/agendamento visível nas mensagens do Discord nem nos metadados (só os botões de publicação imediata). Ver `ler_horarios/proximo_horario` em `clipes/publicacao.py:73-110`; não consegui verificar o comportamento ponta a ponta (sem posts publicados no canal para observar).

## 4. Melhorias priorizadas

### P0 - eliminatórias / bloqueiam publicação
1. **Gate automático dos eliminatórios antes de liberar `Policy`/botões** (novo módulo de revisão, por exemplo em `clipes/previa.py` ou antes da mensagem em `clipes/registro.py`): reprovar/marcar `Policy: revisar` quando (a) largura < 1080 ou altura < 1920; (b) `layout == black_bars_16x9` sem alternativa; (c) `nota < 5` ou origem `heuristic_fallback`; (d) duração > 60 s sem parte 1/N. Hoje 609/609 saem "liberado" (inclui 228 de heurística e 168 em 720p).
2. **Publicar o 1080x1920 no TikTok**, nunca o 720x1280 pensado para o limite de 8-10 MB do Discord (`_resolve_limit_mb`/`bitrate_alvo` em `render.py:1085+`, `previa.py`). Separar o arquivo "para Discord" (comprimido) do "para plataforma" (alta qualidade). Confirmar em `publicar_tiktok.py` qual arquivo é enviado.
3. **Permissão do streamer (O3/O12)**: registrar por canal um campo `permissao` (data, escopo TikTok/monetização) no perfil (`clipes/perfil.py`) e bloquear o botão "TikTok"/"Ambos" sem ele. Hoje o aviso `liberado` não se baseia nisso e os clipes vêm de alanzoka/ishowspeed/cellbit/paulinholokobr (terceiros). Registrar como risco legal/FYF (O10/O12), sem aconselhamento jurídico.
4. **Marca/handle de terceiros**: decidir se `twitch.tv/<canal>` fixo e o cartão final contam como "marca d'água de terceiros" (O2/O10b). Alternativa de menor risco: crédito só na caption (não no vídeo) ou crédito discreto nos primeiros 2-3 s; manter logo próprio (CortesLR).
5. **Corrigir a corrupção de caractere/encoding no hook** (`臂` em "Vai usar outro carro para evitar a臂") e a truncagem com "..." no hook (ver `wrap_top_hook(max_chars=26, max_lines=2)` em `render.py:~588`): quebrar por palavra, resumir pelo LLM em <= 40 caracteres, nunca terminar em "..." ou preposição.

### P1 - retenção
6. **Eliminar o `black_bars_16x9` como padrão**: quando a webcam não é detectada, usar `reframe` do jogo (crop dinâmico no 9:16) ou `blurred_16x9` com o jogo ampliado, em vez de barras pretas; em ishowspeed/paulinholokobr, tratar a webcam embutida no canto do jogo: recortar a webcam (detecção em `facecam.py`, `_resolve_split_for_clip`) e montar `split-barra`. Meta: 0% de `black_bars_16x9`.
7. **Remover (ou encurtar para <= 0,3 s) o cartão final** `OUTRO_DURATION=1.2` (`render.py:20`, `:619-628`, `:847`, `:872`); em vez disso, tornar o final um loop para o início ou colocar "Live completa" só no crédito inicial/caption. Critério D4.
8. **Reduzir `min_dur` de 25 s para ~12-15 s e o `max_dur` para ~45 s** para momentos únicos (`snap.py:293-294`, `cli.py` `--min-dur/--max-dur`), conforme C1 (10-35 s); clipes >60 s só com `part_total>1` e justificativa. Hoje 59,5% estão acima de 35 s.
9. **Hook de verdade**: gerar `gancho_topo` como promessa/pergunta/tensão de <= 6 palavras (não repetir a fala); no `split-barra` considerar um segundo estado do título (ex.: hook 0-3 s e depois título curto), e garantir PT quando a fala for EN (traduzir hook; legenda bilíngue ou tradução curta).
10. **Teste de H2/H3 por áudio/ASR** (pico de áudio e primeira palavra <= 1 s) no pipeline (`signals.py`, `snap.py`: `MIN_PRE_ROLL`); cortar contexto de entrada até <= 2 s e tempo morto > 1 s (D2/D3).
11. **Tamanho/posição da legenda**: padronizar `cap_style` >= 54-60 px no 1080x1920 e dentro de Y 240-1260; em layouts com base preta, não colocar a legenda abaixo de Y ~1260 (`render.py:505-540`). Checar no app (Z4) um clipe por template.

### P2 - descoberta e conteúdo
12. **Caption**: keyword (streamer + jogo + momento) no início, 1-2 linhas, sem repetir o hook da tela (C5), sem URL (inútil no TikTok), pergunta genuína no final (C4). Hoje a 1ª linha é a fala transcrita e os links ocupam espaço (`format_three_line_caption`, `llm.py:511-524`).
13. **Hashtags**: 2-5 conforme relevância (C2/C3: 5 é teto): `#<jogo>`, 1-2 de nicho (ex.: `#gta`, `#gtaonline`), no máximo 1 geral; banir `#conflito/#reacao/#live/#twitchtv<canal>` genéricos e as de erro; dedupe case-insensitive (`#Conflito` x `#conflito`) em `sanitize_hashtags` (`llm.py:459-508`). Detectar o jogo (título/OCR/categoria Twitch) para a keyword.
14. **Qualidade do LLM**: título com artefato ` ?`/`!?` (123), títulos sem sentido ("you you you you"), descrições "A conversa é leve, mas..." repetidas e sem informação. Reprovar títulos que são só ruído do ASR (repetição de palavra, < 3 palavras distintas) e gerar título/descrição com o contexto da cena, não só a transcrição.
15. **Variedade/duplicação (O7/O4)**: 11 nomes de arquivo repetidos e muitos "conflito/reação" seguidos; adicionar hash do trecho/fingerprint do hook e bloquear publicar dois clipes do mesmo trecho; espaçar clipes do mesmo streamer/trecho no agendador.
16. **Conteúdo**: priorizar momentos com payoff claro (humor, susto, conquista, conflito real) e evitar clipes de menu, loading e "pausas e confusão" (ex.: "legenda não funciona", "pausas e confusão"); evitar temas sensíveis sem contexto (ex.: "Shirley é câncer?").

### P3 - processo e medição
17. **Registro Discord enriquecido** (`clipes/registro.py`, `clipes/bot.py`): incluir duração, resolução, layout, origem da nota (llm/heurística), nº de hashtags, flags dos eliminatórios (Z1/D1/O2/O3) e nota 0-1 por critério, para calibrar contra métricas reais (síntese 5.9). Adicionar botão **Reprovar/Reeditar** e **Aprovar** quando o gate P0 passar.
18. **Registrar modo de publicação** (público manual vs `SELF_ONLY` via API) por clipe e excluir os privados das medianas (L10/E4); só medir posts públicos.
19. **Agendador**: espaçar publicações por canal (2-5/semana por conta, sem sequência do mesmo som/trecho), horários em teste (fuso de Brasília) e registrar o horário no Discord; hoje o `proximo_horario` existe mas não há evidência de uso nos posts lidos.
20. **Perda de janelas**: investigar "janela perdida (fila cheia)" (74) e "lease com outro PC": aumentar capacidade/prioridade por streamer ou descartar de forma intencional (menor nota primeiro).
21. **Higiene do canal**: mover cartões e avisos operacionais para um canal separado do #general (que é compartilhado com conversa humana) e alinhar `DISCORD_CHANNEL_ID` ao canal onde as threads são criadas.
22. **Checagem pós-publicação** (A4/O9): após publicar, abrir no Studio e conferir áudio mutado e elegibilidade FYF; adicionar ao checklist manual.

## 5. Limitações
- **Sem ffmpeg/ffprobe** no PC: duração, fps e resolução vieram do OpenCV/`clips.json`/metadados do Discord; **áudio (A1-A3, H5, voz sobre jogo, silêncio inicial, loudness real, música da live) e legendas (L1/L3 precisão, timing) não foram analisados**; só confirmei que a trilha existe (atom `mp4a`). Pelo código, o áudio é normalizado em -14 LUFS.
- Frames: só 11 clipes inspecionados visualmente (4 locais + 7 do Discord, 4 frames cada, miniaturas ~270 px); os outros ~640 só por metadados. A afirmação "100% black_bars" é para `out/` (44 de 44 pelo `clips.json`) e para a amostra de 7 do Discord (1 split-barra, 6 black_bars/híbridos); não tenho contagem de layout para os 609.
- 720x1280 em 168 clipes foi lido do metadado do anexo; não verifiquei se o arquivo enviado ao TikTok é outro.
- Não analisei os 65 clipes/threads de cellbit no Discord (instrução) e `queue/` não tem vídeos de clipe; `cellbit_1795007700` ficou só como `source.mkv` em processamento.
- Nenhum post do TikTok/YouTube foi observado (sem links de publicação nas mensagens): **não há métricas reais**; nada aqui é diagnóstico de flop e os limiares seguem como regras internas a calibrar (L2, L10, L11).
- Direitos/permissão (O3) e política de música (A1) não foram verificados no código nem com os streamers; "marca d'água de terceiros" (O2) é interpretação do risco, não regra oficial do guideline (ver O10b).
- Backups dos arquivos de trabalho: frames e downloads estão só em `C:\Users\joper\AppData\Local\Temp\claude\7ce3dd51663d884c\8c46cd13-3a80-4f59-aa6a-568febdeaf4d\scratchpad` e podem ser limpos.
