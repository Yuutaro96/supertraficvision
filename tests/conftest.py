import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
import database


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Base de datos SQLite temporal y aislada para cada test."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return config.DB_PATH
