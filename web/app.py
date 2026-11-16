"""NetGrab Web — téléchargeur de vidéos et de MP3 par lien (yt-dlp + FFmpeg).

Lancement local :  uvicorn app:app --host 0.0.0.0 --port 8000
Réglages par variables d'environnement : voir README.
"""
import hashlib
import hmac
import ipaddress
import mimetypes
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from yt_dlp.utils import DownloadCancelled, match_filter_func

# ---------------------------------------------------------------- réglages
PASSWORD = os.environ.get("NETGRAB_PASSWORD", "")            # vide = accès libre
MAX_JOBS = int(os.environ.get("NETGRAB_MAX_JOBS", "2"))      # téléchargements simultanés
TTL_MIN = int(os.environ.get("NETGRAB_TTL_MIN", "30"))       # durée de conservation des fichiers
MAX_DURATION_MIN = int(os.environ.get("NETGRAB_MAX_DURATION_MIN", "0"))   # 0 = pas de limite
RATE_LIMIT = int(os.environ.get("NETGRAB_RATE_LIMIT", "12"))  # téléchargements par adresse IP / 10 min
ALLOW_PRIVATE = os.environ.get("NETGRAB_ALLOW_PRIVATE", "") == "1"   # tests locaux uniquement
DATA_DIR = Path(os.environ.get("NETGRAB_DATA_DIR", Path(tempfile.gettempdir()) / "netgrab"))

STATIC_DIR = Path(__file__).parent / "static"
QUALITIES = {"best": None, "1080": 1080, "720": 720, "480": 480, "mp3": None}
AUTH_COOKIE = "netgrab_auth"
AUTH_TOKEN = hmac.new(b"netgrab", PASSWORD.encode(), hashlib.sha256).hexdigest() if PASSWORD else ""

shutil.rmtree(DATA_DIR, ignore_errors=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="NetGrab Web", docs_url=None, redoc_url=None, openapi_url=None)

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_JOBS)
recent: dict[str, deque] = defaultdict(deque)


class ApiError(HTTPException):
    def __init__(self, status: int, message: str):
        super().__init__(status_code=status, detail=message)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ---------------------------------------------------------------- sécurité
def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def require_auth(request: Request) -> None:
    if not PASSWORD:
        return
    token = request.cookies.get(AUTH_COOKIE, "")
    if not hmac.compare_digest(token, AUTH_TOKEN):
        raise ApiError(401, "Mot de passe requis.")


def check_url(url: str) -> str:
    """N'accepte que des liens web publics : le serveur ne doit jamais aller lire le réseau interne."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or len(url) > 2000:
        raise ApiError(400, "Colle un lien valide commençant par http:// ou https://")
    if ALLOW_PRIVATE:
        return url
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ApiError(400, "Adresse introuvable. Vérifie le lien.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise ApiError(400, "Ce lien n'est pas autorisé.")
    return url


def friendly(err: Exception) -> str:
    msg = str(err).replace("ERROR: ", "").strip()
    low = msg.lower()
    if "unsupported url" in low:
        return "Ce site n'est pas pris en charge."
    if "sign in" in low or "login required" in low or "not a bot" in low:
        return "Le site demande une connexion ou bloque ce serveur pour cette vidéo."
    if "private" in low:
        return "Cette vidéo est privée."
    if "unavailable" in low or "removed" in low or "404" in low:
        return "Vidéo introuvable ou supprimée."
    if "timed out" in low or "unable to download webpage" in low or "connection" in low:
        return "Le site ne répond pas. Réessaie dans un instant."
    return (msg[:300] or "Erreur inconnue.")


def base_opts() -> dict:
    return {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "updatetime": False,
        "windowsfilenames": True,
        "retries": 3,
        "socket_timeout": 20,
        "cachedir": False,
    }


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def config(request: Request):
    ok = True
    if PASSWORD:
        ok = hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), AUTH_TOKEN)
    return {
        "auth_required": bool(PASSWORD),
        "authenticated": ok,
        "ttl_min": TTL_MIN,
        "max_duration_min": MAX_DURATION_MIN,
        "engine": yt_dlp.version.__version__,
    }


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    if not PASSWORD:
        return {"ok": True}
    time.sleep(0.6)   # ralentit les essais en série
    if not hmac.compare_digest(body.password.encode(), PASSWORD.encode()):
        raise ApiError(401, "Mot de passe incorrect.")
    res = JSONResponse({"ok": True})
    res.set_cookie(AUTH_COOKIE, AUTH_TOKEN, max_age=30 * 24 * 3600, httponly=True, samesite="lax",
                   secure=request.url.scheme == "https")
    return res


# ---------------------------------------------------------------- analyse du lien
class UrlBody(BaseModel):
    url: str


@app.post("/api/info")
def info(body: UrlBody, request: Request):
    require_auth(request)
    url = check_url(body.url)
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 — yt-dlp lève des erreurs très variées
        raise ApiError(422, friendly(e))
    if data.get("entries"):
        entries = [e for e in data["entries"] if e]
        if not entries:
            raise ApiError(422, "Aucune vidéo trouvée à cette adresse.")
        data = entries[0]
    heights = sorted({f["height"] for f in data.get("formats") or []
                      if f.get("height") and f.get("vcodec") not in (None, "none")}, reverse=True)
    return {
        "title": data.get("title") or "Vidéo",
        "uploader": data.get("uploader") or data.get("channel") or "",
        "duration": data.get("duration"),
        "thumbnail": data.get("thumbnail") or "",
        "site": data.get("extractor_key") or "",
        "max_height": heights[0] if heights else None,
    }


# ---------------------------------------------------------------- téléchargements
class JobBody(BaseModel):
    url: str
    quality: str = "best"


def public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "status", "progress", "eta", "speed", "title", "filename",
                                "size", "error", "quality")}


def run_job(job: dict) -> None:
    jobdir = DATA_DIR / job["id"]
    jobdir.mkdir(parents=True, exist_ok=True)
    audio = job["quality"] == "mp3"

    def on_progress(d: dict) -> None:
        if job["cancel"]:
            raise DownloadCancelled("annulé")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = (d.get("downloaded_bytes", 0) / total * 100) if total else 0
            parts = len((d.get("info_dict") or {}).get("requested_formats") or []) or 1
            job["progress"] = round(min(99.0, (job["parts_done"] * 100 + pct) / parts), 1)
            job["eta"] = d.get("eta")
            job["speed"] = d.get("speed")
            job["title"] = (d.get("info_dict") or {}).get("title") or job["title"]
        elif d.get("status") == "finished":
            job["parts_done"] += 1

    def on_post(d: dict) -> None:
        if job["cancel"]:
            raise DownloadCancelled("annulé")
        if d.get("status") == "started":
            job["status"] = "finishing"
            job["progress"] = 99.0

    opts = base_opts()
    opts.update({
        "outtmpl": str(jobdir / "%(title).80B [%(id)s].%(ext)s"),
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_post],
    })
    if MAX_DURATION_MIN > 0:
        opts["match_filter"] = match_filter_func(f"duration <=? {MAX_DURATION_MIN * 60}")
    if audio:
        opts["format"] = "ba/b"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                   "preferredquality": "0"}]
    else:
        # La résolution d'abord ; à résolution égale, H.264 + AAC pour une lecture partout.
        cap = QUALITIES[job["quality"]]
        opts["format"] = "bv*+ba/b"
        opts["format_sort"] = ([f"res:{cap}"] if cap else ["res", "fps"]) + ["vcodec:h264", "acodec:m4a"]
        opts["merge_output_format"] = "mp4"

    with slots:
        if job["cancel"]:
            job["status"] = "cancelled"
            shutil.rmtree(jobdir, ignore_errors=True)
            return
        job["status"] = "running"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(job["url"], download=True)
            files = [f for f in jobdir.iterdir()
                     if f.is_file() and f.suffix not in (".part", ".ytdl", ".tmp")]
            if not files:
                if MAX_DURATION_MIN > 0:
                    raise RuntimeError(f"Vidéo trop longue (limite : {MAX_DURATION_MIN} minutes).")
                raise RuntimeError("Aucun fichier produit.")
            out = max(files, key=lambda f: f.stat().st_size)
            job.update(path=str(out), filename=out.name, size=out.stat().st_size,
                       progress=100.0, status="done", eta=None, speed=None)
        except DownloadCancelled:
            job["status"] = "cancelled"
            shutil.rmtree(jobdir, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            if job["cancel"]:
                job["status"] = "cancelled"
            else:
                job["status"] = "error"
                job["error"] = friendly(e)
            shutil.rmtree(jobdir, ignore_errors=True)


@app.post("/api/jobs")
def create_job(body: JobBody, request: Request):
    require_auth(request)
    if body.quality not in QUALITIES:
        raise ApiError(400, "Qualité inconnue.")
    url = check_url(body.url)

    ip = client_ip(request)
    now = time.time()
    hits = recent[ip]
    while hits and now - hits[0] > 600:
        hits.popleft()
    if len(hits) >= RATE_LIMIT:
        raise ApiError(429, "Trop de téléchargements d'un coup. Réessaie dans quelques minutes.")
    hits.append(now)

    job = {
        "id": uuid.uuid4().hex, "url": url, "quality": body.quality, "status": "queued",
        "progress": 0.0, "eta": None, "speed": None, "title": "", "filename": "", "size": 0,
        "error": "", "path": "", "cancel": False, "parts_done": 0, "created": now,
    }
    with jobs_lock:
        jobs[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return public(job)


def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise ApiError(404, "Téléchargement introuvable ou expiré.")
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    require_auth(request)
    return public(get_job(job_id))


@app.delete("/api/jobs/{job_id}")
def job_cancel(job_id: str, request: Request):
    require_auth(request)
    job = get_job(job_id)
    job["cancel"] = True
    if job["status"] == "done":
        shutil.rmtree(DATA_DIR / job["id"], ignore_errors=True)
        job["status"] = "cancelled"
    return public(job)


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str, request: Request):
    require_auth(request)
    job = get_job(job_id)
    path = Path(job["path"]) if job["path"] else None
    if job["status"] != "done" or not path or not path.is_file():
        raise ApiError(404, "Fichier indisponible ou expiré.")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, filename=job["filename"], media_type=media)


# ---------------------------------------------------------------- ménage
def cleaner() -> None:
    while True:
        time.sleep(60)
        limit = time.time() - TTL_MIN * 60
        with jobs_lock:
            old = [j for j in jobs.values() if j["created"] < limit and j["status"] not in ("queued", "running", "finishing")]
            for j in old:
                jobs.pop(j["id"], None)
        for j in old:
            shutil.rmtree(DATA_DIR / j["id"], ignore_errors=True)


threading.Thread(target=cleaner, daemon=True).start()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
