"""Acha a webcam do streamer na live.

Como funciona:
1. Tira cerca de 10 quadros espalhados pela live (rápido, mesmo em arquivos de 40 GB).
2. Procura rostos em cada quadro (OpenCV, roda no seu computador).
3. O rosto do streamer fica no mesmo lugar o tempo todo, e rostos do jogo mudam de lugar. Então vale o
   rosto que aparece parado em vários quadros.
4. Estima o retângulo da webcam em volta do rosto, um pouco menor que a webcam inteira para nunca pegar o jogo.

A área é só uma sugestão. A tela deixa você arrastar o retângulo ou desenhar outro por cima do quadro.
Sem o OpenCV instalado, o app só guarda um quadro e você marca a área na mão.

Todas as coordenadas são frações do quadro (0 a 1), então valem para qualquer resolução.
"""
from __future__ import annotations

import importlib.util
import math
import os
import shutil
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import media

FRAME_NAME = "camframe.jpg"


@lru_cache(maxsize=1)
def _cascade_path() -> Optional[str]:
    """Modelo de rosto do OpenCV (Haar). O opencv-python-headless 4.x traz esse arquivo."""
    if importlib.util.find_spec("cv2") is None:
        return None
    import cv2

    cands = [os.environ.get("CORTALIVE_CASCADE", "")]
    data = getattr(cv2, "data", None)
    if data is not None:
        cands.append(os.path.join(data.haarcascades, "haarcascade_frontalface_default.xml"))
    for c in cands:
        if c and os.path.isfile(c) and not cv2.CascadeClassifier(c).empty():
            return c
    return None


def available() -> bool:
    """True quando dá para achar a webcam sozinho (OpenCV instalado e com o modelo de rosto)."""
    return _cascade_path() is not None


# Um localizador de rostos recebe a imagem em cinza e devolve caixas (x, y, largura, altura) em pixels.
Finder = Callable[[np.ndarray], list]


def _grab(src, t: float, out: Path, width: int = 1280) -> None:
    media.run_ffmpeg(["-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
                      "-q:v", "3", str(out)])


def estimate_rect(cx: float, cy: float, fw: float, aspect: float) -> dict:
    """Retângulo da webcam (16:9) em volta de um rosto. cx, cy e fw são frações do quadro; aspect = largura/altura."""
    w = min(1.0, 3.0 * fw)
    h = min(1.0, w * aspect * 9 / 16)   # altura em fração do quadro para um retângulo 16:9 em pixels
    x = min(1.0 - w, max(0.0, cx - w / 2))
    y = min(1.0 - h, max(0.0, cy - 0.45 * h))   # o rosto fica um pouco acima do centro
    # Não encosta na borda de propósito: muitas webcams têm margem, e uma área um pouco menor que a webcam
    # nunca mostra fundo da live. Para pegar a webcam inteira, o usuário ajusta o retângulo na tela.
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def _cluster(dets: list) -> list:
    """Agrupa detecções que ficam no mesmo lugar. Cada detecção: dict com idx, cx, cy, fw, fh (frações)."""
    clusters: list = []
    for d in dets:
        for c in clusters:
            mcx = statistics.median(p["cx"] for p in c)
            mcy = statistics.median(p["cy"] for p in c)
            mfw = statistics.median(p["fw"] for p in c)
            if abs(d["cx"] - mcx) < 0.04 and abs(d["cy"] - mcy) < 0.06 and 0.7 < d["fw"] / mfw < 1.45:
                c.append(d)
                break
        else:
            clusters.append([d])
    return clusters


def pick_webcam(dets: list, n_frames: int, aspect: float) -> Optional[tuple]:
    """Devolve (retângulo, índice do quadro para mostrar) ou None se nenhum rosto ficou parado."""
    best, best_n = None, 0
    for c in _cluster(dets):
        n = len({p["idx"] for p in c})
        if n > best_n:
            best, best_n = c, n
    if best is None or best_n < max(3, math.ceil(0.3 * n_frames)):
        return None
    cx = statistics.median(p["cx"] for p in best)
    cy = statistics.median(p["cy"] for p in best)
    fw = statistics.median(p["fw"] for p in best)
    mid = sorted(best, key=lambda p: abs(p["cx"] - cx) + abs(p["cy"] - cy))[0]
    return estimate_rect(cx, cy, fw, aspect), mid["idx"]


def _haar_finder(gray: np.ndarray) -> list:
    import cv2

    H, W = gray.shape[:2]
    casc = cv2.CascadeClassifier(_cascade_path())
    found = casc.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=6, minSize=(30, 30),
                                  maxSize=(W // 3, H // 2))
    return [tuple(map(int, f)) for f in found]


def _faces(path: Path, finder: Finder) -> tuple:
    import cv2

    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0, 0, []
    H, W = img.shape[:2]
    return W, H, [tuple(map(int, f)) for f in finder(img)]


def detect(src, duration: float, out_dir, n: int = 10, jitter: float = 0.0, finder: Optional[Finder] = None) -> dict:
    """Acha a webcam e salva o quadro para o ajuste manual em out_dir/camframe.jpg."""
    out_dir = Path(out_dir)
    ts = []
    for i in range(n):
        f = 0.06 + 0.88 * (i + 0.5) / n + (jitter if i % 2 else -jitter)
        ts.append(min(max(1.0, duration * f), max(1.0, duration - 1.0)))
    frames: list = []
    for i, t in enumerate(ts):
        p = out_dir / f"_cam{i}.jpg"
        try:
            _grab(src, t, p)
            frames.append(p)
        except media.MediaError:
            continue
    if not frames:
        raise media.MediaError("Não consegui tirar quadros da live para achar a webcam.")

    can = finder is not None or available()
    finder = finder or (_haar_finder if can else None)
    info = {"available": can, "detected": False, "rect": None, "frame": FRAME_NAME, "aspect": 16 / 9}
    show = frames[len(frames) // 2]
    try:
        if finder is not None:
            dets, aspect = [], 16 / 9
            for idx, p in enumerate(frames):
                W, H, faces = _faces(p, finder)
                if not W:
                    continue
                aspect = W / H
                for (x, y, w, h) in faces:
                    dets.append({"idx": idx, "cx": (x + w / 2) / W, "cy": (y + h / 2) / H, "fw": w / W, "fh": h / H})
            info["aspect"] = aspect
            picked = pick_webcam(dets, len(frames), aspect)
            if picked:
                info["rect"], i_show = picked
                info["detected"] = True
                show = frames[i_show]
        shutil.copyfile(show, out_dir / FRAME_NAME)
        try:
            from PIL import Image

            with Image.open(out_dir / FRAME_NAME) as im:
                info["aspect"] = im.width / im.height
        except Exception:
            pass
    finally:
        for p in frames:
            p.unlink(missing_ok=True)
    return info
