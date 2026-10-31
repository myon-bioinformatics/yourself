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
    assert report["count"] == 5
    assert report["installers"] == {"other": 1, "pip": 1, "unknown": 1, "uv": 2}
    assert report["packages"] is None


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
    (lambda name: (_ for _ in ()).throw(ValueError("uno.__spec__ is None")), False),
    (lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name)), False)])
def test_os_module_is_looked_up_not_imported(finder, expected):
    report = yourself.python_packages(distributions=[], find_spec=finder)
    assert report["importable"] == {"uno": expected} and report["count"] == 0


def test_real_interpreter_is_consistent():
    report = yourself.python_packages(listing=True)
    assert report["count"] == len(report["packages"]) >= 1
    assert sum(report["installers"].values()) == report["count"]
    assert "pytest" in {item["name"].lower() for item in report["packages"]}
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
    assert yourself.main(["--no-network", "--no-probes"]) in (0, 2)
    out = capsys.readouterr().out
    assert "python_packages: 1 (uv 1)" in out and "package.tidy" not in out
    assert yourself.main(["--no-network", "--no-probes", "--packages"]) in (0, 2)
    assert "package.tidy: 1.0 (uv)" in capsys.readouterr().out
    assert yourself.main(["--no-network", "--no-probes", "--packages", "--format", "json"]) in (0, 2)
    assert json.loads(capsys.readouterr().out)["python_packages"]["packages"][0]["installer"] == "uv"
    assert calls == [False, True, True]
    assert yourself.main(["--minimal"]) == 0
    assert "python_packages" not in capsys.readouterr().out and calls == [False, True, True]
