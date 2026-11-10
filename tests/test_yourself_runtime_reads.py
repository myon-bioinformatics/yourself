"""Runtime metadata reads stay bounded and never consume non-regular files."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import yourself


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
