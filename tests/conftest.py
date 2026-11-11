import time
from types import SimpleNamespace

import pytest
import langgraph.pregel._retry as retry


@pytest.fixture(autouse=True)
def retry_delays(monkeypatch):
    """只替换重试调度器的等待，不影响业务计时和其他模块的 sleep。"""
    delays = []
    monkeypatch.setattr(retry, "time", SimpleNamespace(**{**vars(time), "sleep": delays.append}))
    return delays
