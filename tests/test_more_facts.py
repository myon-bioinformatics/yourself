"""Resources (cgroup v1/v2), session facts and listener bind addresses."""
import pytest
import yourself


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


def test_session_facts_virtualenv_details(monkeypatch):
    monkeypatch.setattr(yourself.sys, "prefix", "/env/myenv")
    monkeypatch.setattr(yourself.sys, "base_prefix", "/base/python")
    s = yourself.session_facts()
    assert s["virtualenv"] is True
    assert s["virtualenv_name"] == "myenv"
    facts = yourself.collect()
    facts["session"] = s
    assert "virtualenv: true (myenv)\n" in yourself.to_text(facts)

    monkeypatch.setattr(yourself.sys, "prefix", "/base/python")
    s2 = yourself.session_facts()
    assert s2["virtualenv"] is False
    assert s2["virtualenv_name"] is None
    facts2 = yourself.collect()
    facts2["session"] = s2
    assert "virtualenv: false\n" in yourself.to_text(facts2)


def test_unsupported_platform_keeps_the_bindings_shape(monkeypatch):
    # macOS / Windows: same keys as Linux, measured=False, so the CLI output shape is stable.
    monkeypatch.setattr(yourself.platform, "system", lambda: "Darwin")
    out = yourself.listener_ports(addresses=True)
    assert out == {"measured": False, "tcp_ports": [], "errors": ["unsupported_platform"], "bindings": []}
    facts = yourself.collect()
    facts["listeners"] = out
    assert "listening: unmeasured (unsupported_platform)\n" in yourself.to_text(facts)  # not "(none)"
