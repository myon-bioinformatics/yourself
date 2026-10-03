"""Downstream integration of the pinned shared pytest adapter."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import xprobe

ROOT = Path(__file__).resolve().parents[1]


def test_adapter_provenance():
    provenance = json.loads((ROOT / 'vendor/xprobe_pytest.provenance.json').read_text())
    data = (ROOT / 'vendor/xprobe_pytest.py').read_bytes()
    commit = '326acd667e13b21bf53ccc1590af960edf8cbf6c'
    blob = '70fac53151e97f5f28d23f9961d3837b7a885ea8'
    sha256 = 'c0ea71f9d971bf47a9f5f1794ef8a7f641dd17694ea8e7f29795d326c5229285'
    assert provenance['repository'] == 'myon-bioinformatics/xprobe'
    assert provenance['path'] == 'scripts/xprobe_pytest.py'
    assert provenance['commit'] == commit
    assert provenance['sha256'] == sha256
    assert provenance['blob_sha'] == blob
    assert hashlib.sha256(data).hexdigest() == sha256
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == blob


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
        [sys.executable, '-m', 'pytest', '-c', os.devnull, '-p', 'vendor.xprobe_pytest',
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
    if junit:
        rows = [json.loads(line) for line in text.splitlines()]
        native = sorted((r['value']['node'].split('::')[-1],
                         'failure' if r['value']['phase'] == 'call' else 'error')
                        for r in rows if r['value'].get('outcome') in
                        {'failed', 'error', 'xpass_strict'})
        identities = sorted((r['value']['test'], r['value']['kind']) for r in report['cases'])
        assert not report['truncated']
        assert native == identities == [
            ('test_cleanup', 'error'), ('test_failure', 'failure'),
            ('test_failure', 'failure'), ('test_setup', 'error'),
            ('test_unexpected_pass', 'failure')], repr(identities)
        assert all(r['context'] == receipt['context'] for r in report['cases'])
        assert all(value in xml for value in (
            'private_parameter_one', 'private_parameter_two', 'private diagnostic',
            'private_stdout', 'private_stderr', 'private_setup', 'private teardown'))
        assert 'private' not in compact
