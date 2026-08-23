"""State persistence for Cloud Run (see notes/GCP_DEPLOYMENT.md).

Cloud Run instances are ephemeral, but VentureBot currently keeps runtime
state on local disk (data/venturebot.db, state.json, archives, checkpoints).
Until Phase B (backend amnesia) removes persistent storage entirely, this
script bridges the gap:

  restore : download the latest snapshot from gs://$GCS_DATA_BUCKET into ./data
            (run once at container boot, before uvicorn starts)
  push    : upload a fresh snapshot (tar.gz of the data dir) to GCS
  watch   : loop `push` every GCS_SYNC_SECONDS (default 300), run in background

Snapshots exclude sessions.db* (ephemeral auth sessions; a stolen snapshot
must not contain even hashed session tokens).

Auth: Application Default Credentials (Cloud Run service account). Needs the
roles/storage.objectAdmin on the bucket. No-op if google-cloud-storage is
not installed or GCS_DATA_BUCKET is unset.
"""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time

BUCKET = os.environ.get("GCS_DATA_BUCKET", "").strip()
OBJECT = "venturebot-data-snapshot.tar.gz"
SYNC_SECONDS = int(os.environ.get("GCS_SYNC_SECONDS", "300"))
DATA_DIR = os.environ.get("VENTUREBOT_DATA", "data")
EXCLUDE_PREFIXES = ("sessions.db",)  # ephemeral + sensitive


def _client():
    if not BUCKET:
        print("[snapshot] GCS_DATA_BUCKET not set — skipping")
        return None
    try:
        from google.cloud import storage
    except ImportError:
        print("[snapshot] google-cloud-storage not installed — skipping")
        return None
    return storage.Client().bucket(BUCKET)


def _make_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if not os.path.isdir(DATA_DIR):
            return b""
        for name in sorted(os.listdir(DATA_DIR)):
            if name.startswith(EXCLUDE_PREFIXES):
                continue
            full = os.path.join(DATA_DIR, name)
            tar.add(full, arcname=os.path.join("data", name))
    return buf.getvalue()


def restore() -> int:
    client = _client()
    if client is None:
        return 0
    blob = client.blob(OBJECT)
    if not blob.exists():
        print("[snapshot] no existing snapshot in bucket — starting clean")
        return 0
    raw = blob.download_as_bytes()
    os.makedirs(DATA_DIR, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        # Safety: only extract regular files under data/
        for member in tar.getmembers():
            if member.name.startswith("/") or ".." in member.name:
                continue
            tar.extract(member, filter="data")
    print(f"[snapshot] restored {len(raw)} bytes from gs://{BUCKET}/{OBJECT}")
    return 0


def push() -> int:
    client = _client()
    if client is None:
        return 0
    raw = _make_tarball()
    if not raw:
        print("[snapshot] nothing to push")
        return 0
    client.blob(OBJECT).upload_from_string(raw, content_type="application/gzip")
    print(f"[snapshot] pushed {len(raw)} bytes to gs://{BUCKET}/{OBJECT}")
    return 0


def watch() -> int:
    print(f"[snapshot] watch loop every {SYNC_SECONDS}s")
    while True:
        try:
            push()
        except Exception as e:  # keep looping; transient GCS errors happen
            print(f"[snapshot] push failed: {e}")
        time.sleep(SYNC_SECONDS)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    rc = {"restore": restore, "push": push, "watch": watch}.get(cmd)
    if rc is None:
        print(__doc__)
        sys.exit(2)
    sys.exit(rc())
