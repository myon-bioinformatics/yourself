"""Deterministic package metadata for CLI tests, including copied-file children."""
import sys

import pytest
import yourself


@pytest.fixture
def fake_python_packages(monkeypatch):
    original = yourself.python_packages
    def fake():
        return original(distributions=[])
    monkeypatch.setattr(yourself, "python_packages", fake)


@pytest.fixture
def fake_package_cli():
    # -S still exercises the copied single-file __main__ entry point. Replace
    # only package metadata inputs before executing it; never inventory
    # the test runner's actual installed packages.
    script = '''import runpy, sys
from importlib import metadata
metadata.distributions = lambda: ()
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    return lambda target, *args: [sys.executable, "-S", "-c", script, str(target), *args]
