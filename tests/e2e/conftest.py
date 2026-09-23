"""Browser end-to-end fixtures.

Boots a private server on its own port against a freshly seeded throwaway DB,
then drives the user's installed Google Chrome. Headed (visible on screen) by
default; E2E_HEADLESS=1 for CI.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = int(os.getenv("E2E_PORT", "8799"))
BASE = f"http://127.0.0.1:{PORT}"
HEADLESS = os.getenv("E2E_HEADLESS") == "1"

PHONE = {"width": 375, "height": 812}
TABLET = {"width": 768, "height": 1024}
DESKTOP = {"width": 1366, "height": 860}


@pytest.fixture(scope="session")
def server():
    tmp = tempfile.mkdtemp(prefix="dealradar-e2e-")
    env = {
        **os.environ,
        "DB_PATH": os.path.join(tmp, "e2e.db"),
        "SECRET_KEY": "e2e-secret-key",
        # Nothing in a test run may reach Telegram, stores or Render.
        "TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": "",
        "GOOGLE_SHEET_ID": "", "PUBLIC_URL": "",
        "LIVENESS_CHECK": "false", "KEEPALIVE_ENABLED": "false",
        "POLL_INTERVAL_SECONDS": "86400",
        "PRIORITY_AUDIENCE": "off",
    }
    seeded = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "e2e", "seed.py")],
                            env=env, cwd=ROOT, capture_output=True, text=True, check=True)
    cookie = next(line.split(" ", 1)[1] for line in seeded.stdout.splitlines() if line.startswith("COOKIE "))

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        env=env, cwd=ROOT,
    )
    deadline = time.time() + 30
    while True:
        try:
            urllib.request.urlopen(f"{BASE}/api/ping", timeout=1)
            break
        except OSError:
            if time.time() > deadline or proc.poll() is not None:
                proc.kill()
                raise RuntimeError("e2e server did not start")
            time.sleep(0.3)
    yield {"base": BASE, "cookie": cookie}
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=HEADLESS, slow_mo=0 if HEADLESS else 120)
        yield b
        b.close()


@pytest.fixture
def open_page(browser, server):
    """open_page(viewport, signed_in=True, color_scheme='light', path='/') -> (page, console_errors)."""
    contexts = []

    def _open(viewport=DESKTOP, signed_in=True, color_scheme="light", path="/"):
        ctx = browser.new_context(viewport=viewport, color_scheme=color_scheme,
                                  service_workers="block", base_url=server["base"])
        if signed_in:
            ctx.add_cookies([{"name": "tgdeals_session", "value": server["cookie"],
                              "url": server["base"]}])
        contexts.append(ctx)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(path)
        page.wait_for_selector("#boot", state="detached")
        return page, errors

    yield _open
    for ctx in contexts:
        ctx.close()
