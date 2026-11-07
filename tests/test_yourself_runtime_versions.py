"""Node installs and Python/Node version pins: directory names, headers and small files only."""
import json
import os

import pytest

import yourself


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
