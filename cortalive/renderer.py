"""Gera os arquivos finais com ffmpeg: prévia leve, corte cru e corte 9:16 com legenda."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from . import captions, config, media

SIZES = {"720": (720, 1280), "1080": (1080, 1920), "2160": (2160, 3840)}
BITRATE = {"720": "6M", "1080": "12M", "2160": "40M"}


@lru_cache(maxsize=1)
def pick_encoder() -> str:
    """Usa a placa de vídeo quando dá (bem mais rápido); senão, libx264 no processador."""
    want = os.environ.get("CORTALIVE_ENCODER", "auto").lower()
    if want in ("cpu", "libx264"):
        return "libx264"
    names = {"nvenc": "h264_nvenc", "videotoolbox": "h264_videotoolbox", "qsv": "h264_qsv"}
    order = [names[want]] if want in names else list(names.values())
    for enc in order:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
             "-c:v", enc, "-f", "null", "-"], capture_output=True)
        if r.returncode == 0:
            return enc
    return "libx264"


def _video_args(quality: str) -> list:
    enc = pick_encoder()
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-pix_fmt", "yuv420p"]
    if enc == "h264_videotoolbox":
        return ["-c:v", enc, "-b:v", BITRATE.get(quality, "12M"), "-pix_fmt", "yuv420p"]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-global_quality", "20", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


_AUDIO = ["-c:a", "aac", "-b:a", "192k"]


def render_raw(src, start: float, dur: float, out, on_progress=None) -> None:
    """Corte cru: o mesmo enquadramento da live, sem legenda e sem edição."""
    media.run_ffmpeg(
        ["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", "-map", "0:v:0", "-map", "0:a?",
         *_video_args("1080"), *_AUDIO, "-movflags", "+faststart", out],
        dur, on_progress)


def clean_cam(raw) -> dict | None:
    """Área da webcam como frações do quadro (x, y, w, h). Devolve None se estiver inválida."""
    try:
        x, y, w, h = (float(raw[k]) for k in ("x", "y", "w", "h"))
    except (TypeError, KeyError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and w >= 0.03 and h >= 0.03):
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(min(w, 1 - x), 4), "h": round(min(h, 1 - y), 4)}


def cam_layout_graph(W: int, H: int, cam: dict, camh: float, cropx: float) -> str:
    """Layout com a webcam em cima e a live embaixo, os dois ao mesmo tempo.

    Em cima: só a área da webcam, preenchendo a largura. Embaixo: a live (recorte central, ajustável).
    Uma linha fina separa os dois. A tela desenha a prévia com a mesma conta.
    """
    camh = min(0.5, max(0.2, float(camh)))
    ch = int(round(H * camh / 2)) * 2
    gh = H - ch
    c = cam
    return (
        f"[0:v]split=2[a][b];"
        f"[a]crop=trunc(iw*{c['w']}/2)*2:trunc(ih*{c['h']}/2)*2:iw*{c['x']}:ih*{c['y']},"
        f"scale={W}:{ch}:force_original_aspect_ratio=increase,crop={W}:{ch}[cam];"
        f"[b]scale={W}:{gh}:force_original_aspect_ratio=increase,"
        f"crop={W}:{gh}:(in_w-out_w)*{cropx:.3f}:(in_h-out_h)/2[game];"
        f"[cam][game]vstack=inputs=2,"
        f"drawbox=x=0:y={ch - H // 640}:w={W}:h={H // 320}:color=0xE5412F:t=fill,setsar=1"
    )


def render_edit(src, start: float, dur: float, words: list, out, *, style="impacto", pos="bot",
                quality="1080", reframe="fill", cropx=0.5, cam=None, camh=0.38, on_progress=None) -> None:
    """Corte editado: 9:16, reenquadramento (preencher, fundo desfocado ou webcam em cima) e legenda queimada."""
    W, H = SIZES.get(quality, SIZES["1080"])
    cropx = min(1.0, max(0.0, float(cropx)))
    cam = clean_cam(cam)
    tmp = Path(tempfile.mkdtemp(prefix="cortalive-"))
    try:
        sub = ""
        if words:
            (tmp / "cap.ass").write_text(captions.build_ass(words, style, pos), encoding="utf-8")
            fonts = config.fonts_dir()
            sub = "subtitles=cap.ass"
            if fonts.exists() and any(fonts.iterdir()):
                sub += ":fontsdir='" + str(fonts).replace("\\", "/").replace(":", "\\:").replace("'", "\\'") + "'"
        if reframe == "cam" and cam:
            graph = cam_layout_graph(W, H, cam, camh, cropx)
        elif reframe == "blur":
            graph = (f"[0:v]split[a][b];"
                     f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:6[bg];"
                     f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
        else:
            graph = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                     f"crop={W}:{H}:(in_w-out_w)*{cropx:.3f}:(in_h-out_h)/2,setsar=1")
        graph += (("," + sub) if sub else "") + "[v]"
        out_abs = str(Path(out).resolve())
        media.run_ffmpeg(
            ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}", "-filter_complex", graph,
             "-map", "[v]", "-map", "0:a?", *_video_args(quality), *_AUDIO, "-movflags", "+faststart", out_abs],
            dur, on_progress, cwd=str(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_preview(src, start: float, dur: float, out_mp4, out_jpg, peak_offset: float) -> None:
    """Prévia leve (540p) do trecho e uma miniatura. É o que a tela do app toca."""
    media.run_ffmpeg(
        ["-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}", "-map", "0:v:0", "-map", "0:a?",
         "-vf", "scale=-2:540", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out_mp4])
    media.run_ffmpeg(
        ["-ss", f"{max(0.0, min(peak_offset, dur - 0.2)):.2f}", "-i", str(out_mp4), "-frames:v", "1",
         "-vf", "scale=-2:320", "-q:v", "4", out_jpg])
