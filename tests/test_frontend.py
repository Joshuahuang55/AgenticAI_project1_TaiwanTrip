"""Include dependency-free Node frontend checks in the usual pytest gate."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_frontend_state_transitions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend checks; CI installs it.")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "--test", "tests/frontend_state.test.cjs"], cwd=root,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
