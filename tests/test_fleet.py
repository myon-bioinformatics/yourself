"""Fleet contracts through the parent's shared checks (vendor/fleet_check.py): locks, pins, benchmark, Node entry."""
import json
import os

import pytest

from vendor import fleet_check as fleet

ROOT = fleet.repo_root(__file__)
PARENT_WORKFLOW_PIN = "9e3da7ae1846e9604ebc24bb5178f7de094a4e77"
# Node sources and the shared checks stay on accepted upstream commits with exact bytes.
ACCEPTED = {
    "vendor/xprobe.py": "1ffbb7b984075c683690bbe4e9deb06199cdfe94",
    "vendor/py_launch.cjs": "384f5dd532076062f7833038764e9a62322c387c",
    "vendor/fleet_check.py": "81f6aea6231d4490e581443fdeb43a09e6018ff0",
}
TOOL, ENV_VAR = "yourself.py", "YOURSELF_PYTHON"
CASES = {
    "help": (["--help"], 0),
    "bogus": (["--bogus"], 2),
    "minimal-json": (["--directory", "no-such-dir", "--minimal", "--no-network", "--no-probes", "--format", "json"], 0),
}
needs_node = pytest.mark.skipif(fleet.node() is None, reason="Node 26+ not available (set NODE)")


def test_vendor_lock_matches_checked_in_bytes_and_accepted_commits():
    fleet.check_lock(ROOT, commits=ACCEPTED, sources={
        "vendor/py_launch.cjs": (fleet.PARENT, "py_launch.cjs"),
        "vendor/fleet_check.py": (fleet.PARENT, "fleet_check.py"),
        "vendor/myon-bioinformatics-LICENSE": (fleet.PARENT, "LICENSE"),
    })


def test_parent_workflow_references_use_reviewed_rollout_commit():
    assert set(fleet.parent_refs(ROOT)) == {PARENT_WORKFLOW_PIN}


@pytest.mark.parametrize("optimized", [False, True], ids=["normal", "optimized"])
@pytest.mark.parametrize("mismatch", [False, True], ids=["same", "mismatch"])
def test_benchmark_parity_in_both_interpreter_modes(optimized, mismatch):
    patch = "g['sample'] = lambda *args: ['different']" if mismatch else ""
    result = fleet.run_main(ROOT, "scripts/benchmark_directory_sample.py", patch, optimized=optimized)
    if mismatch:
        assert (result.returncode, result.stdout) == (1, ""), result.stderr
        assert "result mismatch" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("{"), result.stdout


def test_ci_provisions_node_before_tests():
    # The guard is run in bash; on Windows a bare "bash" is the WSL launcher, so that lane checks the step order only.
    job = fleet.workflow_job(ROOT, "test", node_floor=None if os.name == "nt" else 26)
    assert job.index("actions/setup-node@") < job.index("node_major=") < job.index("name: Test with branch coverage")


def test_workflow_actions_are_pinned_by_full_sha_with_version():
    assert fleet.check_actions(ROOT)


def test_no_per_tool_wrapper_and_readme_documents_the_direct_command():
    assert not (ROOT / "yourself.cjs").exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"node vendor/py_launch.cjs --env-var {ENV_VAR} -- {TOOL}" in readme
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert pkg["engines"]["node"] == ">=26"


@needs_node
@pytest.mark.parametrize("args,expected_rc", CASES.values(), ids=CASES)
def test_node_matches_python(args, expected_rc):
    via_node, via_python = fleet.node_parity(ROOT, TOOL, ENV_VAR, *args)
    assert via_python[0] == expected_rc
    assert via_node == via_python


@needs_node
def test_vendored_launcher_keeps_its_contract():
    fleet.launcher_contract(ROOT, ENV_VAR)
