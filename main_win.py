#!/usr/bin/env python3
"""Windows entry point — used by PyInstaller to build the .exe."""
import sys, os, socket, threading, time
from pathlib import Path

# Resolve bundle root
# PyInstaller 6.x onedir: data files go to _internal/ (sys._MEIPASS)
# PyInstaller 5.x onedir: data files go next to the .exe
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys._MEIPASS)   # _internal/ — where static/ and ffmpeg_bin/ live
else:
    BASE_DIR = Path(__file__).parent

# Tell app.py where to find static/ and ffmpeg_bin/
os.environ['VOLEAN_BASE_DIR'] = str(BASE_DIR)
# Make relative paths like StaticFiles(directory="static") work
os.chdir(str(BASE_DIR))

PORT = 8001
URL  = f"http://127.0.0.1:{PORT}"


def server_up():
    try:
        s = socket.create_connection(("127.0.0.1", PORT), 0.5)
        s.close()
        return True
    except Exception:
        return False


def start_server():
    import uvicorn
    from app import app as fastapi_app

    t = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": fastapi_app,
            "host": "127.0.0.1",
            "port": PORT,
            "log_level": "error",
        },
        daemon=True,
    )
    t.start()
    for _ in range(150):
        time.sleep(0.4)
        if server_up():
            return True
    return False


if not server_up():
    if not start_server():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Сервер не запустился.\nПереустанови VOLEAN Downloader.",
                "VOLEAN Downloader",
                0x10,
            )
        except Exception:
            pass
        sys.exit(1)

import webview
webview.create_window(
    "VOLEAN Downloader", URL,
    width=980, height=720,
    resizable=True, min_size=(600, 400),
)
webview.start()
