"""reach (opt-in TLS reachability through a CONNECT proxy) and runtime_facts (directory names only)."""
import json
import socket
import ssl
import threading

import pytest

import yourself


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


def test_runtime_facts_from_directories(tmp_path, monkeypatch):
    browsers, pythons = tmp_path / "pw", tmp_path / "uv"
    for name in ("chromium-1194", "chromium_headless_shell-1194", "ffmpeg-1011", ".links"):
        (browsers / name).mkdir(parents=True)
    (browsers / "README").write_text("x", encoding="utf-8")
    for name in ("cpython-3.10.20-linux-x86_64-gnu", "pypy-3.11.13-linux-x86_64-gnu", ".lock", "junk"):
        (pythons / name).mkdir(parents=True)
    env = {"PLAYWRIGHT_BROWSERS_PATH": str(browsers), "UV_PYTHON_INSTALL_DIR": str(pythons)}
    monkeypatch.setattr(yourself, "command_inventory",
                        lambda names, versions, timeout: [{"name": "git", "version": "2.37.1", "status": "measured"}])
    facts = yourself.runtime_facts(environ=env, versions=True)
    assert facts["playwright"] == {"path": str(browsers), "builds": [
        {"name": "chromium", "revision": 1194}, {"name": "chromium_headless_shell", "revision": 1194},
        {"name": "ffmpeg", "revision": 1011}]}
    assert facts["uv_pythons"] == [
        {"implementation": "cpython", "version": "3.10.20", "platform": "linux-x86_64-gnu"},
        {"implementation": "pypy", "version": "3.11.13", "platform": "linux-x86_64-gnu"}]
    assert facts["git"] == {"version": "2.37.1", "status": "measured", "merge_tree_write_tree": False}
    monkeypatch.setattr(yourself, "command_inventory",
                        lambda names, versions, timeout: [{"name": "git", "version": None, "status": "not_found"}])
    empty = yourself.runtime_facts(environ={"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "none"),
                                            "UV_PYTHON_INSTALL_DIR": str(tmp_path / "none")}, versions=True)
    assert empty["playwright"] == {"path": None, "builds": []} and empty["uv_pythons"] == []
    assert empty["git"]["merge_tree_write_tree"] is None
    assert yourself.runtime_facts(environ={})["git"] is None


@pytest.mark.parametrize("platform, env, tail", [
    ("win32", {"LOCALAPPDATA": "L", "APPDATA": "A"}, (("L", "ms-playwright"), ("A", "uv", "python"))),
    ("win32", {}, (None, None)),
    ("darwin", {}, (("Library", "Caches", "ms-playwright"), (".local", "share", "uv", "python"))),
    ("linux", {"XDG_CACHE_HOME": "C", "XDG_DATA_HOME": "D", "PLAYWRIGHT_BROWSERS_PATH": "0"},
     (("C", "ms-playwright"), ("D", "uv", "python"))),
])
def test_default_locations(monkeypatch, platform, env, tail):
    monkeypatch.setattr(yourself.sys, "platform", platform)
    for found, expected in zip((yourself._playwright_dirs(env), yourself._uv_python_dirs(env)), tail):
        if expected is None:
            assert found == []
        else:
            assert found[0].parts[-len(expected):] == expected


def test_cli_reach_and_runtime_lines(monkeypatch, capsys):
    assert yourself.main(["--reach", "bad host"]) == 1
    assert capsys.readouterr().out == "reach.bad host: invalid_host\n"
    monkeypatch.setattr(yourself, "reach", lambda hosts, timeout: [
        {"host": "a.example", "via": "proxy", "status": "ok", "detail": "TLSv1.3", "ms": 5}])
    assert yourself.main(["--reach", "a.example"]) == 0
    assert capsys.readouterr().out == "reach.a.example: ok (TLSv1.3) via proxy 5ms\n"
    assert yourself.main(["--reach", "a.example", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["reach"][0]["status"] == "ok"

    def too_many(hosts, timeout):
        raise ValueError("give 1 to 20 hosts")
    monkeypatch.setattr(yourself, "reach", too_many)
    assert yourself.main(["--reach", "a"]) == 2
    capsys.readouterr()
    for builds, path, expected in (
            ([("chromium", 1)], "/pw", "playwright_chromium: full only (--no-shell)"),
            ([("chromium_headless_shell", 1)], "/pw", "playwright_chromium: shell only (--only-shell)"),
            ([("firefox", 1)], "/pw", "playwright_chromium: none"),
            ([], "/pw", "playwright_browsers: (none in /pw)"),
            ([], None, "playwright_browsers: (no cache)")):
        facts = {"os": {"system": "Linux", "release": "x", "machine": "y"},
                 "runtime": {"implementation": "CPython", "version": "3.12", "bits": 64},
                 "runtimes": {"playwright": {"path": path, "builds": [{"name": n, "revision": r} for n, r in builds]},
                              "uv_pythons": [], "path_pythons": ["3.12"],
                              "git": {"version": "2.43.0", "status": "measured", "merge_tree_write_tree": True}}}
        text = yourself.to_text(facts)
        assert expected in text and "path_pythons: 3.12" in text and "git_merge_tree_write_tree: true" in text
        assert "uv_pythons: (none)" in text
    monkeypatch.setattr(yourself, "runtime_facts", lambda versions: {
        "playwright": {"path": None, "builds": []}, "uv_pythons": [], "path_pythons": [], "git": None})
    assert yourself.main(["--no-network", "--no-probes"]) in (0, 2)
    out = capsys.readouterr().out
    assert "playwright_browsers: (no cache)" in out and "path_pythons: (none)" in out
    assert "git_merge_tree_write_tree" not in out


def test_runtime_facts_path_pythons_range(monkeypatch):
    monkeypatch.setattr(yourself.shutil, "which", lambda cmd: "/usr/bin/" + cmd if cmd in ("python3.8", "python3.15") else None)
    facts = yourself.runtime_facts()
    assert facts["path_pythons"] == ["3.8", "3.15"]
