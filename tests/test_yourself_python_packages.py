import json

import pytest
import yourself


class Dist:
    """Stand-in for importlib.metadata.Distribution: a name, a version and an INSTALLER file."""

    def __init__(self, name, version="1.0", installer=None, error=None):
        self._name, self.version, self._installer, self._error = name, version, installer, error

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


def found(*modules):
    return lambda name: object() if name in modules else None


def test_counts_by_installer_and_first_name_wins():
    dists = [Dist("Zed", "2", "uv\n"), Dist("alpha", "1", "pip\n"), Dist("py_thon.pkg", "3", "UV"),
             Dist("PY-THON-pkg", "9", "pip"),            # same project further down sys.path: shadowed
             Dist("os-owned", "4", None),                # OS-packaged: no INSTALLER file
             Dist("odd", "5", "has space\n"),            # not a one-word installer
             Dist(None), Dist(KeyError), Dist("broken", error=PermissionError), Dist("bad", error=UnicodeDecodeError(
                 "utf-8", b"\xff", 0, 1, "x"))]
    report = yourself.python_packages(distributions=dists, find_spec=found())
    assert report["count"] == 7
    assert report["installers"] == {"other": 1, "pip": 1, "unknown": 3, "uv": 2}
    assert report["packages"] is None
    assert report["complete"] is False and report["sha256"] is None
    assert report["errors"] == ["installer:PermissionError", "installer:UnicodeDecodeError",
                                "metadata:KeyError", "metadata:ValueError"]


def test_listing_is_sorted_and_digest_ignores_order():
    dists = [Dist("b", "2", "uv"), Dist("A", "1", "pip")]
    listed = yourself.python_packages(listing=True, distributions=dists, find_spec=found())
    assert listed["packages"] == [{"name": "A", "version": "1", "installer": "pip"},
                                  {"name": "b", "version": "2", "installer": "uv"}]
    flipped = yourself.python_packages(distributions=dists[::-1], find_spec=found())
    assert flipped["sha256"] == listed["sha256"] and flipped["packages"] is None
    other = yourself.python_packages(distributions=[Dist("A", "1"), Dist("b", "3")], find_spec=found())
    assert other["sha256"] != listed["sha256"]


@pytest.mark.parametrize("finder, expected", [
    (found("uno"), True), (found(), False),
    (lambda name: (_ for _ in ()).throw(ValueError("uno.__spec__ is None")), None),
    (lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)), None)])
def test_os_module_is_looked_up_not_imported(finder, expected):
    report = yourself.python_packages(distributions=[], find_spec=finder)
    assert report["importable"] == {"uno": expected} and report["count"] == 0
    assert report["complete"] is (expected is not None)
    assert bool(report["errors"]) is (expected is None)


def test_injected_discovery_is_consistent(monkeypatch):
    from importlib import metadata, util
    monkeypatch.setattr(metadata, "distributions", lambda: iter([Dist("example", installer="uv")]))
    monkeypatch.setattr(util, "find_spec", found("uno"))
    report = yourself.python_packages(listing=True)
    assert report["count"] == len(report["packages"]) == 1
    assert sum(report["installers"].values()) == report["count"]
    assert report["packages"] == [{"name": "example", "version": "1.0", "installer": "uv"}]
    assert report["complete"] is True and report["errors"] == []
    assert json.loads(yourself.to_json(report)) == report


def base_facts(report):
    return {"os": {"system": "Linux", "release": "x", "machine": "y"},
            "runtime": {"implementation": "CPython", "version": "3.14.0", "bits": 64},
            "python_packages": report}


def test_text_lines_and_control_characters_in_names():
    dists = [Dist("tidy", "1.0", "uv"), Dist("evil\x1b[31m\nname", "2", "pip")]
    text = yourself.to_text(base_facts(yourself.python_packages(distributions=dists, find_spec=found("uno"))))
    assert "python_packages: 2 (pip 1, uv 1)\n" in text
    assert "python_importable.uno: true\n" in text and "package." not in text
    listed = yourself.to_text(base_facts(yourself.python_packages(
        listing=True, distributions=dists, find_spec=found())))
    assert "package.tidy: 1.0 (uv)\n" in listed
    assert "package.evil\\u001b[31m\\u000aname: 2 (pip)\n" in listed
    assert "\x1b" not in listed and listed.count("\n") == len(listed.splitlines())
    assert "python_importable.uno: false\n" in listed
    empty = yourself.to_text(base_facts(yourself.python_packages(distributions=[], find_spec=found())))
    assert "python_packages: 0 (none)\n" in empty
    assert "python_packages" not in yourself.to_text({k: v for k, v in base_facts({}).items() if k != "python_packages"})


def test_cli_flag_and_minimal(monkeypatch, capsys):
    calls = []
    real = yourself.python_packages

    def fake(listing=False):
        calls.append(listing)
        return real(listing=listing, distributions=[Dist("tidy", "1.0", "uv")],
                                        find_spec=found("uno"))
    monkeypatch.setattr(yourself, "python_packages", fake)
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
    out = capsys.readouterr().out
    assert "python_packages: 1 (uv 1)" in out and "package.tidy" not in out
    assert yourself.main(["--no-network", "--no-probes", "--packages"]) == 0
    assert "package.tidy: 1.0 (uv)" in capsys.readouterr().out
    assert yourself.main(["--no-network", "--no-probes", "--packages", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["python_packages"]["packages"][0]["installer"] == "uv"
    assert yourself.main(["--no-network", "--no-probes", "--packages", "--format", "markdown"]) == 0
    assert "| tidy | 1.0 | uv |" in capsys.readouterr().out
    assert calls == [False, True, True, True]
    assert yourself.main(["--minimal"]) == 0
    assert "python_packages" not in capsys.readouterr().out and calls == [False, True, True, True]
    monkeypatch.setattr(yourself, "python_packages", lambda **kwargs: real(
        distributions=[Dist("tidy", error=PermissionError)], find_spec=found()))
    assert yourself.main(["--no-network", "--no-probes"]) == 2
    assert "python_packages: 1 (unknown 1) [partial]" in capsys.readouterr().out


def test_invalid_installer_keeps_package(tmp_path):
    from importlib.metadata import PathDistribution
    dist = tmp_path / "example-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: example\nVersion: 1.0\n", encoding="utf-8")
    (dist / "INSTALLER").write_bytes(b"\xff")
    report = yourself.python_packages(listing=True, distributions=[PathDistribution(dist)], find_spec=found())
    assert report["packages"] == [{"name": "example", "version": "1.0", "installer": "unknown"}]
    assert report["count"] == 1 and report["sha256"] is None
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
    report = yourself.python_packages(distributions=[PathDistribution(dist)], find_spec=found())
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
    report = yourself.python_packages(find_spec=found())
    assert report["count"] == int(partial)
    assert report["complete"] is False and report["sha256"] is None
    assert report["errors"] == ["discovery:PermissionError"]
    assert "private path" not in yourself.to_json(report)


def test_uno_lookup_error_is_unmeasured():
    def finder(name):
        raise RuntimeError("private finder message")
    report = yourself.python_packages(distributions=[], find_spec=finder)
    assert report["importable"]["uno"] is None and not report["complete"]
    assert report["errors"] == ["importable.uno:RuntimeError"]
    assert "python_importable.uno: unmeasured" in yourself.to_text(base_facts(report))
    assert "python_importable.uno | unmeasured" in yourself.to_markdown(base_facts(report))
    assert "private finder message" not in yourself.to_json(report)


def test_markdown_packages_are_opt_in_and_escaped():
    dists = [Dist("<private|project>", "1`2", "pip")]
    hidden = yourself.python_packages(distributions=dists, find_spec=found())
    markdown = yourself.to_markdown(base_facts(hidden))
    assert "## Python packages" in markdown and "private" not in markdown
    listed = yourself.python_packages(listing=True, distributions=dists, find_spec=found())
    markdown = yourself.to_markdown(base_facts(listed))
    assert "&lt;private&#124;project&gt;" in markdown and "1&#96;2" in markdown
    assert "<private|project>" not in markdown

