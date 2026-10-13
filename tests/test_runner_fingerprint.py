import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import yourself

ROOT = Path(__file__).resolve().parents[1]


def _fake_imds_opener(payload, *, exc=None):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            return json.dumps(payload).encode("utf-8")

    class Opener:
        def open(self, request, timeout=1):
            if exc:
                raise exc
            return Response()

    return Opener()


def test_schema_and_env_soft_missing(monkeypatch):
    monkeypatch.delenv("RUNNER_OS", raising=False)
    fp = yourself.runner_fingerprint(environ={}, root="/nope", etc_root="/nope/etc", sys_root="/nope/sys")
    assert fp["schema"] == "runner-fingerprint/1"
    assert fp["github_actions_env"]["RUNNER_OS"] == {"ok": False, "reason": "unset"}
    assert fp["imds"] is None


def test_imagedata_garbage_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    bad = img_dir / "imagedata.json"
    bad.write_text("{not json", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is False
    assert fp["imagedata"]["reason"] == "invalid_json"


def test_imagedata_success(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    (img_dir / "imagedata.json").write_text('{"image":"ubuntu-24.04"}', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is True
    assert fp["imagedata"]["value"]["object"]["image"] == "ubuntu-24.04"


def test_imagedata_github_array(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    payload = [{"group": "Image", "detail": "ubuntu-24.04"}, {"group": "OS", "detail": "linux"}]
    (img_dir / "imagedata.json").write_text(json.dumps(payload), encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is True
    assert fp["imagedata"]["value"]["kind"] == "array"
    assert fp["imagedata"]["value"]["entries"][0]["group"] == "Image"


def test_imagedata_deep_nesting_fails_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    (img_dir / "imagedata.json").write_text("[" * 80000, encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path),
                                     etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["ok"] is False
    assert fp["imagedata"]["reason"] in ("recursion_depth", "invalid_json")


def test_imagedata_read_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    target = img_dir / "imagedata.json"
    target.write_text("{}", encoding="utf-8")
    real_read = Path.read_bytes

    def fail_bytes(self):
        if self == target:
            raise PermissionError
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fail_bytes)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["reason"] == "PermissionError"


def test_imagedata_unsupported_type(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    img_dir = tmp_path / "imagegeneration"
    img_dir.mkdir()
    (img_dir / "imagedata.json").write_text('"scalar"', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["reason"] == "unsupported_type"


def test_os_release_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\n', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(etc),
                                     sys_root=str(tmp_path))
    assert fp["os_identity"]["os_release"]["value"]["ID"] == "ubuntu"


def test_os_release_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path))
    assert fp["os_identity"]["os_release"]["reason"] == "not_found"


def test_os_release_skips_comment_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text('#ID=poison\nVERSION_ID="24.04"\n', encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(etc),
                                     sys_root=str(tmp_path))
    parsed = fp["os_identity"]["os_release"]["value"]
    assert parsed["VERSION_ID"] == "24.04"
    assert not any(key.startswith("#") for key in parsed)


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


def test_cloud_guess_waagent_only(tmp_path):
    wa = tmp_path / "var" / "lib" / "waagent"
    wa.mkdir(parents=True)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path))
    assert fp["cloud"]["guess"] == "azure"
    assert fp["cloud"]["evidence"] == ["path:/var/lib/waagent"]


def test_dmi_empty_values_not_evidence(tmp_path):
    dmi = tmp_path / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_text("", encoding="utf-8")
    (dmi / "product_name").write_text("", encoding="utf-8")
    (dmi / "chassis_asset_tag").write_text("", encoding="utf-8")
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path))
    assert fp["cloud"]["evidence"] == []


def test_waagent_exists_oserror(tmp_path, monkeypatch):
    target = tmp_path / "var" / "lib" / "waagent"

    def boom(self):
        raise OSError("denied")

    monkeypatch.setattr(type(target), "exists", boom)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path))
    assert fp["cloud"]["waagent"]["reason"] == "OSError"


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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/usr/bin/dpkg-query" if name == "dpkg-query" else None)
    payload = b"pkg-a\t1.0\n   \npkg-b\t2.0\n"

    def run(argv, **kwargs):
        assert argv[0] == "/usr/bin/dpkg-query"
        return SimpleNamespace(returncode=0, stdout=payload, stderr=b"")

    monkeypatch.setattr(yourself.subprocess, "run", run)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path),
                                     sys_root=str(tmp_path), dpkg_list_path="packages.txt")
    assert fp["dpkg"]["ok"] is True
    assert fp["dpkg"]["value"]["count"] == 2
    assert fp["dpkg"]["value"]["list_path"]["value"] == "packages.txt"
    assert (tmp_path / "packages.txt").read_text(encoding="utf-8") == payload.decode("utf-8")


def test_dpkg_oserror(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/dpkg-query")

    def boom(*args, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(yourself.subprocess, "run", boom)
    assert yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")["dpkg"]["reason"] == "OSError"


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
    def forbidden(*args, **kwargs):
        pytest.fail("urllib used without opt-in")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["imds"] is None


def test_imds_opt_in_allowlist(monkeypatch):
    payload = {"compute": {"osType": "Linux", "vmSize": "Standard_D2s_v3", "location": "eastus",
                            "subscriptionId": "SECRET-SUB", "resourceGroupName": "rg",
                            "vmId": "vm-id", "name": "hostname", "tags": "sensitive"}}
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *args, **kwargs: _fake_imds_opener(payload))
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    assert fp["imds"]["ok"] is True
    blob = json.dumps(fp["imds"])
    assert "SECRET" not in blob
    assert "hostname" not in blob
    assert fp["imds"]["value"]["compute"]["osType"] == "Linux"


@pytest.mark.parametrize("exc,reason", [
    (TimeoutError(), "timeout"),
    (OSError("down"), "OSError"),
])
def test_imds_errors(monkeypatch, exc, reason):
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *args, **kwargs: _fake_imds_opener({}, exc=exc))
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    assert fp["imds"]["reason"] == reason


def test_imds_url_error(monkeypatch):
    import urllib.error
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *args, **kwargs: _fake_imds_opener({}, exc=urllib.error.URLError("nope")))
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x", imds=True)
    assert fp["imds"]["reason"] == "url_error"


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
          "imds": {"ok": True, "value": {"compute": {"osType": "Linux", "location": "eastus"}}}}
    text = yourself.runner_fingerprint_summary(fp)
    assert "RUNNER_OS: Linux" in text
    assert "cloud_guess: azure" in text
    assert "imds: ok (osType=Linux, location=eastus)" in text


def test_summary_os_release_id_fallback():
    fp = {"schema": "runner-fingerprint/1",
          "github_actions_env": {k: {"ok": False, "reason": "unset"} for k in
                                 ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion")},
          "os_identity": {"platform": "Linux",
                          "os_release": {"ok": True, "value": {"ID": "ubuntu", "VERSION_ID": "24.04"}}},
          "cloud": {"guess": None, "evidence": []},
          "dpkg": {"ok": False, "reason": "not_available"},
          "tool_cache_contents": [],
          "imds": None}
    assert "os: ubuntu 24.04" in yourself.runner_fingerprint_summary(fp)


@pytest.mark.parametrize("argv", [
    [],
    ["--minimal"],
    ["--format", "json"],
    ["--no-probes", "--no-network"],
])
def test_cli_default_unchanged_without_runner_env(argv):
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), *argv],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode in (0, 2)
    assert "runner-fingerprint" not in proc.stdout
    assert "runner-fingerprint/1" not in proc.stdout


def test_default_text_keys_probe_delta_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(yourself.shutil, "which", lambda name: None)
    monkeypatch.setattr(yourself, "probe_all",
                        lambda **kwargs: {"synthetic.probe": {"ok": True, "lines": ["ok"], "truncated": False}})

    def keys(argv):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            yourself.main(argv)
        return {line.split(":", 1)[0] for line in buf.getvalue().splitlines() if ":" in line}

    full = keys([])
    no_probes = keys(["--no-probes"])
    assert "local_time" in full
    assert full - no_probes <= {"synthetic.probe"}


def test_cli_runner_env_json():
    proc = subprocess.run([sys.executable, str(ROOT / "yourself.py"), "--runner-env", "--runner-env-json"],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["schema"] == "runner-fingerprint/1"


def test_runner_env_flags_require_runner_env(capsys):
    assert yourself.main(["--runner-env-json"]) == 2
    assert "require --runner-env" in capsys.readouterr().err


def test_cli_runner_env_imds_mocked(monkeypatch, capsys):
    monkeypatch.setattr(yourself, "_imds_facts",
                        lambda **kwargs: {"ok": True, "value": {"compute": {"osType": "Linux"}}})
    assert yourself.main(["--runner-env", "--runner-env-json", "--runner-env-imds"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["imds"]["ok"] is True


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


def test_unknown_platform_os_identity(monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "FreeBSD")
    fp = yourself.runner_fingerprint(environ={}, root="/x", etc_root="/x", sys_root="/x")
    assert fp["os_identity"]["detail"]["reason"] == "unsupported_platform"


def test_windows_imagedata_path(monkeypatch, tmp_path):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Windows")
    target = tmp_path / "imagedata.json"
    target.write_text('{"image":"windows-2022"}', encoding="utf-8")

    def paths(root="/"):
        return [target]

    monkeypatch.setattr(yourself, "_imagedata_paths", paths)
    fp = yourself.runner_fingerprint(environ={}, root=str(tmp_path), etc_root=str(tmp_path), sys_root=str(tmp_path))
    assert fp["imagedata"]["value"]["object"]["image"] == "windows-2022"


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


def test_summary_without_cloud_guess():
    fp = {"schema": "runner-fingerprint/1",
          "github_actions_env": {k: {"ok": False, "reason": "unset"} for k in
                                 ("RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion")},
          "os_identity": {"platform": "Linux", "os_release": {"ok": False, "reason": "not_found"}},
          "cloud": {"guess": None, "evidence": []},
          "dpkg": {"ok": False, "reason": "not_available"},
          "tool_cache_contents": [],
          "imds": {"ok": False, "reason": "url_error"}}
    text = yourself.runner_fingerprint_summary(fp)
    assert "cloud_guess: (none)" in text
    assert "imds: (url_error)" in text


def test_github_summary_write_failure_soft(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing" / "summary.md"))
    assert yourself.main(["--runner-env", "--runner-env-github-summary"]) == 0
    assert "GITHUB_STEP_SUMMARY" in capsys.readouterr().err


def test_mutation_script_zero_survivors():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "runner_fingerprint_mutations.py")],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "survivors: 0" in proc.stdout
