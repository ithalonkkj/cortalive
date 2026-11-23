"""Servidor local do app. A tela (static/index.html) conversa com ele por /api."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from starlette.concurrency import run_in_threadpool

from . import analyzer, config, downloader, media, pipeline, renderer, transcribe, webcam

LOCAL = {"127.0.0.1", "::1", "localhost"}


def _err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def _is_local(request: Request) -> bool:
    return bool(request.client) and request.client.host in LOCAL


def _safe_path(request: Request, raw: str):
    """Caminho de arquivo vindo da tela. Quem acessa de outro aparelho só pode usar o que enviou por upload."""
    p = Path(raw).expanduser()
    if not _is_local(request):
        try:
            p.resolve().relative_to(config.uploads_dir().resolve())
        except ValueError:
            return None
    return p


async def csrf_guard(request: Request, call_next):
    # Sites de terceiros não conseguem mandar este cabeçalho sem passar pelo CORS, que não liberamos.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-cortalive") != "1":
        return _err("Requisição não autorizada.", 403)
    return await call_next(request)


# ---------- rotas ----------

async def health(request: Request):
    return JSONResponse({
        "ffmpeg": media.ffmpeg_ok(),
        "whisper": transcribe.whisper_available(),
        "ytdlp": downloader.ytdlp_available(),
        "opencv": webcam.available(),
        "encoder": renderer.pick_encoder() if media.ffmpeg_ok() else None,
        "local": _is_local(request),
    })


async def probe(request: Request):
    body = await request.json()
    p = _safe_path(request, body.get("path", ""))
    if p is None or not p.is_file():
        return _err("Arquivo não encontrado.", 404)
    try:
        info = media.probe(p)
    except media.MediaError as e:
        return _err(str(e))
    return JSONResponse({"name": p.name, "size": info["size"] or p.stat().st_size, "duration": info["duration"],
                         "path": str(p)})


async def pick_file(request: Request):
    if not _is_local(request):
        return _err("Escolher arquivo só funciona no computador onde o app está rodando.", 403)
    code = ("import tkinter as tk\nfrom tkinter import filedialog\nr=tk.Tk();r.withdraw();r.attributes('-topmost',True)\n"
            "print(filedialog.askopenfilename(filetypes=[('Vídeos','*.mp4 *.mkv *.mov *.webm *.ts *.flv *.avi'),('Todos','*.*')]))")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _err("A janela de escolha demorou demais.")
    path = r.stdout.strip()
    if r.returncode != 0:
        return _err("Não consegui abrir a janela de arquivos aqui. Cole o caminho do arquivo no campo ao lado.", 501)
    return JSONResponse({"path": path})


async def upload(request: Request):
    name = re.sub(r"[^\w.\- ]+", "_", request.query_params.get("name", "live.mp4"))[:120] or "live.mp4"
    dest = config.uploads_dir() / f"{uuid.uuid4().hex[:8]}-{name}"
    try:
        with open(dest, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        return _err("O envio foi interrompido.")
    return JSONResponse({"path": str(dest), "name": name})


def _options(raw: dict) -> analyzer.Options:
    lo, hi = (raw.get("length") or "30-60").split("-")
    return analyzer.Options(length=(float(lo), float(hi)), count=max(1, min(30, int(raw.get("count", 8)))),
                            language=str(raw.get("language", "pt")), model=str(raw.get("model", "small")))


async def create_project(request: Request):
    body = await request.json()
    src = body.get("source") or {}
    if not media.ffmpeg_ok():
        return _err("O ffmpeg não foi encontrado. Instale o ffmpeg e abra o app de novo.", 500)
    if src.get("type") == "youtube":
        source = {"type": "youtube", "url": str(src.get("url", "")), "quality": str(src.get("quality", "1080"))}
        if not downloader.YT_RE.match(source["url"].strip()):
            return _err("Esse link não parece ser do YouTube.")
    elif src.get("type") in ("path", "upload"):
        p = _safe_path(request, str(src.get("path", "")))
        if p is None or not p.is_file():
            return _err("Arquivo não encontrado.", 404)
        source = {"type": "path", "path": str(p)}
    else:
        return _err("Fonte inválida.")
    try:
        opts = _options(body.get("options") or {})
    except (ValueError, TypeError):
        return _err("Opções inválidas.")
    return JSONResponse({"id": pipeline.start_project(source, opts)})


async def list_projects(request: Request):
    return JSONResponse(pipeline.list_projects())


async def get_project(request: Request):
    pid = request.path_params["pid"]
    state = pipeline.PROJECTS.get(pid)
    if state is not None:
        if state.get("status") == "done":  # o resultado do disco é o mais novo (a área da webcam pode ter mudado)
            res = pipeline.load_result(pid)
            if res is not None:
                state = {**state, "result": pipeline.public(pid, res)}
        return JSONResponse(state)
    res = pipeline.load_result(pid)
    if res is None:
        return _err("Projeto não encontrado.", 404)
    return JSONResponse({"id": pid, "status": "done", "progress": 100, "result": pipeline.public(pid, res)})


def _project_file(pid: str, sub: str, name: str):
    f = config.projects_dir() / Path(pid).name / sub / Path(name).name
    return f if f.is_file() else None


async def preview(request: Request):
    f = _project_file(request.path_params["pid"], "previews", request.path_params["name"])
    return FileResponse(f) if f else _err("Não encontrado.", 404)


async def export_file(request: Request):
    name = request.path_params["name"]
    f = _project_file(request.path_params["pid"], "exports", name)
    return FileResponse(f, filename=Path(name).name) if f else _err("Não encontrado.", 404)


async def cam_frame(request: Request):
    f = _project_file(request.path_params["pid"], ".", webcam.FRAME_NAME)
    return FileResponse(f, headers={"Cache-Control": "no-cache"}) if f else _err("Não encontrado.", 404)


async def save_webcam(request: Request):
    body = await request.json()
    raw = body.get("rect")
    rect = renderer.clean_cam(raw) if raw else None
    if raw and rect is None:
        return _err("Área da webcam inválida.")
    try:
        return JSONResponse(pipeline.set_webcam_rect(request.path_params["pid"], rect))
    except media.MediaError as e:
        return _err(str(e), 404)


async def detect_webcam(request: Request):
    try:
        return JSONResponse(await run_in_threadpool(pipeline.redetect_webcam, request.path_params["pid"]))
    except media.MediaError as e:
        return _err(str(e))


async def start_export(request: Request):
    pid = request.path_params["pid"]
    body = await request.json()
    if not body.get("clips"):
        return _err("Marque ao menos um corte.")
    body["cam"] = renderer.clean_cam(body.get("cam"))
    try:
        body["camh"] = min(0.5, max(0.2, float(body.get("camh", 0.38))))
    except (TypeError, ValueError):
        body["camh"] = 0.38
    if body.get("reframe") == "cam" and not body["cam"]:
        return _err("Marque a área da webcam antes de gerar no layout com webcam.")
    try:
        eid = pipeline.start_export(pid, body)
    except media.MediaError as e:
        return _err(str(e), 404)
    return JSONResponse({"id": eid})


async def get_export(request: Request):
    st = pipeline.EXPORTS.get((request.path_params["pid"], request.path_params["eid"]))
    return JSONResponse(st) if st else _err("Exportação não encontrada.", 404)


async def open_folder(request: Request):
    if not _is_local(request):
        return _err("Só funciona no computador onde o app está rodando.", 403)
    body = await request.json()
    d = config.projects_dir() / Path(str(body.get("id", ""))).name / "exports"
    d.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(d))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(d)])
    else:
        subprocess.Popen(["xdg-open", str(d)])
    return JSONResponse({"path": str(d)})


def create_app(lan: bool = False) -> Starlette:
    middleware = [Middleware(BaseHTTPMiddleware, dispatch=csrf_guard)]
    if not lan:  # protege contra sites que tentam falar com o app pelo navegador (DNS rebinding)
        middleware.append(Middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]))
    routes = [
        Route("/api/health", health),
        Route("/api/probe", probe, methods=["POST"]),
        Route("/api/pick-file", pick_file, methods=["POST"]),
        Route("/api/upload", upload, methods=["PUT"]),
        Route("/api/open-folder", open_folder, methods=["POST"]),
        Route("/api/projects", list_projects, methods=["GET"]),
        Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/{pid}", get_project),
        Route("/api/projects/{pid}/preview/{name}", preview),
        Route("/api/projects/{pid}/thumb/{name}", preview),
        Route("/api/projects/{pid}/camframe.jpg", cam_frame),
        Route("/api/projects/{pid}/webcam", save_webcam, methods=["POST"]),
        Route("/api/projects/{pid}/webcam/detect", detect_webcam, methods=["POST"]),
        Route("/api/projects/{pid}/export", start_export, methods=["POST"]),
        Route("/api/projects/{pid}/export/{eid}", get_export),
        Route("/api/projects/{pid}/files/{name}", export_file),
        Mount("/", StaticFiles(directory=str(config.static_dir()), html=True), name="static"),
    ]
    return Starlette(routes=routes, middleware=middleware)
