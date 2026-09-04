import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mi import db  # noqa: E402
from mi.config import Config  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    connection = db.init(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def config(tmp_path):
    return Config(db_path=tmp_path / "test.db")
