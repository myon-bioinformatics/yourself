import json
from pathlib import Path
import subprocess
import sys

import pytest
import yourself


def test_package_fixture_is_synthetic(monkeypatch, fake_python_packages):
    from importlib import metadata
    def forbidden(*args, **kwargs):
        raise AssertionError("real package discovery attempted")
    monkeypatch.setattr(metadata, "distributions", forbidden)
    report = yourself.python_packages()
    assert report["count"] == 0 and report["installers"] == {} and report["complete"]


def test_package_fixture_child_is_synthetic(tmp_path, fake_package_cli):
    target = tmp_path / "synthetic child.py"
    target.write_text('''import sys
from importlib import metadata
assert list(metadata.distributions()) == []
assert __name__ == "__main__"
assert sys.argv[1:] == ["--fixture"]
print("synthetic")
''', encoding="utf-8")
    proc = subprocess.run(fake_package_cli(target, "--fixture"), cwd=tmp_path,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "synthetic\n"
    assert not (tmp_path / "__pycache__").exists()


def test_default_readonly(monkeypatch, tmp_path, capsys, fake_python_packages):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(yourself.subprocess, "run", lambda *a, **k: pytest.fail("executed tool"))
    (tmp_path / "pyproject.toml").write_text("SECRET_TOKEN = 'never-read'")
    assert yourself.main(["--format", "json", "--no-probes"]) == 0  # probes are the only executions
    report = json.loads(capsys.readouterr().out)
    assert report["host"] is None
    # The CLI now lists local listeners (proc read, not an execution).
    assert report["listeners"] is None or "bindings" in report["listeners"]
    assert report["workspace"]["markers"] == ["pyproject.toml"]
    assert {row["name"] for row in report["commands"]} >= {"gh", "uv", "playwright", "actionlint"}
    assert "never-read" not in json.dumps(report)


def test_minimal(capsys):
    assert yourself.main(["--minimal"]) == 0
    assert "Tools" not in capsys.readouterr().out


def test_bad_directory(capsys, tmp_path):
    assert yourself.main(["--directory", str(tmp_path / "absent")]) == 2
    assert capsys.readouterr().err == "FileNotFoundError\n"


@pytest.mark.parametrize("status", ["timeout", "execution_error", "nonzero_exit", "unrecognized_version"])
def test_partial(monkeypatch, capsys, status, fake_python_packages):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": []},
        "commands": [{"status": status, "name": "git", "available": True, "version": None}],
    })
    assert yourself.main(["--versions"]) == 2


def test_workspace_error(monkeypatch, capsys, fake_python_packages):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": ["permission"]}, "commands": [],
    })
    assert yourself.main([]) == 2


def test_copy_one_file_execution(tmp_path, fake_package_cli):
    target = tmp_path / "tool.py"
    target.write_bytes((Path(__file__).parents[1] / "yourself.py").read_bytes())
    proc = subprocess.run(fake_package_cli(target, "--format", "json"),
                          cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["workspace"] is not None


def test_copy_one_file_minimal_entry(tmp_path):
    target = tmp_path / "standalone.py"
    target.write_bytes((Path(__file__).parents[1] / "yourself.py").read_bytes())
    proc = subprocess.run([sys.executable, "-S", str(target), "--minimal", "--format", "json"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["os"]["system"] and report["runtime"]["version"]
    assert "python_packages" not in report and report["directory"] is None
    assert list(tmp_path.iterdir()) == [target]  # no siblings or __pycache__ required

