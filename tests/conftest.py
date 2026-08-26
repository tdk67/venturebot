"""Test isolation  -- MUST run before any src import.

Without these env vars the test suite reads/writes the PRODUCTION state file
(data/state.json), database and archives: tests have polluted live debate
feeds before ("Run cancelled: test kill" appearing for real users) and even
cancelled real runs via the shared run manager.
"""
import os
import tempfile

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
