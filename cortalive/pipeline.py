"""Orquestra tudo: download (se houver), análise, prévias e exportação. Roda em threads."""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import analyzer, config, downloader, media, renderer, transcribe, webcam

S_DOWNLOAD = "Baixando do YouTube"
S_WEBCAM = "Procurando a webcam"
WEIGHTS = {S_DOWNLOAD: 30, analyzer.S_AUDIO: 12, analyzer.S_TENSION: 3, analyzer.S_ASR: 38,
           analyzer.S_PICK: 1, S_WEBCAM: 3, analyzer.S_PREVIEW: 13}

PROJECTS: dict = {}   # id -> estado vivo (para a tela acompanhar)
EXPORTS: dict = {}    # (id, export_id) -> estado
_lock = threading.Lock()


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def slug(text: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:48] or "corte"


class Tracker:
    """Converte (etapa, fração) em um progresso geral de 0 a 100 para a tela."""

    def __init__(self, state: dict, stages: list):
        self.s, self.stages = state, stages
        self.total = sum(WEIGHTS[x] for x in stages)

    def __call__(self, stage: str, frac: float, msg: str = "") -> None:
        idx = self.stages.index(stage)
        done = sum(WEIGHTS[x] for x in self.stages[:idx])
        pct = (done + WEIGHTS[stage] * max(0.0, min(1.0, frac))) / self.total * 100
        self.s.update(stage=idx, progress=round(pct, 1), message=msg)


def public(pid: str, result: dict) -> dict:
    """Resultado como a tela espera, com os endereços de prévia e miniatura."""
    pdir = config.projects_dir() / pid
    out = dict(result)
    out["clips"] = []
    for c in result["clips"]:
        c = dict(c)
        if (pdir / "previews" / f"{c['id']}.mp4").exists():
            c["preview"] = f"/api/projects/{pid}/preview/{c['id']}.mp4"
        if (pdir / "previews" / f"{c['id']}.jpg").exists():
            c["thumb"] = f"/api/projects/{pid}/thumb/{c['id']}.jpg"
        out["clips"].append(c)
    out.pop("source", None)
    cam = result.get("webcam")
    if cam:
        cam = dict(cam)
        f = pdir / webcam.FRAME_NAME
        if f.exists():
            cam["frame_url"] = f"/api/projects/{pid}/camframe.jpg?v={int(f.stat().st_mtime)}"
        out["webcam"] = cam
    return out


def save_result(pid: str, result: dict) -> None:
    (config.projects_dir() / pid / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def set_webcam_rect(pid: str, rect) -> dict:
    """Guarda a área da webcam escolhida pelo usuário (ou None para tirar)."""
    res = load_result(pid)
    if res is None:
        raise media.MediaError("Projeto não encontrado.")
    cam = res.setdefault("webcam", {"available": webcam.available(), "frame": webcam.FRAME_NAME})
    cam["rect"] = rect
    cam["user_set"] = True
    save_result(pid, res)
    return public(pid, res)["webcam"]


def redetect_webcam(pid: str) -> dict:
    """Procura a webcam de novo, com outros quadros da live."""
    res = load_result(pid)
    if res is None:
        raise media.MediaError("Projeto não encontrado.")
    src = Path(res["source"])
    if not src.exists():
        raise media.MediaError("O arquivo original da live não está mais no lugar de antes.")
    found = webcam.detect(src, res["duration"], config.projects_dir() / pid, n=16, jitter=0.03)
    old = res.get("webcam") or {}
    found["user_set"] = False
    if not found["rect"] and old.get("rect"):
        found["rect"] = old["rect"]  # não perde a área que já existia se a busca não achou nada
    res["webcam"] = found
    save_result(pid, res)
    return public(pid, res)["webcam"]


def load_result(pid: str):
    f = config.projects_dir() / pid / "result.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def list_projects() -> list:
    rows = []
    for d in config.projects_dir().iterdir():
        r = d / "result.json"
        if r.exists():
            try:
                j = json.loads(r.read_text(encoding="utf-8"))
            except ValueError:
                continue
            rows.append({"id": d.name, "name": j.get("name", d.name), "created": j.get("created", 0),
                         "clips": len(j.get("clips", [])), "duration": j.get("duration", 0)})
    return sorted(rows, key=lambda x: -x["created"])


def start_project(source: dict, opts: analyzer.Options) -> str:
    pid = new_id()
    stages = ([S_DOWNLOAD] if source["type"] == "youtube" else []) + analyzer.STAGES[:-1] + [S_WEBCAM] \
        + analyzer.STAGES[-1:]
    PROJECTS[pid] = {"id": pid, "status": "running", "stages": stages, "stage": 0, "progress": 0.0,
                     "message": "", "error": None, "result": None}
    threading.Thread(target=_run, args=(pid, source, opts), daemon=True).start()
    return pid


def _run(pid: str, source: dict, opts: analyzer.Options) -> None:
    state = PROJECTS[pid]
    track = Tracker(state, state["stages"])
    pdir = config.projects_dir() / pid
    try:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "previews").mkdir(exist_ok=True)
        if source["type"] == "youtube":
            track(S_DOWNLOAD, 0, "Conectando ao YouTube")
            path, name = downloader.download(source["url"], source.get("quality", "1080"), pdir,
                                             lambda f, m: track(S_DOWNLOAD, f, m))
        else:
            path = Path(source["path"])
            name = path.stem
        if not path.exists():
            raise media.MediaError("Não encontrei o arquivo de vídeo.")

        result = analyzer.analyze(path, pdir, opts, transcribe.get_transcriber(opts.model), track)

        track(S_WEBCAM, 0, "Procurando a webcam do streamer")
        try:
            result["webcam"] = webcam.detect(path, result["duration"], pdir)
        except Exception as e:  # sem webcam o app segue normal; a área pode ser marcada na mão
            result["webcam"] = {"available": webcam.available(), "detected": False, "rect": None,
                                "frame": None, "error": str(e)[:200]}
        track(S_WEBCAM, 1, "")

        track(analyzer.S_PREVIEW, 0, "")
        clips = result["clips"]
        counter = {"n": 0}

        def one(c: dict) -> None:
            renderer.make_preview(str(path), c["start"], c["dur"], pdir / "previews" / f"{c['id']}.mp4",
                                  pdir / "previews" / f"{c['id']}.jpg", c["peakT"] - c["start"])
            with _lock:
                counter["n"] += 1
                track(analyzer.S_PREVIEW, counter["n"] / len(clips), f"Prévia {counter['n']} de {len(clips)}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(one, clips))

        result.update(id=pid, name=name, source=str(path), created=time.time(),
                      options={"length": list(opts.length), "count": opts.count, "language": opts.language})
        (pdir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        (pdir / "audio.wav").unlink(missing_ok=True)  # libera espaço: o áudio só serve durante a análise
        state.update(status="done", progress=100.0, result=public(pid, result))
    except media.MediaError as e:
        state.update(status="error", error=str(e))
    except Exception as e:
        state.update(status="error", error=f"Erro inesperado: {e}")


# ---------- exportação ----------

def start_export(pid: str, req: dict) -> str:
    result = load_result(pid)
    if result is None:
        raise media.MediaError("Projeto não encontrado.")
    wanted = {int(i) for i in req.get("clips", [])}
    clips = [c for c in result["clips"] if c["id"] in wanted]
    variant = req.get("variant", "edit")
    quality = str(req.get("quality", "1080"))
    jobs = []
    for c in clips:
        base = f"corte-{c['id']:02d}-{slug(c['title'])}"
        if variant in ("edit", "both"):
            jobs.append({"clip": c, "kind": "edit",
                         "name": f"{base}-9x16-{'4k' if quality == '2160' else quality + 'p'}.mp4"})
        if variant in ("raw", "both"):
            jobs.append({"clip": c, "kind": "raw", "name": f"{base}-cru.mp4"})
    eid = uuid.uuid4().hex[:8]
    state = {"id": eid, "done": False, "error": None,
             "items": [{"name": j["name"], "p": 0.0, "done": False, "error": None} for j in jobs]}
    EXPORTS[(pid, eid)] = state
    threading.Thread(target=_export, args=(pid, result, jobs, state, req), daemon=True).start()
    return eid


def _export(pid: str, result: dict, jobs: list, state: dict, req: dict) -> None:
    out_dir = config.projects_dir() / pid / "exports"
    out_dir.mkdir(exist_ok=True)
    src = result["source"]
    for job, item in zip(jobs, state["items"]):
        c = job["clip"]
        try:
            def prog(f, item=item):
                item["p"] = round(f * 100, 1)
            out = out_dir / job["name"]
            if job["kind"] == "raw":
                renderer.render_raw(src, c["start"], c["dur"], out, prog)
            else:
                renderer.render_edit(src, c["start"], c["dur"], c["words"], out,
                                     style=req.get("style", "impacto"), pos=req.get("pos", "bot"),
                                     quality=str(req.get("quality", "1080")), reframe=req.get("reframe", "fill"),
                                     cropx=req.get("cropx", 0.5), cam=req.get("cam"),
                                     camh=req.get("camh", 0.38), on_progress=prog)
            item.update(p=100.0, done=True)
        except Exception as e:
            item.update(done=True, error=str(e)[:300])
    state["done"] = True
