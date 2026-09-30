import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yourself

ROOT = Path(__file__).resolve().parents[1]


class FactsTests(unittest.TestCase):
    def test_collect_default_without_sensitive_data_or_io(self):
        with patch.object(Path, "open", side_effect=AssertionError("file read")), \
             patch.object(yourself.os, "scandir", side_effect=AssertionError("scan")), \
             patch.object(yourself.platform, "node", side_effect=AssertionError("host")):
            facts = yourself.collect()
        self.assertEqual(facts["schema_version"], 1)
        self.assertIsNone(facts["host"])
        self.assertIsNone(facts["directory"])
        self.assertEqual(facts["runtime"]["version"], yourself.platform.python_version())
        self.assertEqual(set(facts), {"schema_version", "os", "runtime", "host", "directory"})

    def test_host_opt_in(self):
        with patch.object(yourself.platform, "node", return_value="test-host"):
            self.assertEqual(yourself.collect(include_host=True)["host"], {"name": "test-host"})

    def test_shallow_counts_and_sorted_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z.txt").write_text("do not inspect contents")
            (root / ".hidden").write_text("secret")
            (root / "a").mkdir()
            (root / "a" / "nested").write_text("nested")
            with patch.object(Path, "open", side_effect=AssertionError("content read")):
                facts = yourself.collect(directory=root, sample_size=2)
            summary = facts["directory"]
            self.assertEqual(summary["counts"], {"files": 2, "directories": 1,
                                                "symlinks": 0, "other": 0})
            self.assertEqual(summary["sample"], [".hidden", "a"])
            self.assertEqual(summary["errors"], [])
            self.assertEqual(yourself.directory_summary(root, sample_size=0)["sample"], [])

    def test_symlink_count_and_root_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            self.assertEqual(yourself.directory_summary(root)["counts"]["symlinks"], 1)
            with self.assertRaises(ValueError):
                yourself.directory_summary(link)

    def test_invalid_roots_and_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sample in (-1, True, 1.5):
                with self.assertRaises(ValueError):
                    yourself.directory_summary(tmp, sample_size=sample)
            with self.assertRaises(FileNotFoundError):
                yourself.directory_summary(Path(tmp) / "missing")
            path = Path(tmp) / "file"
            path.touch()
            with self.assertRaises(ValueError):
                yourself.directory_summary(path)
            with patch.object(yourself.os, "scandir", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    yourself.directory_summary(tmp)

    def test_json_deterministic_and_round_trip(self):
        facts = yourself.collect()
        result = yourself.to_json(facts)
        self.assertEqual(json.loads(result), facts)
        self.assertEqual(result, yourself.to_json(dict(reversed(list(facts.items())))))
        with self.assertRaises(ValueError):
            yourself.to_json({"value": float("nan")})

    def test_markdown_controls_html_and_table_delimiters(self):
        facts = {"os": {"system": "x|`<script>\n\x00\x1b"}, "runtime": {}, "host": None,
                 "directory": None}
        result = yourself.to_markdown(facts)
        self.assertIn("&#124;", result)
        self.assertIn("&#96;", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("\\u0000", result)
        self.assertNotIn("\x1b", result)
        self.assertNotIn("<script>", result)
        self.assertEqual(result, yourself.to_markdown(facts))


class IntegrationTests(unittest.TestCase):
    def test_vendored_module_import_is_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(ROOT / "yourself.py", Path(tmp) / "vendor_self.py")
            proc = subprocess.run([sys.executable, "-B", "-c",
                "import vendor_self as y; assert y.collect()['runtime']['version']; "
                "assert y.to_markdown(y.collect()).startswith('# Environment')"],
                cwd=tmp, capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")

    def test_stdlib_only_no_execution_network_imports(self):
        tree = ast.parse((ROOT / "yourself.py").read_text())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module.split(".")[0])
        for name in modules:
            self.assertIn(name, sys.stdlib_module_names)
        self.assertFalse(set(modules) & {"subprocess", "socket", "urllib", "http"})

    def test_cli_from_other_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, str(ROOT / "scripts/yourself_cli.py")]
            proc = subprocess.run(command + ["--format", "json", "--directory", tmp],
                                  cwd=tmp, capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            facts = json.loads(proc.stdout)
            self.assertEqual(facts["directory"]["counts"]["files"], 0)
            bad = subprocess.run(command + ["--directory", str(Path(tmp) / "missing")],
                                 cwd=tmp, capture_output=True, text=True, timeout=20)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(bad.stdout, "")
