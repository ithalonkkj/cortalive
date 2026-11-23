#!/usr/bin/env bash
# Abre o CortaLive no Linux ou no Mac.   Uso: ./iniciar.sh   (ou ./iniciar.sh --lan)
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  echo "O ffmpeg não foi encontrado. No Mac: brew install ffmpeg. No Ubuntu: sudo apt install ffmpeg"
  exit 1
fi
if [ ! -f .venv/instalado.txt ]; then
  echo "Primeira vez: instalando o CortaLive. Isso leva alguns minutos..."
  rm -rf .venv
  python3 -m venv .venv || { echo "Não consegui criar o ambiente do Python."; exit 1; }
  .venv/bin/pip install --upgrade pip
  echo "[1/3] Instalando o essencial..."
  .venv/bin/pip install starlette uvicorn numpy || { echo "Falhou ao instalar o essencial."; exit 1; }
  echo "[2/3] Instalando o download do YouTube..."
  .venv/bin/pip install yt-dlp || echo "AVISO: o link do YouTube não vai funcionar."
  echo "[3/3] Instalando o Whisper das legendas..."
  .venv/bin/pip install faster-whisper || echo "AVISO: sem o Whisper, os cortes saem sem legenda. Use o Python 3.12 se o seu for mais novo."
  echo ok > .venv/instalado.txt
fi
if [ ! -f .venv/opencv.txt ]; then
  echo "Instalando a detecção de webcam..."
  .venv/bin/pip install "opencv-python-headless<5" || echo "AVISO: a webcam não será achada sozinha, mas você pode marcar a área na mão."
  echo ok > .venv/opencv.txt
fi
exec .venv/bin/python -m cortalive "$@"
