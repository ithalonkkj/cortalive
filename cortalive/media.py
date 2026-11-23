"""Utilitários de mídia: ffprobe, extração de áudio, energia por segundo e ffmpeg com progresso."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

Progress = Optional[Callable[[float], None]]


class MediaError(RuntimeError):
    """Erro com mensagem pronta para mostrar ao usuário."""


def ffmpeg_ok() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise MediaError("Não consegui abrir esse arquivo de vídeo. " + r.stderr.strip()[-300:])
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(fmt.get("duration") or 0)
    if not dur:
        for s in streams:
            try:
                dur = max(dur, float(s.get("duration") or 0))
            except ValueError:
                pass
    if video is None:
        raise MediaError("O arquivo não tem vídeo.")
    if dur <= 0:
        raise MediaError("Não consegui descobrir a duração do vídeo.")
    return {
        "duration": dur,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": audio is not None,
        "size": int(fmt.get("size") or 0),
    }


def run_ffmpeg(args, duration: Optional[float] = None, on_progress: Progress = None, cwd=None) -> None:
    """Roda o ffmpeg e chama on_progress(0..1) conforme o tempo de saída avança."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *map(str, args)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", cwd=cwd)
    err: list[str] = []
    reader = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    reader.start()
    for line in p.stdout:
        if on_progress and duration and (line.startswith("out_time_us=") or line.startswith("out_time_ms=")):
            try:
                sec = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            on_progress(max(0.0, min(1.0, sec / duration)))
    p.wait()
    reader.join(timeout=5)
    if p.returncode != 0:
        raise MediaError("O ffmpeg falhou: " + "".join(err)[-600:])
    if on_progress:
        on_progress(1.0)


def extract_audio(src, dst, duration: float, on_progress: Progress = None) -> None:
    """Áudio mono 16 kHz em WAV. É o formato que o Whisper usa e é leve para analisar."""
    run_ffmpeg(["-i", src, "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
               duration, on_progress)


def rms_db_per_second(wav_path, on_progress: Progress = None) -> np.ndarray:
    """Volume (dBFS) de cada segundo do áudio, lido em blocos de 60 s para não gastar memória."""
    out: list[np.ndarray] = []
    with wave.open(str(wav_path)) as w:
        sr, ch, total = w.getframerate(), w.getnchannels(), max(1, w.getnframes())
        done = 0
        while True:
            raw = w.readframes(sr * 60)
            if not raw:
                break
            a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if ch > 1:
                a = a[: len(a) // ch * ch].reshape(-1, ch).mean(1)
            full = len(a) // sr
            if full:
                x = a[: full * sr].reshape(full, sr)
                out.append(20 * np.log10(np.sqrt((x ** 2).mean(1)) + 1e-5))
            rest = a[full * sr:]
            if len(rest) > sr // 4:
                out.append(np.array([20 * np.log10(np.sqrt((rest ** 2).mean()) + 1e-5)]))
            done += len(raw) // 2 // ch
            if on_progress:
                on_progress(min(1.0, done / total))
    return np.concatenate(out) if out else np.zeros(0)


def read_window(wav_path, t0: float, t1: float) -> np.ndarray:
    """Trecho do WAV como float32 mono 16 kHz (entrada direta do Whisper)."""
    with wave.open(str(wav_path)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        w.setpos(max(0, min(w.getnframes(), int(t0 * sr))))
        raw = w.readframes(max(0, int((t1 - t0) * sr)))
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a[: len(a) // ch * ch].reshape(-1, ch).mean(1)
    return a
