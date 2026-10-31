"""Keep stdlib compatibility separate from development-tool support."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_floor_is_python310_on_every_runner_os():
    # This module must remain importable during python -S unittest discovery.
    import yaml

    workflow = yaml.load(
        (ROOT / '.github/workflows/runtime-compatibility.yml').read_text(encoding='utf-8'),
        Loader=yaml.BaseLoader,
    )
    runtime = workflow['jobs']['runtime']
    assert runtime['runs-on'] == "${{ matrix.os }}"  # setup-python has 3.10 builds for every current runner image
    assert runtime['strategy']['matrix'] == {
        'os': ['ubuntu-latest'],
        'python': ['3.10', '3.11', '3.12', '3.13', '3.14'],
        'include': [
            {'os': 'windows-latest', 'python': '3.10'},
            {'os': 'macos-latest', 'python': '3.10'},
        ],
    }
    assert workflow['permissions'] == {'contents': 'read'}
    assert runtime['steps'][-1]['run'].splitlines() == [
        'python -S -m unittest discover -s tests -p test_yourself.py -v',
        'python -S -m unittest discover -s tests -p test_workspace_runtime.py -v',
    ]
    assert 'continue-on-error' not in runtime
