# Pedido consolidado: clipes-lives, ondas A/B/C (2026-11-17)

Repo de código: ~/Projetos/clipes-lives (Python, Docker, CPU-only hoje). Specs: .specs/ (symlink para ~/Projetos/planos_tlc-spec-driven/clipes-lives). Estado atual: ver .specs/INDEX.md, STATE.md, CONTINUAR.md e as 5 features em features/clipes-lives/.
Referência de design (SEM licença: não copiar código): https://github.com/cardosoGu/LiveCliper (clonar só para ler, em pasta temporária).

## Itens do usuário
1. Integrar com o Discord (servidor guild 1572417049932599479, canal 1572417050771595386). Bot posta prévia dos clipes em thread; recomprime para o limite de upload; comandos. Credenciais do Developer Portal serão obtidas depois pelo usuário/Chrome; nunca em arquivo versionado.
2. Várias gravações simultâneas, em vários PCs, sem clipes repetidos: fila no MongoDB (claim atômico + lease/heartbeat, índice único por canal+transmissão+janela) e dashboard leve, acessível de qualquer lugar (autenticado).
   2a. Dashboard mostra status e desempenho de cada PC (online/offline por heartbeat, CPU, RAM, disco, temperatura, GPU, job atual).
   2b. Detectar a capacidade total de cada PC (núcleos, RAM, GPU) e auto-ajustar limites em vez do padrão fixo de 3 CPUs/nice 19; fila pode preferir o PC mais forte. Variante de imagem com GPU (CUDA) é frente separada (Onda D, fora do escopo agora, só deixar o desenho preparado).
3. Comando "publicar <id>" publica o clipe no TikTok e no YouTube Shorts. TikTok e YouTube ficam PRIVADOS até a auditoria dos apps. Isso reverte a AD-006 (publicação fora de escopo): registrar nova decisão.
4. Enquanto a live estiver ao vivo, ficar clipando (janela deslizante/chunks até a live acabar). Hoje: ./clipar <url> --live-minutes N grava tempo fixo; monitor/ só reage a picos de chat da Twitch.
5. No topo do clipe, mensagem de gancho parecida com a do LiveCliper (título curto <= ~35 chars, pergunta/promessa, sem emoji, nos primeiros ~5 s, respeitando safe zone do TikTok).
6. Nomear o clipe com mais qualidade (título e nome do arquivo `clip_NN_<slug>.mp4`).
7. Versão longa da descrição.
8. Usar APIs de nuvem (Gemini com cascata de modelos/chaves; Claude como reserva paga com teto diário) para título/descrição/nota, mantendo o Qwen local como fallback offline. Reverte "100% local, grátis" para esta etapa: registrar nova decisão e a implicação de privacidade (transcrição sai da máquina).

## Ondas e ordem (todas mexem em cli.py, render.py, llm.py, monitor.py: SERIAL)
- Onda A: (8) camada de IA em nuvem; depois (6, 7, 5).
- Onda B: (2, 2a, 2b) fila Mongo + dashboard + métricas dos PCs; depois (4).
- Onda C: (1) Discord; (3) publicar TikTok/Shorts.
- Onda D (só desenho): imagem com GPU.

## Restrições duras
- NUNCA acessar banco de dados (regra global do usuário), em nenhum ambiente. O código usa o driver Mongo, mas testes usam fake em memória; ninguém roda contra Mongo real. Para dados reais, escrever a query e pedir ao usuário.
- Nunca versionar nem colar em specs/logs chaves, tokens, .env, hostnames. Exemplos com dado fictício.
- Sem git push, sem deploy, sem publicar em rede social durante o desenvolvimento. Commits locais atômicos (Conventional Commits) apenas no repo clipes-lives. O repo de planos NÃO é commitado por agente (cron do usuário às 17:50).
- Tudo roda em Docker; nada instalado no host. PC de piso: Ryzen 5 5500U, 9 GB RAM (~2,6 livres), 16 GB de disco livre, sem GPU, sensor de temperatura lendo 0 °C (vigia-temp quebrado: corrigir faz parte da Onda B). Já desligou 3x sob carga de 6 threads. Não rodar benchmarks pesados.
- PCs do usuário: (1) Linux acima; (2) Windows 11, Ryzen 7 5700X 8c/16t, 15,9 GB, GTX 1660 SUPER 6 GB, 648 GB livres, SEM Docker (precisa Docker Desktop/WSL2); (3) Windows, specs pendentes.
- Só clipar canais autorizados; manter policy.json (blocked/allowed/delay_h).
- tlc-spec-driven: spec com IDs e critérios EARS, validate_spec.py/validate_tasks.py, uma issue por onda em features/clipes-lives/<feature>/issues/<slug>/, FEATURE.md vivo e INDEX.md atualizado. Ler a skill specs-organizacao.

## Defaults assumidos (usuário não respondeu; marcar como suposição na spec)
- Item 2: "simultâneas" = vários canais por PC E vários PCs; Mongo gratuito hospedado (ex.: Atlas free) alcançável pelos 3 PCs; dashboard FastAPI/estático leve atrás de túnel + token.
- Item 6: ambos (título do vídeo e nome do arquivo).
- Item 5: texto nos primeiros ~5 s, depois some.
- Item 4: modo do `clipar <url>` e do monitor limitado a um canal, até a live acabar.
- Item 3: o comando vem do Discord (`publicar <id>`), ids estáveis nos clips.json.

## Decisões do usuário (rodada 2, 2026-11-17)
- Painel: deploy na Vercel lendo o MongoDB (sem túnel, sem domínio obrigatório). Autenticação no próprio app (senha em env da Vercel). Usuário do banco do painel com permissão mínima (leitura + escrita só na coleção de comandos).
- Atlas: liberar 0.0.0.0/0 (Vercel não tem IP fixo) com senha forte. A administração do Atlas (cluster, IP list, usuário) o usuário autorizou pelo Chrome; mesmo assim NUNCA abrir o Data Explorer, nunca consultar dados, nunca montar connection string em .env por agente: a string é colocada pelo usuário.
- Sem sensor de temperatura: só avisar. Painel ganha botão "liberar recursos" para nível crítico: grava um comando na coleção `commands`; o agente do PC executa (whitelist: parar/matar containers do clipes-lives e reiniciar o ciclo). Desenhar com confirmação e log.
- Live perdida (dono caiu): marcar janelas como `lost` e reatribuir a live a um PC ativo, que retoma do ponto atual; nunca regerar clipe de janela já feita (índice único canal+transmissão+janela).
- Lives contínuas simultâneas por PC: INVESTIGAR com números (ver estimativa abaixo); o plano deve prever medição com bench curto em cada PC antes de fixar o limite.
- PC 3 (Windows 11 Pro): i7-13620H 10c/16t, 15,7 GB RAM, RTX 4050 Laptop 6 GB, 17 GB livres de disco (pouco), sem Docker. Notebook: risco térmico.
- Os dois PCs Windows precisam de Docker Desktop (WSL2) para rodar; GPU em Docker fica para a Onda D.
- Estimativa medida em 17/11 neste PC (Ryzen 5 5500U, 3 CPUs): 20 min de live gravada = ~400 s de processamento (razão ~0,33 do tempo real) com 1 worker sequencial. 2 lives = ~0,66 de ocupação do worker, quase sem folga se o vigia térmico pausar. live_source.mkv de 20 min = ~1,1 GB: 2 lives = ~6,6 GB/h, e o disco livre deste PC é 16 GB e do PC 3 é 17 GB: apagar o source de cada janela logo após processar.

## Decisão do usuário (rodada 3): 100% gratuito
- Nada pago, em nenhuma camada. Texto: Gemini FREE TIER (cascata de modelos/chaves, respeitando cotas) e Qwen3-1.7B local como fallback. SEM Claude API (pago), SEM Cloud Billing. Remover CLAUDE_* e o teto em dólar do plano; registrar no STATE.md a implicação: no free tier o Google pode usar o texto da transcrição para treino (aceita).
- Infra só em camadas gratuitas: Atlas M0, Vercel Hobby, Discord, TikTok/YouTube APIs (privado até auditoria). Se algum item exigir pagamento, parar e perguntar; não propor.
- Defaults mantidos: publicar <id> só pelo admin do Discord; publicar nas plataformas ligadas e avisar a outra.

## Decisão do usuário (rodada 4): deploy do painel e URI do Atlas
- Quando o front estiver pronto (painel/), o agente faz o deploy na Vercel pelo CLI e posta o link no chat geral do Discord (guild 1572417049932599479).
- O usuário autorizou o agente a pegar a URI no site do Atlas (Chrome) para ligar a conexão. Escopo acordado: a URI vai SÓ para as variáveis de ambiente da Vercel e para o arquivo de segredos local (~/.config/clipes-lives/segredos.env, modo 600); nunca em chat, spec, log ou git. O agente não roda consultas, não abre o Data Explorer e não testa a conexão com driver. Usar o usuário de banco `painel` (permissão mínima).
