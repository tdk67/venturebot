"""Test isolation  -- MUST run before any src import.

Without these env vars the test suite reads/writes the PRODUCTION state file
(data/state.json), database and archives: tests have polluted live debate
feeds before ("Run cancelled: test kill" appearing for real users) and even
cancelled real runs via the shared run manager.
"""
import os
import tempfile

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--regenerate-openapi",
        action="store_true",
        default=False,
        help="rewrite tests/openapi_snapshot.json from the current app schema",
    )


@pytest.fixture
def regenerate_openapi(request):
    return request.config.getoption("--regenerate-openapi")

_TMP = tempfile.mkdtemp(prefix="venturebot-tests-")

_ISOLATION = {
    "VENTUREBOT_STATE": os.path.join(_TMP, "state.json"),
    "VENTUREBOT_DATA": _TMP,
    "VENTUREBOT_DB": os.path.join(_TMP, "test.db"),
    "VENTUREBOT_CHECKPOINT_DIR": os.path.join(_TMP, "checkpoints"),
    "VENTUREBOT_ARCHIVE_DIR": os.path.join(_TMP, "archives"),
    "VENTUREBOT_WORKSPACE": os.path.join(_TMP, "workspace"),
    "VENTUREBOT_SANDBOX": os.path.join(_TMP, "sandbox"),
    # Tests must not inherit the operator's auth mode from the live .env:
    # API tests assume open access; test_auth_flow patches NO_AUTH itself.
    "VENTUREBOT_NO_AUTH": "1",
}

for _key, _val in _ISOLATION.items():
    os.environ[_key] = _val


@pytest.fixture(autouse=True)
def _regenerate_openapi_on_request(regenerate_openapi):
    """Regenerate the committed OpenAPI snapshot when explicitly requested."""
    if regenerate_openapi:
        import json
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.dashboard import app

        snap = json.dumps(app.openapi(), sort_keys=True, indent=2)
        (Path(__file__).resolve().parent / "openapi_snapshot.json").write_text(snap + "\n")
