"""yourself.cjs: Node entry parity with yourself.py through the vendored shared py_launch.cjs."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = "yourself"
ENV_VAR = "YOURSELF_PYTHON"
CASES = [{"id": "help", "args": ["--help"], "rc": 0}, {"id": "bogus", "args": ["--bogus"], "rc": 2}, {"id": "minimal-json", "args": ["--directory", "no-such-dir", "--minimal", "--no-network", "--no-probes", "--format", "json"], "rc": 0}]


def _node() -> str | None:
    node = os.environ.get("NODE") or shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"v(\d+)", out.strip())
    return node if match and int(match.group(1)) >= 24 else None


NODE = _node()
needs_node = pytest.mark.skipif(NODE is None, reason="Node 24+ not available (set NODE)")


def test_ci_provisions_node_instead_of_skipping_node_parity():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        assert NODE is not None, "CI must configure Node >=24 before pytest"

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = workflow.split("\n  test:\n", 1)[1].split("\n  failure-identity:", 1)[0]
    assert "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1" in job
    assert "node-version: '26'" in job
    assert job.index("actions/setup-node@") < job.index("name: Test with branch coverage")


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env[ENV_VAR] = sys.executable
    env.pop("PY_LAUNCH_ERROR_FORMAT", None)
    return env


def _run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    p = subprocess.run(argv, cwd=cwd, env=_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, p.stdout, p.stderr


def _parity(root: Path, args: list[str]) -> tuple[tuple[int, str, str], tuple[int, str, str]]:
    via_node = _run([NODE, str(root / f"{TOOL}.cjs"), *args], root)
    via_python = _run([sys.executable, "-S", str(ROOT / f"{TOOL}.py"), *args], root)
    return via_node, via_python


def test_vendored_launcher_matches_lock():
    lock = json.loads((ROOT / "vendor.lock.json").read_text(encoding="utf-8"))
    entry = next(f for f in lock["files"] if f["destination"] == "vendor/py_launch.cjs")
    assert entry["repository"] == "myon-bioinformatics/myon-bioinformatics"
    assert entry["source"] == "py_launch.cjs"
    data = (ROOT / "vendor" / "py_launch.cjs").read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    assert blob == entry["blob_sha"]


def test_entry_uses_shared_launcher_only():
    text = (ROOT / f"{TOOL}.cjs").read_text(encoding="utf-8")
    assert 'require("./vendor/py_launch.cjs")' in text
    assert "child_process" not in text
    assert f'"{TOOL}.py"' in text
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert pkg["engines"]["node"] == ">=24"


@needs_node
@pytest.mark.parametrize("args,expected_rc", [(c["args"], c["rc"]) for c in CASES],
                         ids=[c["id"] for c in CASES])
def test_node_matches_python(args, expected_rc):
    via_node, via_python = _parity(ROOT, args)
    assert via_python[0] == expected_rc
    assert via_node == via_python


@needs_node
def test_missing_interpreter_is_launcher_failure(tmp_path):
    env = _env()
    env[ENV_VAR] = str(tmp_path / "no-such-python")
    p = subprocess.run([NODE, str(ROOT / f"{TOOL}.cjs"), "--help"], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2
    assert p.stdout == ""


@needs_node
@pytest.mark.parametrize("old,new", [
    (f'"{TOOL}.py"', '"missing_tool.py"'),
    ("main(PY_SCRIPT, PY_LAUNCH);", "main(PY_SCRIPT, { ...PY_LAUNCH, argv: [] });"),
    ("noSite: true", "noSite: true, isolated: true, argv: ['--help']"),
])
def test_mutated_entry_breaks_parity(tmp_path, old, new):
    (tmp_path / "vendor").mkdir()
    shutil.copy(ROOT / "vendor" / "py_launch.cjs", tmp_path / "vendor" / "py_launch.cjs")
    shutil.copy(ROOT / f"{TOOL}.py", tmp_path / f"{TOOL}.py")
    text = (ROOT / f"{TOOL}.cjs").read_text(encoding="utf-8")
    assert old in text
    (tmp_path / f"{TOOL}.cjs").write_text(text.replace(old, new), encoding="utf-8")
    assert any(_parity(tmp_path, c["args"])[0] != _parity(ROOT, c["args"])[1] for c in CASES)


@needs_node
def test_node_floor_rejects_old_versions():
    script = (
        "const m = require(process.argv[1]);"
        "const bad = ['v22.14.0', '18.0.0', 'junk'].filter((v) => { try { m.assertNodeFloor(v); return true; } catch (e) { return !/requires Node 24\\+/.test(e.message); } });"
        "m.assertNodeFloor('v24.0.0');"
        "process.stdout.write(JSON.stringify({bad, min: m.NODE_MIN_MAJOR}));"
    )
    p = subprocess.run([NODE, "-e", script, str(ROOT / f"{TOOL}.cjs")],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"bad": [], "min": 24}


@needs_node
@pytest.mark.skipif(os.name == "nt", reason="POSIX signal forwarding contract")
@pytest.mark.parametrize("json_errors", [False, True], ids=["text", "json"])
def test_entry_forwards_repeated_signals_until_child_exits(tmp_path, json_errors):
    """Exercise the real entry and vendored launcher, with ack-driven signals."""
    import selectors
    import signal

    (tmp_path / "vendor").mkdir()
    shutil.copy(ROOT / "vendor" / "py_launch.cjs", tmp_path / "vendor" / "py_launch.cjs")
    shutil.copy(ROOT / f"{TOOL}.cjs", tmp_path / f"{TOOL}.cjs")
    (tmp_path / f"{TOOL}.py").write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGINT, lambda *_: print('ACK', flush=True))\n"
        "print('READY', flush=True)\n"
        "while True: time.sleep(60)\n",
        encoding="utf-8",
    )
    env = _env()
    env["PY_LAUNCH_ERROR_FORMAT"] = "json" if json_errors else ""
    child = subprocess.Popen(
        [NODE, str(tmp_path / f"{TOOL}.cjs")],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            for expected, next_signal in [
                (b"READY\n", signal.SIGINT),
                (b"ACK\n", signal.SIGINT),
                (b"ACK\n", signal.SIGTERM),
            ]:
                assert selector.select(timeout=5), "child did not acknowledge forwarded signal"
                assert child.stdout.readline() == expected
                child.send_signal(next_signal)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 2
        assert stdout == b""
        if json_errors:
            error = json.loads(stderr)
            assert error["code"] == "ESIGNAL"
            assert error["exit_code"] == 2
            assert "SIGTERM" in error["message"]
        else:
            assert b"terminated by signal SIGTERM" in stderr
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.communicate(timeout=5)


@needs_node
def test_entry_no_site_also_protects_interpreter_probe(tmp_path):
    marker = tmp_path / "site-hook-ran"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
        encoding="utf-8",
    )
    env = _env()
    env["PYTHONPATH"] = str(tmp_path)
    result = subprocess.run(
        [NODE, str(ROOT / f"{TOOL}.cjs"), "--help"], env=env,
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0
    assert not marker.exists(), "noSite must cover interpreter discovery too"
