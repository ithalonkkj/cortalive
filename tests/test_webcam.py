"""Teste da webcam: detecção, estimativa da área e layout com webcam em cima.

Rode com:  python tests/test_webcam.py
Precisa de scikit-image e Pillow só para o teste (geram um rosto de verdade). O app em si não precisa.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import test_engine as T
from cortalive import media, renderer, webcam


def main() -> None:
    # --- estimativa da área a partir de um rosto
    r = webcam.estimate_rect(0.86, 0.12, 0.05, 16 / 9)
    assert 0 <= r["x"] and r["x"] + r["w"] <= 1.0001 and 0 <= r["y"] and r["y"] + r["h"] <= 1.0001
    assert r["y"] >= 0.0 and r["h"] > 0, "rosto perto da borda fica com a área dentro do quadro"
    mid = webcam.estimate_rect(0.5, 0.5, 0.05, 16 / 9)
    assert abs(mid["x"] + mid["w"] / 2 - 0.5) < 0.01, "no meio do quadro a área fica centrada no rosto"

    # --- agrupamento: rosto parado em vários quadros vence rostos que mudam de lugar
    dets = [{"idx": i, "cx": 0.85, "cy": 0.15, "fw": 0.05, "fh": 0.09} for i in range(6)]
    dets += [{"idx": i, "cx": 0.2 + 0.07 * i, "cy": 0.6, "fw": 0.06, "fh": 0.1} for i in range(8)]
    got = webcam.pick_webcam(dets, 10, 16 / 9)
    assert got and abs(got[0]["x"] + got[0]["w"] / 2 - 0.85) < 0.1, got
    assert webcam.pick_webcam(dets[6:], 10, 16 / 9) is None, "rostos que andam não são webcam"
    assert webcam.pick_webcam(dets[:2], 10, 16 / 9) is None, "poucas aparições não bastam"

    # --- detecção de ponta a ponta com um rosto de verdade
    T.patch_webcam()
    tmp = Path(tempfile.mkdtemp(prefix="cl-webcam-"))
    live = tmp / "live.mp4"
    T.make_video(live, cam=True)
    info = webcam.detect(live, 240, tmp)
    print("detecção:", info["rect"])
    assert info["available"] and info["detected"] and info["rect"]
    t = T.CAM_TRUTH
    cx, cy = info["rect"]["x"] + info["rect"]["w"] / 2, info["rect"]["y"] + info["rect"]["h"] / 2
    assert abs(cx - (t["x"] + t["w"] / 2)) < 0.06 and abs(cy - (t["y"] + t["h"] / 2)) < 0.08, "centro longe da webcam"
    rc = info["rect"]
    inside = (rc["x"] >= t["x"] - 0.01 and rc["y"] >= t["y"] - 0.01
              and rc["x"] + rc["w"] <= t["x"] + t["w"] + 0.01 and rc["y"] + rc["h"] <= t["y"] + t["h"] + 0.01)
    assert inside, f"a área detectada {rc} passa para fora da webcam {t}"
    assert (tmp / "camframe.jpg").stat().st_size > 1000
    assert not list(tmp.glob("_cam*.jpg")), "quadros temporários devem ser apagados"

    # --- live sem webcam: não inventa
    nocam = tmp / "nocam.mp4"
    T.make_video(nocam, cam=False)
    off = webcam.detect(nocam, 240, tmp)
    assert off["detected"] is False and off["rect"] is None

    # --- layout: webcam em cima, jogo embaixo
    cam = info["rect"]
    words = T.fake_transcriber(np.zeros(16000 * 20, dtype="float32"), "pt")
    out = tmp / "cam.mp4"
    renderer.render_edit(live, 60, 6, words, out, quality="720", reframe="cam", cam=cam, camh=0.4, pos="mid")
    i = media.probe(out)
    assert (i["width"], i["height"]) == (720, 1280)
    fr = tmp / "cam.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(out), "-frames:v", "1", str(fr)], check=True)
    a = np.asarray(Image.open(fr).convert("RGB")).astype(float)
    ch = int(round(1280 * 0.4 / 2)) * 2
    top, bottom = a[10:ch - 10], a[ch + 10:]
    # o rosto tem tons de pele e laranja do traje; o padrão de teste é feito de cores puras
    pure = lambda x: float(np.mean(((x < 40) | (x > 215)).all(axis=2)))  # pixel com todos os canais "no talo"
    print("fração de cores puras  topo: %.2f  baixo: %.2f" % (pure(top), pure(bottom)))
    assert pure(top) < 0.3 and pure(bottom) > 0.6, "em cima deve estar a webcam e embaixo a live"

    # --- a webcam em cima muda o resultado em relação ao preenchimento normal
    out2 = tmp / "fill.mp4"
    renderer.render_edit(live, 60, 6, words, out2, quality="720", reframe="fill")
    fr2 = tmp / "fill.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(out2), "-frames:v", "1", str(fr2)], check=True)
    assert pure(np.asarray(Image.open(fr2).convert("RGB")).astype(float)[10:ch - 10]) > 0.6

    # --- área inválida
    assert renderer.clean_cam({"x": 2, "y": 0, "w": 0.2, "h": 0.2}) is None
    assert renderer.clean_cam({"x": 0.1, "y": 0.1, "w": 0.001, "h": 0.2}) is None
    assert renderer.clean_cam("lixo") is None
    assert renderer.clean_cam({"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}) == {"x": 0.9, "y": 0.9, "w": 0.1, "h": 0.1}
    print("OK: webcam funcionando.")


if __name__ == "__main__":
    main()
