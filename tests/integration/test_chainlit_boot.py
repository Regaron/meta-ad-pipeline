import os
import select
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration_live

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_chainlit_boot(live_settings):
    port = _pick_free_port()
    process = subprocess.Popen(
        ["uv", "run", "chainlit", "run", "app.py", "--headless", "--port", str(port)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
        bufsize=1,
    )

    lines: list[str] = []
    ready = False

    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if process.stdout is None:
                break

            remaining = deadline - time.time()
            if remaining <= 0:
                break
            ready_fds, _, _ = select.select([process.stdout], [], [], min(0.2, remaining))
            if not ready_fds:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if line:
                lines.append(line.rstrip())
                if "Traceback" in line or "ModuleNotFoundError" in line:
                    break
                if str(port) in line and (
                    "http://127.0.0.1" in line
                    or "http://localhost" in line
                    or "Uvicorn running on" in line
                ):
                    ready = True
                    break
            elif process.poll() is not None:
                break
            else:
                time.sleep(0.1)

        output = "\n".join(lines)
        assert process.poll() is None, output
        assert ready, output or "Chainlit never reported a listening URL."
        assert "Traceback" not in output
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
