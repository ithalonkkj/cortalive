@echo off
chcp 65001 >nul
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo O ffmpeg nao foi encontrado. Instale com:  winget install Gyan.FFmpeg
  echo Depois feche esta janela e abra o iniciar.bat de novo.
  pause
  exit /b 1
)

if not exist ".venv\instalado.txt" (
  echo Primeira vez: instalando o CortaLive. Isso leva alguns minutos...
  if exist ".venv" rmdir /s /q ".venv"
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Nao consegui criar o ambiente do Python. Reinstale o Python em python.org e marque Add Python to PATH.
    pause
    exit /b 1
  )
  .venv\Scripts\python -m pip install --upgrade pip
  echo.
  echo [1/3] Instalando o essencial...
  .venv\Scripts\python -m pip install starlette uvicorn numpy
  if errorlevel 1 (
    echo.
    echo Falhou ao instalar o essencial. Veja a mensagem acima, confira a internet e rode de novo.
    pause
    exit /b 1
  )
  echo.
  echo [2/3] Instalando o download do YouTube...
  .venv\Scripts\python -m pip install yt-dlp
  if errorlevel 1 echo AVISO: o link do YouTube nao vai funcionar. O resto do app funciona.
  echo.
  echo [3/3] Instalando o Whisper das legendas...
  .venv\Scripts\python -m pip install faster-whisper
  if errorlevel 1 (
    echo AVISO: o Whisper nao instalou, entao os cortes saem sem legenda.
    echo Se seu Python for o 3.13 ou mais novo, instale o Python 3.12 e apague a pasta .venv para tentar de novo.
  )
  echo ok> ".venv\instalado.txt"
  echo.
  echo Instalacao concluida.
)

if not exist ".venv\opencv.txt" (
  echo.
  echo Instalando a deteccao de webcam...
  .venv\Scripts\python -m pip install "opencv-python-headless<5"
  if errorlevel 1 echo AVISO: a webcam nao sera achada sozinha, mas voce pode marcar a area na mao.
  echo ok> ".venv\opencv.txt"
)

.venv\Scripts\python -m cortalive %*
pause
