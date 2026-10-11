"""NO_PROXY coverage of loopback names, localhost resolution and the IPv6 loopback."""
import pytest
import yourself


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
