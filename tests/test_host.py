"""Host facts from local files and fixed probes: network, container / WSL, resources,
session, listeners, probes, hosts / loopback, and the opt-in --reach.
"""

import json, shutil, socket, ssl, subprocess, threading
from pathlib import Path

import pytest

import yourself

ROOT = Path(__file__).resolve().parents[1]


# ── network and container / WSL facts; no-argument overview lines ─────────────────────────────


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


def test_no_argument_run_is_one_key_per_line(tmp_path, fake_package_cli):
    shutil.copy(ROOT / "yourself.py", tmp_path / "yourself.py")  # single file, no siblings
    proc = subprocess.run(fake_package_cli(tmp_path / "yourself.py"), cwd=tmp_path,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    facts = _lines(proc.stdout)
    assert facts["cwd"] == str(tmp_path) and facts["tool.python"] == "true"
    assert {"hostname", "ipv4", "in_container", "default_gateway", "dns", "disk_free"} <= set(facts)
    assert not (tmp_path / "__pycache__").exists()


def test_no_network_and_minimal(tmp_path, fake_package_cli):
    shutil.copy(ROOT / "yourself.py", tmp_path / "yourself.py")
    run = lambda *a: subprocess.run(fake_package_cli(tmp_path / "yourself.py", *a), cwd=tmp_path,
                                    capture_output=True, text=True, timeout=30).stdout
    assert "ipv4" not in _lines(run("--no-network")) and "tool.git" in _lines(run("--no-network"))
    minimal = set(_lines(run("--minimal")))
    assert {"os", "python"} <= minimal <= {"os", "os_release", "python"}


def test_text_escapes_control_characters():
    facts = yourself.collect()
    facts["cwd"] = "/tmp/a\nb"
    assert "cwd: /tmp/a\\u000ab" in yourself.to_text(facts)


# ── resources (cgroup v1/v2), session facts and listener bind addresses ───────────────────────

@pytest.mark.parametrize("text,family,expected", [
    ("0100007F", "tcp", "127.0.0.1"),
    ("00000000", "tcp", "0.0.0.0"),
    ("00000000000000000000000001000000", "tcp6", "::1"),
    ("00000000000000000000000000000000", "tcp6", "::"),
    ("0000000000000000FFFF00000100007F", "tcp6", "::ffff:127.0.0.1"),
])
def test_proc_address_decoding(text, family, expected):
    assert yourself._proc_address(text, family) == expected


def test_listener_bind_addresses(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    (tmp_path / "net").mkdir()
    header = "  sl  local_address rem_address   st\n"
    (tmp_path / "net" / "tcp").write_text(header +
        "   0: 0100007F:1F90 00000000:0000 0A\n   1: 00000000:0016 00000000:0000 0A\n"
        "   2: 0100007F:9C40 0100007F:1F90 01\n")
    (tmp_path / "net" / "tcp6").write_text(header +
        "   0: 00000000000000000000000001000000:0BB8 00000000000000000000000000000000:0000 0A\n")
    out = yourself.listener_ports(proc_root=tmp_path, addresses=True)
    assert out["bindings"] == [{"port": 22, "address": "0.0.0.0"}, {"port": 3000, "address": "::1"},
                               {"port": 8080, "address": "127.0.0.1"}]
    assert "bindings" not in yourself.listener_ports(proc_root=tmp_path)  # opt-in for the library
    facts = yourself.collect()
    facts["listeners"] = out
    assert "listening: 0.0.0.0:22, [::1]:3000, 127.0.0.1:8080\n" in yourself.to_text(facts)


def _proc(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "meminfo").write_text("MemTotal: 2048 kB\nMemAvailable: 1024 kB\nSwapTotal: 0 kB\n")
    (proc / "loadavg").write_text("0.50 0.25 0.10 1/90 123\n")
    (proc / "uptime").write_text("90061.5 100.0\n")
    return proc


def test_resources_cgroup_v2(tmp_path):
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "memory.max").write_text("536870912\n")
    (cg / "cpu.max").write_text("150000 100000\n")
    res = yourself.resource_facts(proc_root=_proc(tmp_path), cgroup_root=cg)
    assert (res["memory_total_bytes"], res["memory_available_bytes"]) == (2048 * 1024, 1024 * 1024)
    assert (res["memory_limit_bytes"], res["cpu_limit"]) == (536870912, 1.5)
    assert res["load"] == ["0.50", "0.25", "0.10"] and res["uptime_seconds"] == 90061
    facts = yourself.collect()
    facts["resources"] = res
    text = yourself.to_text(facts)
    assert "cgroup limit 1.5" in text and "cgroup limit 0.5 GiB" in text and "uptime: 1d 1h01m" in text


def test_resources_cgroup_v1_and_unlimited(tmp_path):
    cg = tmp_path / "cg"
    (cg / "memory").mkdir(parents=True)
    (cg / "cpu").mkdir()
    (cg / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")  # v1 "unlimited"
    (cg / "cpu" / "cpu.cfs_quota_us").write_text("-1\n")
    (cg / "cpu" / "cpu.cfs_period_us").write_text("100000\n")
    res = yourself.resource_facts(proc_root=_proc(tmp_path), cgroup_root=cg)
    assert res["memory_limit_bytes"] is None and res["cpu_limit"] is None
    (cg / "cpu" / "cpu.cfs_quota_us").write_text("200000\n")
    assert yourself.resource_facts(proc_root=_proc(tmp_path), cgroup_root=cg)["cpu_limit"] == 2.0


def test_missing_sources_are_none(tmp_path):
    res = yourself.resource_facts(proc_root=tmp_path, cgroup_root=tmp_path)
    assert res["memory_total_bytes"] is None and res["load"] is None and res["cpu_limit"] is None


def test_session_facts_flags_only_for_secrets(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/secret/path.pem")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    s = yourself.session_facts()
    assert s["ca_env"]["SSL_CERT_FILE"] is True and "/secret" not in str(s)
    assert s["timezone"] == "Asia/Tokyo" and s["display"] == {"DISPLAY": False, "WAYLAND_DISPLAY": False}
    facts = yourself.collect()
    facts["session"] = s
    assert "display: false (headless)\n" in yourself.to_text(facts)


def test_unsupported_platform_keeps_the_bindings_shape(monkeypatch):
    # macOS / Windows: same keys as Linux, measured=False, so the CLI output shape is stable.
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")
    out = yourself.listener_ports(addresses=True)
    assert out == {"measured": False, "tcp_ports": [], "errors": ["unsupported_platform"], "bindings": []}
    facts = yourself.collect()
    facts["listeners"] = out
    assert "listening: unmeasured (unsupported_platform)\n" in yourself.to_text(facts)  # not "(none)"


def test_session_facts_virtualenv_name(monkeypatch):
    monkeypatch.setattr(yourself.sys, 'prefix', str(Path('env') / 'myenv'))
    monkeypatch.setattr(yourself.sys, 'base_prefix', 'base-python')
    session = yourself.session_facts()
    assert session['virtualenv'] is True and session['virtualenv_name'] == 'myenv'
    facts = yourself.collect()
    facts['session'] = session
    assert 'virtualenv: true (myenv)\n' in yourself.to_text(facts)
    monkeypatch.setattr(yourself.sys, 'prefix', 'base-python')
    session = yourself.session_facts()
    assert session['virtualenv'] is False and session['virtualenv_name'] is None
    facts['session'] = session
    assert 'virtualenv: false\n' in yourself.to_text(facts)


# ── default probes: fixed argv, true/false per probe, bounded output; /etc/hosts ──────────────

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


def test_hosts_facts_windows_fallback(tmp_path, monkeypatch):
    win_dir = tmp_path / "Windows" / "System32" / "drivers" / "etc"
    win_dir.mkdir(parents=True)
    (win_dir / "hosts").write_text("127.0.0.1 localhost\n")
    monkeypatch.setattr(yourself.platform, "system", lambda: "Windows")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))

    orig_exists = yourself.Path.exists
    def mock_exists(self):
        if str(self).replace("\\", "/").endswith("/etc/hosts") and not str(self).startswith(str(tmp_path)):
            return False
        return orig_exists(self)

    monkeypatch.setattr(yourself.Path, "exists", mock_exists)
    out = yourself.hosts_facts()
    assert out["measured"] is True
    assert out["names"]["localhost"] == ["127.0.0.1"]


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


# ── NO_PROXY coverage of loopback names, localhost resolution, IPv6 loopback ──────────────────

@pytest.mark.parametrize("value,expected", [
    ("", {"localhost": False, "127.0.0.1": False, "::1": False}),
    ("*", {"localhost": True, "127.0.0.1": True, "::1": True}),
    ("localhost,127.0.0.1", {"localhost": True, "127.0.0.1": True, "::1": False}),
    (" .localhost , 127.0.0.0/8 ,[::1]", {"localhost": True, "127.0.0.1": True, "::1": True}),
    ("example.com,10.0.0.0/8", {"localhost": False, "127.0.0.1": False, "::1": False}),
    ("LOCALHOST", {"localhost": True, "127.0.0.1": False, "::1": False}),
])
def test_no_proxy_covers(value, expected):
    assert yourself.no_proxy_covers(value) == expected


def test_no_proxy_value_is_never_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_PROXY", "secret-internal.example,localhost")
    facts = yourself.network_facts(proc_root=tmp_path, sys_root=tmp_path, etc_root=tmp_path)
    assert facts["no_proxy_covers"]["localhost"] is True
    assert "secret-internal" not in str(facts)


def test_ipv6_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    assert yourself._ipv6_loopback(tmp_path) is False  # IPv6 disabled: no if_inet6
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "if_inet6").write_text("00000000000000000000000000000001 01 80 10 80       lo\n")
    assert yourself._ipv6_loopback(tmp_path) is True
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")
    assert yourself._ipv6_loopback(tmp_path) is None


def test_localhost_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.platform, "system", lambda: "Linux")
    (tmp_path / "hosts").write_text("127.0.0.1 localhost\n")
    facts = yourself.collect()
    facts["hosts"] = yourself.hosts_facts(etc_root=tmp_path, proc_root=tmp_path)
    facts["probes"] = {"localhost.resolves_to": {"ok": True, "lines": ["::1             localhost"]}}
    text = yourself.to_text(facts)
    assert "hosts.localhost: 127.0.0.1\n" in text and "localhost.ipv6_loopback: false\n" in text
    assert "localhost.resolves_to: true" in text and "::1" in text


def test_resolver_probe_is_a_fixed_argv():
    assert yourself._PROBES["localhost.resolves_to"] == ("getent", "hosts", "localhost")


# ── reach: opt-in TLS reachability through a CONNECT proxy (fake proxies on 127.0.0.1) ────────

def serve_once(reply, seen):
    """A one-connection fake proxy on 127.0.0.1: records the request, sends ``reply``, closes."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def run():
        conn, _ = server.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            seen.append(data.decode("ascii", "replace"))
            conn.sendall(reply)
        server.close()
    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1]


def closed_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_validation():
    with pytest.raises(TypeError):
        yourself.reach("github.com")
    for bad in ([], ["a"] * 21):
        with pytest.raises(ValueError):
            yourself.reach(bad)
    for timeout in (0, 31, True):
        with pytest.raises(ValueError):
            yourself.reach(["a"], timeout=timeout)
    rows = yourself.reach(["bad host", "a..b", "-x", "x:0", "x:99999", 7], environ={})
    assert [r["status"] for r in rows] == ["invalid_host"] * 6 and rows[-1]["host"] == "7"


def test_proxy_denied_with_reason_and_credentials_sent():
    seen = []
    port = serve_once(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 20\r\n\r\nblocked:   no  rule\n", seen)
    [row] = yourself.reach(["cdn.example.com"], environ={"HTTPS_PROXY": "http://u:p@127.0.0.1:%d" % port})
    assert row["status"] == "proxy_denied" and row["detail"] == "403 blocked: no rule" and row["via"] == "proxy"
    assert seen[0].startswith("CONNECT cdn.example.com:443 HTTP/1.1\r\n")
    assert "Proxy-Authorization: Basic dTpw" in seen[0]
    assert "u:p" not in json.dumps(row)
    seen = []
    port = serve_once(b"HTTP/1.1 403 Forbidden\r\n\r\n", seen)
    yourself.reach(["cdn.example.com"], environ={"HTTPS_PROXY": "http://a%%40b:p%%3Aq@127.0.0.1:%d" % port})
    assert "Proxy-Authorization: Basic YUBiOnA6cQ==" in seen[0]


def test_proxy_without_reason_and_bad_reply():
    port = serve_once(b"HTTP/1.1 407 Proxy Auth\r\n\r\n", [])
    [row] = yourself.reach(["h.example:8443"], environ={"https_proxy": "127.0.0.1:%d" % port})
    assert (row["status"], row["detail"], row["host"]) == ("proxy_denied", "407", "h.example:8443")
    port = serve_once(b"nonsense\r\n\r\n", [])
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://127.0.0.1:%d" % port})
    assert (row["status"], row["detail"]) == ("unreachable", "bad_proxy_reply")


def test_established_then_tls_failure_and_success(monkeypatch):
    port = serve_once(b"HTTP/1.1 200 Connection Established\r\n\r\nnot tls at all\r\n", [])
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://127.0.0.1:%d" % port}, timeout=3)
    assert row["status"] in ("tls_error", "unreachable") and row["detail"]

    class Tls:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def version(self):
            return "TLSv1.3"

    class Context:
        def wrap_socket(self, sock, server_hostname):
            assert server_hostname == "h.example"
            return Tls()
    used = []
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: used.append(cafile) or Context())
    port = serve_once(b"HTTP/1.1 200 Connection Established\r\n\r\n", [])
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://127.0.0.1:%d" % port,
                                                    "SSL_CERT_FILE": "/ca.pem"})
    assert (row["status"], row["detail"]) == ("ok", "TLSv1.3") and used == ["/ca.pem"] and row["ms"] >= 0

    class Failing:
        def wrap_socket(self, sock, server_hostname):
            raise socket.timeout()
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: Failing())
    port = serve_once(b"HTTP/1.1 200 OK\r\n\r\n", [])
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://127.0.0.1:%d" % port})
    assert row["status"] == "timeout"

    class Broken:
        def wrap_socket(self, sock, server_hostname):
            raise ConnectionResetError()
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: Broken())
    port = serve_once(b"HTTP/1.1 200 OK\r\n\r\n", [])
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://127.0.0.1:%d" % port})
    assert (row["status"], row["detail"]) == ("unreachable", "ConnectionResetError")


def test_direct_paths_and_no_proxy(monkeypatch):
    port = closed_port()
    [row] = yourself.reach(["127.0.0.1:%d" % port], environ={"HTTPS_PROXY": "http://127.0.0.1:1",
                                                             "NO_PROXY": "127.0.0.1"})
    assert (row["status"], row["via"], row["detail"]) == ("refused", "direct", "host")
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "socks5://127.0.0.1:1"})
    assert (row["status"], row["detail"]) == ("unreachable", "unsupported_proxy")
    for error, status in ((socket.gaierror(), "dns"), (socket.timeout(), "timeout"), (PermissionError(), "unreachable")):
        def fail(*args, error=error, **kwargs):
            raise error
        monkeypatch.setattr(socket, "create_connection", fail)
        [row] = yourself.reach(["h.example"], environ={})
        assert row["status"] == status and row["via"] == "direct"
    [row] = yourself.reach(["h.example"], environ={"HTTPS_PROXY": "http://proxy.example:3128"})
    assert (row["status"], row["detail"]) == ("unreachable", "PermissionError")
