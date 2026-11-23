"""Teste do servidor de ponta a ponta (sem Whisper nem internet).

Sobe o app, cria um projeto a partir de um vídeo sintético, acompanha o progresso,
confere prévia/miniatura, exporta os dois formatos e baixa os arquivos.
Rode com:  python tests/test_server.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["CORTALIVE_HOME"] = tempfile.mkdtemp(prefix="cl-home-")

import uvicorn

import test_engine
from cortalive import media, transcribe
from cortalive.server import create_app

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
H = {"X-CortaLive": "1", "Content-Type": "application/json"}

# troca o Whisper por um transcritor falso
transcribe.get_transcriber = lambda model="small": test_engine.fake_transcriber
test_engine.patch_webcam()  # detector de rosto do scikit-image no lugar do OpenCV (só no teste)


def call(path, body=None, method=None, headers=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if body is not None else "GET"),
                                 headers=headers if headers is not None else H)
    try:
        with urllib.request.urlopen(req) as r:
            return r if raw else json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read() or b"{}"
        try:
            return {"_status": e.code, **json.loads(text)}
        except ValueError:
            return {"_status": e.code}


def main() -> None:
    srv = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            call("/api/health")
            break
        except Exception:
            time.sleep(0.1)

    h = call("/api/health")
    print("health:", h)
    assert h["ffmpeg"] is True

    # segurança: POST sem o cabeçalho é recusado
    r = call("/api/projects", {"a": 1}, headers={"Content-Type": "application/json"})
    assert r.get("_status") == 403, r

    # página principal
    html = urllib.request.urlopen(BASE + "/").read().decode()
    assert "CortaLive" in html

    video = Path(tempfile.mkdtemp()) / "live.mp4"
    test_engine.make_video(video)

    probe = call("/api/probe", {"path": str(video)})
    print("probe:", probe)
    assert abs(probe["duration"] - 240) < 2

    bad = call("/api/projects", {"source": {"type": "path", "path": "/nao/existe.mp4"}})
    assert bad.get("_status") == 404

    pid = call("/api/projects", {"source": {"type": "path", "path": str(video)},
                                 "options": {"length": "15-30", "count": 3, "language": "pt"}})["id"]
    last = -1.0
    for _ in range(600):
        st = call(f"/api/projects/{pid}")
        assert st["progress"] >= last - 0.01, "o progresso não pode andar para trás"
        last = st["progress"]
        if st["status"] != "running":
            break
        time.sleep(0.5)
    assert st["status"] == "done", st.get("error")
    res = st["result"]
    print("cortes:", [(c["start"], c["dur"], c["score"]) for c in res["clips"]])
    assert len(res["clips"]) == 3 and all(c.get("preview") and c.get("thumb") for c in res["clips"])

    # a prévia precisa aceitar Range (o navegador pede pedaços para poder pular no vídeo)
    c = res["clips"][0]
    req = urllib.request.Request(BASE + c["preview"], headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206 and len(r.read()) == 100
    assert urllib.request.urlopen(BASE + c["thumb"]).read()[:2] == b"\xff\xd8"

    # a lista de recentes e a reabertura do projeto
    assert any(p["id"] == pid for p in call("/api/projects"))
    assert call(f"/api/projects/{pid}")["status"] in ("done", "running")

    # exportação: editado + cru
    eid = call(f"/api/projects/{pid}/export",
               {"clips": [c["id"]], "variant": "both", "quality": "720", "style": "neon", "pos": "mid",
                "reframe": "blur", "cropx": 0.5})["id"]
    for _ in range(300):
        ex = call(f"/api/projects/{pid}/export/{eid}")
        if ex["done"]:
            break
        time.sleep(0.5)
    print("exportação:", [(i["name"], i["error"]) for i in ex["items"]])
    assert ex["done"] and len(ex["items"]) == 2 and all(i["error"] is None for i in ex["items"])
    for item in ex["items"]:
        with urllib.request.urlopen(BASE + f"/api/projects/{pid}/files/{item['name']}") as r:
            body = r.read()
        assert len(body) > 10000
        f = Path(tempfile.mkdtemp()) / item["name"]
        f.write_bytes(body)
        info = media.probe(f)
        print("  ", item["name"], info["width"], "x", info["height"])
        assert (info["width"], info["height"]) == ((720, 1280) if "9x16" in item["name"] else (1280, 720))

    # live sem webcam: o projeto traz a área vazia e o app segue normal
    assert res["webcam"]["detected"] is False and res["webcam"]["rect"] is None
    no_cam_export = call(f"/api/projects/{pid}/export", {"clips": [1], "variant": "edit", "reframe": "cam"})
    assert no_cam_export.get("_status") == 400, "layout com webcam sem área deve ser recusado"

    # ---------- webcam ----------
    cvideo = video.parent / "cam.mp4"
    test_engine.make_video(cvideo, cam=True)
    pid2 = call("/api/projects", {"source": {"type": "path", "path": str(cvideo)},
                                  "options": {"length": "15-30", "count": 2, "language": "pt"}})["id"]
    for _ in range(600):
        st2 = call(f"/api/projects/{pid2}")
        if st2["status"] != "running":
            break
        time.sleep(0.5)
    assert st2["status"] == "done", st2.get("error")
    assert "Procurando a webcam" in st2["stages"]
    cam = st2["result"]["webcam"]
    print("webcam do projeto:", cam["rect"])
    assert cam["detected"] and cam["rect"] and cam["frame_url"].startswith(f"/api/projects/{pid2}/camframe.jpg")
    assert urllib.request.urlopen(BASE + cam["frame_url"]).read()[:2] == b"\xff\xd8"

    mine = {"x": 0.72, "y": 0.02, "w": 0.25, "h": 0.25}
    saved = call(f"/api/projects/{pid2}/webcam", {"rect": mine})
    assert saved["rect"] == mine and saved["user_set"] is True
    assert call(f"/api/projects/{pid2}")["result"]["webcam"]["rect"] == mine, "a área tem que ficar salva"
    assert call(f"/api/projects/{pid2}/webcam", {"rect": {"x": 5, "y": 0, "w": 0.2, "h": 0.2}}).get("_status") == 400
    redo = call(f"/api/projects/{pid2}/webcam/detect", {})
    assert redo["detected"] and redo["rect"] and redo["user_set"] is False

    eid = call(f"/api/projects/{pid2}/export",
               {"clips": [1], "variant": "edit", "quality": "720", "style": "impacto", "pos": "mid",
                "reframe": "cam", "cam": mine, "camh": 0.4})["id"]
    for _ in range(300):
        ex = call(f"/api/projects/{pid2}/export/{eid}")
        if ex["done"]:
            break
        time.sleep(0.5)
    assert ex["done"] and ex["items"][0]["error"] is None, ex
    f = Path(tempfile.mkdtemp()) / "cam.mp4"
    f.write_bytes(urllib.request.urlopen(BASE + f"/api/projects/{pid2}/files/{ex['items'][0]['name']}").read())
    ci = media.probe(f)
    print("exportado com webcam:", ci["width"], "x", ci["height"])
    assert (ci["width"], ci["height"]) == (720, 1280)

    # não deixa sair da pasta do projeto
    assert call(f"/api/projects/{pid}/files/..%2F..%2Fresult.json", raw=False).get("_status") == 404
    print("OK: servidor funcionando de ponta a ponta.")


if __name__ == "__main__":
    main()
