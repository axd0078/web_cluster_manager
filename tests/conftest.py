from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


TEST_ROOT = Path(tempfile.mkdtemp(prefix="wcm-pytest-"))
os.environ["WCM_DATA_DIR"] = str(TEST_ROOT / "data")
os.environ["WCM_UPDATES_DIR"] = str(TEST_ROOT / "updates")
os.environ["WCM_LOGS_DIR"] = str(TEST_ROOT / "logs")
os.environ["WCM_BOOTSTRAP_ADMIN_PASSWORD"] = "test-bootstrap-password"
os.environ["WCM_DEBUG"] = "false"
os.environ["WCM_TRUSTED_HOSTS"] = '["localhost","127.0.0.1","testserver"]'
PROJECT_ROOT = Path(__file__).parents[1]
SERVER_ROOT = PROJECT_ROOT / "server"
AGENT_ROOT = PROJECT_ROOT / "agent"
sys.path.insert(0, str(SERVER_ROOT))
sys.path.insert(1, str(PROJECT_ROOT))
sys.path.append(str(AGENT_ROOT))


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as test_client:
        yield test_client
