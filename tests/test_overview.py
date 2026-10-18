"""No-argument overview: key: value lines with container and network facts."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yourself

ROOT = Path(__file__).resolve().parents[1]


def _lines(stdout):
    pairs = [line.split(": ", 1) for line in stdout.splitlines()]
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), "every key appears once"
    return dict(pairs)


@pytest.fixture
def fake_roots(tmp_path):
    proc, sysfs, etc = tmp_path / "proc", tmp_path / "sys", tmp_path / "etc"
    (proc / "net").mkdir(parents=True)
    (proc / "1").mkdir()
    (proc / "net" / "fib_trie").write_text(
        "Main:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 172.17.0.5\n        /32 host LOCAL\n"
        "     |-- 172.17.255.255\n        /32 link BROADCAST\n     |-- 127.0.0.1\n        /32 host LOCAL\n"
        "Local:\n     |-- 172.17.0.5\n        /32 host LOCAL\n")
    (proc / "net" / "if_inet6").write_text("fe800000000000000042acfffe110005 02 40 20 80 eth0\n")
    (proc / "net" / "route").write_text("Iface\tDestination\tGateway\tFlags\n"
                                        "eth0\t000011AC\t00000000\t0001\neth0\t00000000\t010011AC\t0003\n")
    (proc / "1" / "cgroup").write_text("0::/system.slice/docker-abc.scope\n")
    (proc / "cpuinfo").write_text("flags\t\t: fpu vme hypervisor\n")
    for name, state in (("eth0", "up"), ("lo", "unknown")):
        (sysfs / "class" / "net" / name).mkdir(parents=True)
        (sysfs / "class" / "net" / name / "operstate").write_text(state + "\n")
    etc.mkdir()
    (etc / "resolv.conf").write_text("# comment\nnameserver 127.0.0.11\nsearch corp.example\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / ".dockerenv").write_text("")
    return {"proc": proc, "sys": sysfs, "etc": etc, "root": root}


def test_network_facts_from_files_only(fake_roots, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@proxy:3128")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    net = yourself.network_facts(proc_root=fake_roots["proc"], sys_root=fake_roots["sys"], etc_root=fake_roots["etc"])
    assert net["ipv4"] == ["172.17.0.5", "127.0.0.1"]
    assert net["ipv6"] == [{"address": "fe80:0000:0000:0000:0042:acff:fe11:0005", "interface": "eth0"}]
    assert net["default_route"] == {"interface": "eth0", "gateway": "172.17.0.1"}
    assert net["dns"] == {"nameservers": ["127.0.0.11"], "search": ["corp.example"]}
    assert net["interfaces"] == [{"name": "eth0", "state": "up"}, {"name": "lo", "state": "unknown"}]
    assert net["proxy_set"]["HTTPS_PROXY"] is True and net["proxy_set"]["HTTP_PROXY"] is False
    assert "secret" not in json.dumps(net) and net["errors"] == []


def test_missing_sources_are_errors_not_empty_facts(tmp_path):
    net = yourself.network_facts(proc_root=tmp_path, sys_root=tmp_path, etc_root=tmp_path)
    assert {e.split(":")[0] for e in net["errors"]} == {"interfaces", "ipv4", "route", "dns"}


def test_container_hints(fake_roots, monkeypatch):
    monkeypatch.delenv("container", raising=False)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    facts = yourself.container_facts(proc_root=fake_roots["proc"], root=fake_roots["root"])
    assert facts == {"in_container": True, "kinds": ["docker", "kubernetes"], "virtualized": True,
                     "wsl": None}
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")
    monkeypatch.setenv("container", "podman")
    empty = fake_roots["root"].parent / "plain"
    empty.mkdir()
    assert yourself.container_facts(proc_root=empty, root=empty) == {
        "in_container": True, "kinds": ["podman"], "virtualized": None, "wsl": None}


def test_wsl_detection_is_not_a_container(tmp_path):
    proc, root = tmp_path / "proc", tmp_path / "root"
    (proc / "sys" / "kernel").mkdir(parents=True)
    root.mkdir()
    (proc / "sys" / "kernel" / "osrelease").write_text("5.15.153.1-microsoft-standard-WSL2\n", encoding="ascii")
    facts = yourself.container_facts(proc_root=proc, root=root)
    assert facts["wsl"] == "wsl2" and facts["in_container"] is False and facts["kinds"] == []
    (proc / "sys" / "kernel" / "osrelease").unlink()
    (proc / "version").write_text("Linux version 4.4.0-19041-Microsoft (Microsoft@Microsoft.com)\n", encoding="ascii")
    assert yourself.container_facts(proc_root=proc, root=root)["wsl"] == "wsl1"
    (proc / "version").write_text("Linux version 6.8.0-45-generic (buildd@ubuntu)\n", encoding="ascii")
    assert yourself.container_facts(proc_root=proc, root=root)["wsl"] is None
    (root / ".dockerenv").touch()  # a container inside WSL keeps both facts
    (proc / "version").write_text("Linux version 5.15.153.1-microsoft-standard-WSL2\n", encoding="ascii")
    facts = yourself.container_facts(proc_root=proc, root=root)
    assert facts["in_container"] is True and facts["kinds"] == ["docker"] and facts["wsl"] == "wsl2"


def test_no_argument_run_is_one_key_per_line(tmp_path):
    shutil.copy(ROOT / "yourself.py", tmp_path / "yourself.py")  # single file, no siblings
    proc = subprocess.run([sys.executable, "-S", str(tmp_path / "yourself.py")], cwd=tmp_path,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    facts = _lines(proc.stdout)
    assert facts["cwd"] == str(tmp_path) and facts["tool.python"] == "true"
    assert {"hostname", "ipv4", "in_container", "default_gateway", "dns", "disk_free"} <= set(facts)
    assert not (tmp_path / "__pycache__").exists()


def test_no_network_and_minimal(tmp_path):
    shutil.copy(ROOT / "yourself.py", tmp_path / "yourself.py")
    run = lambda *a: subprocess.run([sys.executable, "-S", str(tmp_path / "yourself.py"), *a], cwd=tmp_path,
                                    capture_output=True, text=True, timeout=30).stdout
    assert "ipv4" not in _lines(run("--no-network")) and "tool.git" in _lines(run("--no-network"))
    minimal = set(_lines(run("--minimal")))
    assert {"os", "python"} <= minimal <= {"os", "os_release", "python"}


def test_text_escapes_control_characters():
    facts = yourself.collect()
    facts["cwd"] = "/tmp/a\nb"
    assert "cwd: /tmp/a\\u000ab" in yourself.to_text(facts)
