# Medições M3 e M4 — UBv07VGeD24 (2026-11-17)

Hardware do host: **Intel Core i5-10500 @ 3.10 GHz, 6 núcleos / 12 threads, 32 GB RAM**. `nproc` = 12. Container `clipes-lives:latest` com limite de 8 GB e `CLIPES_CPU_THREADS=6`. **Não é o Ryzen 5 5500U** do critério da spec. Os números abaixo não fecham o piso 5500U.

Dados: `~/Downloads/clipes-lives-v2/UBv07VGeD24/.cache/words.json` (Whisper `small`, pt, 3835 palavras, 8 janelas) e `meta.json` ("BISTECONE & GABRIEL COXINHA - Podpah #1049"). Scripts na worktree `dev-medicoes`: `bench/m3_llm.py`, `bench/m4_topics.py`. Saída bruta: `bench/out/m3.json`, `bench/out/m4.json`.

## M3 — Qwen3-1.7B-Q4_K_M (LLM-05)

`/opt/models/llm/` só tem `Qwen3-1.7B-Q4_K_M.gguf` (1,1 GB). O GGUF do Qwen3-4B-Instruct-2507 não está na imagem, então o 4B não foi medido.

Corrida limpa (load de 1 min ~7 no início, sem outro job de CPU no host). `llama-server` com `-t 6 -c 2048 --reasoning-budget 0`, uma sessão para os 8 trechos, `temperature 0`, `seed 42`, `max_tokens 300`.

| Métrica | Medido | Critério da spec |
|---|---|---|
| `llama-bench -t 6 -p 512 -n 128` tg | **19,74 tok/s** (pp 132,63 tok/s) | `tg >= 10` |
| tok/s de geração na tarefa real (média dos 8) | **14,26** | `tg >= 10` |
| tempo médio por clipe | **15,27 s** (máx. 19,03 s) | `<= 20 s` |
| pico de RSS do `llama-server` (`ru_maxrss`) | **2,73 GB** | `<= 2,5 GB` — não passa |
| JSON válido | **8/8** | 8/8 |
| hashtags cruas 3–5, sem `#fyp`/hífen | **8/8** | 8/8 |
| nomes próprios alucinados no título | **0/8** | 0 |
| título válido pela regra inteira (`?`/`!`, <= 60, <= 1 emoji) | **7/8** | — |

Startup do servidor: 1,42 s. O título reprovado não inventa nome: é `BISTECONE & GABRIEL COXINHA - Podpah #1049`, copiado do título da live, sem `?` nem `!`.

| Janela (s) | s/clipe | tok gerados | tok/s | Título | Hashtags cruas |
|---|---|---|---|---|---|
| 177–387 | 11,79 | 109 | 18,55 | Bistecone & Gabriel Coxinha ? | #Panini #Copa2026 #Potipá |
| 1588–1798 | 12,78 | 124 | 18,25 | BISTECONE & GABRIEL COXINHA - Podpah #1049 | #Bistecone #GabrielCoxinha #Podpah #Trauma #Evolução |
| 2334–2424 | 14,68 | 118 | 13,62 | Bistecone & Gabriel Coxinha ? | #DesigualdadeBrasil #EmpregoBrasil #PobrezaNoBrasil #VidaCidadã |
| 3591–3681 | 14,84 | 118 | 13,29 | BISTECONE & GABRIEL COXINHA - Podpah #1049 ? | #Bistecone #Coxinha #Roubo #Moto #Ferramenta |
| 4737–4827 | 17,55 | 104 | 9,04 | BISTECONE & GABRIEL COXINHA ? | #churrasco #Bistecone #GabrielCoxinha |
| 5981–6071 | 19,03 | 107 | 13,10 | Bistecone & Gabriel Coxinha ? | #Podpa #Bistecone #Coxinha #RioDeJaneiro #Trombou |
| 8303–8513 | 18,09 | 115 | 13,14 | BISTECONE & GABRIEL COXINHA ? | #Bistecone #Coxinha #VidaCarnaval (repetida 3x) |
| 8580–8790 | 13,38 | 97 | 15,06 | BISTECONE & GABRIEL COXINHA - Podpah #1049? | #ex #carro #acidente #vazio |

Exemplos de gancho: "Panini, a Copa do Mundo FIFA 2026 não começa no campo, tá?" (nota 8); "Oliver no Rio, trombou com o Podpa !" (nota 9); "Gabriel capotou carro, ninguém ajudou" (nota 8).

Uma corrida anterior, com load ~28 (outros containers no mesmo host), deu média de 71 s/clipe, 2,93 tok/s e 1 timeout aos 120 s. O `llama-bench` no fim dessa corrida, já com o host aliviado, deu tg 19,16 tok/s. Esses tempos de parede não entram na tabela. RSS foi 2,72 GB nas duas, então o estouro de 2,5 GB não é efeito da contenção.

## M4 — fronteiras MiniLM ONNX vs fallback léxico (PART-07)

Modelo `/opt/models/embed/onnx/model_quint8_avx2.onnx`. Frases por `words_to_sentences` (pausa >= 0,8 s ou fim de frase). Rótulo manual: fronteira só quando a pausa até a frase nova é >= 0,8 s **e** o texto muda de assunto (pergunta nova, história nova ou outro referente). Risada, plateia e a mesma anedota atravessando a pausa não contam. 17 fronteiras nas 8 janelas (565 frases). Acerto = corte previsto a até 3,0 s do início da frase-fronteira.

| Métrica | MiniLM | Fallback de pausa | Critério |
|---|---|---|---|
| Recall | **0,706** (12/17) | 0,529 (9/17) | `>= 0,75` — não passa |
| Precisão | **0,124** (12/97) | 0,107 (9/84) | — |
| Tempo de embedding | **12,51 s** no total; 1,12–2,88 s por janela | ~0 | `< 5 s` por janela passa; a soma das 8 não |
| Delta cosseno intra − inter | **0,082** (0,600 vs 0,518) | — | `>= 0,10` — não passa |
| RAM | delta 349 MB, pico 673 MB | — | `< 0,9 GB` — passa |

O MiniLM acerta mais fronteiras que o fallback (12 contra 9) e erra por excesso: a regra `média − 1 desvio` marca 97 vales onde o rótulo tem 17. Em duas janelas o delta é negativo (177–387: −0,038; 2334–2424: −0,099), ou seja, o corte de assunto não é o ponto de menor similaridade.

Rótulo (índice da frase onde o assunto novo começa):

| Janela | Frase | De → para | MiniLM | Léxico |
|---|---|---|---|---|
| 177 | 19 | anúncios (Audible/Panini/Amazon) → "vamos falar dos convidados" | acerta | erra |
| 177 | 29 | palmas → entrada, "Boa noite" | erra | acerta |
| 1588 | 26 | mágoa/ex → "Tu já fez um trisal?" | acerta | acerta |
| 1588 | 39 | trisal → a vela do bolo apaga | erra | erra |
| 1588 | 72 | bolo/prato → "Gabriel tá morando em São Paulo" | acerta | erra |
| 2334 | 35 | "Brasil é lugar de vagabundo" → ambição pessoal | erra | acerta |
| 3591 | 28 | notebook no ônibus → "que que você perdeu?" (a moto) | acerta | acerta |
| 4737 | 19 | trailer de GTA VI / churrasco → vizinho com inveja | acerta | acerta |
| 4737 | 44 | vizinho no banho → corte para o Didi | erra | acerta |
| 5981 | 30 | Oliver Tree no Rio → "Tu fala inglês?" | acerta | erra |
| 8303 | 37 | tag nojento/frango → "pior pra chegar em mulher" | acerta | acerta |
| 8303 | 55 | chegar em mulher → situação constrangedora | erra | erra |
| 8303 | 70 | situação constrangedora → "quem inventa história falsa" | acerta | erra |
| 8303 | 85 | verdade/mentira → "quem arruma treta bêbado" | acerta | acerta |
| 8580 | 13 | conselho sobre ex → "quem vai ser preso primeiro" | acerta | erra |
| 8580 | 28 | acidente de carro → "um conselho um pro outro" | acerta | erra |
| 8580 | 58 | sociabilidade → fetiche | acerta | acerta |

## Desvios

- Spec pede `tests/test_bench_m3_llm.py` e `tests/test_bench_m4_topics.py` com `pytest`. O despacho mandou script em `bench/`, sem suíte pytest e sem suíte completa. Não há teste novo em `tests/`.
- Critério cita o Ryzen 5 5500U. A medição rodou no i5-10500.
- 4B não medido: arquivo ausente.
- `meta.json` não traz canal, então `_infer_channel` usa o título do vídeo como canal. A legenda de fallback sai `Crédito: BISTECONE & GABRIEL COXINHA - Podpah #1049` e o saneamento completa com `#bisteconegabrielcoxinhapodpah1049`. Observação, sem mudança de código.
