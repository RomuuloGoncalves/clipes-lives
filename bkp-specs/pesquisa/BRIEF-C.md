# Pesquisa C: credito/encerramento de clipes e modelos de IA leves em CPU
Parte 1 (web): como clipes de sucesso fazem credito (etiqueta do canal, @, link, marca d'agua, tempo na tela, posicao fora das safe zones) e encerramento (fade de audio, congelar quadro, cartao final 1-3 s, CTA, "Parte 2 ->", loop). Entregue o que viu em exemplos reais e a receita ffmpeg/ASS correspondente.
Parte 2 (modelos, MEDIR quando possivel num container 6 GB/6 threads; senao citar benchmarks com fonte): escolher o menor conjunto de modelos que melhore a qualidade:
 a) LLM de texto local para titulo+legenda+hashtags+nota 0-10 em pt-BR: candidatos GGUF Q4 (Qwen2.5/Qwen3 1.5B-4B, Gemma 2/3 2-4B, Llama 3.2 3B, Phi) via llama.cpp/llama-cpp-python: RAM, tokens/s em 6 threads, qualidade em pt-BR, tamanho do download, licenca. Recomende UM padrao e um fallback.
 b) Embeddings multilingues pequenos para segmentar assunto na transcricao (ex.: multilingual-e5-small, paraphrase-multilingual-MiniLM, via ONNX/sentence-transformers): RAM, velocidade, pt-BR. Compare com TextTiling puro (sem modelo).
 c) Detector de rosto/webcam para reenquadramento vertical (YuNet/OpenCV, MediaPipe): custo por quadro, como amostrar 1 fps.
 d) Detector de risada/grito (YAMNet/PANNs-tiny) para reforcar o sinal de loudness: vale o custo?
 e) ASR: faster-whisper small vs distil-large-v3 vs large-v3-turbo em int8 no piso: velocidade (xRT), RAM, WER pt-BR.
Entregue em pesquisa/C-credito-modelos.md com tabela e recomendacao final + orcamento de RAM/tempo por etapa do pipeline.
