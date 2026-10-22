from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bililife.storage import Storage
from bililife.task_executor import TaskExecutor


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


@pytest.fixture
def engine(tmp_path):
    return TaskExecutor(SimpleNamespace(), tmp_path / "data", tmp_path / "runtime")
