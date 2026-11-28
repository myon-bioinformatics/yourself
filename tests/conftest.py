"""Deterministic package metadata for CLI tests, including copied-file children."""
import importlib.util
import sys
from pathlib import Path
import pytest
import yourself


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_cli = load_vendored("yourself_cli_args", "vendor/cli_args.py").run_cli


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
    return lambda target, *args, cwd, timeout: run_cli(
        target, *args, python_options=("-S", "-c", script), cwd=cwd, timeout=timeout)
