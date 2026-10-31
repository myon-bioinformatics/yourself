"""Deterministic package metadata for CLI tests, including copied-file children."""
import sys

import pytest
import yourself


@pytest.fixture
def fake_python_packages(monkeypatch):
    original = yourself.python_packages
    def fake(*, listing=False):
        return original(listing=listing, distributions=[], find_spec=lambda name: None)
    monkeypatch.setattr(yourself, "python_packages", fake)


@pytest.fixture
def fake_package_cli():
    # -S still exercises the copied single-file __main__ entry point. Replace
    # only package metadata/finder inputs before executing it; never inventory
    # the test runner's actual installed packages.
    script = '''import runpy, sys
from importlib import metadata, util
metadata.distributions = lambda: ()
original_find_spec = util.find_spec
util.find_spec = lambda name, *a, **kw: None if name == "uno" else original_find_spec(name, *a, **kw)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    return lambda target, *args: [sys.executable, "-S", "-c", script, str(target), *args]
