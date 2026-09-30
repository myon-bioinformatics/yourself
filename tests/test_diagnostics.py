import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yourself

ROOT = Path(__file__).resolve().parents[1]


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
    rows = yourself.command_inventory(['flutter', 'goma', 'rg'], versions=True)
    assert [t['status'] for t in rows] == ['presence_only', 'presence_only', 'not_found']


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
    proc = subprocess.run([sys.executable, str(ROOT / 'scripts/yourself_cli.py'), '--diagnose',
                           '--versions', '--command', 'python', '--directory', str(tmp_path), '--format', 'json'],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    facts = json.loads(proc.stdout)
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
    import shutil
    shutil.copy(ROOT / 'yourself.py', tmp_path / 'standalone.py')
    proc = subprocess.run([sys.executable, '-B', '-c',
        "import standalone as y; f=y.diagnose(commands=[]); "
        "assert f['commands'] == []; assert 'Environment' in y.to_markdown(f)"],
        cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ''
