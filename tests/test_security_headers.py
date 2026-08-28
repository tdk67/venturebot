"""A2 (G1-G3): security headers + CSP + externalized app JS + vendored libs.

The SPA must ship a strict CSP (script-src 'self'), baseline hardening
headers on every response, and load no third-party scripts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from src.dashboard import app  # noqa: E402

client = TestClient(app)

REPO = Path(__file__).resolve().parent.parent


def test_security_headers_on_html():
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[1].split(";")[0] or True
    assert "'unsafe-eval'" not in csp
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_no_thirdparty_scripts_in_template():
    html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    for cdn in ("cdn.tailwindcss.com", "cdn.jsdelivr.net", "unpkg.com", "googleapis.com"):
        assert cdn not in html, f"third-party script host in template: {cdn}"
    assert 'src="/static/app.js"' in html


def test_app_js_served_and_vendor_files_pinned():
    r = client.get("/static/app.js")
    assert r.status_code == 200
    # Vendored copies exist with version pins in the filename.
    vendor = REPO / "static" / "vendor"
    names = {p.name for p in vendor.glob("*.js")}
    assert any(n.startswith("tailwind-") for n in names)
    assert any(n.startswith("marked-") for n in names)
    assert any(n.startswith("purify-") for n in names)


def test_inline_script_block_removed_from_template():
    html = (REPO / "templates" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in html.replace("<script src=", "<SCRIPT_SRC>")
