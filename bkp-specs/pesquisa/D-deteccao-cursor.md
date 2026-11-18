# Pesquisa D: detectar bons momentos sem depender de picos

Agente Cursor, 2026-11-17. Web + leitura de `clipes/signals.py`, `candidates.py`, `snap.py`, `asr.py` e `docs/teste-real.md`. Nada instalado nesta tarefa. Números de ASR são os já medidos no piso (teste real); o resto que não medi está marcado.

## O que o código faz hoje

`pick_candidate_peaks` (`candidates.py`) só devolve picos de `bundle.smoothed`: limiar = mediana + 0,08×(máx−mediana), `find_peaks` com distância de 90 s e proeminência 0,01. Se a curva é plana ou o máximo é 0, **devolve lista vazia**. Cada pico vira uma janela de ±45 s. O ASR (`asr.py`) só transcreve essas janelas. `select_clips` (`snap.py`) exige o pico entre 35% e 75% do clipe e duração 25–55 s, depois faz NMS por sobreposição de tempo. Ou seja: sem pico forte, `max_clips` não é atingido, e um assunto calmo nunca vira candidato.

A curva já é relativa ao próprio vídeo (`robust_percentile_norm` em `signals.py`). O furo não é o limiar absoluto de loudness; é **não existir um segundo caminho quando não há pico**.

## 1. Como os outros escolhem momento sem pico de áudio

- **ClipsAI** (open source, feito para podcast/entrevista): transcreve tudo com WhisperX e segmenta com **TextTiling sobre embeddings de frase**. Não usa áudio. Devolve início/fim de cada trecho coerente, sem nota de "viral" ([clipsai.com/references/clip](https://www.clipsai.com/references/clip), [GitHub](https://github.com/ClipsAI/clipsai), lidos em 2026-11-17). O exemplo da doc gera vetor de **1024 dimensões** — modelo grande, da ordem de gigabytes. Não cabe folgado junto com o Whisper nos 6 GB. A ideia (vale + borda de assunto) cabe; o modelo deles, não. Substituto já medido no relatório C: MiniLM-L12 multilíngue, 175 frases/s, ~0,8 GB, e separou assunto (cos 0,52 vs 0,02).
- **TextTiling clássico** (Hearst, 1994/1997, [ACL P94-1002](https://aclanthology.org/P94-1002.pdf), [J97-1003](https://aclanthology.org/J97-1003.pdf)): sem modelo nenhum. Compara blocos vizinhos pela repetição de palavras e corta no vale. Funciona em texto expositivo. Em fala coloquial de live, a repetição lexical é fraca (opinião; **não medi** neste corpus). Serve de fallback se o embedding não carregar.
- **OpusClip** (comercial): nota 0–99 de gancho, fluxo narrativo, valor emocional e tendência, treinada em pares clipe×desempenho real. A página deles lista sinais de texto (gancho, arco, densidade, citação) e de áudio (ritmo, variação de tom, risada, pausa). Não publicam pesos ([help.opus.pro](https://help.opus.pro/docs/article/virality-score), [blog da API](https://www.opus.pro/blog/viral-moment-detection-api), 2026-11-17). É API paga: fora da regra "sem chave, offline".
- **Vizard e similares:** o método não está publicado. **Não verificado.**
- Literatura de "video highlight" (TVSum, SumMe) usa imagem e título, e espera GPU. Não é reutilizável neste piso. **Não verificado** um paper recente que ganhe de texto+áudio barato em podcast pt-BR.

O que dá para reusar em CPU/6 GB: TextTiling com MiniLM (ou léxico puro), mais as features que já temos (loudness, chat, RMS). Diarização (pyannote, que o ClipsAI usa para reenquadrar) pede token do Hugging Face e modelo pesado: **não usar**.

## 2. Segmentar tudo e pontuar tudo

Não transcrever tudo de primeira (seção 3). No áudio que for transcrito:

1. Frases = grupos de `Word` já cortados em `ends_sentence` ou pausa ≥ 0,8 s (a mesma regra de `render.generate_title`).
2. Embedding por frase (MiniLM). Similaridade entre a média das 3 frases anteriores e das 3 seguintes. **Borda de assunto = vale** abaixo de média − 1 desvio, e só se coincidir com pausa ou fim de frase.
3. Segmento = entre duas bordas, limitado a 25–180 s. Abaixo de 25 s, funde com o vizinho mais parecido. Acima de 180 s, corta na pausa mais longa.
4. Nota **sem LLM**, na ordem:
   - `texto`: +1 se tem `?`, +1 se tem `!`, +1 se a última frase é curta (≤ 8 palavras) depois de uma longa (formato setup→punchline), +1 se contém palavra de reação (`TITLE_KEYWORDS` em `render.py`)
   - `ritmo`: palavras por segundo no segmento, normalizado pelo percentil 90 **deste** vídeo (fala acelerada pontua; monotonia não)
   - `audio`: média do loudness 1 Hz já calculado, mais "houve risada depois da frase" (o `_extend_past_reaction` do `snap.py` já detecta isso por RMS)
   - `chat`: média do sinal de chat no intervalo, 0 se não houver chat
   - `nota = 0,4×texto + 0,2×ritmo + 0,2×audio + 0,2×chat`, tudo em 0–1 por percentil do próprio vídeo
5. LLM (Qwen3-4B, relatório C) **não escolhe o momento**. Roda só nos top-K já escolhidos, para título e legenda. A nota 0–10 dele não discrimina (todos deram 7–9 no mesmo texto).

## 3. Custo de transcrever tudo

Medido no piso (`docs/teste-real.md`): 24 min de áudio em 703 s com `small` int8 e 6 threads. RTF = 0,49. Uma VOD de 3 h daria **cerca de 88 min** de ASR se fosse 100% fala. O VAD do faster-whisper (já ligado, `vad_filter=True`) pula silêncio; numa live com música/pausa o tempo cai, num podcast quase não cai. Viável de madrugada, inviável no monitor ao vivo, onde a máquina também grava.

Não usar Whisper `tiny`/`base` como pré-transcrição: no pt-BR o `tiny` tem WER ~24% no FLEURS ([Handy](https://models.handy.computer/languages/pt/), citado no relatório C). Segmentar assunto em cima de texto errado piora o corte. **Não verificado** se o `base` (WER ~14%) bastaria só para achar a borda.

Plano de orçamento, sem modelo novo:

- O RMS do vídeo inteiro já custa 0,62 s numa VOD de 2 h 41 min. Dividir em blocos de 10 min e ordenar por fração de segundos acima do silêncio é de graça.
- Transcrever os blocos nessa ordem até gastar **no máximo 25% da duração** em áudio (3 h → 45 min de áudio → ~22 min de ASR). Os blocos dos picos entram primeiro e não contam duas vezes.
- Se a cota de clipes (seção 4) fechar antes, parar.

## 4. Cota, limiar, diversidade, dedup

- **Modo piso:** `cota = clamp(duração/3600 × 4, mínimo 4, máximo max_clips)`. Se os picos não enchem a cota, completar com segmentos. Nunca devolver 0 clipe se houve fala.
- **Limiar por percentil, não absoluto.** O limiar atual (`mediana + 0,08×(máx−mediana)`) num podcast calmo fica colado na mediana e o `find_peaks` não acha nada. Trocar por: candidato é segundo acima do **percentil 90 desta curva**, com a mesma distância de 90 s. O percentil se adapta sozinho a canal quieto ou barulhento.
- **Diversidade:** na escolha, no máximo 2 clipes por bloco de 10 min, salvo se o terceiro tiver nota ≥ 1,5× a melhor nota do bloco seguinte ainda vazio. Entre textos, descartar o de menor nota quando o cosseno MiniLM for > 0,82 (mesma história repetida).
- O NMS atual só olha sobreposição de tempo. O dedup por texto é o que falta.

## 5. Calibrar sem GPU

Conjunto semente, fraco de propósito: os 8 clipes de `UBv07VGeD24` e os 2 da live em `docs/teste-real.md` são o que o pipeline **já escolheu**, não o que um humano marcou como bom. Servem de positivo fraco. Falta o negativo: 15 janelas de 45 s do mesmo VOD que não foram escolhidas, ouvidas por uma pessoa (cerca de 15 min de trabalho).

Métrica: **precision@8** = quantos dos 8 primeiros sobrepõem um positivo com IoU ≥ 0,5. Sem treino. Ajustar os quatro pesos da seção 2 na mão até a precision@8 subir neste conjunto de ~25 exemplos. Guardar `views` depois que publicar, para um dia trocar o rótulo fraco por desempenho real. OpusClip faz isso com centenas de milhares de pares; nós não temos essa base, e inventar peso "ótimo" seria chute.

## 6. Desenho e ordem

```
sinais 1 Hz (já existe, vídeo inteiro)
  -> picos por percentil 90            # candidates.py, caminho A
  -> se len < cota: blocos de 10 min por fala, até o orçamento de ASR
       -> ASR small só nesses blocos   # asr.py
       -> frases -> MiniLM -> vales    # função nova, ~80 linhas
       -> nota léxica+ritmo+áudio+chat
  -> junta A e B, dedup por tempo e por cosseno, teto de 2 por 10 min
  -> snap com bounds travados          # snap.py
  -> LLM só no título dos escolhidos
```

O que muda:

- `candidates.py`: limiar por percentil; se não encher a cota, emitir candidatos "bloco" com `raw_score` baixo para o ASR pegar. Nova função `segments_from_words(...)` devolvendo os mesmos `CandidatePeak`, com `peak` = tempo da frase-punchline (a última frase curta, ou a de maior loudness).
- `asr.py`: aceitar o orçamento (parar de transcrever quando o áudio acumulado passar de 0,25× a duração).
- `snap.py`: se o candidato já traz início e fim de frase, **não** recentralizar no pico nem exigir o pico entre 35% e 75%. Só encostar as bordas na frase. Segmento de 60–180 s vira partes de 45–60 s (decisão 3), com a punchline na última parte.
- `cli.py`: `--min-per-hour` (padrão 4) e `--asr-budget` (padrão 0,25). Sem os dois flags, o comportamento de hoje continua.

Ordem, cada passo testável sozinho:

1. Percentil + cota mínima **sem** ASR novo (um vídeo calmo passa a renderizar alguma coisa, mesmo que fraca).
2. Orçamento de ASR por bloco de fala.
3. Segmentação por embedding e nota léxica; comparar precision@8 contra o passo 1 nos 10 clipes do teste real.
4. Dedup e teto por bloco de 10 min.
5. Bounds travados no `snap.py` e divisão em partes.
6. LLM só para título, depois que a seleção estiver estável.
