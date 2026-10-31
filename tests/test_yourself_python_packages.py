import json

import pytest
import yourself


class Dist:
    """Stand-in for importlib.metadata.Distribution: a name and an INSTALLER file."""

    def __init__(self, name, installer=None, error=None):
        self._name, self._installer, self._error = name, installer, error

    @property
    def metadata(self):
        if self._name is KeyError:
            raise KeyError("Name")
        return {"Name": self._name}

    def read_text(self, filename):
        assert filename == "INSTALLER"
        if self._error:
            raise self._error
        return self._installer


def test_counts_by_installer_and_first_name_wins():
    dists = [Dist("Zed", "uv\n"), Dist("alpha", "pip\n"), Dist("py_thon.pkg", "UV"),
             Dist("PY-THON-pkg", "pip"),               # same project further down sys.path: shadowed
             Dist("os-owned", None),                   # OS-packaged: no INSTALLER file
             Dist("odd", "has space\n"),               # not a one-word installer
             Dist(None), Dist(KeyError), Dist("broken", error=PermissionError),
             Dist("bad", error=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x"))]
    report = yourself.python_packages(distributions=dists)
    assert report["count"] == 7
    assert report["installers"] == {"other": 1, "pip": 1, "unknown": 3, "uv": 2}
    assert report["complete"] is False
    assert report["errors"] == ["installer:PermissionError", "installer:UnicodeDecodeError",
                                "metadata:KeyError", "metadata:ValueError"]


def test_injected_discovery_is_consistent(monkeypatch):
    from importlib import metadata
    monkeypatch.setattr(metadata, "distributions", lambda: iter([Dist("example", installer="uv")]))
    report = yourself.python_packages()
    assert report == {"count": 1, "installers": {"uv": 1}, "complete": True, "errors": []}
    assert json.loads(yourself.to_json(report)) == report


def base_facts(report):
    return {"os": {"system": "Linux", "release": "x", "machine": "y"},
            "runtime": {"implementation": "CPython", "version": "3.14.0", "bits": 64},
            "python_packages": report}


def test_text_and_markdown_lines():
    text = yourself.to_text(base_facts(yourself.python_packages(
        distributions=[Dist("tidy", "uv"), Dist("other", "pip")])))
    assert "python_packages: 2 (pip 1, uv 1)\n" in text and "python_packages_errors" not in text
    assert "python_packages: 0 (none)\n" in yourself.to_text(base_facts(yourself.python_packages(distributions=[])))
    partial = yourself.python_packages(distributions=[Dist("tidy", error=PermissionError)])
    assert "python_packages: 1 (unknown 1) [partial]\n" in yourself.to_text(base_facts(partial))
    assert "python_packages_errors: installer:PermissionError\n" in yourself.to_text(base_facts(partial))
    markdown = yourself.to_markdown(base_facts(partial))
    assert "## Python packages" in markdown and "| count | 1 |" in markdown and "| complete | false |" in markdown
    assert "python_packages" not in yourself.to_text({k: v for k, v in base_facts({}).items() if k != "python_packages"})


def test_cli_line_minimal_and_partial_exit(monkeypatch, capsys):
    real = yourself.python_packages
    monkeypatch.setattr(yourself, "python_packages", lambda: real(distributions=[Dist("tidy", "uv")]))
    facts = {key: value for key, value in base_facts({}).items() if key != "python_packages"}
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: dict(facts))
    monkeypatch.setattr(yourself, "collect", lambda **kwargs: dict(facts))
    monkeypatch.setattr(yourself, "session_facts", lambda: None)
    monkeypatch.setattr(yourself, "resource_facts", lambda: None)
    monkeypatch.setattr(yourself, "runtime_facts", lambda **kwargs: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected real environment probe")
    for name in ("probe_all", "network_facts", "listener_ports", "container_facts", "hosts_facts"):
        monkeypatch.setattr(yourself, name, forbidden)
    assert yourself.main(["--no-network", "--no-probes"]) == 0
    assert "python_packages: 1 (uv 1)" in capsys.readouterr().out
    assert yourself.main(["--no-network", "--no-probes", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["python_packages"]["installers"] == {"uv": 1}
    assert yourself.main(["--no-network", "--no-probes", "--format", "markdown"]) == 0
    assert "| installers | {\"uv\": 1} |" in capsys.readouterr().out
    assert yourself.main(["--minimal"]) == 0
    assert "python_packages" not in capsys.readouterr().out
    monkeypatch.setattr(yourself, "python_packages", lambda: real(distributions=[Dist("tidy", error=PermissionError)]))
    assert yourself.main(["--no-network", "--no-probes"]) == 2
    assert "python_packages: 1 (unknown 1) [partial]" in capsys.readouterr().out


def test_invalid_installer_keeps_package(tmp_path):
    from importlib.metadata import PathDistribution
    dist = tmp_path / "example-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: example\nVersion: 1.0\n", encoding="utf-8")
    (dist / "INSTALLER").write_bytes(b"\xff")
    report = yourself.python_packages(distributions=[PathDistribution(dist)])
    assert report["count"] == 1 and report["installers"] == {"unknown": 1}
    assert report["errors"] == ["installer:UnicodeDecodeError"]


def test_reader_suppressed_installer_error_is_unknown(tmp_path, monkeypatch):
    from importlib.metadata import PathDistribution
    from pathlib import Path
    dist = tmp_path / "example-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: example\nVersion: 1.0\n", encoding="utf-8")
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path.name == "INSTALLER":
            raise PermissionError("synthetic denied metadata")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    report = yourself.python_packages(distributions=[PathDistribution(dist)])
    # PathDistribution suppresses this error before the probe can observe it.
    assert report["installers"] == {"unknown": 1}
    assert report["complete"] is True and report["errors"] == []


@pytest.mark.parametrize("partial", [False, True])
def test_discovery_error_is_partial(monkeypatch, partial):
    from importlib import metadata
    def discovery():
        if partial:
            yield Dist("example")
        raise PermissionError("private path must not escape")
    monkeypatch.setattr(metadata, "distributions", discovery)
    report = yourself.python_packages()
    assert report["count"] == int(partial) and report["complete"] is False
    assert report["errors"] == ["discovery:PermissionError"]
    assert "private path" not in yourself.to_json(report)
