"""Command line entry point: `uv run sumer <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time

from sumer.advisors import ADVISOR_IDS
from sumer.config import settings
from sumer.protocol import provision_identities


def _up(args) -> None:
    """Start the SLIM node, one process per advisor (seven), and the web chamber."""
    py = [sys.executable, "-m", "sumer.cli"]
    env = {**os.environ, "SLIM_SHARED_SECRET": settings.slim_secret}
    provision_identities()
    procs = []
    if os.getenv("SUMER_OBSERVE", "true").lower() != "false":
        from sumer.collector import command
        from sumer.config import CACHE_DIR, ROOT
        CACHE_DIR.mkdir(exist_ok=True)
        procs.append(subprocess.Popen(command(), cwd=ROOT, env=env))
    procs.append(subprocess.Popen([*py, "node"], env=env))
    time.sleep(2)
    procs += [subprocess.Popen([*py, "agent", a], env=env) for a in ADVISOR_IDS]
    time.sleep(3)
    procs.append(subprocess.Popen([*py, "web"], env=env))
    print(f"\nCouncil of Sumer: open http://localhost:{settings.web_port}\n", flush=True)

    def stop(*_):
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while all(p.poll() is None for p in procs):
        time.sleep(1)
    stop()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
    p = argparse.ArgumentParser(prog="sumer")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up", help="run node + 7 advisor agents + web UI (the demo)")
    sub.add_parser("node", help="run the SLIM node")
    a = sub.add_parser("agent", help="run one advisor agent")
    a.add_argument("advisor", choices=ADVISOR_IDS)
    sub.add_parser("web", help="run the web chamber")
    c = sub.add_parser("collector", help="run the native local Observe collector")
    c.add_argument("--install", action="store_true", help="download and verify the pinned binary")
    b = sub.add_parser("bench", help="council vs baselines over many seeds")
    b.add_argument("--seeds", type=int, default=30)
    b.add_argument("--transport", default="inproc", choices=["inproc", "slim"])
    args = p.parse_args()

    if args.cmd == "up":
        _up(args)
    elif args.cmd == "node":
        from sumer.transport.slim import run_node

        asyncio.run(run_node())
    elif args.cmd == "agent":
        from sumer.transport.slim import serve_advisor
        provision_identities()
        asyncio.run(serve_advisor(args.advisor))
    elif args.cmd == "collector":
        from sumer import collector
        collector.install() if args.install else collector.run()
    elif args.cmd == "web":
        import uvicorn

        uvicorn.run("sumer.web:app", host="0.0.0.0", port=settings.web_port, log_level="warning")
    elif args.cmd == "bench":
        from sumer.bench import run

        asyncio.run(run(args.seeds, args.transport))


if __name__ == "__main__":
    main()
