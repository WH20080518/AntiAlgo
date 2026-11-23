"""Zero-console Windows launcher for Anti-Algorithm Personal Hub."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

ROOT_DIR = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8765
HUB_URL = f"http://{HOST}:{PORT}"
STATUS_URL = f"{HUB_URL}/api/status"
SERVICE_NAME = "anti-algo-hub"


def is_server_ready(timeout: float = 1.0) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        req = Request(STATUS_URL, method="GET")
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            return isinstance(data, dict) and data.get("service") == SERVICE_NAME
    except (URLError, OSError, json.JSONDecodeError, ValueError):
        return False


def start_background_server() -> None:
    venv_pythonw = ROOT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    executable = venv_pythonw if venv_pythonw.exists() else Path(sys.executable)

    creation_flags = 0
    for flag_name in (
        "CREATE_NO_WINDOW",
        "DETACHED_PROCESS",
        "CREATE_NEW_PROCESS_GROUP",
    ):
        creation_flags |= getattr(subprocess, flag_name, 0)

    subprocess.Popen(
        [
            str(executable),
            "-m",
            "pipeline.server",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )


def main() -> None:
    if not is_server_ready():
        start_background_server()
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if is_server_ready(timeout=0.5):
                break
            time.sleep(0.25)

    webbrowser.open(HUB_URL)


if __name__ == "__main__":
    main()
