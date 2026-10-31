"""Keep stdlib compatibility separate from development-tool support."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_floor_is_python311_on_every_runner_os():
    # This module must remain importable during python -S unittest discovery.
    import yaml

    workflow = yaml.load(
        (ROOT / '.github/workflows/runtime-compatibility.yml').read_text(encoding='utf-8'),
        Loader=yaml.BaseLoader,
    )
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
        'python -S -m unittest discover -s tests -p test_yourself.py -v',
        'python -S -m unittest discover -s tests -p test_workspace_runtime.py -v',
        'python -S -m unittest discover -s tests -p test_diagnose_runtime.py -v',
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
    import json
    import yaml

    workflow = yaml.load(
        (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'),
        Loader=yaml.BaseLoader,
    )
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
    import re
    import yaml

    for filename, job_id, requested in [
        ('ci.yml', 'resolve-vendor', '3.15'),
        ('ci.yml', 'test', '${{ matrix.python }}'),
        ('runtime-compatibility.yml', 'runtime', '${{ matrix.python }}'),
    ]:
        workflow = yaml.load(
            (ROOT / '.github/workflows' / filename).read_text(encoding='utf-8'),
            Loader=yaml.BaseLoader,
        )
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
        assert setup['with']['enable-cache'] == 'false'
        if job_id == 'test':
            sync = next(step for step in steps
                        if step.get('name') == 'Sync locked development environment')
            assert sync['run'].strip() == 'uv sync --locked'
