"""Inicia o CortaLive:  python -m cortalive   (use --lan para abrir também no celular)."""
from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

import uvicorn

from . import media
from .server import create_app


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="cortalive")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lan", action="store_true",
                    help="deixa o celular acessar pela mesma rede Wi-Fi (o celular só consegue enviar arquivos)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    if not media.ffmpeg_ok():
        print("ATENÇÃO: o ffmpeg não foi encontrado no PATH. Instale-o antes de usar o app.")
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    print(f"\nCortaLive rodando em  http://localhost:{a.port}")
    if a.lan:
        print(f"No celular (mesma rede Wi-Fi), abra  http://{lan_ip()}:{a.port}")
    print("Para fechar, aperte Ctrl+C.\n")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
    uvicorn.run(create_app(lan=a.lan), host=host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
