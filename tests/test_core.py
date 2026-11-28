"""Core report API: collect, directory_summary, to_json / to_markdown, diagnose,
command_inventory, workspace_facts and listener_ports.

The unittest.TestCase classes also run without pytest or site-packages
(`python -S -m unittest discover -s tests -p test_core.py`, runtime-compatibility.yml).
"""

import importlib.util
import ast, io, json, random, shutil, subprocess, sys, tempfile, unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    import pytest
except ImportError:  # `python -S` (no site-packages): only the unittest.TestCase classes below run.
    class _NoPytest:  # pytest-style tests stay defined but are never collected by unittest
        def __getattr__(self, name): return self
        def __call__(self, *args, **kwargs):
            return args[0] if len(args) == 1 and not kwargs and callable(args[0]) else self
    pytest = _NoPytest()

import yourself


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_cli_args = load_vendored("yourself_cli_args", "vendor/cli_args.py")
Command = _cli_args.Command
run_cli = _cli_args.run_cli
run_command = _cli_args.run_command


# ── collect / directory_summary / to_json / to_markdown (stdlib only) ─────────────────────────

ROOT = Path(__file__).resolve().parents[1]


def run_copy(directory, module, code):
    """Copy yourself.py alone into ``directory`` as ``<module>.py``; run ``code`` there in a fresh interpreter."""
    shutil.copy(ROOT / "yourself.py", Path(directory) / (module + ".py"))
    return run_command(Command((sys.executable, "-B", "-c", code)), cwd=directory, timeout=20)


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
            proc = run_copy(tmp, "vendor_self",
                "import vendor_self as y; assert y.collect()['runtime']['version']; "
                "assert y.to_markdown(y.collect()).startswith('# Environment')")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")

    def test_stdlib_only_no_network_imports(self):
        tree = ast.parse((ROOT / "yourself.py").read_text(encoding="utf-8"))
        # socket is allowed only inside the explicit opt-in reachability helpers (--reach).
        opt_in = {"reach", "_reach_one", "_connect_via"}
        allowed = {id(n) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name in opt_in
                   for n in ast.walk(f)}
        self.assertTrue(allowed)
        modules = []
        for node in ast.walk(tree):
            if id(node) in allowed and isinstance(node, (ast.Import, ast.ImportFrom)):
                self.assertTrue(all(a.name in ("socket", "ssl", "time", "base64") for a in node.names))
                continue
            if isinstance(node, ast.Import):
                modules.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module.split(".")[0])
        stdlib_names = getattr(sys, "stdlib_module_names", None)
        if stdlib_names is not None:
            for name in modules:
                self.assertIn(name, stdlib_names)
        self.assertFalse(set(modules) & {"socket", "http"})

    def test_cli_from_other_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = ROOT / "scripts/yourself_cli.py"
            proc = run_cli(script, "--format", "json", "--directory", tmp, cwd=tmp, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            facts = proc.json()
            self.assertEqual(facts["directory"]["counts"]["files"], 0)
            bad = run_cli(script, "--directory", Path(tmp) / "missing", cwd=tmp, timeout=20)
            self.assertEqual(bad.returncode, 2)
            self.assertEqual(bad.stdout, "")


# ── workspace markers and presence-only tools (stdlib only) ───────────────────────────────────

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


# ── diagnose() from fake observations, never real probes (stdlib only) ────────────────────────

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
        for name in ('session_facts', 'resource_facts', 'runtime_facts', 'version_pins'):
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


# ── diagnose, command_inventory, workspace_facts, listener_ports ──────────────────────────────

def test_default_diagnostics_never_execute_or_connect(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('default diagnosis executed a command')
    monkeypatch.setattr(yourself.subprocess, 'run', forbidden)
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: None)
    report = yourself.diagnose()
    assert report['host'] is None
    assert report['workspace'] is None
    assert report['listeners'] is None
    assert {t['name'] for t in report['commands']} >= {'docker', 'goma', 'gomacc', 'python'}
    assert next(t for t in report['commands'] if t['name'] == 'python')['available']
    assert 'repository' not in report
    assert 'Tools' in yourself.to_markdown(report)


@pytest.mark.parametrize('output,code,status,version', [
    (b'git version 2.47.1\nsecret=sentinel-secret', 0, 'measured', '2.47.1'),
    (b'sentinel-secret', 0, 'unrecognized_version', None),
    (b'version 1.2.3 sentinel-secret', 2, 'nonzero_exit', None),
])
def test_fixed_version_argv_and_no_output_leak(monkeypatch, output, code, status, version):
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: '/trusted/git')
    def run(argv, **kwargs):
        assert argv == ['/trusted/git', '--version']
        assert kwargs['shell'] is False
        assert kwargs['stdin'] == subprocess.DEVNULL
        assert kwargs['timeout'] == 1
        assert 'cwd' not in kwargs
        return SimpleNamespace(stdout=output, stderr=b'error=sentinel-secret', returncode=code)
    monkeypatch.setattr(yourself.subprocess, 'run', run)
    rows = yourself.command_inventory(['git'], versions=True, timeout=1)
    assert rows == [{'name': 'git', 'available': True, 'version': version, 'status': status}]
    assert 'sentinel-secret' not in json.dumps(rows)
    assert '/trusted' not in json.dumps(rows)


def test_node_v_prefixed_version_is_normalized(monkeypatch):
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: '/trusted/node')
    def run(argv, **kwargs):
        assert argv == ['/trusted/node', '--version']
        return SimpleNamespace(stdout=b'v22.14.0\\n', stderr=b'', returncode=0)
    monkeypatch.setattr(yourself.subprocess, 'run', run)
    assert yourself.command_inventory(['node'], versions=True) == [
        {'name': 'node', 'available': True, 'version': '22.14.0', 'status': 'measured'}]


@pytest.mark.parametrize('exception,status', [
    (subprocess.TimeoutExpired(['git'], 1), 'timeout'), (PermissionError(), 'execution_error')])
def test_probe_failures_preserved(monkeypatch, exception, status):
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: '/trusted/git')
    def run(*args, **kwargs):
        raise exception
    monkeypatch.setattr(yourself.subprocess, 'run', run)
    assert yourself.command_inventory(['git'], versions=True)[0]['status'] == status


def test_presence_only_and_missing_never_run(monkeypatch):
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: None if name == 'rg' else '/trusted/tool')
    def forbidden(*args, **kwargs):
        pytest.fail('presence-only command ran')
    monkeypatch.setattr(yourself.subprocess, 'run', forbidden)
    rows = yourself.command_inventory(['flutter', 'goma', 'podman', 'mise', 'just', 'rg'], versions=True)
    assert [t['status'] for t in rows] == ['presence_only', 'presence_only', 'presence_only', 'presence_only', 'presence_only', 'not_found']


def test_new_workspace_markers(tmp_path):
    (tmp_path / 'Makefile').touch()
    (tmp_path / 'Cargo.toml').touch()
    (tmp_path / 'go.mod').touch()
    (tmp_path / 'tsconfig.json').touch()
    report = yourself.workspace_facts(tmp_path)
    assert report['markers'] == ['Makefile', 'Cargo.toml', 'go.mod', 'tsconfig.json']


@pytest.mark.parametrize('timeout', [0, -1, True, '2', float('nan'), float('inf'), 31])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        yourself.command_inventory(timeout=timeout)


def test_validate_all_names_before_any_probe(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid names triggered discovery')
    monkeypatch.setattr(yourself.shutil, 'which', forbidden)
    with pytest.raises(ValueError):
        yourself.command_inventory(['git', 'sh'])
    with pytest.raises(TypeError):
        yourself.command_inventory('git')


def test_workspace_marker_presence_not_contents(monkeypatch, tmp_path):
    (tmp_path / 'Dockerfile').write_text('sentinel-secret', encoding='utf-8')
    (tmp_path / 'pubspec.yaml').touch()
    (tmp_path / 'package.json').mkdir()  # Directory is not a file marker.
    (tmp_path / '.goma').mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail('workspace read file contents')
    monkeypatch.setattr(Path, 'open', forbidden)
    report = yourself.workspace_facts(tmp_path)
    assert report['markers'] == ['pubspec.yaml', 'Dockerfile', '.goma']
    assert report['storage']['total_bytes'] > 0
    assert not report['errors']
    assert 'sentinel-secret' not in json.dumps(report)
    with pytest.raises(ValueError):
        yourself.workspace_facts(tmp_path / 'Dockerfile')


def test_workspace_errors_and_symlink_marker(monkeypatch, tmp_path):
    (tmp_path / 'Dockerfile').touch()
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path.name == 'Dockerfile':
            raise PermissionError
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    def fail_disk(path):
        raise PermissionError
    monkeypatch.setattr(yourself.shutil, 'disk_usage', fail_disk)
    report = yourself.workspace_facts(tmp_path)
    assert report['storage'] is None
    assert report['errors'] == [{'marker': 'Dockerfile', 'reason': 'PermissionError'},
                               {'marker': 'storage', 'reason': 'PermissionError'}]


def test_proc_listener_ports_are_not_network_scans(monkeypatch, tmp_path):
    monkeypatch.setattr(yourself.platform, 'system', lambda: 'Linux')
    net = tmp_path / 'net'
    net.mkdir()
    (net / 'tcp').write_text('header\n 0: 0100007F:1F90 00000000:0000 0A\n'
                            ' 1: 0100007F:1234 00000000:0000 01\n'
                            ' short\n 2: malformed 0000 0A\n'
                            ' 3: 0000:10000 0000 0A\n', encoding='ascii')
    (net / 'tcp6').write_text('header\n 0: 00000000:01BB 0000:0000 0A\n'
                             ' 1: 00000000:1F90 0000:0000 0A\n', encoding='ascii')
    result = yourself.listener_ports(proc_root=tmp_path)
    assert result == {'measured': True, 'tcp_ports': [443, 8080], 'errors': ['malformed_tcp']}
    assert '0100007F' not in json.dumps(result)


def test_proc_unavailable_empty_and_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(yourself.platform, 'system', lambda: 'Windows')
    assert yourself.listener_ports()['errors'] == ['unsupported_platform']
    monkeypatch.setattr(yourself.platform, 'system', lambda: 'Linux')
    assert not yourself.listener_ports(proc_root=tmp_path)['measured']
    net = tmp_path / 'net'
    net.mkdir()
    (net / 'tcp').write_text('header\n', encoding='ascii')
    result = yourself.listener_ports(proc_root=tmp_path)
    assert result['measured'] and result['tcp_ports'] == []
    assert result['errors'] == ['tcp6:FileNotFoundError']
    (net / 'tcp6').write_text('header\n', encoding='ascii')
    assert yourself.listener_ports(proc_root=tmp_path)['errors'] == []


def test_diagnose_optional_observations_and_os_release_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(yourself.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(yourself.platform, 'freedesktop_os_release', lambda: {'ID': 'debian', 'VERSION_ID': '12', 'SECRET': 'sentinel-secret'})
    monkeypatch.setattr(yourself, 'listener_ports', lambda: {'measured': True, 'tcp_ports': [], 'errors': []})
    report = yourself.diagnose(directory=tmp_path, ports=True, commands=[])
    assert report['os_release'] == {'ID': 'debian', 'VERSION_ID': '12'}
    assert report['workspace']['storage']
    assert 'sentinel-secret' not in yourself.to_json(report)
    assert 'workspace:' in yourself.to_markdown(report)
    def fail_release():
        raise OSError
    monkeypatch.setattr(yourself.platform, 'freedesktop_os_release', fail_release)
    assert yourself.diagnose(commands=[])['os_release'] is None


def test_cli_explicit_version_context(tmp_path):
    proc = run_cli(ROOT / 'scripts/yourself_cli.py', '--diagnose', '--versions', '--command', 'python',
                   '--directory', tmp_path, '--format', 'json', timeout=20)
    assert proc.returncode == 0, proc.stderr
    facts = proc.json()
    assert facts['commands'][0]['version'] == yourself.platform.python_version()
    assert facts['workspace']['markers'] == []
    assert facts['host'] is None and facts['listeners'] is None


def test_workspace_symlink_markers_ignored(tmp_path):
    target = tmp_path / 'target'
    target.touch()
    link = tmp_path / 'Dockerfile'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    assert 'Dockerfile' not in yourself.workspace_facts(tmp_path)['markers']


def test_extended_api_standalone_vendor(tmp_path):
    proc = run_copy(tmp_path, 'standalone',
        "import standalone as y; f=y.diagnose(commands=[]); "
        "assert f['commands'] == []; assert 'Environment' in y.to_markdown(f)")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ''


@pytest.mark.parametrize('spelling', ['justfile', 'Justfile'])
def test_justfile_alias_has_canonical_name(tmp_path, spelling):
    (tmp_path / spelling).touch()
    assert yourself.workspace_facts(tmp_path)['markers'] == ['justfile']


def test_case_variants_are_one_logical_marker(monkeypatch, tmp_path):
    # Simulate both actual directory entries even on case-insensitive hosts.
    monkeypatch.setattr(Path, 'iterdir', lambda root: iter([root / 'justfile', root / 'Justfile']))
    original = Path.lstat
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o100644)
                        if path.name in ('justfile', 'Justfile') else original(path))
    assert yourself.workspace_facts(tmp_path)['markers'] == ['justfile']


@pytest.mark.parametrize('system', ['Windows', 'Darwin', 'Linux'])
@pytest.mark.parametrize('spelling,expected', [('pixi.toml', ['pixi.toml']), ('Pixi.toml', []),
                                             ('JUSTFILE', []), ('Justfile', ['justfile'])])
def test_marker_spelling_independent_of_os(monkeypatch, tmp_path, system, spelling, expected):
    monkeypatch.setattr(yourself.platform, 'system', lambda: system)
    (tmp_path / spelling).touch()
    original = Path.lstat
    # Emulate case-insensitive lstat: even unregistered casing would resolve.
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=0o100644)
                        if path.name.casefold() == spelling.casefold() else original(path))
    assert yourself.workspace_facts(tmp_path)['markers'] == expected


def test_distinct_hardlinked_markers_are_preserved(tmp_path):
    first = tmp_path / 'pyproject.toml'
    first.touch()
    (tmp_path / 'requirements.txt').hardlink_to(first)
    assert yourself.workspace_facts(tmp_path)['markers'] == ['pyproject.toml', 'requirements.txt']


def test_all_added_presence_tools_never_execute(monkeypatch):
    names = ('poetry pdm pixi task brew bash zsh fish git-lfs mvn gradle ruby bundle mix nix rye '
             'conda winget apt dnf yum pacman scoop choco gem swift bazel meson mypy grep sed awk '
             'find fd ag ack tree fzf pipenv mamba virtualenv black corepack javac php composer '
             'elixir gzip bzip2 xz zstd 7z patch diff rsync docker-compose podman-compose yq').split()
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: '/trusted/' + name)
    def forbidden(*args, **kwargs):
        pytest.fail('presence-only command ran')
    monkeypatch.setattr(yourself.subprocess, 'run', forbidden)
    assert all(row['status'] == 'presence_only' for row in yourself.command_inventory(names, versions=True))


def test_ruff_fixed_opt_in_version(monkeypatch):
    monkeypatch.setattr(yourself.shutil, 'which', lambda name: '/trusted/ruff')
    def run(argv, **kwargs):
        assert argv == ['/trusted/ruff', '--version']
        assert kwargs['shell'] is False
        return SimpleNamespace(stdout=b'ruff 0.8.0\n', stderr=b'', returncode=0)
    monkeypatch.setattr(yourself.subprocess, 'run', run)
    assert yourself.command_inventory(['ruff'], versions=True) == [
        {'name': 'ruff', 'available': True, 'version': '0.8.0', 'status': 'measured'}]


def test_workspace_listing_error_is_not_absence(monkeypatch, tmp_path):
    def fail(root):
        raise PermissionError
    monkeypatch.setattr(Path, 'iterdir', fail)
    assert yourself.workspace_facts(tmp_path)['errors'] == [
        {'marker': 'workspace', 'reason': 'PermissionError'}]


def test_all_new_workspace_markers(tmp_path):
    names = ('justfile Taskfile.yml Taskfile.yaml flake.nix pixi.toml Brewfile .python-version '
             '.node-version .tool-versions .mise.toml poetry.lock pdm.lock uv.lock Pipfile '
             'pom.xml build.gradle build.gradle.kts Gemfile mix.exs default.nix bun.lockb '
             'bunfig.toml Rakefile build.sbt .pre-commit-config.yaml mise.toml ruff.toml .ruff.toml').split()
    for name in names:
        (tmp_path / name).touch()
    (tmp_path / '.venv').mkdir()
    report = yourself.workspace_facts(tmp_path)
    assert set(report['markers']) == set(names) | {'.venv'}
    assert len(report['markers']) == len(names) + 1


# ── directory_summary: bounds, sorted sample, entry errors and Markdown ───────────────────────

class Entries:
    """Fake os.scandir() context: yields the given entries; plain names become regular files."""
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(SimpleNamespace(name=entry, is_symlink=lambda: False, is_dir=lambda **kw: False,
                                    is_file=lambda **kw: True) if isinstance(entry, str) else entry
                    for entry in self.entries)

    def __exit__(self, *args):
        pass


@pytest.mark.parametrize('sample_size', [0, 1, 2, 10])
def test_sample_bounds_and_order(tmp_path, sample_size):
    for name in ['z', '日本', 'a']:
        (tmp_path / name).touch()
    report = yourself.directory_summary(tmp_path, sample_size=sample_size)
    assert report['sample'] == sorted(['z', '日本', 'a'])[:sample_size]
    assert report['counts']['files'] == 3


@pytest.mark.parametrize('value', [-1, True, '1', 1.5, None])
def test_invalid_sample(tmp_path, value):
    with pytest.raises(ValueError):
        yourself.directory_summary(tmp_path, sample_size=value)


def test_entry_error_preserved(monkeypatch, tmp_path):
    entry = Mock(name='entry')
    entry.name = 'unreadable'
    entry.is_symlink.side_effect = PermissionError
    monkeypatch.setattr(yourself.os, 'scandir', lambda path: Entries([entry]))
    report = yourself.directory_summary(tmp_path)
    assert report['errors'] == [{'name': 'unreadable', 'reason': 'PermissionError'}]
    assert sum(report['counts'].values()) == 0


def test_directory_markdown_unicode_and_shallow_data(tmp_path):
    (tmp_path / '日本.txt').touch()
    facts = yourself.collect(directory=tmp_path)
    markdown = yourself.to_markdown(facts)
    assert 'immediate entries only' in markdown
    assert '日本.txt' in markdown
    assert json.loads(yourself.to_json(facts)) == facts


def test_no_project_or_git_facts(tmp_path):
    (tmp_path / 'README.md').write_text('fictional project', encoding='utf-8')
    (tmp_path / '.git').mkdir()
    facts = yourself.collect(directory=tmp_path)
    assert 'repository' not in facts
    assert 'project' not in facts
    assert 'fictional project' not in yourself.to_json(facts)


def test_other_entry_type(tmp_path):
    import os
    if not hasattr(os, 'mkfifo'):
        pytest.skip('FIFO unavailable')
    os.mkfifo(tmp_path / 'fifo')
    assert yourself.directory_summary(tmp_path)['counts']['other'] == 1


def test_sample_matches_sorted_reference(tmp_path):
    rng=random.Random(20261104)
    names=[str(i) for i in range(200)]+['日本語','🙂','A','a']
    for _ in range(5):
        rng.shuffle(names)
        for limit in (0,1,10,100,300):
            with patch.object(yourself.os,'scandir',return_value=Entries(names)):
                result=yourself.directory_summary(tmp_path,sample_size=limit)
            assert result['sample']==sorted(names)[:limit]
            assert result['counts']=={'files':len(names),'directories':0,'symlinks':0,'other':0}
            assert result['errors']==[]
