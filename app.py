import os
import ssl
import uuid
import socket
import threading
import subprocess
import asyncio
import concurrent.futures
import requests as req_lib
from pathlib import Path

# Patch LibreSSL on macOS — allows TLS connections to modern sites
ssl._create_default_https_context = ssl._create_unverified_context

from fastapi import FastAPI, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DOWNLOADS = Path.home() / "Desktop" / "VOLEAN Downloader"
DOWNLOADS.mkdir(parents=True, exist_ok=True)
jobs: dict = {}

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/downloads", StaticFiles(directory=str(DOWNLOADS)), name="downloads")

def _find_ffmpeg():
    import shutil, platform, urllib.request, zipfile, tarfile

    # 1. bundled with the app (shipped in installer — always present)
    _exe_name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    _base = Path(os.environ.get('VOLEAN_BASE_DIR', str(Path(__file__).parent)))
    bundled = _base / "ffmpeg_bin" / _exe_name
    if bundled.exists():
        return str(bundled), str(bundled.parent)

    # 2. system PATH
    p = shutil.which("ffmpeg")
    if p:
        return p, str(Path(p).parent)

    # 3. common locations
    for candidate in ["/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
                      str(Path.home() / ".local" / "bin" / "ffmpeg")]:
        if Path(candidate).exists():
            return candidate, str(Path(candidate).parent)

    # 3. imageio_ffmpeg bundled
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).exists():
            return p, str(Path(p).parent)
    except Exception:
        pass

    # 4. download static binary
    local = Path(__file__).parent / "ffmpeg_bin"
    local.mkdir(exist_ok=True)
    exe = local / ("ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg")
    if exe.exists():
        return str(exe), str(local)

    print("ffmpeg не найден — скачиваю...", flush=True)
    sys_name = platform.system()
    machine = platform.machine().lower()
    try:
        if sys_name == "Darwin":
            arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
            url = f"https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
            tmp = local / "ffmpeg.zip"
            urllib.request.urlretrieve(url, tmp)
            with zipfile.ZipFile(tmp) as z:
                z.extractall(local)
            tmp.unlink(missing_ok=True)
        elif sys_name == "Windows":
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
            tmp = local / "ffmpeg.zip"
            urllib.request.urlretrieve(url, tmp)
            with zipfile.ZipFile(tmp) as z:
                for m in z.namelist():
                    if m.endswith("/ffmpeg.exe"):
                        data = z.read(m)
                        exe.write_bytes(data)
                        break
            tmp.unlink(missing_ok=True)
        else:
            url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
            tmp = local / "ffmpeg.tar.xz"
            urllib.request.urlretrieve(url, tmp)
            with tarfile.open(tmp) as t:
                for m in t.getmembers():
                    if m.name.endswith("/ffmpeg"):
                        f = t.extractfile(m)
                        exe.write_bytes(f.read())
                        break
            tmp.unlink(missing_ok=True)

        exe.chmod(0o755)
        if exe.exists():
            print(f"ffmpeg скачан: {exe}", flush=True)
            return str(exe), str(local)
    except Exception as e:
        print(f"Не удалось скачать ffmpeg: {e}", flush=True)

    return None, None

_ffmpeg_lock = threading.Lock()
_FFMPEG = None
_FFMPEG_DIR = None

def get_ffmpeg():
    global _FFMPEG, _FFMPEG_DIR
    with _ffmpeg_lock:
        if _FFMPEG is None:
            _FFMPEG, _FFMPEG_DIR = _find_ffmpeg()
        return _FFMPEG, _FFMPEG_DIR

# Lazy alias — resolved on first use
def _get_ffmpeg_path():
    p, _ = get_ffmpeg()
    return p


def is_youtube(url: str) -> bool:
    return any(x in url for x in ["youtube.com/watch", "youtu.be/", "youtube.com/shorts"])


@app.get("/")
async def root():
    return FileResponse("static/index.html")


# ── PROXY ───────────────────────────────────────────────────────────────────

_LOCAL_PORTS = [1080, 1087, 1086, 7890, 7891, 8080, 8118, 10808, 10809, 1088, 9050, 4780, 1090]
_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=5000&country=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
]


def _port_open(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.4)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        return ok
    except Exception:
        return False


_PROXY_TEST_URLS = [
    "https://xhamster.com/",
    "https://www.pornhub.com/",
    "https://www.xvideos.com/",
    "http://httpbin.org/ip",
]

def _proxy_works(proxy: str, timeout: int = 7) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    for test_url in _PROXY_TEST_URLS:
        try:
            r = req_lib.get(
                test_url,
                proxies={"http": proxy, "https": proxy},
                timeout=timeout, verify=False, headers=headers,
            )
            if r.status_code == 200:
                return True
        except Exception:
            continue
    return False


def _scan_local() -> str:
    for port in _LOCAL_PORTS:
        if _port_open(port):
            for scheme in ("socks5", "http"):
                p = f"{scheme}://127.0.0.1:{port}"
                if _proxy_works(p, timeout=6):
                    return p
    return ""


def _fetch_free_proxies() -> list:
    result = []
    for src in _PROXY_SOURCES:
        try:
            r = req_lib.get(src, timeout=12, verify=False)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line and ":" in line and not line.startswith("#"):
                        result.append(f"socks5://{line}")
                if result:
                    break
        except Exception:
            pass
    return result


def _find_working_proxy() -> str:
    # 1. local VPN/proxy ports
    local = _scan_local()
    if local:
        return local
    # 2. free proxy lists
    proxies = _fetch_free_proxies()
    if not proxies:
        return ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(_proxy_works, p, 6): p for p in proxies[:80]}
        for fut in concurrent.futures.as_completed(futures):
            if fut.result():
                return futures[fut]
    return ""


@app.post("/set_proxy")
async def set_proxy(proxy: str = Form(default="")):
    global PROXY
    PROXY = proxy.strip()
    return {"proxy": PROXY, "active": bool(PROXY)}

@app.get("/get_proxy")
async def get_proxy():
    return {"proxy": PROXY, "active": bool(PROXY)}

@app.post("/find_proxy")
async def find_proxy_endpoint():
    global PROXY
    loop = asyncio.get_event_loop()
    found = await loop.run_in_executor(None, _find_working_proxy)
    if found:
        PROXY = found
        src = "local" if "127.0.0.1" in found else "free"
        return {"proxy": PROXY, "active": True, "source": src}
    return {"proxy": "", "active": False, "error": "Рабочий прокси не найден"}


# ── INFO ────────────────────────────────────────────────────────────────────

@app.post("/info")
async def get_info(url: str = Form(...)):
    try:
        if is_youtube(url):
            return info_youtube(url)
        else:
            return info_ytdlp(url)
    except Exception as e:
        return {"error": str(e)}


def info_youtube(url: str) -> dict:
    from pytubefix import YouTube
    yt = YouTube(url)
    resolutions = sorted(
        {int(s.resolution[:-1]) for s in yt.streams.filter(adaptive=True, only_video=True) if s.resolution},
        reverse=True
    )
    return {
        "title": yt.title,
        "thumbnail": yt.thumbnail_url,
        "duration": yt.length,
        "uploader": yt.author,
        "resolutions": resolutions[:6],
    }


PROXY: str = ""  # set via /set_proxy endpoint


def _ytdlp_base(skip=True) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": skip,
        "cookiesfrombrowser": ("chrome",),
        "nocheckcertificate": True,
        "socket_timeout": 30,
    }
    if PROXY:
        opts["proxy"] = PROXY
    return opts


def info_ytdlp(url: str) -> dict:
    import yt_dlp

    def _try(opts):
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    # 1st: normal extractor
    try:
        info = _try(_ytdlp_base())
    except Exception as e1:
        # 2nd: generic extractor (for sites yt-dlp doesn't recognise)
        try:
            info = _try({**_ytdlp_base(), "force_generic_extractor": True})
        except Exception:
            # 3rd: Playwright headless browser intercept
            video_url = playwright_intercept(url)
            if not video_url:
                raise Exception(f"Не удалось получить видео: {e1}")
            title = _page_title(url)
            return {
                "title": title,
                "thumbnail": "",
                "duration": 0,
                "uploader": "",
                "resolutions": [],
                "_direct_url": video_url,
            }

    resolutions = sorted(
        {f["height"] for f in info.get("formats", []) if f.get("height") and f.get("vcodec") != "none"},
        reverse=True
    )
    return {
        "title": info.get("title", "Без названия"),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", ""),
        "resolutions": resolutions[:6],
    }


def playwright_intercept(page_url: str, timeout: int = 25):
    """Launch headless Chrome, intercept video stream URLs."""
    async def _run():
        from playwright.async_api import async_playwright
        import re
        found = []
        skip_kw = ["preview", "thumb", "poster", "/ads/", "advertisement", "banner", "analytics", "tracking"]
        video_ext = [".mp4", ".m3u8", ".webm", ".ts", ".flv"]

        def is_video_url(u):
            if any(k in u.lower() for k in skip_kw):
                return False
            return any(ext in u for ext in video_ext)

        async with async_playwright() as p:
            launch_args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            if PROXY:
                launch_args.append(f"--proxy-server={PROXY}")
            browser = await p.chromium.launch(headless=True, args=launch_args)
            ctx = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
                ignore_https_errors=True,
            )
            page = await ctx.new_page()

            # Intercept requests
            async def on_request(r):
                u = r.url
                if is_video_url(u) and u not in found:
                    found.append(u)

            # Also intercept responses for JSON that might contain video URLs
            async def on_response(r):
                u = r.url
                ct = r.headers.get("content-type", "")
                if "json" in ct or "javascript" in ct:
                    try:
                        body = await r.text()
                        for m in re.finditer(r'https?://[^\s\'"<>]+\.(?:mp4|m3u8|webm)[^\s\'"<>]*', body):
                            vu = m.group(0)
                            if not any(k in vu.lower() for k in skip_kw) and vu not in found:
                                found.append(vu)
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)

            try:
                await page.goto(page_url, timeout=timeout * 1000, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000)
                # Try clicking play button if video hasn't started
                if not found:
                    for sel in ["button[class*='play']", ".play-button", "#player", "video"]:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible():
                                await el.click()
                                await page.wait_for_timeout(3000)
                                break
                        except Exception:
                            pass
            except Exception:
                pass
            await browser.close()
        return found

    import concurrent.futures

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            urls = ex.submit(run_in_thread).result(timeout=timeout + 15)
        m3u8 = [u for u in urls if ".m3u8" in u]
        mp4 = [u for u in urls if ".mp4" in u or ".webm" in u]
        return (m3u8 or mp4 or [None])[0]
    except Exception:
        return None


def _page_title(url: str) -> str:
    try:
        r = req_lib.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        import re
        m = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.IGNORECASE)
        return m.group(1).strip() if m else "Видео"
    except Exception:
        return "Видео"


# ── DOWNLOAD ─────────────────────────────────────────────────────────────────

@app.post("/download")
async def start_download(url: str = Form(...), mode: str = Form(...), quality: str = Form("best")):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "pending", "progress": 0, "msg": "Подготовка..."}
    thread = threading.Thread(target=run_download, args=(job_id, url, mode, quality))
    thread.daemon = True
    thread.start()
    return {"job_id": job_id}


def set_progress(job_id, pct, msg):
    jobs[job_id]["progress"] = pct
    jobs[job_id]["msg"] = msg


def _is_connection_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ["timed out", "connection", "eof", "ssl", "network", "000", "refused"])


def run_download(job_id: str, url: str, mode: str, quality: str):
    global PROXY
    try:
        if is_youtube(url):
            download_youtube(job_id, url, mode, quality)
        else:
            download_ytdlp(job_id, url, mode, quality)
    except Exception as e:
        # Auto-proxy: if connection error and no proxy set, find one and retry
        if _is_connection_error(e) and not PROXY:
            set_progress(job_id, 5, "Сайт недоступен — ищу рабочий прокси...")
            found = _find_working_proxy()
            if found:
                PROXY = found
                set_progress(job_id, 15, f"Прокси найден: {found} — повторяю загрузку...")
                try:
                    if is_youtube(url):
                        download_youtube(job_id, url, mode, quality)
                    else:
                        download_ytdlp(job_id, url, mode, quality)
                    return
                except Exception as e2:
                    e = e2  # fall through to Playwright

        # Final fallback: Playwright interception + direct download
        try:
            set_progress(job_id, 10, "Запуск браузера для поиска видео...")
            direct_url = playwright_intercept(url)
            if not direct_url:
                jobs[job_id] = {"status": "error", "error": str(e)[:400]}
                return
            download_direct(job_id, direct_url, url)
        except Exception as e2:
            jobs[job_id] = {"status": "error", "error": str(e2)[:400]}


# ── YOUTUBE via pytubefix ─────────────────────────────────────────────────────

def download_youtube(job_id: str, url: str, mode: str, quality: str):
    from pytubefix import YouTube

    def on_progress(stream, chunk, remaining):
        total = stream.filesize or 1
        done = total - remaining
        pct = int(done / total * 85)
        speed_kb = len(chunk) // 1024
        jobs[job_id]["progress"] = pct
        jobs[job_id]["msg"] = f"{pct}%  ·  {speed_kb} KB/s"

    set_progress(job_id, 5, "Получение информации...")
    yt = YouTube(url, on_progress_callback=on_progress)
    out_dir = str(DOWNLOADS)

    if mode == "audio":
        # best m4a audio
        stream = (yt.streams.filter(only_audio=True, mime_type="audio/mp4")
                  .order_by("abr").desc().first()
                  or yt.streams.filter(only_audio=True).order_by("abr").desc().first())
        if not stream:
            jobs[job_id] = {"status": "error", "error": "Аудио дорожка не найдена"}
            return
        set_progress(job_id, 10, "Скачивание аудио...")
        fname = stream.download(output_path=out_dir, filename=f"{job_id}.m4a")
        _finish(job_id, Path(fname), yt.title)

    elif mode == "video_only":
        vid_streams = yt.streams.filter(adaptive=True, only_video=True, mime_type="video/mp4")
        if quality != "best":
            vid_streams = vid_streams.filter(res=f"{quality}p")
        stream = vid_streams.order_by("resolution").desc().first()
        if not stream:
            stream = yt.streams.filter(adaptive=True, only_video=True).order_by("resolution").desc().first()
        set_progress(job_id, 10, "Скачивание видео...")
        fname = stream.download(output_path=out_dir, filename=f"{job_id}_video.mp4")
        _finish(job_id, Path(fname), yt.title)

    else:  # video + audio merged
        vid_streams = yt.streams.filter(adaptive=True, only_video=True, mime_type="video/mp4")
        if quality != "best":
            vs = vid_streams.filter(res=f"{quality}p")
            if vs:
                vid_streams = vs
        video_stream = vid_streams.order_by("resolution").desc().first()
        audio_stream = (yt.streams.filter(only_audio=True, mime_type="audio/mp4")
                        .order_by("abr").desc().first())

        if not video_stream or not audio_stream:
            # fallback to progressive
            prog = yt.streams.filter(progressive=True).order_by("resolution").desc().first()
            if not prog:
                jobs[job_id] = {"status": "error", "error": "Нет доступных форматов"}
                return
            set_progress(job_id, 10, "Скачивание (прогрессивный поток)...")
            fname = prog.download(output_path=out_dir, filename=f"{job_id}.mp4")
            _finish(job_id, Path(fname), yt.title)
            return

        set_progress(job_id, 10, "Скачивание видео...")
        v_path = Path(out_dir) / f"{job_id}_v.mp4"
        video_stream.download(output_path=out_dir, filename=f"{job_id}_v.mp4")

        set_progress(job_id, 55, "Скачивание аудио...")
        a_path = Path(out_dir) / f"{job_id}_a.m4a"
        audio_stream.download(output_path=out_dir, filename=f"{job_id}_a.m4a")

        set_progress(job_id, 85, "Склейка видео и аудио...")
        out_path = Path(out_dir) / f"{job_id}.mp4"
        cmd = [get_ffmpeg()[0], "-y",
               "-i", str(v_path), "-i", str(a_path),
               "-c:v", "copy", "-c:a", "aac",
               str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # cleanup temp files
        v_path.unlink(missing_ok=True)
        a_path.unlink(missing_ok=True)

        if result.returncode != 0 or not out_path.exists():
            jobs[job_id] = {"status": "error", "error": f"Ошибка склейки: {result.stderr[-300:]}"}
            return

        _finish(job_id, out_path, yt.title)


# ── OTHER SITES via yt-dlp ────────────────────────────────────────────────────

def download_ytdlp(job_id: str, url: str, mode: str, quality: str):
    import yt_dlp
    out_tmpl = str(DOWNLOADS / f"{job_id}.%(ext)s")

    def hook(d):
        if d["status"] == "downloading":
            try:
                pct = float(d.get("_percent_str", "0%").strip().replace("%", ""))
            except Exception:
                pct = 0
            speed = d.get("_speed_str", "—").strip()
            jobs[job_id]["progress"] = min(int(pct * 0.9), 90)
            jobs[job_id]["msg"] = f"{pct:.0f}%  ·  {speed}"
        elif d["status"] == "finished":
            jobs[job_id]["progress"] = 94
            jobs[job_id]["msg"] = "Обработка..."

    base = {
        **_ytdlp_base(skip=False),
        "outtmpl": out_tmpl,
        "progress_hooks": [hook],
    }
    _, ffmpeg_dir = get_ffmpeg()
    if ffmpeg_dir:
        base["ffmpeg_location"] = ffmpeg_dir

    if mode == "audio":
        base["format"] = "bestaudio[ext=m4a]/bestaudio/best"
    elif mode == "video_only":
        base["format"] = f"bestvideo[height<={quality}]/bestvideo" if quality != "best" else "bestvideo"
    else:
        # Prefer H.264 (avc1) so QuickTime can play without re-encoding
        h264_pref = "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[vcodec^=avc]+bestaudio"
        if quality != "best":
            base["format"] = (
                f"bestvideo[height<={quality}][vcodec^=avc1]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={quality}][vcodec^=avc]+bestaudio"
                f"/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
            )
        else:
            base["format"] = f"{h264_pref}/bestvideo+bestaudio/best"
        base["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        # fallback: generic extractor for unsupported sites (JWPlayer, VideoJS, etc.)
        base["force_generic_extractor"] = True
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(url, download=True)

    out_file = next(
        (f for f in DOWNLOADS.iterdir() if f.stem == job_id and f.stat().st_size > 0),
        None
    )
    if not out_file:
        jobs[job_id] = {"status": "error", "error": "Файл не найден после скачивания"}
        return

    _finish(job_id, out_file, info.get("title", out_file.stem))


def download_direct(job_id: str, video_url: str, page_url: str):
    """Download a direct video URL (mp4/m3u8) intercepted by Playwright."""
    ext = "mp4"
    if ".m3u8" in video_url:
        # Use ffmpeg to download HLS stream
        out_path = DOWNLOADS / f"{job_id}.mp4"
        set_progress(job_id, 30, "Скачивание HLS потока...")
        cmd = [get_ffmpeg()[0], "-y", "-i", video_url, "-c", "copy", str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists():
            jobs[job_id] = {"status": "error", "error": f"ffmpeg error: {result.stderr[-200:]}"}
            return
        _finish(job_id, out_path, _page_title(page_url))
        return

    # Direct mp4/webm download with requests + progress
    out_path = DOWNLOADS / f"{job_id}.{ext}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Referer": page_url,
    }
    set_progress(job_id, 20, "Скачивание видео...")
    with req_lib.get(video_url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded / total * 90)
                    kb = downloaded // 1024
                    jobs[job_id]["progress"] = pct
                    jobs[job_id]["msg"] = f"{pct}%  ·  {kb//1024} MB скачано"

    _finish(job_id, out_path, _page_title(page_url))


def _safe_filename(title: str, ext: str) -> str:
    import re
    name = re.sub(r'[\\/*?:"<>|]', "", title)
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:120] or "video"
    candidate = DOWNLOADS / f"{name}.{ext}"
    if not candidate.exists():
        return f"{name}.{ext}"
    i = 1
    while (DOWNLOADS / f"{name} ({i}).{ext}").exists():
        i += 1
    return f"{name} ({i}).{ext}"


def _video_codec(path: Path) -> str:
    """Return video codec name by parsing ffmpeg -i output."""
    import re
    try:
        result = subprocess.run(
            [get_ffmpeg()[0], "-i", str(path)],
            capture_output=True, text=True, timeout=10
        )
        # ffmpeg prints stream info to stderr: "Video: h264 ..." or "Video: hevc ..."
        m = re.search(r"Video:\s+(\w+)", result.stderr)
        return m.group(1).lower() if m else ""
    except Exception:
        return ""


def _ensure_h264(job_id: str, path: Path) -> Path:
    """Re-encode to H.264 if the video track isn't avc/h264."""
    if path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
        return path
    codec = _video_codec(path)
    if not codec or codec in ("h264", "avc", "avc1"):
        return path
    jobs[job_id]["msg"] = f"Конвертация {codec.upper()} → H.264 для QuickTime..."
    out = path.with_suffix(".h264.mp4")
    cmd = [
        get_ffmpeg()[0], "-y", "-i", str(path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
        path.unlink(missing_ok=True)
        return out
    out.unlink(missing_ok=True)
    return path  # fallback: return original if conversion failed


def _finish(job_id: str, path: Path, title: str, convert: bool = True):
    if not path.exists() or path.stat().st_size == 0:
        jobs[job_id] = {"status": "error", "error": "Файл пустой или не найден"}
        return
    if convert:
        path = _ensure_h264(job_id, path)
    named = _safe_filename(title, path.suffix.lstrip("."))
    named_path = DOWNLOADS / named
    try:
        path.rename(named_path)
        path = named_path
    except Exception:
        pass
    jobs[job_id] = {
        "status": "done", "progress": 100, "msg": "Готово!",
        "filename": path.name,
        "url": f"/downloads/{path.name}",
        "title": title,
    }


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})

@app.get("/downloads_list")
async def downloads_list():
    files = []
    for f in sorted(DOWNLOADS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file() and not f.name.startswith('.'):
            stat = f.stat()
            files.append({
                "filename": f.name,
                "url": f"/downloads/{f.name}",
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    return files

@app.post("/reveal_download/{filename}")
async def reveal_download(filename: str):
    import platform
    path = DOWNLOADS / filename
    if path.exists() and path.parent.resolve() == DOWNLOADS.resolve():
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            subprocess.Popen(["open", "-R", str(path)])
    return {"ok": True}

@app.post("/delete_download/{filename}")
async def delete_download(filename: str):
    path = DOWNLOADS / filename
    if path.exists() and path.parent.resolve() == DOWNLOADS.resolve():
        path.unlink()
        return {"ok": True}
    return {"ok": False}
