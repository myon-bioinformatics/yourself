"""Default probes: fixed argv, true/false per probe, bounded output; /etc/hosts."""
import subprocess

import pytest
import yourself


class Done:
    def __init__(self, code, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = code, out, err


@pytest.fixture
def fake_run(monkeypatch):
    calls = []
    outputs = {
        "context": Done(0, b"default\n"),
        "version": Done(1, b"", b"failed to connect to the docker API at unix:///var/run/docker.sock\nmore\n"),
        "ls": Done(0, b"bridge bridge local\nhost host local\n"),
        "nvidia-smi": None,
    }
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["shell"] is False and kwargs["timeout"] == 3
        key = argv[0] if argv[0] == "nvidia-smi" else argv[1] if argv[1] in ("context", "version") else argv[2]
        if key == "nvidia-smi":
            raise subprocess.TimeoutExpired(argv, 3)
        return outputs.get(key, Done(0, b"\n".join(b"img%d:latest 1MB" % i for i in range(25))))
    monkeypatch.setattr(yourself.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(yourself.subprocess, "run", run)
    return calls


def test_each_probe_is_true_or_false_with_reason(fake_run):
    out = yourself.probe_all()
    assert out["docker.context"] == {"ok": True, "lines": ["default"], "truncated": False}
    assert out["docker.server"]["ok"] is False and out["docker.server"]["error"].startswith("failed to connect")
    assert out["docker.networks"]["lines"] == ["bridge bridge local", "host host local"]
    assert out["docker.images"]["truncated"] and len(out["docker.images"]["lines"]) == 20
    assert out["gpu"] == {"ok": False, "lines": [], "error": "timeout"}
    assert all(isinstance(argv, list) for argv, _ in fake_run)
    assert any("{{.Name}} {{.Driver}} {{.Scope}}" in argv for argv, _ in fake_run)


def test_missing_command_is_not_installed_without_running(monkeypatch):
    monkeypatch.setattr(yourself.shutil, "which", lambda name: None)
    monkeypatch.setattr(yourself.subprocess, "run", lambda *a, **k: pytest.fail("ran a missing tool"))
    assert yourself.probe(("nvidia-smi", "-L")) == {"ok": False, "lines": [], "error": "not_installed"}


def test_unknown_probe_name():
    with pytest.raises(ValueError):
        yourself.probe_all(["rm"])


def test_text_lines_for_probes(fake_run):
    facts = yourself.collect()
    facts["probes"] = yourself.probe_all()
    text = yourself.to_text(facts)
    assert "docker.context: true default\n" in text
    assert "docker.server: false (failed to connect" in text
    assert "gpu: false (timeout)\n" in text
    assert "; ... (20)" in text  # images capped in the line


def test_hosts_shows_every_localhost_mapping(tmp_path):
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n::1 localhost ip6-localhost # v6\n"
                                    "192.168.65.254 host.docker.internal\n")
    out = yourself.hosts_facts(etc_root=tmp_path)
    assert out["names"]["localhost"] == ["127.0.0.1", "::1"]
    assert out["names"]["host.docker.internal"] == ["192.168.65.254"]
    facts = yourself.collect()
    facts["hosts"] = out
    text = yourself.to_text(facts)
    assert "hosts.localhost: 127.0.0.1, ::1\n" in text and "hosts.host.docker.internal: 192.168.65.254" in text
    assert "gateway.docker.internal" not in text
    assert yourself.hosts_facts(etc_root=tmp_path / "none")["measured"] is False
