# CortaLive

App local que recebe uma live inteira (10, 12, 40 GB), acha os momentos de tensão e gera cortes
verticais 9:16 com legenda estilizada. Cada corte tem prévia, e você escolhe baixar o corte cru,
o editado ou os dois.

Tudo roda no seu computador. O vídeo não é enviado para nenhum servidor.

## Instalar e abrir

1. Instale o **Python 3.10 ou mais novo** (python.org).
2. Instale o **ffmpeg**: no Windows, `winget install Gyan.FFmpeg`. No Mac, `brew install ffmpeg`.
3. Dê dois cliques em **iniciar.bat** (Windows) ou rode `./iniciar.sh` (Mac e Linux).

Na primeira vez ele instala as bibliotecas e baixa o modelo do Whisper (cerca de 500 MB para o
"small"). Depois abre o navegador em http://localhost:8765.

## Como usar

1. **Importar:** clique em "Escolher no computador" (o arquivo não é copiado, então serve para 40 GB),
   cole o caminho do arquivo, ou use a aba "Link do YouTube".
2. **Analisar:** acompanhe o progresso. Você pode deixar a aba aberta e sair.
3. **Cortes e download:** clique nos picos do gráfico ou na lista, veja a prévia, troque estilo de
   legenda, posição e enquadramento, marque os cortes e clique em "Gerar arquivos".

Os arquivos ficam em `~/CortaLive/projects/<projeto>/exports` (botão "Abrir pasta dos cortes").
Projetos antigos aparecem na aba "Recentes".

## Como ele escolhe os cortes

1. Extrai o áudio e mede o volume de cada segundo.
2. A tensão sobe quando o volume passa muito acima do normal daquele trecho (grito, explosão, risada
   alta) e quando está alto em termos absolutos.
3. Pega os picos mais fortes, sem deixar dois cortes no mesmo lugar.
4. Transcreve só a janela em volta de cada pico (não a live inteira), o que deixa a análise rápida.
5. Dá mais nota para picos com fala acelerada, ajusta começo e fim para caber em frases inteiras e
   monta título e etiquetas.

Ele mede som, não imagem. Um momento tenso em silêncio (um clutch calado, por exemplo) pode passar
batido. Se um corte não ficou bom, desmarque e escolha outro pico no gráfico.

## Desempenho

- Com placa NVIDIA, o render usa a GPU automaticamente. Para forçar o processador: `CORTALIVE_ENCODER=cpu`.
- O Whisper "small" no processador transcreve bem mais rápido que o tempo real. Se tiver GPU NVIDIA com CUDA,
  ele usa sozinho. Para mais precisão, escolha "Alta (medium)" na tela.
- O áudio da live é extraído uma vez por projeto e apagado no fim.

## Celular

`python -m cortalive --lan` abre o app para a sua rede Wi-Fi. No celular, abra o endereço que aparece
no terminal. Pelo celular dá para enviar um vídeo, ver a prévia e baixar os cortes. A análise e o render
continuam rodando no computador. Use só em rede de confiança: nessa opção, quem estiver na rede acessa o app.

Um app instalável no celular, com o processamento no próprio aparelho, é outro projeto (veja abaixo).

## Webcam em cima

O corte pode sair com a **webcam do streamer em cima e a live embaixo**, os dois ao mesmo tempo.

- Na análise, o app procura a webcam sozinho: tira cerca de 10 quadros da live, acha o rosto que fica
  parado no mesmo lugar e marca a área em volta dele. Se achar, o layout "Webcam em cima" já vem ligado.
- Confira a área em **"Ajustar área da webcam"**. Arraste o retângulo, puxe a bolinha do canto para mudar o
  tamanho ou desenhe outro por cima do quadro. Funciona com o dedo no celular também.
- Se não achar (webcam sem rosto aparecendo, live sem webcam), o app avisa e você desenha a área na mão.
  "Detectar de novo" tenta com outros quadros.
- **Tamanho da webcam na tela** vai de 25% a 50% da altura. **Posição do jogo na imagem** escolhe qual parte da
  live aparece embaixo (a live é 16:9 e o espaço é quase quadrado, então só cabe uma parte).
- A legenda vai para a divisa entre a webcam e a live. Você pode mudar.
- A área vale para a live toda e fica salva no projeto.

Limites: se a webcam estiver em cima do jogo, ela também pode aparecer no painel de baixo, a menos que a parte
do jogo escolhida deixe ela de fora (use "Posição do jogo na imagem"). A prévia tem resolução menor que o arquivo
final; o arquivo gerado sai nítido, recortado direto da live original.

## Fontes da legenda

Para a legenda ficar igual à prévia, coloque `Anton-Regular.ttf` e `Inter-Bold.ttf` na pasta
`cortalive/fonts/` (baixe no Google Fonts). Sem elas, o ffmpeg usa uma fonte parecida do sistema.

## YouTube

O download usa o yt-dlp. Use só com lives e vídeos seus ou com permissão de quem publicou. Baixar
conteúdo de terceiros vai contra os termos do YouTube. Se o YouTube mudar algo e o download falhar,
atualize com `pip install -U yt-dlp`.

## Testes

```
python tests/test_engine.py    # motor: picos, cortes, legendas e render, com vídeo sintético
python tests/test_webcam.py    # webcam: detecção, área e layout com webcam em cima
python tests/test_server.py    # servidor de ponta a ponta
```

Os testes trocam o Whisper por um transcritor falso e não precisam de internet. Os de webcam precisam de
`scikit-image` e `Pillow` só para gerar um rosto de teste (`pip install scikit-image pillow`).

## Estrutura

```
cortalive/
  analyzer.py    tensão, picos e escolha dos cortes
  transcribe.py  Whisper (faster-whisper)
  captions.py    legendas ASS nos 4 estilos
  renderer.py    prévia, corte cru e corte 9:16 (ffmpeg)
  webcam.py      acha a webcam do streamer (OpenCV)
  downloader.py  YouTube (yt-dlp)
  pipeline.py    junta tudo em tarefas com progresso
  server.py      API local
  static/        a tela do app
```

## Próximos passos possíveis

- Empacotar como app de desktop (.exe e .app) com instalador.
- App de celular com processamento próprio (outro projeto, bem maior).
- Medir também a imagem (movimento, cenas), não só o som.
- Rastrear o rosto para o corte vertical seguir a pessoa quando não há webcam separada.
- Esconder a webcam do painel do jogo quando ela está por cima dele.
- Postar direto no TikTok, Reels e Shorts.
