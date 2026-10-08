"""Downstream integration of the pinned shared pytest adapter."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
from vendor import xprobe

ROOT = Path(__file__).resolve().parents[1]


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
    for entry in lock['files']:
        assert entry['ref'] == 'refs/heads/main'
        assert re.fullmatch(r'[0-9a-f]{40}', entry['commit'])
        data = (ROOT / entry['destination']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['blob_sha']
    assert Path(xprobe.__file__).resolve() == ROOT / 'vendor/xprobe.py'


def test_public_vendor_ci_updates_without_repository_writes():
    import yaml
    ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    jobs = ci['jobs']
    resolve = jobs['resolve-vendor']['steps']
    test = jobs['test']['steps']
    update = next(s for s in resolve if s.get('name') == 'Update public vendor files for this run')
    assert update['if'] == "inputs.vendor-mode != 'locked'"
    assert update['shell'] == 'bash'
    assert not any(word in update['run'] for word in ('|| true', '|| :', 'set +e'))
    assert 'continue-on-error' not in update
    assert update['run'].splitlines() == [
        'python -S .vendor-sync-tools/vendor_sync.py promote --manifest vendor.lock.json | tee vendor-promotion.json',
        'python -S -m json.tool vendor-promotion.json > /dev/null',
        'python -S .vendor-sync-tools/vendor_sync.py check --manifest vendor.lock.json']
    assert ci['on']['workflow_dispatch']['inputs']['vendor-mode']['default'] == 'update'
    assert jobs['test']['needs'] == 'resolve-vendor'
    assert sum('vendor_sync.py promote' in s.get('run', '') for steps in (resolve, test) for s in steps) == 1
    baseline = next(i for i,s in enumerate(resolve) if s.get('name') == 'Verify checked-in vendor copies')
    enroll = next(i for i,s in enumerate(resolve) if s.get('name') == 'Exercise canonical GHI enrollment')
    materialize = next(i for i,s in enumerate(resolve) if s.get('name') == 'Recreate locked vendor files from GitHub')
    promotion = resolve.index(update)
    assert baseline < enroll < materialize < promotion
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
    target = os.environ.get('YOURSELF_FAILURE_EVIDENCE') if junit else None
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
