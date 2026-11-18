# Medições M5 e M6 — monitor-twitch

Data: 2026-11-17, manhã. Leitura anônima da Twitch (streamlink e IRC `justinfan`, sem login e sem credencial). Scripts: `bench/m5_recorder.py` e `bench/m6_irc.py`, rodados com `docker compose run` na imagem `clipes-lives:latest` (6 threads, 8 GB). O material gravado ficou em `/tmp` dentro do container e foi apagado ao fim.

## Hardware real

Não é o Ryzen 5 5500U do piso da spec.

| | Host | Container |
| --- | --- | --- |
| CPU | Intel Core i5-10500 @ 3,10 GHz, 12 threads | `nproc` = 6 |
| RAM | 30 GB (cerca de 12 GB livres no início) | teto 8 GB |

Os números abaixo são deste i5, não do 5500U. Outro container de medição rodou no mesmo host durante parte do teste, então a CPU do worker é um limite superior.

## M5 — 3 gravações `-c copy` + 1 worker (9 min)

Canais ao vivo em pt-BR no momento: `liminhag0d` (IRL), `bt0tv` (IRL) e `rakin` (Path of Exile), em `720p60,720p,480p,best`. Aos 2 min, um trecho de 129,0 s do `liminhag0d` virou job e rodou o pipeline (Whisper `small`) enquanto as 3 gravações continuavam. As 3 pipelines ficaram vivas nos 540 s.

| Métrica | Medido | Critério da spec |
| --- | --- | --- |
| CPU por gravador, regime (após os 5 s iniciais) | média 0,5 / 0,6 / 0,6%; pico 1,2% de 1 núcleo | `< 25%` de 1 núcleo |
| CPU por gravador, abertura (janela de 5 s) | 25,9 / 23,9 / 23,9% | o `liminhag0d` passou 0,9 ponto só na abertura |
| Disco de buffer por canal | pico 126,7 / 126,8 / 127,7 MB | `<= 200 MB` |
| RAM anônima do container (sem cache de página dos `.ts`) | 200 MB só gravando; pico 1.193 MB com o worker | — |
| `memory.peak` do cgroup (inclui cache de página) | 2.266 MB | RSS `<= 4,5 GB` |
| CPU do container | 1,8% só gravando; 281% (cerca de 2,8 núcleos) com o worker | — |

Worker: 487,9 s de parede para 129,0 s de vídeo, quase tudo no ASR (482,2 s, cerca de 3,7× o tempo real em 6 threads). O pipeline terminou com 0 clipes ("nenhum candidato formou clipe válido"): o trecho era IRL de chat baixo, sem pico de assunto. Sinais, candidatos e snap somaram menos de 4 s.

## M6 — IRC anônimo, 80 canais, 60 min

Estava estável aos 15 min (RSS 44 MB, CPU abaixo de 0,1%, 1 conexão, 0 reconexão), então seguiu até 60 min. Entrou em 80 canais; o máximo foi 16 `JOIN` por 10 s (limite da spec: 20).

| Métrica | Medido | Critério da spec |
| --- | --- | --- |
| RSS do processo | 44,5 MB o tempo todo (pico 44,5) | `< 100 MB` |
| CPU média do processo | 0,08% | — |
| Conexões / reconexões | 1 / 0 | 0 desconexão fatal |
| Canais com alguma mensagem | 10 de 80 | a lista tinha 4 lives no início; o resto não gera chat |
| Mensagens | 3.356 | — |
| Picos disparados | 12, todos com `count10 >= 10` e `>= 5` autores | 0 abaixo dos gates: conferido, 0 |
| Janelas de flood rejeitadas (`>= 6` msgs e `< 4` autores) | 15 janelas de 1 s | — |
| Quase-picos (`2,5 <= z < 3,5` e razão `>= 3`) | 64 janelas de 1 s | margem do limiar |

Tráfego por canal: coreano 1.234, rakin 1.110, liminhag0d 573, cellbit 150, felps 109, bt0tv 85, mount 67, alanzoka 19, gaules 8, bagi 1.

Os 12 picos foram rakin (6), coreano (4) e liminhag0d (2). As amostras de texto batem com reação real ("PARABÉNS RAKINNNNN", "cortou a mijada", "CHORA N PORRA"). Dois saíram com `expected = 2,5`, o piso: o de t=60 s no rakin aconteceu porque o warmup de 60 s conta desde o início do loop, e o `JOIN` dele só ocorreu por volta de 16 s (80 canais a 0,65 s), então a baseline ainda estava vazia. O de t=2.408 s é um canal que tinha ficado quieto. `liminhag0d` chegou a z=8,0 sem disparar: a razão `count/expected` segurou.

## Leitura

Gravação `-c copy` cabe folgada no teto de CPU, disco e RAM, neste i5 e com margem para o 5500U. Quem pesa é o worker: 129 s de áudio levaram 488 s, quase tudo no Whisper `small`. O IRC de 80 canais fica em 45 MB e fração de 1% de CPU, sem reconexão. O detector não disparou abaixo dos gates; o ponto de calibração é o warmup global, que deixa o piso de 2,5 valer para canais que entraram tarde.
