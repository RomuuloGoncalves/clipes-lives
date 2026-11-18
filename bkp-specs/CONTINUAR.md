# Continuar (sessão encerrada em 2026-11-17)

Estado: as 5 features estão implementadas e integradas em `clipes-lives` (master). Falta validar. Regras: sem acesso a banco; nada instalado no host; tudo no container Docker; PC de piso Ryzen 5 5500U sem GPU.

## Cuidado antes de rodar qualquer coisa pesada
O PC do usuário desligou 3 vezes em 17/11 (11:25, 13:32, 14:20) sob carga de 6 threads, sem OOM nem aviso térmico no log. Causa não confirmada (suspeita: energia/térmico). Use sempre `./clipar` (3 CPUs, `nice 19`, `./vigia-temp` pausa o container a 85 °C e grava `~/Downloads/clipes-lives/.temp.log`). Pacote da CPU estava em 71 °C ocioso. Não rodar benchmarks com 6 threads.

## Pendências (da mais leve à mais pesada)
1. Rodar o monitor com `--dry-run` (4 canais, ~3 min): `./clipar monitor --channels-file /out/_testes/channels.txt --out-dir /out --dry-run` (criar o channels.txt com 1 canal por linha). Interrompido pelo usuário antes de rodar.
2. Reconferir o fundo preto (novo padrão `CLIPES_BLUR_BG=0`) re-rodando `./clipar "https://www.youtube.com/watch?v=UBv07VGeD24" --max-clips 3 --channel Podpah` (ASR em cache).
3. Reconferir o título do LLM após `is_title_echo` (antes ecoava o título da live + "?"; nota do LLM sempre 8.0, só serve de desempate).
4. Teste real do monitor completo (detectar pico, gravar, processar, gerar clipe). Só houve medições M5/M6 (0 clipes) e um clipe de live direto via `./clipar https://www.twitch.tv/gaules --live-minutes 5`.
5. Smoke test do LLM real: `tests/test_llm_integration.py` (ficou fora da suíte; flaky sob carga).
6. Benchmark M2/M7 (Passada 2): `CLIPES_RUN_BENCH=1 pytest tests/test_bench_m2_m7_quota.py` com cache em `~/Downloads/clipes-lives-v2/*Podpah*/.cache`; só a Passada 1 rodou (96 blocos, quota=8, floor=False). Salvar resultado parcial em disco e gerar `pesquisa/MEDICOES-M2-M7.md`.
7. M1 (WER Whisper small x large-v3-turbo): bloqueado, depende da transcrição de referência de 5 min do usuário.
8. Decidir MiniLM x fallback léxico como padrão (MiniLM abaixo dos critérios nas medições).
9. Itens abertos de `pesquisa/REVISAO-rodada2.md` (os 12 de média/baixa): `--local-dir` do monitor (AC3), tópicos sobrepostos >30% na cota, `eval.py peak_ok` sempre verdadeiro em `edge_mode=topic`, threads padrão 6 no código x `cpus: 3`, ONNX recarregado a cada candidato, llama-server sem PDEATHSIG, Helix consultado 1x, `_stop` síncrono no monitor.
10. Fechamento: atualizar INDEX.md/FEATURE.md (status hoje "implementada (validação final pendente)"), suíte final, liberar worker `dev-deteccao` e o orquestrador do Orca.

## Já corrigido nesta sessão
Padrão do fundo (preto), pasta `data - título [id]`, unpause nos traps do `clipar`/`vigia-temp`, falso pico no IRC após reconectar (`tmi-sent-ts`), SIGTERM no monitor, `total_s` com Passada 1 em dobro, piso 0,25 nos picos fortes do modo auto, 0 clipes sem fala no modo cota, ranking de canais quentes por 60 s, `source.mkv` apagado ao fechar job, eco do título da live no LLM. Testes: 162 passam, 1 skip.

## Lembretes
Tempos medidos em i5-10500 (não no 5500U). Só clipar canais autorizados (ToS/conteúdo reutilizado). Nada é publicado em rede social. Imagem Docker: reconstruir (`docker compose build`) após mudar código, porque `./clipar` usa a imagem, não o código local.
