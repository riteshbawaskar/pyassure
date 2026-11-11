"""Start the application under test on demand (the equivalent of Playwright's `webServer` option).

    web_server:
      command: "{python} app/demo_app/server.py"   # {python} = the interpreter running PyAssure
      url: http://127.0.0.1:8765/api/health        # polled until it answers
      startup_timeout_s: 30

`ensure()` REUSES an already-running server (nothing is started or stopped), otherwise starts the command
and waits for the url. Plain `behave` starts/stops it in the hooks; the parallel runner starts it ONCE
before the workers, which then find it running and leave it alone.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

from pyassure.config import BASE_DIR, ConfigError, section


def is_up(url: str) -> bool:
    """True if the url answers with ANY HTTP status (401/404 still means the server is alive)."""
    try:
        urllib.request.urlopen(url, timeout=1)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def ensure() -> Optional[subprocess.Popen]:
    """Start the configured server if needed. Returns the Popen we started, or None (nothing to stop)."""
    cfg = section("web_server")
    if not (cfg["command"] and cfg["url"]) or is_up(cfg["url"]):
        return None
    proc = subprocess.Popen(shlex.split(cfg["command"].replace("{python}", sys.executable)), cwd=BASE_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + cfg["startup_timeout_s"]
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ConfigError(f"web_server command exited early (code {proc.returncode})")
        if is_up(cfg["url"]):
            return proc
        time.sleep(0.2)
    proc.terminate()
    raise ConfigError(f"web_server did not answer at {cfg['url']} within {cfg['startup_timeout_s']}s")


def stop(proc: Optional[subprocess.Popen]) -> None:
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
