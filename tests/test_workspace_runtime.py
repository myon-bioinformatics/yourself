"""Dependency-free behavioral checks also run under python -S."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yourself


class WorkspaceRuntimeTests(unittest.TestCase):
    def test_alias_and_manifest_spelling(self):
        for spelling, expected in [('justfile', ['justfile']), ('Justfile', ['justfile']),
                                   ('pixi.toml', ['pixi.toml']), ('Pixi.toml', [])]:
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / spelling).touch()
                self.assertEqual(yourself.workspace_facts(tmp)['markers'], expected)

    def test_directory_marker_and_presence_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.venv').mkdir()
            self.assertEqual(yourself.workspace_facts(tmp)['markers'], ['.venv'])
        with patch.object(yourself.shutil, 'which', return_value='/trusted/tool'), \
             patch.object(yourself.subprocess, 'run', side_effect=AssertionError('execution')):
            rows = yourself.command_inventory(['poetry', 'pixi', 'swift', 'rye'], versions=True)
        self.assertTrue(all(row['status'] == 'presence_only' for row in rows))
