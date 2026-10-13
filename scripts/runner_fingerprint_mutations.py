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
    ("imagedata_skip_too_large",
     'if len(raw) > _IMAGEDATA_MAX_BYTES:',
     'if False and len(raw) > _IMAGEDATA_MAX_BYTES:',
     ["test_imagedata_too_large"]),
    ("imagedata_drop_non_dict_array_items",
     '            else:\n                entries.append({"value_type": type(item).__name__})',
     '            else:\n                pass',
     ["test_imagedata_array_non_dict_element"]),
    ("imds_proxy_handler_removed",
     'urllib.request.ProxyHandler({}), NoRedirect())',
     'NoRedirect())',
     ["test_imds_opener_disables_proxy_and_redirects"]),
    ("github_summary_write_noop",
     '                        stream.write("\\n## Runner fingerprint\\n\\n")\n                        stream.write(runner_fingerprint_summary(fingerprint))',
     '                        pass',
     ["test_github_step_summary_success_contents"]),
    ("imds_compute_non_dict_leak",
     '    if not isinstance(compute, dict):\n        return {}',
     '    if not isinstance(compute, dict):\n        return data',
     ["test_sanitize_imds_non_dict_compute", "test_imds_compute_string_no_leak"]),
    ("imds_invalid_json_ok",
     '    if not loaded["ok"]:\n        return loaded',
     '    if not loaded["ok"]:\n        return _soft_ok({})',
     ["test_imds_invalid_json_from_wire"]),
    ("tool_cache_keep_files",
     '            if not tool_dir.is_dir():\n                continue',
     '            if not tool_dir.is_dir():\n                tools[name] = []\n                continue',
     ["test_tool_cache_skips_plain_files"]),
    ("github_summary_force_open",
     'if summary_path:',
     'if True:',
     ["test_github_summary_unset_env"]),
    ("imds_unlimited_read",
     'raw = response.read(_IMDS_READ_BYTES)',
     'raw = response.read()',
     ["test_imds_read_capped_at_64k"]),
    ("imds_redirect_enabled",
     '        def redirect_request(self, req, fp, code, msg, headers, newurl):\n            return None',
     '        def redirect_request(self, req, fp, code, msg, headers, newurl):\n            return urllib.request.Request(newurl)',
     ["test_imds_redirect_request_returns_none"]),
    ("imds_metadata_header_removed",
     'headers={"Metadata": "true"}',
     'headers={}',
     ["test_imds_sends_metadata_header"]),
    ("imds_read_bytes_wrong",
     '_IMDS_READ_BYTES = 65536',
     '_IMDS_READ_BYTES = 32768',
     ["test_imds_read_bytes_constant", "test_imds_read_capped_at_64k"]),
    ("dpkg_sha256_broken",
     'digest = hashlib.sha256(text.encode("utf-8")).hexdigest()',
     'digest = "0" * 64',
     ["test_dpkg_sha256_known_input"]),
    ("cloud_guess_aws_disabled",
     '    elif "amazon" in blob or "ec2" in blob:\n        guess = "aws"',
     '    elif False:\n        guess = "aws"',
     ["test_cloud_guess_aws_from_dmi"]),
    ("cloud_guess_gcp_disabled",
     '    elif "google" in blob:\n        guess = "gcp"',
     '    elif False:\n        guess = "gcp"',
     ["test_cloud_guess_gcp_from_dmi"]),
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
