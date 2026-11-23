"""Baixa o vídeo de um link do YouTube com o yt-dlp, na qualidade escolhida.

Use só com lives e vídeos seus, ou com permissão de quem publicou. Baixar conteúdo de terceiros
vai contra os termos do YouTube.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Callable

from .media import MediaError

YT_RE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", re.I)


def ytdlp_available() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def format_for(quality: str) -> str:
    if quality in ("", "best"):
        return "bv*+ba/b"
    h = int(quality)
    return f"bv*[height<={h}]+ba/b[height<={h}]/b"


def download(url: str, quality: str, dest_dir: Path, on_progress: Callable[[float, str], None]):
    """Devolve (caminho do arquivo, título do vídeo)."""
    if not YT_RE.match(url.strip()):
        raise MediaError("Esse link não parece ser do YouTube.")
    if not ytdlp_available():
        raise MediaError("O yt-dlp não está instalado. Rode: pip install yt-dlp")
    import yt_dlp

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            frac = got / total if total else 0.0
            speed = d.get("speed") or 0
            eta = d.get("eta")
            msg = f"{got / 1e6:,.0f} MB de {total / 1e6:,.0f} MB".replace(",", ".")
            if speed:
                msg += f" · {speed / 1e6:.1f} MB/s"
            if eta is not None:
                msg += f" · restam {int(eta) // 60}:{int(eta) % 60:02d}"
            on_progress(min(1.0, frac), msg)

    opts = {
        "format": format_for(quality),
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as e:  # o yt-dlp lança vários tipos de erro
        raise MediaError("Não consegui baixar esse vídeo: " + str(e).replace("ERROR: ", "")[:300])
    if not path.exists():  # após o merge a extensão pode mudar para .mp4
        found = sorted(dest_dir.glob("source.*"))
        if not found:
            raise MediaError("O download terminou, mas não encontrei o arquivo.")
        path = found[0]
    on_progress(1.0, "Download concluído")
    return path, info.get("title") or path.stem
