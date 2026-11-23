"""Teste do motor com um vídeo sintético: 4 minutos, com 3 explosões de áudio nos segundos 60, 120 e 180.

Rode com:  python tests/test_engine.py
O Whisper é trocado por um transcritor falso, então o teste não precisa de internet nem de modelo.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from cortalive import analyzer, captions, media, renderer


def make_video(path: Path, cam: bool = False) -> None:
    """Vídeo sintético de 4 min. Com cam=True, põe um rosto de verdade (webcam) no canto superior direito."""
    audio = ("aevalsrc='(0.03+0.7*(between(t,58,64)+between(t,118,125)+between(t,178,183)))"
             "*sin(2*PI*300*t)+0.02*(random(0)-0.5)':s=16000:d=240")
    if not cam:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=240",
                        "-f", "lavfi", "-i", audio, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(path)], check=True)
        return
    from PIL import Image
    from skimage import data

    face = path.parent / "face.png"
    Image.fromarray(data.astronaut()).save(face)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=240",
                    "-f", "lavfi", "-i", audio, "-i", str(face),
                    "-filter_complex", "[2]scale=420:420,pad=420:540:0:120:color=gray,crop=420:236:0:111[c];[0][c]overlay=W-w-24:24[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)


# onde a webcam do vídeo sintético fica de verdade (frações do quadro)
CAM_TRUTH = {"x": (1920 - 420 - 24) / 1920, "y": 24 / 1080, "w": 420 / 1920, "h": 236 / 1080}


def skimage_finder(gray):
    """Localizador de rostos para teste. O OpenCV 5 desta máquina vem sem o modelo Haar, então usamos o do scikit-image."""
    from skimage import data
    from skimage.feature import Cascade

    det = Cascade(data.lbp_frontal_face_cascade_filename())
    return [(r["c"], r["r"], r["width"], r["height"])
            for r in det.detect_multi_scale(gray, scale_factor=1.1, step_ratio=1, min_size=(30, 30), max_size=(300, 300))]


def patch_webcam():
    """Troca o localizador do OpenCV pelo do scikit-image (só nos testes)."""
    from cortalive import webcam

    webcam._cascade_path = lambda: "teste"
    webcam._haar_finder = skimage_finder


def fake_transcriber(audio: np.ndarray, language: str) -> list:
    words, t, i = [], 0.3, 0
    phrase = "isso foi insano mano olha o que aconteceu".split()
    while t < len(audio) / 16000 - 0.6:
        w = phrase[i % len(phrase)]
        words.append({"w": w + ("." if i % len(phrase) == len(phrase) - 1 else ""), "t0": t, "t1": t + 0.35,
                      "end": i % len(phrase) == len(phrase) - 1})
        t += 0.42
        i += 1
    return words


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cl-test-"))
    video = tmp / "live.mp4"
    print("gerando vídeo de teste...")
    make_video(video)

    stages = []
    res = analyzer.analyze(video, tmp, analyzer.Options(length=(15, 30), count=3), fake_transcriber,
                           lambda s, f, m: stages.append(s) if s not in stages else None)
    print("etapas:", stages)
    peaks = sorted(c["peakT"] for c in res["clips"])
    print("picos achados:", peaks)
    assert len(res["clips"]) == 3, "devia achar 3 cortes"
    for got, want in zip(peaks, (61, 121.5, 180.5)):
        assert abs(got - want) < 6, f"pico em {got}, esperado perto de {want}"
    for c in res["clips"]:
        assert 13 <= c["dur"] <= 33, c["dur"]
        assert c["start"] <= c["peakT"] <= c["start"] + c["dur"]
        assert c["words"] and c["words"][0]["t0"] >= 0
        print(f"  corte {c['id']}: {c['start']:.1f}s +{c['dur']:.1f}s nota {c['score']} {c['tags']} '{c['title']}'")
    assert len(res["curve"]) == 240 and max(res["curve"]) == 1.0

    c = res["clips"][0]
    ass = captions.build_ass(c["words"], "impacto", "bot")
    assert "Dialogue:" in ass and "ISSO" in ass
    for style in captions.STYLES:
        for pos in captions.POSITIONS:
            assert "Dialogue:" in captions.build_ass(c["words"], style, pos)

    out = tmp / "edit.mp4"
    renderer.render_edit(video, c["start"], c["dur"], c["words"], out, style="karaoke", quality="720")
    info = media.probe(out)
    print("editado:", info["width"], "x", info["height"], f"{info['duration']:.1f}s")
    assert (info["width"], info["height"]) == (720, 1280) and abs(info["duration"] - c["dur"]) < 1.0

    out2 = tmp / "edit-blur.mp4"
    renderer.render_edit(video, c["start"], c["dur"], c["words"], out2, style="neon", quality="720", reframe="blur", pos="top")
    assert media.probe(out2)["height"] == 1280

    raw = tmp / "raw.mp4"
    renderer.render_raw(video, c["start"], c["dur"], raw)
    ri = media.probe(raw)
    print("cru:", ri["width"], "x", ri["height"], f"{ri['duration']:.1f}s")
    assert (ri["width"], ri["height"]) == (1280, 720)

    renderer.make_preview(str(video), c["start"], c["dur"], tmp / "p.mp4", tmp / "p.jpg", 5)
    assert (tmp / "p.mp4").stat().st_size > 0 and (tmp / "p.jpg").stat().st_size > 0
    print("OK: motor funcionando. Arquivos em", tmp)


if __name__ == "__main__":
    main()
