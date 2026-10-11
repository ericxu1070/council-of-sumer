"""Hosting ports must work without a checked-out .env file."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("variables", "expected"),
    [({}, 8000), ({"PORT": "10000"}, 10000),
     ({"PORT": "10000", "WEB_PORT": "8010"}, 8010)],
)
def test_hosting_port(variables, expected):
    env = {k: v for k, v in os.environ.items() if k not in {"PORT", "WEB_PORT"}}
    env.update(variables, PYTHON_DOTENV_DISABLED="1")
    result = subprocess.run(
        [sys.executable, "-c", "from sumer.config import settings; print(settings.web_port)"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == str(expected)
