"""Pinned native OpenTelemetry Collector for local AGNTCY Observe output."""
from __future__ import annotations

import hashlib
import io
import os
import platform
import subprocess
import tarfile

import httpx

from sumer.config import CACHE_DIR, ROOT

VERSION = "0.162.0"
BINARY = CACHE_DIR / "bin" / "otelcol-contrib"


def install() -> None:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if not system or not arch:
        raise RuntimeError("Install otelcol-contrib manually and set SUMER_COLLECTOR_BINARY.")
    name = f"otelcol-contrib_{VERSION}_{system}_{arch}.tar.gz"
    base = f"https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v{VERSION}"
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        checksum = client.get(f"{base}/{name}.sha256")
        checksum.raise_for_status()
        archive = client.get(f"{base}/{name}")
        archive.raise_for_status()
    if hashlib.sha256(archive.content).hexdigest() != checksum.text.split()[0]:
        raise RuntimeError("Collector checksum mismatch.")
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz") as tar:
        member = next(m for m in tar.getmembers() if m.name.rsplit("/", 1)[-1] == "otelcol-contrib")
        data = tar.extractfile(member)
        if data is None:
            raise RuntimeError("Collector binary is missing from the archive.")
        pending = BINARY.with_suffix(".pending")
        pending.write_bytes(data.read())
        pending.chmod(0o755)
        pending.replace(BINARY)


def command() -> list[str]:
    binary = os.getenv("SUMER_COLLECTOR_BINARY", str(BINARY))
    if not os.path.isfile(binary):
        install()
    return [binary, "--config", str(ROOT / "observability" / "collector.yaml")]


def run() -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    subprocess.run(command(), cwd=ROOT, check=True)
