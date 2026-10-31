"""Stdlib-only diagnostic regression: fake observations, never real probes."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import yourself


class DiagnoseRuntimeTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.system = stack.enter_context(patch.object(yourself.platform, 'system', return_value='Linux'))
        # create=False checks that the API exists on the tested runtime floor.
        self.release = stack.enter_context(patch.object(yourself.platform, 'freedesktop_os_release',
            return_value={'ID': 'fake-linux', 'VERSION_ID': '1', 'PRIVATE': 'not-reported'}))
        stack.enter_context(patch.object(yourself, 'collect', return_value={
            'schema_version': 1,
            'os': {'system': 'Linux', 'release': 'fake', 'machine': 'fake'},
            'runtime': {'implementation': 'CPython', 'version': '3.11.0', 'bits': 64},
            'host': None, 'directory': None,
        }))
        stack.enter_context(patch.object(yourself.shutil, 'which', return_value='/fake/tool'))
        self.command_run = stack.enter_context(patch.object(yourself.subprocess, 'run',
            side_effect=AssertionError('tool execution is not allowed')))
        self.open = stack.enter_context(patch('builtins.open',
            side_effect=AssertionError('real file reads are not allowed')))
        stack.enter_context(patch.object(Path, 'read_text',
            side_effect=AssertionError('real file reads are not allowed')))
        self.workspace = stack.enter_context(patch.object(yourself, 'workspace_facts',
            return_value={'markers': [], 'storage': None, 'errors': []}))
        for name in ('session_facts', 'resource_facts', 'runtime_facts'):
            stack.enter_context(patch.object(yourself, name, return_value=None))
        stack.enter_context(patch.object(yourself, 'python_packages', return_value={
            'count': 0, 'installers': {}, 'complete': True, 'errors': []}))
        for name in ('probe_all', 'network_facts', 'container_facts', 'hosts_facts', 'listener_ports'):
            stack.enter_context(patch.object(yourself, name,
                side_effect=AssertionError('unexpected environment probe')))

    def test_linux_diagnose_reads_only_mocked_release_fields(self):
        report = yourself.diagnose(commands=['python', 'git', 'pip', 'uv'])
        self.assertEqual(report['os_release'], {'ID': 'fake-linux', 'VERSION_ID': '1'})
        self.assertTrue(all(row['available'] and row['status'] == 'not_requested'
                            for row in report['commands']))
        self.assertNotIn('not-reported', yourself.to_json(report))
        self.release.assert_called_once_with()
        self.workspace.assert_not_called()
        self.command_run.assert_not_called()
        self.open.assert_not_called()

    def test_unreadable_release_is_not_a_crash_or_a_message_leak(self):
        self.release.side_effect = OSError('private filesystem location')
        report = yourself.diagnose(commands=[])
        self.assertIsNone(report['os_release'])
        self.assertNotIn('private filesystem', yourself.to_json(report))
        self.command_run.assert_not_called()

    def test_non_linux_does_not_request_os_release(self):
        for system in ('Darwin', 'Windows'):
            with self.subTest(system=system):
                self.system.return_value = system
                report = yourself.diagnose(commands=[])
                self.assertIsNone(report['os_release'])
        self.release.assert_not_called()
        self.command_run.assert_not_called()

    def test_cli_exercises_diagnose_without_real_observations(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = yourself.main(['--no-network', '--no-probes', '--format', 'json'])
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report['os_release'], {'ID': 'fake-linux', 'VERSION_ID': '1'})
        self.assertEqual(report['python_packages']['count'], 0)
        self.assertTrue(all(row['status'] == 'not_requested' for row in report['commands']))
        self.release.assert_called_once_with()
        self.workspace.assert_called_once_with('.')
        self.command_run.assert_not_called()
        self.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
