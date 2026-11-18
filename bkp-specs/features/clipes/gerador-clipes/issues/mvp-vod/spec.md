# Gerador de clipes de lives: MVP VOD + live por janela deslizante

Pesquisa de base (worktree `orquestrador` do repo `clipes-lives`): `docs/pesquisa-github.md`, `docs/pesquisa-tecnologias.md`. Design: `design.md` (nesta pasta). Pontos em aberto: `context.md`.

## Problem Statement

O usuário quer colar o link de uma live (YouTube, Twitch; Kick se der) e receber clipes verticais legendados dos melhores momentos, com foco em hype de GTA VI, reações fortes, sustos e comédia. Os clippers open source existentes escolhem trechos só pela transcrição (feitos para podcast) ou exigem API paga, Windows ou 16 GB de RAM. O problema central é escolher trechos **com sentido**: começo e fim naturais, contexto completo, sem cortar frase.

## Goals

- [ ] De um link de VOD de 1 h, gerar até N clipes 1080×1920 legendados em ≤ 60 min de CPU no Ryzen 5 5500U, sem API paga.
- [ ] Recall@N ≥ 50% contra clipes de viewers da Twitch e picos do heatmap do YouTube num conjunto de 5 VODs de referência (meta inicial, recalibrar após a 1ª medição).
- [ ] MidWordCutRate = 0%, SentenceEdgeRate ≥ 85% e ≥ 80% dos clipes aprovados no checklist G1–G5 na revisão manual do usuário.
- [ ] Live em andamento: clipes saindo com atraso ≤ 10 min do momento, reaproveitando o pipeline de VOD.

## Out of Scope

| Feature | Reason |
| --- | --- |
| Publicação automática (YouTube/TikTok/Instagram) | Decisão do usuário |
| APIs pagas (Whisper API, OpenAI, Anthropic, AssemblyAI) | Tudo grátis |
| GPU | Máquina-alvo sem GPU dedicada |
| Clipe em < 1 min de atraso na live | Janela deslizante de 5 min basta sem publicação automática |
| Rastreamento de rosto quadro a quadro, zoom dinâmico | Custo de CPU; facecam de live é overlay fixo |
| Painel React | Pasta + `index.html` estático cobre o MVP; Gradio só na última fase |
| VOD só para inscritos/membros, conteúdo com login | Sem conta quando possível |

---

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Número de clipes por VOD | Até 8, só os que passam o limiar de score | Menos clipes ruins | n |
| LLM | Ollama local (modelo 3–4B); Gemini/Groq free tier opcional por flag | 100% local por padrão; free tier pode usar os dados para treino | n |
| Layout 9:16 | Automático: `split` (facecam em cima, jogo embaixo) se achar rosto fixo; senão `blur` | Formato comum em live de GTA/reação | n |
| Idioma | Detecção automática do Whisper; legenda no idioma falado; título em pt-BR | Há streamers gringos de GTA VI | n |
| Modelo de transcrição | Passe inteiro com `small` int8 se o T4 medir ≤ 15 min/h; senão só nas janelas dos top 3N picos (agy). `large-v3-turbo` nos clipes finais | Divergência com o agy resolvida por medição | n |
| Papel do LLM | Título + veredito de autocontido; refino de borda atrás de flag | Divergência com o agy: bordas determinísticas são mais confiáveis em 4B | y |
| Duração do clipe | 20–60 s | Pedido do usuário | y |
| Atraso aceitável na live | ~5–10 min (janela deslizante) | Sem publicação automática | n |
| Kick | Com chat pelo endpoint público da Kick (como o sharkyclips); sem chat se falhar | Achado na consolidação com o agy | n |
| Base do código | Spike T0 decide: wrapper sobre o sharkyclips (MIT) ou pipeline próprio | O sharkyclips já implementa quase todo o design | n |
| Tempo de processamento aceitável | ≤ 60 min por 1 h de VOD | RAM de ~9 GB obriga a rodar Whisper e LLM em sequência | n |
| VODs de referência para avaliação | 5 VODs escolhidas pelo usuário (≥ 2 da Twitch com clipes de viewers) | Sem ground truth não dá para medir "trecho bom" | n |

**Open questions:** as linhas com `Confirmed? = n` estão listadas em `context.md` para o usuário decidir. Os defaults acima valem até a resposta.

---

## User Stories

### P1: Clipes de uma VOD a partir do link ⭐ MVP

**User Story**: Como criador de cortes, quero colar o link de uma VOD e receber clipes verticais legendados dos melhores momentos, para não assistir horas de live.

**Why P1**: É o produto mínimo; a live reaproveita o mesmo pipeline.

**Acceptance Criteria**:

1. WHEN o usuário roda `clipes vod <url>` com um link público do YouTube, Twitch ou Kick THEN o sistema SHALL gerar uma pasta de saída com até N arquivos `.mp4` 1080×1920 e um `clips.json`.
2. WHEN o download termina THEN o sistema SHALL ter baixado o áudio inteiro e, em vídeo, só os trechos dos clipes escolhidos (com folga de 5 s).
3. IF o link é privado, para inscritos, inexistente ou de plataforma não suportada THEN o sistema SHALL encerrar com código ≠ 0 e uma mensagem dizendo o motivo, sem criar clipes.
4. WHERE houver chat replay disponível o sistema SHALL usar o chat como sinal; IF não houver THEN o sistema SHALL seguir só com áudio, texto e heatmap e registrar isso no `clips.json`.
5. The system SHALL rodar sem nenhuma chave de API paga e sem GPU.
6. WHEN uma etapa já tem saída em cache na pasta de trabalho THEN o sistema SHALL reutilizá-la em vez de refazer (re-executar não baixa nem transcreve de novo).

**Independent Test**: rodar `clipes vod <url de VOD curta da Twitch>` e abrir os `.mp4` gerados.

---

### P1: Detecção de picos por sinais combinados ⭐ MVP

**User Story**: Como criador de cortes, quero que os momentos sejam achados por reação real (chat, grito, risada, replay) e não só pelo texto, para pegar sustos e comédia.

**Why P1**: É o coração do projeto.

**Acceptance Criteria**:

1. The system SHALL calcular, em bins de 1 s, as séries: chatters únicos/s, fração de mensagens de reação, loudness, padrão silêncio→explosão, heatmap e palavras-chave.
2. The system SHALL normalizar cada série por z-score robusto contra uma linha de base móvel de ±150 s.
3. WHEN o chat está disponível THEN o sistema SHALL deslocar a série do chat para trás pelo atraso estimado por correlação cruzada com o loudness, limitado a 3–20 s (padrão 5 s se a correlação for fraca).
4. The system SHALL combinar as séries por soma ponderada com pesos lidos de `config.toml` e dar bônus quando ≥ 2 sinais independentes passam do limiar no mesmo pico.
5. WHEN dois candidatos se sobrepõem em mais de 30% THEN o sistema SHALL manter só o de maior score.
6. The system SHALL gravar em `clips.json`, para cada clipe, o score total e a contribuição de cada sinal.

**Independent Test**: com séries sintéticas, um pico em chat + áudio no mesmo instante vira o candidato nº 1.

---

### P1: Cortes com sentido ⭐ MVP

**User Story**: Como espectador do clipe, quero que ele comece no início de uma fala e termine depois da reação, para entender sem ter visto a live.

**Why P1**: Requisito central do usuário.

**Acceptance Criteria**:

1. The system SHALL iniciar cada clipe no começo de uma frase e terminá-lo no fim de uma frase, segundo os timestamps de palavra.
2. The system SHALL conter o instante de pico dentro do clipe, com pelo menos 6 s antes (setup) e 3 s depois dele.
3. The system SHALL produzir clipes de 20 a 60 s.
4. IF nenhuma combinação de início e fim de frase cabe em 20–60 s em volta do pico THEN o sistema SHALL descartar o candidato.
5. WHERE o refino de borda por LLM está habilitado o sistema SHALL pedir a ele só índices de frase num schema JSON fixo, nunca timestamps livres (padrão: desabilitado; o LLM só dá título e veredito de autocontido).
6. IF a resposta do LLM falha na validação (índices fora da janela, duração fora de 20–60 s, pico fora do trecho, JSON inválido) THEN o sistema SHALL usar o corte heurístico.
7. The system SHALL não iniciar o clipe com conectivo dependente de contexto ("e aí", "por isso", "então ele") quando houver começo de frase alternativo na janela.

**Independent Test**: dada uma transcrição sintética com frases e um pico, as bordas escolhidas caem em limites de frase e respeitam 20–60 s.

---

### P1: Render vertical legendado ⭐ MVP

**User Story**: Como criador de cortes, quero o clipe pronto em 9:16 com legenda word-level e título sugerido, para só revisar e postar.

**Why P1**: Saída pedida.

**Acceptance Criteria**:

1. The system SHALL renderizar 1080×1920, H.264 + AAC, áudio normalizado.
2. WHEN uma facecam fixa é detectada THEN o sistema SHALL usar o layout `split` (facecam em cima, jogo embaixo); IF não THEN o sistema SHALL usar o layout `blur`.
3. WHERE o usuário define a caixa da facecam em `config.toml` o sistema SHALL usá-la em vez da detecção.
4. The system SHALL queimar legenda ASS com destaque na palavra atual, dentro da safe area vertical.
5. The system SHALL sugerir um título de até 70 caracteres por clipe.
6. WHEN o render termina THEN o sistema SHALL gerar `index.html` na pasta de saída listando os clipes com título, score e motivo.

**Independent Test**: renderizar um trecho de 30 s de um vídeo local e conferir resolução, legenda e `index.html`.

---

### P1: Medir a qualidade ⭐ MVP

**User Story**: Como dono do projeto, quero medir se os trechos escolhidos são bons, para ajustar pesos com dados e não no achismo.

**Why P1**: Sem métrica, a detecção não melhora.

**Acceptance Criteria**:

1. WHEN o usuário roda `clipes eval <pasta>` com um arquivo de ground truth (clipes de viewers, picos do heatmap ou timestamps de comentários) THEN o sistema SHALL reportar Recall@5, Recall@10 e precisão@N com acerto definido como sobreposição ≥ 30% ou centro do ground truth dentro do clipe.
2. WHEN o heatmap é usado como ground truth THEN o sistema SHALL rodar a detecção sem o sinal de heatmap (evitar vazamento).
3. The system SHALL reportar MidWordCutRate, SentenceEdgeRate e PeakPositionRatio e gerar o checklist G1–G5 por clipe (G1 sem corte no meio de palavra e G3 pico contido são eliminatórios) com campo para nota manual.

**Independent Test**: com clipes e ground truth sintéticos, recall e precisão batem com o cálculo à mão.

---

### P2: Live em andamento por janela deslizante

**User Story**: Como criador de cortes, quero colar o link de uma live ao vivo e ir recebendo clipes enquanto ela acontece.

**Why P2**: Pedido do usuário, mas depende do MVP de VOD.

**Acceptance Criteria**:

1. WHEN o usuário roda `clipes live <url>` de uma live ativa THEN o sistema SHALL gravar vídeo em segmentos e o chat com timestamp até a live acabar ou o usuário interromper.
2. WHILE a live está ativa o sistema SHALL, a cada 5 min, rodar o pipeline de VOD sobre os últimos 10 min gravados.
3. The system SHALL não gerar dois clipes com sobreposição > 30% entre janelas.
4. IF a gravação cai THEN o sistema SHALL tentar reconectar e registrar o buraco, sem perder os clipes já gerados.
5. WHEN a live acaba THEN o sistema SHALL processar a janela final e encerrar.

**Independent Test**: simular a live com um arquivo local tocado como segmentos e conferir clipes saindo por janela.

---

### P3: Sinais extras e revisão

**User Story**: Como dono do projeto, quero testar sinais mais caros e revisar clipes com botões, quando o básico já funcionar.

**Why P3**: Só vale se a avaliação mostrar ganho.

**Acceptance Criteria**:

1. WHERE o classificador de eventos de áudio está habilitado o sistema SHALL adicionar as séries de risada e grito ao score.
2. WHERE o painel Gradio está habilitado o sistema SHALL permitir aprovar, descartar e ajustar ±s nas bordas de um clipe e renderizá-lo de novo.

---

## Edge Cases

- IF a VOD não tem fala (só jogo) THEN o sistema SHALL usar pausas de áudio como limites de borda e marcar o clipe como `sem_fala`.
- IF a VOD tem mais de 6 h THEN o sistema SHALL processar normalmente, com o áudio em blocos para não estourar a RAM.
- IF o yt-dlp falha por mudança no site THEN o sistema SHALL mostrar o erro do yt-dlp e sugerir `yt-dlp -U`.
- IF o Ollama não está rodando e nenhum LLM remoto está configurado THEN o sistema SHALL seguir só com o corte heurístico e título vazio.
- WHEN dois picos estão a menos de 20 s um do outro THEN o sistema SHALL tratá-los como um candidato só.

---

## Requirement Traceability

| Requirement ID | Story | Phase | Status |
| --- | --- | --- | --- |
| CLIP-01 | P1: VOD a partir do link (AC 1, 3, 5) | Tasks | Pending |
| CLIP-02 | P1: VOD, download mínimo + cache (AC 2, 6) | Tasks | Pending |
| CLIP-03 | P1: VOD, chat opcional (AC 4) | Tasks | Pending |
| CLIP-04 | P1: Sinais (AC 1) | Tasks | Pending |
| CLIP-05 | P1: Sinais, normalização + atraso do chat (AC 2, 3) | Tasks | Pending |
| CLIP-06 | P1: Sinais, fusão + NMS + explicação (AC 4–6) | Tasks | Pending |
| CLIP-07 | P1: Sentido, bordas heurísticas (AC 1–4, 7) | Tasks | Pending |
| CLIP-08 | P1: Sentido, LLM com schema + fallback (AC 5, 6) | Tasks | Pending |
| CLIP-09 | P1: Render (AC 1–4) | Tasks | Pending |
| CLIP-10 | P1: Render, título + index.html (AC 5, 6) | Tasks | Pending |
| CLIP-11 | P1: Qualidade (AC 1–3) | Tasks | Pending |
| CLIP-12 | P2: Live (AC 1–5) | Tasks | Pending |
| CLIP-13 | P3: Eventos de áudio (AC 1) | Tasks | Pending |
| CLIP-14 | P3: Painel Gradio (AC 2) | Tasks | Pending |
