#!/usr/bin/env python3
"""Mutation checks for runner fingerprint helpers; expects zero survivors."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "yourself.py"
TESTS = ROOT / "tests" / "test_runner_fingerprint.py"

# Each mutation is (name, old, new, pytest node ids that must fail if mutation survives)
MUTATIONS = [
    ("imds_default_on", 'if imds:\n        fingerprint["imds"]', 'fingerprint["imds"]',
     ["test_imds_not_called_by_default"]),
    ("imagedata_oserror_skip",
     'except OSError as error:\n            return _soft_err(type(error).__name__, path=str(path))',
     'except OSError as error:\n            continue',
     ["test_imagedata_read_oserror"]),
    ("tool_cache_no_fallback",
     'if not tool_cache["ok"]:\n        tool_cache = _env_soft("AGENT_TOOLSDIRECTORY", environ)',
     'if False and not tool_cache["ok"]:\n        tool_cache = _env_soft("AGENT_TOOLSDIRECTORY", environ)',
     ["test_agent_toolsdirectory_fallback"]),
    ("summary_write_exit2",
     'print("GITHUB_STEP_SUMMARY: " + type(error).__name__, file=sys.stderr)',
     'print(type(error).__name__, file=sys.stderr)\n                    return 2',
     ["test_github_summary_write_failure_soft"]),
    ("dpkg_list_fail_hard",
     '"list_path": _soft_err(type(error).__name__)',
     'raise OSError("mutated")',
     ["test_dpkg_list_write_error"]),
    ("waagent_azure_guess",
     'if waagent_present:\n        evidence.append("path:/var/lib/waagent")',
     'if False and waagent_present:\n        evidence.append("path:/var/lib/waagent")',
     ["test_cloud_guess_waagent_only"]),
    ("os_release_skip_comments",
     'if "=" in line and not line.startswith("#"):',
     'if "=" in line:',
     ["test_os_release_skips_comment_lines"]),
    ("dpkg_oserror_raise",
     '    except OSError as error:\n        return _soft_err(type(error).__name__)\n    if done.returncode:',
     '    except OSError as error:\n        raise\n    if done.returncode:',
     ["test_dpkg_oserror"]),
    ("dpkg_count_blank_lines",
     'lines = [line for line in text.splitlines() if line.strip()]',
     'lines = [line for line in text.splitlines() if line]',
     ["test_dpkg_count_and_list_file"]),
    ("imds_drop_compute_allowlist",
     '_IMDS_COMPUTE_ALLOW = ("osType", "location", "vmSize", "sku", "offer", "version")',
     '_IMDS_COMPUTE_ALLOW = ("subscriptionId", "vmId", "name", "tags")',
     ["test_imds_opt_in_allowlist"]),
    ("summary_no_pretty_fallback", 'parsed.get("PRETTY_NAME") or "%s %s"',
     'parsed.get("PRETTY_NAME") or "?"',
     ["test_summary_os_release_id_fallback"]),
    ("dmi_empty_evidence", 'if item.get("ok") and item.get("value"):',
     'if item.get("ok"):',
     ["test_dmi_empty_values_not_evidence"]),
    ("waagent_exists_oserror",
     '    except OSError as error:\n        waagent_present = None',
     '    except OSError as error:\n        raise',
     ["test_waagent_exists_oserror"]),
    ("summary_imds_ok", 'lines.append("imds: ok"',
     'lines.append("imds: skipped"',
     ["test_summary_human_readable"]),
    ("runner_flags_silent",
     'if runner_flags and not args.runner_env:\n        print("runner-env flags require --runner-env", file=sys.stderr)\n        return 2',
     'if False and runner_flags and not args.runner_env:\n        print("runner-env flags require --runner-env", file=sys.stderr)\n        return 2',
     ["test_runner_env_flags_require_runner_env"]),
]


def _run_tests(mutated_path, nodeids):
    nodes = ["%s::%s" % (TESTS, node) for node in nodeids]
    code = (
        "import importlib.util\n"
        "import sys\n"
        "spec = importlib.util.spec_from_file_location('yourself', %r)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['yourself'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "import pytest\n"
        "raise SystemExit(pytest.main(['-q'] + %r))\n"
    ) % (str(mutated_path), nodes)
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)


def main():
    original = SOURCE.read_text(encoding="utf-8")
    rows = []
    survivors = []
    for name, old, new, nodeids in MUTATIONS:
        if old not in original:
            rows.append((name, "SKIP", "pattern missing"))
            survivors.append(name)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "yourself.py"
            target.write_text(original.replace(old, new, 1), encoding="utf-8")
            proc = _run_tests(target, nodeids)
        status = "KILL" if proc.returncode != 0 else "SURVIVE"
        rows.append((name, status, proc.returncode))
        if status == "SURVIVE":
            survivors.append(name)
    print("| mutation | result | exit |")
    print("| --- | --- | --- |")
    for name, status, code in rows:
        print("| %s | %s | %s |" % (name, status, code))
    print("")
    print("survivors: %d" % len(survivors))
    if survivors:
        for name in survivors:
            print("  -", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
