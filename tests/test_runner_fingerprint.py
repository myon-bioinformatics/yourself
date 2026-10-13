import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import yourself

ROOT = Path(__file__).resolve().parents[1]


def test_schema_and_env_soft_missing(monkeypatch):
    monkeypatch.delenv("RUNNER_OS", raising=False)
    fp = yourself.runner_fingerprint(environ={}, root="/nope", etc_root="/nope/etc", sys_root="/nope/sys")
    assert fp["schema"] == "runner-fingerprint/1"
    assert fp["github_actions_env"]["RUNNER_OS"] == {"ok": False, "reason": "unset"}
    assert fp["imds"] is None


def test_imagedata_garbage_and_missing(tmp_path):
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    bad = img_dir / "imagedata.json"
    bad.write_text("{not json", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is False
    assert fp["imagedata"]["reason"] == "invalid_json"


def test_imagedata_success(tmp_path):
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    (img_dir / "imagedata.json").write_text('{"image":"ubuntu-24.04"}', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is True
    assert fp["imagedata"]["value"]["object"]["image"] == "ubuntu-24.04"


def test_os_release_parsed(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(etc),
                                     sys_root=str(tmp_path))
    assert fp["os_identity"]["os_release"]["value"]["ID"] == "ubuntu"


def test_dmi_cloud_guess_and_waagent(tmp_path):
    dmi = tmp_path / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_text("Microsoft Corporation", encoding="utf-8")
    (dmi / "product_name").write_text("Virtual Machine", encoding="utf-8")
    (dmi / "chassis_asset_tag").write_text("", encoding="utf-8")
    wa = tmp_path / "var" / "lib" / "waagent"
    wa.mkdir(parents=True)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                       sys_root=str(tmp_path))
    assert fp["cloud"]["guess"] == "azure"
    assert any("waagent" in item for item in fp["cloud"]["evidence"])


def test_unreadable_dmi(tmp_path, monkeypatch):
    dmi = tmp_path / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    path = dmi / "sys_vendor"

    def boom(_self, *args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(type(path), "read_text", boom)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path))
    assert fp["cloud"]["dmi"]["sys_vendor"]["reason"] == "PermissionError"


def test_dpkg_count_and_list_file(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/usr/bin/dpkg-query" if name == "dpkg-query" else None)
    payload = b"pkg-a\t1.0\npkg-b\t2.0\n"

    def run(argv, **kwargs):
        assert argv[0] == "/usr/bin/dpkg-query"
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(yourself.subprocess, "run", run)
    out = tmp_path / "packages.txt"
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path), dpkg_list_path=str(out))
    assert fp["dpkg"]["ok"] is True
    assert fp["dpkg"]["value"]["count"] == 2
    assert len(fp["dpkg"]["value"]["sha256"]) == 64
    assert out.read_text(encoding="utf-8") == payload.decode("utf-8")


def test_dpkg_not_available(monkeypatch):
    monkeypatch.setattr(yourself.shutil, "which", lambda name: None)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["dpkg"] == {"ok": False, "reason": "not_available"}


def test_dpkg_non_linux(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/dpkg-query")
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["dpkg"]["reason"] == "unsupported_platform"


def test_tool_cache_inventory(tmp_path):
    cache = tmp_path / "cache"
    (cache / "node" / "20.0.0" / "x64").mkdir(parents=True)
    (cache / "Python" / "3.12.3" / "x64").mkdir(parents=True)
    fp = yourself.runner_fingerprint(environ={"RUNNER_TOOL_CACHE": str(cache)},
                                     root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    tools = fp["tool_cache_contents"][0]["value"]["tools"]
    assert tools["node"] == ["20.0.0"]
    assert tools["Python"] == ["3.12.3"]


def test_imds_not_called_by_default(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")

    def forbidden(argv, **kwargs):
        if argv[0] in ("curl", "wget"):
            pytest.fail("IMDS client invoked without opt-in")
        raise AssertionError("unexpected subprocess")

    monkeypatch.setattr(yourself.subprocess, "run", forbidden)
    monkeypatch.setattr(yourself.shutil, "which", lambda name: None)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["imds"] is None


def test_imds_opt_in(monkeypatch):
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/bin/curl" if name == "curl" else None)

    def run(argv, **kwargs):
        assert argv[0] == "curl"
        assert "169.254.169.254" in argv[-1]
        body = b'{"compute":{"osType":"Linux"}}'
        return SimpleNamespace(returncode=0, stdout=body, stderr=b"")

    monkeypatch.setattr(yourself.subprocess, "run", run)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    assert fp["imds"]["ok"] is True
    assert fp["imds"]["value"]["osType"] == "Linux"


def test_summary_human_readable():
    fp = {"schema": "runner-fingerprint/1",
          "github_actions_env": {"RUNNER_OS": {"ok": True, "value": "Linux"},
                                 "RUNNER_ARCH": {"ok": True, "value": "X64"},
                                 "RUNNER_ENVIRONMENT": {"ok": False, "reason": "unset"},
                                 "ImageOS": {"ok": True, "value": "ubuntu24"},
                                 "ImageVersion": {"ok": True, "value": "20241001.1.0"}},
          "os_identity": {"platform": "Linux",
                          "os_release": {"ok": True, "value": {"PRETTY_NAME": "Ubuntu 24.04 LTS"}}},
          "cloud": {"guess": "azure", "evidence": ["dmi:sys_vendor=Microsoft"]},
          "dpkg": {"ok": True, "value": {"count": 3, "sha256": "a" * 64}},
          "tool_cache_contents": [{"ok": True, "value": {"tools": {"node": ["20"]}}}],
          "imds": None}
    text = yourself.runner_fingerprint_summary(fp)
    assert "RUNNER_OS: Linux" in text
    assert "cloud_guess: azure" in text


def test_cli_default_unchanged_without_runner_env():
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--minimal"],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert "schema" not in proc.stdout
    assert "runner-fingerprint" not in proc.stdout


def test_cli_runner_env_json():
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--runner-env", "--runner-env-json"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["schema"] == "runner-fingerprint/1"


def test_github_step_summary_append(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--runner-env",
                           "--runner-env-github-summary"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "Runner fingerprint" in summary.read_text(encoding="utf-8")


def test_mac_os_identity(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(yourself.platform, "mac_ver", lambda: ("14.0", ("", "", ""), "arm64"))
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["os_identity"]["mac_ver"]["ok"] is True


def test_windows_os_identity(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Windows")
    monkeypatch.setattr(yourself.platform, "win32_ver", lambda: ("11", "10.0.22631", "SP0", "Multiprocessor"))
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["os_identity"]["win32_ver"]["ok"] is True


def test_imagedata_not_object(tmp_path):
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    (img_dir / "imagedata.json").write_text("[1,2]", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["reason"] == "not_object"


def test_cloud_guess_aws_gcp(tmp_path):
    dmi = tmp_path / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_text("Amazon EC2", encoding="utf-8")
    (dmi / "product_name").write_text("t3.micro", encoding="utf-8")
    (dmi / "chassis_asset_tag").write_text("", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["cloud"]["guess"] == "aws"
    (dmi / "sys_vendor").write_text("Google", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["cloud"]["guess"] == "gcp"


def test_agent_toolsdirectory_fallback(tmp_path):
    cache = tmp_path / "tools"
    cache.mkdir()
    fp = yourself.runner_fingerprint(environ={"AGENT_TOOLSDIRECTORY": str(cache)},
                                     root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["tool_cache"]["ok"] is True


def test_dpkg_failures(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/dpkg-query")

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(yourself.subprocess, "run", timeout)
    assert yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")["dpkg"]["reason"] == "timeout"

    def bad_exit(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(yourself.subprocess, "run", bad_exit)
    assert yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")["dpkg"]["reason"] == "nonzero_exit"


def test_dpkg_list_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/dpkg-query")
    monkeypatch.setattr(yourself.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"a\t1\n", stderr=b""))
    target = tmp_path / "missing" / "out.txt"
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path), dpkg_list_path=str(target))
    assert fp["dpkg"]["value"]["list_path"]["reason"] == "FileNotFoundError"


def test_tool_cache_errors(tmp_path, monkeypatch):
    assert yourself._tool_cache_inventory(str(tmp_path / "nope"))["reason"] == "not_a_directory"
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "broken").mkdir()
    real_iter = Path.iterdir

    def flaky_iter(self):
        if self.name == "broken":
            raise PermissionError
        return real_iter(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iter)
    result = yourself._tool_cache_inventory(str(cache))
    assert result["entry_errors"][0]["reason"] == "PermissionError"


@pytest.mark.parametrize("handler,reason", [
    ("timeout", "timeout"), ("oserror", "OSError"), ("badexit", "nonzero_exit"),
    ("badjson", None), ("listjson", None)])
def test_imds_branches(monkeypatch, handler, reason):
    calls = {"n": 0}

    def which(name):
        if name == "curl":
            return "curl"
        return None

    monkeypatch.setattr(yourself.shutil, "which", which)

    def run(argv, **kwargs):
        calls["n"] += 1
        if handler == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        if handler == "oserror":
            raise OSError("nope")
        if handler == "badexit":
            return SimpleNamespace(returncode=7, stdout=b"", stderr=b"fail")
        if handler == "badjson":
            return SimpleNamespace(returncode=0, stdout=b"not-json", stderr=b"")
        if handler == "listjson":
            return SimpleNamespace(returncode=0, stdout=b"[1]", stderr=b"")
        raise AssertionError(handler)

    monkeypatch.setattr(yourself.subprocess, "run", run)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    if reason:
        assert fp["imds"]["reason"] == reason
    else:
        assert fp["imds"]["ok"] is True


def test_imds_no_client(monkeypatch):
    monkeypatch.setattr(yourself.shutil, "which", lambda name: None)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    assert fp["imds"]["reason"] == "no_http_client"


def test_summary_without_cloud_guess():
    fp = {"schema": "runner-fingerprint/1",
          "github_actions_env": {k: {"ok": False, "reason": "unset"} for k in
                                 ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion")},
          "os_identity": {"platform": "Linux", "os_release": {"ok": False, "reason": "not_found"}},
          "cloud": {"guess": None, "evidence": []},
          "dpkg": {"ok": False, "reason": "not_available"},
          "tool_cache_contents": [],
          "imds": {"ok": False, "reason": "no_http_client"}}
    text = yourself.runner_fingerprint_summary(fp)
    assert "cloud_guess: (none)" in text
    assert "imds: (no_http_client)" in text


def test_cli_runner_env_imds_flag():
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--runner-env", "--runner-env-json",
                           "--runner-env-imds"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["imds"] is not None
    assert "ok" in data["imds"]


def test_github_summary_write_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing" / "summary.md"))
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--runner-env",
                           "--runner-env-github-summary"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "FileNotFoundError" in proc.stderr or "OSError" in proc.stderr
