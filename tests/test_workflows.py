"""CI workflow, vendor lock and native pytest evidence contracts."""

import importlib.util
import json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

import pytest


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fleet = load_vendored("yourself_fleet_check", "vendor/fleet_check.py")
xprobe = load_vendored("yourself_xprobe", "vendor/xprobe.py")
_cli_args = load_vendored("yourself_cli_args", "vendor/cli_args.py")
build_env = _cli_args.build_env
pytest_command = _cli_args.pytest_command
run_command = _cli_args.run_command

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name):
    """Parsed .github/workflows/<name>; PyYAML is imported here, so this module imports without it."""
    import yaml
    return yaml.load((ROOT / '.github/workflows' / name).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


# ── workflow matrices and the supported Python floor ──────────────────────────────────────────


def test_runtime_floor_is_python311_on_every_runner_os():
    workflow = _workflow('runtime-compatibility.yml')
    runtime = workflow['jobs']['runtime']
    assert runtime['runs-on'] == "${{ matrix.os }}"
    assert runtime['strategy']['matrix'] == {
        'os': ['ubuntu-latest'],
        'python': ['3.11', '3.12', '3.13', '3.14'],
        'include': [
            {'os': 'windows-latest', 'python': '3.11'},
            {'os': 'macos-latest', 'python': '3.11'},
        ],
    }
    assert workflow['permissions'] == {'contents': 'read'}
    assert runtime['steps'][-1]['run'].splitlines() == [
        'python -S -m unittest discover -s tests -p test_core.py -v',
    ]
    assert 'continue-on-error' not in runtime


def test_runtime_and_lock_metadata_share_the_supported_floor():
    import tomllib

    assert fleet.python_floor(ROOT) == ('3.11', ['3.11', '3.12', '3.13', '3.14'])
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert project['project']['dependencies'] == []
    assert project['tool']['uv']['dependency-groups']['dev']['requires-python'] == '>=3.11'


def test_ci_matrix_reports_and_documentation_stay_aligned():
    workflow = _workflow('ci.yml')
    matrix = workflow['jobs']['test']['strategy']['matrix']
    assert matrix == {
        'os': ['ubuntu-latest'],
        'python': ['3.11', '3.12', '3.13', '3.14', '3.15'],
        'include': [
            {'os': 'windows-latest', 'python': '3.15'},
            {'os': 'macos-latest', 'python': '3.15'},
        ],
    }
    lanes = [
        {'os': os_name, 'python': version}
        for os_name in matrix['os'] for version in matrix['python']
    ] + matrix['include']
    reports = json.loads(workflow['jobs']['failure-identity']['with']['expected-reports'])
    assert reports == [
        f"test-report-{lane['os']}-py{lane['python']}/junit.xml" for lane in lanes
    ]
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert ('pytest with coverage runs on Python 3.11–3.15 (Linux) '
            'and 3.15 (Windows/macOS).') in readme


def test_setup_uv_activates_each_requested_python_without_cache():
    for filename, job_id, requested in [
        ('ci.yml', 'resolve-vendor', '3.15'),
        ('ci.yml', 'test', '${{ matrix.python }}'),
        ('runtime-compatibility.yml', 'runtime', '${{ matrix.python }}'),
    ]:
        job = fleet.workflow_job(ROOT, job_id, filename, setup='astral-sh/setup-uv@', inputs=(
            f'python-version: "{requested}"', 'activate-environment: true', 'enable-cache: true',
            "save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}",  # only main writes
            'prune-cache: true'))
        assert 'actions/setup-python@' not in job
        if job_id == 'test':
            sync = next(step for step in _workflow(filename)['jobs'][job_id]['steps']
                        if step.get('name') == 'Sync locked development environment')
            assert sync['run'].strip() == 'uv sync --locked'


# ── the vendor enrollment step fails closed ───────────────────────────────────────────────────

def test_failed_vendor_operation_does_not_execute_check(tmp_path):
    step_name = 'Exercise canonical GHI enrollment'
    mode = 'enroll'
    ci = _workflow('ci.yml')
    step = next(s for s in ci['jobs']['resolve-vendor']['steps'] if s.get('name') == step_name)
    assert step['shell'] == 'bash'
    interpreter = shlex.quote(Path(sys.executable).as_posix())
    tool = tmp_path / '.vendor-sync-tools/vendor_sync.py'
    tool.parent.mkdir()
    tool.write_text('import pathlib, sys\n'
                    f'if sys.argv[1] == {mode!r}: sys.exit(2)\n'
                    'pathlib.Path("check-ran").touch()\n', encoding='utf-8')
    vendor = tmp_path / 'vendor'
    vendor.mkdir()
    for name in ('gh_identity.py', 'gh_identity-LICENSE'):
        (vendor / name).write_bytes(b'locked fixture\n')
    command = step['run'].replace('python -S ', interpreter + ' -S ')
    bash = 'bash'
    if sys.platform == 'win32':
        # Actions uses Git Bash; PATH's bash.exe can instead be the WSL launcher.
        git = Path(shutil.which('git'))
        bash = next(str(p) for p in (git.parent / 'bash.exe', git.parent.parent / 'bin/bash.exe') if p.is_file())
    result = subprocess.run([bash, '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', command], cwd=tmp_path)
    assert result.returncode == 2
    assert not (tmp_path / 'check-ran').exists()


# ── canonical vendor evidence staging against real locked bytes ───────────────────────────────

@pytest.mark.skipif(not (ROOT / '.vendor-sync-tools/vendor_stage.py').is_file(),
                    reason='requires the workflow full-SHA parent checkout')
def test_vendor_staging_workflow_payload_and_fail_closed():
    assert len(fleet.vendor_staging(ROOT)) == 2


# ── vendor lock provenance, vendor CI wiring, native pytest evidence ──────────────────────────

def test_vendor_lock_provenance():
    # Every row: full-SHA ref == commit, checked-in bytes match blob_sha and sha256; no unlisted row of these repositories.
    fleet.check_lock(ROOT, sources={
        'vendor/xprobe_pytest.py': ('myon-bioinformatics/xprobe', 'scripts/xprobe_pytest.py'),
        'vendor/xprobe.py': ('myon-bioinformatics/xprobe', 'xprobe.py'),
        'vendor/xprobe-LICENSE': ('myon-bioinformatics/xprobe', 'LICENSE'),
        'vendor/gh_identity.py': ('myon-bioinformatics/gh_identity', 'gh_identity.py'),
        'vendor/gh_identity-LICENSE': ('myon-bioinformatics/gh_identity', 'LICENSE'),
        'vendor/cli_args.py': ('myon-bioinformatics/cli_args', 'cli_args.py'),
        'vendor/cli_args-LICENSE': ('myon-bioinformatics/cli_args', 'LICENSE'),
    })
    assert Path(xprobe.__file__).resolve() == ROOT / 'vendor/xprobe.py'


def test_public_vendor_ci_updates_without_repository_writes():
    ci = _workflow('ci.yml')
    jobs = ci['jobs']
    resolve = jobs['resolve-vendor']['steps']
    test = jobs['test']['steps']
    assert not any(s.get('name') == 'Update public vendor files for this run' for s in resolve)
    dispatch = ci.get('on', {}).get('workflow_dispatch')
    assert dispatch is None or not (isinstance(dispatch, dict) and 'vendor-mode' in dispatch.get('inputs', {}))
    assert set(jobs['test']['needs']) == {'changes', 'resolve-vendor'}
    assert jobs['resolve-vendor']['needs'] == 'changes'
    assert jobs['test']['if'] == "needs.changes.outputs.product_changed == 'true'"
    assert not any('vendor_sync.py promote' in s.get('run', '') for steps in (resolve, test) for s in steps)
    assert not any('vendor_sync.py update' in s.get('run', '') for steps in (resolve, test) for s in steps)
    baseline = next(i for i,s in enumerate(resolve) if s.get('name') == 'Verify checked-in vendor copies')
    enroll = next(i for i,s in enumerate(resolve) if s.get('name') == 'Exercise canonical GHI enrollment')
    materialize = next(i for i,s in enumerate(resolve) if s.get('name') == 'Recreate locked vendor files from GitHub')
    assert baseline < enroll < materialize
    assert 'if' not in resolve[enroll]
    assert 'vendor_sync.py enroll --manifest vendor.lock.json | tee vendor-enrollment.json' in resolve[enroll]['run']
    assert all(name in resolve[materialize]['run'] for name in ('gh_identity.py', 'gh_identity-LICENSE'))
    download = next(i for i,s in enumerate(test) if s.get('uses', '').startswith('actions/download-artifact@'))
    verify = next(i for i,s in enumerate(test) if s.get('name') == 'Verify resolved vendor snapshot')
    assert test[download]['with']['name'] == 'vendor-snapshot'
    smoke = next(i for i,s in enumerate(test) if s.get('name') == 'Verify enrolled GHI is stdlib-loadable')
    run_tests = next(i for i,s in enumerate(test) if s.get('name') == 'Test with branch coverage')
    assert download < verify < smoke < run_tests
    assert 'vendor_sync.py check' in test[verify]['run']
    assert all('vendor_sync.py promote' not in s.get('run', '') for s in test)
    for steps, name in ((resolve, 'Preserve resolved vendor snapshot'), (test, 'Preserve vendor lock used by this run')):
        upload = next(s for s in steps if s.get('name') == name)
        assert upload['if'] == 'always()'
        assert upload['with']['if-no-files-found'] == 'error'
        assert upload['with']['path'].startswith('build/vendor-evidence-')
    pins = [s['with']['ref'] for steps in (resolve, test) for s in steps
            if s.get('with', {}).get('repository') == 'myon-bioinformatics/myon-bioinformatics']
    assert len(pins) == 2 and len(set(pins)) == 1
    assert all(re.fullmatch('[0-9a-f]{40}', pin) for pin in pins)
    assert not (ROOT / '.github/workflows/vendor-update.yml').exists()
    assert ci['permissions'] == {'contents': 'read'}
    for steps in (resolve, test):
        assert all(s['with']['persist-credentials'] == 'false' for s in steps if s.get('uses','').startswith('actions/checkout@'))
    text = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert not any(word in text for word in ('VENDOR_UPDATE_TOKEN', 'VENDOR_UPDATES_ENABLED', 'GH_TOKEN', 'git push', 'git commit', 'gh pr', 'continue-on-error'))


def test_git_checkout_retains_locked_bytes_with_autocrlf(tmp_path):
    # Exercise Windows-style checkout conversion, particularly LICENSE files.
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'core.autocrlf', 'true'], check=True)
    shutil.copyfile(ROOT / '.gitattributes', tmp_path / '.gitattributes')
    lock = json.loads((ROOT / 'vendor.lock.json').read_text(encoding='utf-8'))
    files = [entry['destination'] for entry in lock['files']]
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    subprocess.run(['git', '-C', str(tmp_path), 'add', '--', '.gitattributes', *files], check=True, capture_output=True)
    for relative in files:
        (tmp_path / relative).unlink()
    subprocess.run(['git', '-C', str(tmp_path), 'checkout', '--', *files], check=True, capture_output=True)
    assert all((tmp_path / relative).read_bytes() == (ROOT / relative).read_bytes() for relative in files)


@pytest.mark.parametrize("junit", [False, True])
def test_native_failure_evidence(tmp_path, junit):
    suite = tmp_path / 'test_sample.py'
    suite.write_text('''import pytest
import sys
import yourself
# All private values below are dummy sentinels.
@pytest.mark.parametrize('expected', ['private_parameter_one', 'private_parameter_two'])
def test_failure(expected):
    print('private_stdout')
    print('private_stderr', file=sys.stderr)
    assert yourself.to_json({'ok': True}) == expected, 'private diagnostic'
@pytest.mark.xfail(reason="private reason")
def test_known_gap():
    assert False
@pytest.mark.xfail(strict=True)
def test_unexpected_pass():
    pass
@pytest.fixture
def resource():
    yield
    raise RuntimeError("private teardown")
def test_cleanup(resource):
    pass
@pytest.fixture
def broken_setup():
    assert yourself.to_json({'ok': True}) == 'private_setup'
def test_setup(broken_setup): pass
def test_pass():
    assert 'true' in yourself.to_json({'ok': True})
@pytest.mark.skip(reason='private_skip')
def test_skip(): pass''', encoding='utf-8')
    destination = tmp_path / 'events.jsonl'
    env = build_env({'PYTHONPATH': str(ROOT), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
    env.pop('PYTEST_ADDOPTS', None)
    result = run_command(pytest_command([str(suite)], extra=[
        '-c', os.devnull, '--rootdir=' + str(tmp_path), '-p', 'vendor.xprobe_pytest',
        '--xprobe-jsonl=' + str(destination), '--xprobe-repository=myon-bioinformatics/yourself',
        '--xprobe-run-id=controlled-child',
        *(['--junitxml=' + str(tmp_path / 'junit.xml'), '-o', 'junit_logging=all'] if junit else []),
    ]), cwd=tmp_path, env=env, timeout=30)
    # Save available raw evidence before importer/receipt/assertion failures.
    evidence = None
    target = os.environ.get('FAILURE_EVIDENCE_DIR') if junit else None
    if target:
        evidence = Path(target)
        evidence.mkdir(parents=True)
        (evidence / 'child-exit.json').write_text(
            json.dumps({'returncode': result.returncode}) + '\n', encoding='utf-8')
        for name in ('events.jsonl', 'junit.xml'):
            if (tmp_path / name).exists():
                shutil.copyfile(tmp_path / name, evidence / name)
    if junit:
        xml = (tmp_path / 'junit.xml').read_text(encoding='utf-8')
        report = xprobe.cases_from_junit(xml, repository='myon-bioinformatics/yourself',
                                       report_id='controlled-child')
        compact = xprobe.corpus_to_json(report['cases'], jsonl=True)
        if evidence is not None:
            (evidence / 'failures.jsonl').write_text(compact, encoding='utf-8')
    text = destination.read_text(encoding='utf-8')
    receipt = xprobe.pytest_receipt(text, expected_context={
        'repository': 'myon-bioinformatics/yourself', 'commit_sha': None,
        'report_id': 'controlled-child'})
    if evidence is not None:
        (evidence / 'receipt.json').write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    assert result.returncode == receipt['exitstatus'] == 1
    assert receipt['phase_counts'] == {
        'setup': {'passed': 6, 'skipped': 1, 'error': 1},
        'call': {'failed': 2, 'passed': 2, 'xfail': 1, 'xpass_strict': 1},
        'teardown': {'passed': 7, 'error': 1}}
    assert 'private' not in text
    rows = [json.loads(line) for line in text.splitlines()]
    phase_nodes = [r['value']['node'] for r in rows if 'phase' in r['value']]
    assert phase_nodes and all(node.startswith('test_sample.py::') for node in phase_nodes)
    if junit:
        native = sorted((r['value']['node'].split('::')[-1],
                         'failure' if r['value']['phase'] == 'call' else 'error')
                        for r in rows if r['value'].get('outcome') in
                        {'failed', 'error', 'xpass_strict'})
        identities = sorted((r['value']['test'], r['value']['kind']) for r in report['cases'])
        assert not report['truncated']
        assert all(r['value']['class'] == 'test_sample' for r in report['cases'])
        assert native == identities == [
            ('test_cleanup', 'error'), ('test_failure', 'failure'),
            ('test_failure', 'failure'), ('test_setup', 'error'),
            ('test_unexpected_pass', 'failure')], repr(identities)
        assert all(r['context'] == receipt['context'] for r in report['cases'])
        assert all(value in xml for value in (
            'private_parameter_one', 'private_parameter_two', 'private diagnostic',
            'private_stdout', 'private_stderr', 'private_setup', 'private teardown'))
        assert 'private' not in compact
