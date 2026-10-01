import json
from pathlib import Path
import subprocess
import sys

import pytest
import yourself


def test_default_readonly(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(yourself.subprocess, "run", lambda *a, **k: pytest.fail("executed tool"))
    (tmp_path / "pyproject.toml").write_text("SECRET_TOKEN = 'never-read'")
    assert yourself.main(["--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["host"] is None
    assert report["listeners"] is None
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
def test_partial(monkeypatch, capsys, status):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": []},
        "commands": [{"status": status, "name": "git", "available": True, "version": None}],
    })
    assert yourself.main(["--versions"]) == 2


def test_workspace_error(monkeypatch, capsys):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": ["permission"]}, "commands": [],
    })
    assert yourself.main([]) == 2


def test_copy_one_file_execution(tmp_path):
    target = tmp_path / "tool.py"
    target.write_bytes((Path(__file__).parents[1] / "yourself.py").read_bytes())
    proc = subprocess.run([sys.executable, "-S", str(target), "--format", "json"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["workspace"] is not None
