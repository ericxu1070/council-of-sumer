import asyncio

import pytest

from sumer import bench
from sumer.transport import slim


@pytest.mark.parametrize("failure", [None, RuntimeError("failed"), asyncio.CancelledError()])
async def test_benchmark_always_closes_transport(monkeypatch, failure):
    closed = []
    class Council:
        async def connect(self):
            pass
        async def ask(self, brief):
            raise AssertionError("No live calls")
        async def close(self):
            closed.append(True)
    async def benchmark(*args):
        if failure is not None:
            raise failure
        return {"done": True}
    async def never(*args, **kwargs):
        raise RuntimeError("unwrapped benchmark")
    monkeypatch.setattr(slim, "SlimCouncil", Council)
    monkeypatch.setattr(bench, "_run_benchmark", benchmark, raising=False)
    monkeypatch.setattr(bench, "play_council", never)
    if failure is None:
        assert await bench.run(1, "slim") == {"done": True}
    else:
        with pytest.raises(type(failure)):
            await bench.run(1, "slim")
    assert closed == [True]


async def test_connect_failure_also_closes(monkeypatch):
    closed = []
    class Council:
        async def connect(self):
            raise RuntimeError("connect failed")
        async def close(self):
            closed.append(True)
    monkeypatch.setattr(slim, "SlimCouncil", Council)
    with pytest.raises(RuntimeError, match="connect failed"):
        await bench.run(1, "slim")
    assert closed == [True]
