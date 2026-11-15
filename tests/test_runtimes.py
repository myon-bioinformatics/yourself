"""Runtimes observed without running them: Node installs, Python / Node version pins,
bounded metadata reads, runtime_facts and Python package counts.
"""

import json, os, subprocess, sys
from pathlib import Path

import pytest

import yourself


# ── Node installs and Python / Node version pins: directory names, headers, small files ───────

def make_node(prefix, version=None, *, windows=False):
    """An unpacked install: bin/node (or node.exe) plus the header Node ships next to it."""
    binary = prefix / "node.exe" if windows else prefix / "bin" / "node"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    if version:
        major, minor, patch = version.split(".")
        header = prefix / "include" / "node" / "node_version.h"
        header.parent.mkdir(parents=True)
        header.write_text("#define NODE_MAJOR_VERSION %s\n#define NODE_MINOR_VERSION %s\n"
                          "#define NODE_PATCH_VERSION %s\n" % (major, minor, patch), encoding="ascii")
    return binary


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A fake Linux machine: home and the conventional install parent live under tmp_path."""
    home, opt = tmp_path / "home", tmp_path / "opt"
    home.mkdir()
    opt.mkdir()
    monkeypatch.setattr(yourself.sys, "platform", "linux")
    monkeypatch.setattr(yourself.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setitem(yourself._NODE_PREFIX_PARENTS, "linux", (str(opt),))
    manager_dirs = yourself._node_manager_dirs
    monkeypatch.setattr(yourself, "_node_manager_dirs", lambda env:
                        manager_dirs({"N_PREFIX": str(tmp_path / "n"), **env}))
    return home, opt


def test_node_installs_from_managers_prefixes_and_path(tmp_path, machine):
    home, opt = machine
    for relative in (".nvm/versions/node/v20.11.1", ".nvm/versions/node/not-a-version",
                     ".local/share/fnm/node-versions/v22.1.0", ".volta/tools/image/node/18.19.0",
                     ".local/share/mise/installs/node/24.0.0", ".asdf/installs/nodejs/21.7.3"):
        (home / relative).mkdir(parents=True)
    (tmp_path / "n" / "n" / "versions" / "node" / "16.20.2").mkdir(parents=True)
    keep = make_node(opt / "node24", "24.22.1")
    make_node(opt / "tool-with-node-binary")  # not named node*: never scanned
    (opt / "node-broken").mkdir()  # named node* but no binary
    stray = make_node(tmp_path / "stray", "26.0.0")
    shim = tmp_path / "shim"
    shim.mkdir()
    try:
        os.symlink(keep, shim / "node")  # PATH reaches the prefix install through a symlink
    except OSError:
        pytest.skip("symlinks unavailable")
    env = {"N_PREFIX": str(tmp_path / "n"), "PATH": os.pathsep.join([str(shim), str(stray.parent), "/does/not/exist", ""])}
    found = yourself.node_installs(env)
    assert [(n["source"], n["version"], n["on_path"], n["active"]) for n in found] == [
        ("nvm", "20.11.1", False, False), ("fnm", "22.1.0", False, False), ("volta", "18.19.0", False, False),
        ("mise", "24.0.0", False, False), ("asdf", "21.7.3", False, False), ("n", "16.20.2", False, False),
        ("prefix", "24.22.1", True, True), ("path", "26.0.0", True, False)]
    assert found[6]["path"] == str(opt / "node24") and found[7]["path"] == os.path.realpath(stray)


def test_active_node_is_the_first_on_path(tmp_path, machine):
    _, opt = machine
    first = make_node(opt / "node22", "22.23.4")
    make_node(opt / "node24", "24.22.1")
    both = os.pathsep.join([str(opt / "node24" / "bin"), str(first.parent)])
    found = yourself.node_installs({"PATH": both})
    assert [(n["version"], n["on_path"], n["active"]) for n in found] == [
        ("22.23.4", True, False), ("24.22.1", True, True)]
    assert [n["active"] for n in yourself.node_installs({"PATH": ""})] == [False, False]


def test_node_without_header_has_unknown_version(tmp_path, machine):
    binary = make_node(tmp_path / "bare")
    [only] = yourself.node_installs({"PATH": str(binary.parent)})
    assert (only["source"], only["version"], only["active"]) == ("path", None, True)


@pytest.mark.parametrize("platform, env, tails", [
    ("win32", {"LOCALAPPDATA": "L"}, {"fnm": ("L", "fnm", "node-versions"), "volta": ("L", "Volta", "tools", "image", "node"),
                                      "mise": ("L", "mise", "installs", "node")}),
    ("win32", {"NVM_HOME": "N"}, {"nvm-windows": ("N",)}),
    ("darwin", {}, {"fnm": ("Library", "Application Support", "fnm", "node-versions"),
                    "mise": (".local", "share", "mise", "installs", "node"), "n": ("n", "versions", "node")}),
    ("linux", {"XDG_DATA_HOME": "D", "NVM_DIR": "V", "FNM_DIR": "F", "VOLTA_HOME": "T", "MISE_DATA_DIR": "M",
               "ASDF_DATA_DIR": "A", "N_PREFIX": "P"},
     {"nvm": ("V", "versions", "node"), "fnm": ("F", "node-versions"), "volta": ("T", "tools", "image", "node"),
      "mise": ("M", "installs", "node"), "asdf": ("A", "installs", "nodejs"), "n": ("P", "n", "versions", "node")}),
    ("linux", {"XDG_DATA_HOME": "D"}, {"fnm": ("D", "fnm", "node-versions"), "mise": ("D", "mise", "installs", "node")}),
])
def test_node_manager_locations_per_platform(monkeypatch, platform, env, tails):
    monkeypatch.setattr(yourself.sys, "platform", platform)
    found = dict(yourself._node_manager_dirs(env))
    for source, tail in tails.items():
        assert found[source].parts[-len(tail):] == tail
    assert ("n" in found) == (platform != "win32")


def test_windows_prefixes_and_header_free_version(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.sys, "platform", "win32")
    make_node(tmp_path / "pf" / "nodejs", windows=True)
    assert yourself._node_prefixes({"ProgramFiles": str(tmp_path / "pf")}) == [tmp_path / "pf" / "nodejs"]
    assert yourself._node_prefixes({}) == []


def test_python_defaults_and_pyenv(tmp_path, monkeypatch):
    monkeypatch.setattr(yourself.sys, "platform", "linux")
    root, config = tmp_path / "pyenv", tmp_path / "cfg"
    for name in ("3.12.3", "3.10.14", "miniconda3-latest", "bad name"):
        (root / "versions" / name).mkdir(parents=True)
    (root / "version").write_text("# global\n3.12.3\n", encoding="utf-8")
    (config / "uv").mkdir(parents=True)
    (config / "uv" / ".python-version").write_text("3.13 # newest\n", encoding="utf-8")
    env = {"PYENV_ROOT": str(root), "XDG_CONFIG_HOME": str(config)}
    assert yourself._pyenv_pythons(env) == ["3.10.14", "3.12.3", "miniconda3-latest"]
    assert yourself._python_default_pins(env) == [{"source": "pyenv", "value": "3.12.3"},
                                                  {"source": "uv", "value": "3.13"}]
    (root / "version").write_text("`rm -rf`\n", encoding="utf-8")
    assert yourself._python_default_pins(env)[0] == {"source": "pyenv", "value": "(unreadable)"}
    empty = {"PYENV_ROOT": str(tmp_path / "none"), "XDG_CONFIG_HOME": str(tmp_path / "none")}
    assert yourself._pyenv_pythons(empty) == [] and yourself._python_default_pins(empty) == []
    monkeypatch.setattr(yourself.sys, "platform", "win32")
    monkeypatch.setattr(yourself.Path, "home", classmethod(lambda cls: tmp_path / "h"))
    assert yourself._pyenv_pythons({}) == []
    (tmp_path / "h" / ".pyenv" / "pyenv-win").mkdir(parents=True)
    (tmp_path / "h" / ".pyenv" / "pyenv-win" / "version").write_text("3.11.9\n", encoding="utf-8")
    (tmp_path / "ap" / "uv").mkdir(parents=True)
    (tmp_path / "ap" / "uv" / ".python-version").write_text("3.12\n", encoding="utf-8")
    assert yourself._python_default_pins({"APPDATA": str(tmp_path / "ap")}) == [
        {"source": "pyenv", "value": "3.11.9"}, {"source": "uv", "value": "3.12"}]


def test_version_pins_from_project_files(tmp_path):
    project = tmp_path / "work" / "app"
    project.mkdir(parents=True)
    (tmp_path / "work" / ".python-version").write_text("3.11\n", encoding="utf-8")  # one level up
    (project / ".nvmrc").write_text("lts/*\n", encoding="utf-8")
    (project / ".node-version").write_text("\n# comment only\n24.1.0\n", encoding="utf-8")
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.3\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[tool.x]\nrequires-python = ">=9"\n[project]\nname = "a"\nrequires-python = ">=3.10"\n', encoding="utf-8")
    (project / "package.json").write_text(json.dumps({"engines": {"node": ">=24 <27"}}), encoding="utf-8")
    (project / ".tool-versions").write_text("python 3.12.3 3.11.9\nnodejs 22.1.0  # lts\nruby 3.3\n", encoding="utf-8")
    assert yourself.version_pins(project) == {
        "python": [{"source": ".python-version", "value": "3.11", "up": 1},
                   {"source": ".venv/pyvenv.cfg", "value": "3.12.3", "up": 0},
                   {"source": "pyproject.toml requires-python", "value": ">=3.10", "up": 0},
                   {"source": ".tool-versions", "value": "3.12.3", "up": 0}],
        "node": [{"source": ".node-version", "value": "24.1.0", "up": 0},
                 {"source": ".nvmrc", "value": "lts/*", "up": 0},
                 {"source": "package.json engines.node", "value": ">=24 <27", "up": 0},
                 {"source": ".tool-versions", "value": "22.1.0", "up": 0}]}
    assert yourself.version_pins(tmp_path / "missing") == {"python": [], "node": []}


def test_version_pins_reject_odd_content_and_directories(tmp_path):
    (tmp_path / ".python-version").write_text("$(touch pwned)\n", encoding="utf-8")
    (tmp_path / ".nvmrc").mkdir()
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = "<script>"\n', encoding="utf-8")
    assert yourself.version_pins(tmp_path) == {
        "python": [{"source": ".python-version", "value": "(unreadable)", "up": 0}], "node": []}
    (tmp_path / "package.json").write_text('{"engines": {"node": 24}}', encoding="utf-8")
    assert yourself.version_pins(tmp_path)["node"] == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_version_pins_do_not_block_on_a_named_pipe(tmp_path):
    os.mkfifo(tmp_path / ".python-version")
    assert yourself.version_pins(tmp_path)["python"] == []


def test_text_lines_and_old_shapes(monkeypatch, capsys, fake_python_packages):
    facts = {"os": {"system": "Linux", "release": "x", "machine": "y"},
             "runtime": {"implementation": "CPython", "version": "3.12", "bits": 64},
             "runtimes": {"playwright": {"path": None, "builds": []}, "uv_pythons": [], "path_pythons": [], "git": None,
                          "pyenv_pythons": ["3.12.3"], "python_defaults": [{"source": "uv", "value": "3.13"}],
                          "node_installs": [
                              {"source": "prefix", "version": "22.23.4", "path": "/a", "on_path": True, "active": True},
                              {"source": "prefix", "version": "24.22.1", "path": "/b", "on_path": False, "active": False},
                              {"source": "path", "version": None, "path": "/c", "on_path": True, "active": False}]},
             "pins": {"node": [{"source": ".nvmrc", "value": "24", "up": 2}], "python": []}}
    text = yourself.to_text(facts)
    assert "pyenv_pythons: 3.12.3\n" in text and "python_defaults: uv=3.13\n" in text
    assert "node_installs: 22.23.4 [prefix, active], 24.22.1 [prefix], ? [path, on PATH]\n" in text
    assert "pin.node: .nvmrc=24 (up 2)\n" in text and "pin.python: (none)\n" in text
    del facts["runtimes"]["node_installs"], facts["runtimes"]["python_defaults"], facts["pins"]
    old = yourself.to_text(facts)
    assert "node_installs" not in old and "python_defaults" not in old and "pin." not in old
    monkeypatch.setattr(yourself, "version_pins", lambda directory: {"python": [], "node": []})
    assert yourself.main(["--no-network", "--no-probes"]) in (0, 2)
    out = capsys.readouterr().out
    assert "node_installs: " in out and "pin.python: (none)" in out and "pin.node: (none)" in out
    assert yourself.main(["--minimal"]) == 0
    assert "node_installs" not in capsys.readouterr().out


@pytest.mark.parametrize("value, ok", [
    ("3.12", True), ("3.12.3", True), ("v24.1.0", True), (">=3.10", True), (">=3.10,<4", True), (">=24 <27", True),
    ("^20.9 || >=22", True), ("lts/*", True), ("lts/iron", True), ("latest", True), ("system", True),
    ("3.12.3:3.11.8", True), ("miniconda3-latest", True), ("pypy3.10-7.3.15", True), ("3.*", True),
    ("<script>", False), ("<b>1</b>", False), ("$(touch x)", False), ("`rm -rf`", False), ("*", False),
    ("", False), ("3." + "9" * 70, False), ("3.12; rm", False), ("python", False)])
def test_pin_values_are_version_specs_not_free_text(value, ok):
    assert yourself._pin_ok(value) is ok


# ── runtime metadata reads stay bounded and skip non-regular files ────────────────────────────

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
@pytest.mark.parametrize("relative", [
    ".python-version", ".node-version", ".nvmrc", ".tool-versions",
    "package.json", "pyproject.toml", ".venv/pyvenv.cfg", "include/node/node_version.h",
])
def test_runtime_metadata_fifo_is_skipped_without_blocking(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    # A subprocess timeout makes a regression fail instead of hanging the suite.
    subprocess.run([sys.executable, "-c", """
import sys
from pathlib import Path
import yourself
root = Path(sys.argv[1])
assert yourself.version_pins(root) == {"python": [], "node": []}
assert yourself._node_header_version(root / "bin" / "node") is None
""", str(tmp_path)], cwd=Path(yourself.__file__).parent, check=True, timeout=5)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_file_replaced_by_fifo_between_stat_and_open_is_skipped(tmp_path):
    path = tmp_path / ".python-version"
    path.write_text("3.12", encoding="utf-8")
    subprocess.run([sys.executable, "-c", """
import os, sys
from pathlib import Path
import yourself
path = Path(sys.argv[1])
original_open = os.open
opened = []
def replace_and_open(target, flags):
    path.unlink()
    os.mkfifo(path)
    fd = original_open(target, flags)
    opened.append(fd)
    return fd
yourself.os.open = replace_and_open
assert yourself._read_pin(path) is None
assert len(opened) == 1
try:
    os.fstat(opened[0])
except OSError:
    pass
else:
    raise AssertionError("descriptor leaked")
""", str(path)], cwd=Path(yourself.__file__).parent, check=True, timeout=5)


@pytest.mark.parametrize("encoding", ["utf-8", "ascii"])
def test_runtime_text_reads_only_requested_bytes(tmp_path, monkeypatch, encoding):
    path = tmp_path / "large"
    path.write_bytes(b"3.12\n" + b" " * 1048576)
    original_fdopen = os.fdopen
    reads = []

    class CheckedStream:
        def __init__(self, *args, **kwargs):
            self.stream = original_fdopen(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size=-1):
            reads.append(size)
            assert size == 512
            return self.stream.read(size)

    monkeypatch.setattr(os, "fdopen", CheckedStream)
    assert len(yourself._read_runtime_text(path, 512, encoding=encoding)) == 512
    assert reads == [512]


def test_runtime_text_preserves_regular_symlinks(tmp_path):
    path = tmp_path / "version"
    path.write_text("3.12\n", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert yourself._read_pin(link) == "3.12"


@pytest.mark.parametrize("relative, prefix, suffix, limit", [
    (".venv/pyvenv.cfg", "", "version = 3.12\n", 4096),
    (".tool-versions", "", "python 3.12\n", 4096),
    ("pyproject.toml", "[project]\n", 'requires-python = ">=3.12"\n', 262144),
    ("include/node/node_version.h", "", "#define NODE_MAJOR_VERSION 24\n#define NODE_MINOR_VERSION 1\n#define NODE_PATCH_VERSION 0\n", 65536),
])
def test_runtime_metadata_does_not_parse_beyond_limit(tmp_path, relative, prefix, suffix, limit):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prefix + "\n" * limit + suffix, encoding="utf-8")
    assert yourself.version_pins(tmp_path) == {"python": [], "node": []}
    assert yourself._node_header_version(tmp_path / "bin" / "node") is None


def test_package_json_limit_and_invalid_utf8(tmp_path):
    path = tmp_path / "package.json"
    data = b'{"engines":{"node":">=24"}}'
    path.write_bytes(data + b" " * (262143 - len(data)))
    assert yourself.version_pins(tmp_path)["node"][0]["value"] == ">=24"
    path.write_bytes(data + b" " * (262144 - len(data)))
    assert yourself.version_pins(tmp_path)["node"] == []
    path.write_bytes(data + b"\xff")
    assert yourself.version_pins(tmp_path)["node"] == []


# ── runtime_facts: Playwright builds, uv / PATH Pythons, git; --reach and runtime lines ───────

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


def test_cli_reach_and_runtime_lines(monkeypatch, capsys, fake_python_packages):
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
    monkeypatch.setattr(yourself.shutil, 'which', lambda cmd: '/trusted/' + cmd if cmd in ('python3.8', 'python3.15') else None)
    assert yourself.runtime_facts(environ={})['path_pythons'] == ['3.8', '3.15']


# ── python_packages: counts per installer ─────────────────────────────────────────────────────

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
