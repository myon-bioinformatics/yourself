"""CI workflow, vendor lock and native pytest evidence contracts."""

import hashlib, json, os, re, shlex, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

import pytest

from vendor import fleet_check as fleet
from vendor import xprobe

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

    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    lock = tomllib.loads((ROOT / 'uv.lock').read_text(encoding='utf-8'))
    assert project['project']['requires-python'] == lock['requires-python'] == '>=3.11'
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
        workflow = _workflow(filename)
        steps = workflow['jobs'][job_id]['steps']
        actions = [step.get('uses', '') for step in steps]
        assert not any(action.startswith('actions/setup-python@') for action in actions)
        setups = [step for step in steps
                  if step.get('uses', '').startswith('astral-sh/setup-uv@')]
        assert len(setups) == 1
        setup = setups[0]
        assert re.fullmatch(r'astral-sh/setup-uv@[0-9a-f]{40}', setup['uses'])
        assert setup['with']['python-version'] == requested
        assert setup['with']['activate-environment'] == 'true'
        assert setup['with']['enable-cache'] == 'true'
        assert setup['with']['save-cache'] == "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"  # only main writes
        assert setup['with']['prune-cache'] == 'true'
        if job_id == 'test':
            sync = next(step for step in steps
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

class VendorStagingTests(unittest.TestCase):
    def test_workflow_payload_and_fail_closed(self):
        tool = ROOT / '.vendor-sync-tools/vendor_stage.py'
        if not tool.is_file():
            self.skipTest('requires the workflow full-SHA parent checkout')
        commands = []
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            text = workflow.read_text(encoding='utf-8')
            for match in re.finditer(r'(?m)^( +)- name: Stage canonical vendor evidence\n', text):
                indent = match[1]
                tail = text[match.end():]
                block = re.split(r'\n' + indent + r'- ', tail, maxsplit=1)[0] + '\n'
                self.assertIn('if: always()', block)
                self.assertNotIn('continue-on-error', block)
                run = re.search(r'(?m)^' + indent + r'  run: >-\n((?:' + indent + r'    [^\n]*\n)+)', block)
                self.assertIsNotNone(run)
                command = ' '.join(line.strip() for line in run[1].splitlines())
                self.assertNotIn('--promotion-receipt', command)
                output = shlex.split(command)[shlex.split(command).index('--output') + 1]
                upload = re.split(r'\n' + indent + r'- ', tail, maxsplit=1)[1]
                self.assertIn('path: ' + output, upload.split('retention-days:', 1)[0])
                commands.append(command)
        self.assertTrue(commands)
        for command in commands:
            kinds = (shlex.split(command)[shlex.split(command).index('--kind') + 1],)
            for kind in kinds:
                for receipt in (False, True):
                    with self.subTest(command=command, kind=kind, receipt=receipt), tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        args = shlex.split(command.replace('$VENDOR_EVIDENCE_KIND', kind))
                        args[0] = sys.executable
                        args[2] = str(tool)
                        manifest = args[args.index('--manifest') + 1]
                        lock = json.loads((ROOT / manifest).read_text(encoding='utf-8'))
                        for entry in lock['files']:
                            dest = root / entry['destination']
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(ROOT / entry['destination'], dest)
                        # A newly enrolled member must be included without a workflow edit.
                        data = b'extra reviewed fixture\n'
                        extra = dict(lock['files'][0], destination='extra/LICENSE',
                                     blob_sha=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(),
                                     sha256=hashlib.sha256(data).hexdigest())
                        lock['files'].append(extra)
                        (root / 'extra').mkdir()
                        (root / 'extra/LICENSE').write_bytes(data)
                        (root / manifest).parent.mkdir(parents=True, exist_ok=True)
                        (root / manifest).write_text(json.dumps(lock), encoding='utf-8')
                        legacy = [args[i + 1] for i, value in enumerate(args) if value == '--legacy-evidence']
                        runtime = [args[i + 1] for i, value in enumerate(args) if value == '--runtime-evidence']
                        for member in legacy:
                            (root / member).parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(ROOT / member, root / member)
                        for member in runtime:
                            (root / member).write_text('{}', encoding='utf-8')
                        if receipt:
                            (root / 'vendor-promotion.json').write_text('{}', encoding='utf-8')
                        result = subprocess.run(args, cwd=root, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        output = root / args[args.index('--output') + 1]
                        evidence = json.loads((output / 'vendor-evidence.json').read_text(encoding='utf-8'))
                        expected = {manifest, *(e['destination'] for e in lock['files'])}
                        self.assertEqual(set(evidence['locked']), expected)
                        self.assertEqual(set(evidence['candidate']), expected)
                        self.assertEqual(evidence['kind'], kind)
                        self.assertEqual(set(evidence['legacy']), set(legacy))
                        if receipt and '--promotion-receipt' in args:
                            runtime.append('vendor-promotion.json')
                        self.assertEqual(set(evidence['runtime']), set(runtime))
                        expected |= set(legacy) | set(runtime)
                        self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}, expected | {'vendor-evidence.json'})
                        for member in expected:
                            self.assertEqual((output / member).read_bytes(), (root / member).read_bytes())
                            self.assertEqual(evidence['sha256'][member], hashlib.sha256((root / member).read_bytes()).hexdigest())
                        shutil.rmtree(output)
                        (root / lock['files'][0]['destination']).write_bytes(b'corrupt')
                        result = subprocess.run(args, cwd=root, capture_output=True, text=True)
                        self.assertEqual(result.returncode, 2)
                        self.assertFalse(output.exists())


# ── vendor lock provenance, vendor CI wiring, native pytest evidence ──────────────────────────

def test_vendor_lock_provenance():
    lock = json.loads((ROOT / 'vendor.lock.json').read_text(encoding='utf-8'))
    assert lock['schema'] == 'vendor-lock/1'
    xprobe_entries = [entry for entry in lock['files']
                      if entry['repository'] == 'myon-bioinformatics/xprobe']
    assert {(entry['source'], entry['destination']) for entry in xprobe_entries} == {
        ('scripts/xprobe_pytest.py', 'vendor/xprobe_pytest.py'),
        ('xprobe.py', 'vendor/xprobe.py'), ('LICENSE', 'vendor/xprobe-LICENSE')}
    assert len(xprobe_entries) == 3
    ghi_entries = [entry for entry in lock['files']
                   if entry['repository'] == 'myon-bioinformatics/gh_identity']
    assert {(entry['source'], entry['destination']) for entry in ghi_entries} == {
        ('gh_identity.py', 'vendor/gh_identity.py'),
        ('LICENSE', 'vendor/gh_identity-LICENSE')}
    assert len(ghi_entries) == 2
    # Every row: full-SHA ref == commit, checked-in bytes match blob_sha and sha256.
    assert set(fleet.check_lock(ROOT)) == {entry['destination'] for entry in lock['files']}
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
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-c', os.devnull, '--rootdir=' + str(tmp_path), '-p', 'vendor.xprobe_pytest',
         '--xprobe-jsonl=' + str(destination), '--xprobe-repository=myon-bioinformatics/yourself',
         '--xprobe-run-id=controlled-child',
         *(['--junitxml=' + str(tmp_path / 'junit.xml'), '-o', 'junit_logging=all'] if junit else []),
         str(suite)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
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
