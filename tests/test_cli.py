"""CLIs: `python yourself.py` (yourself.main, also as a copied single file) and scripts/yourself_cli.py."""

import importlib.util, json, subprocess, sys
from pathlib import Path

import pytest

import yourself


def load_vendored(name, relpath):
    """Load a vendored file by path under a repo-unique name (cli_args README: Loading a vendored copy)."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relpath)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


run_cli = load_vendored("yourself_cli_args", "vendor/cli_args.py").run_cli

ROOT = Path(__file__).resolve().parents[1]


# ── python yourself.py: yourself.main and the copied single file ──────────────────────────────

def test_package_fixture_is_synthetic(monkeypatch, fake_python_packages):
    from importlib import metadata
    def forbidden(*args, **kwargs):
        raise AssertionError("real package discovery attempted")
    monkeypatch.setattr(metadata, "distributions", forbidden)
    report = yourself.python_packages()
    assert report["count"] == 0 and report["installers"] == {} and report["complete"]


def test_package_fixture_child_is_synthetic(tmp_path, fake_package_cli):
    target = tmp_path / "synthetic child.py"
    target.write_text('''import sys
from importlib import metadata
assert list(metadata.distributions()) == []
assert __name__ == "__main__"
assert sys.argv[1:] == ["--fixture"]
print("synthetic")
''', encoding="utf-8")
    proc = fake_package_cli(target, "--fixture", cwd=tmp_path, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.replace("\r\n", "\n") == "synthetic\n"  # run_cli keeps the child's own newline
    assert not (tmp_path / "__pycache__").exists()


def test_default_readonly(monkeypatch, tmp_path, capsys, fake_python_packages):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(yourself.subprocess, "run", lambda *a, **k: pytest.fail("executed tool"))
    (tmp_path / "pyproject.toml").write_text("SECRET_TOKEN = 'never-read'")
    assert yourself.main(["--format", "json", "--no-probes"]) == 0  # probes are the only executions
    report = json.loads(capsys.readouterr().out)
    assert report["host"] is None
    # The CLI now lists local listeners (proc read, not an execution).
    assert report["listeners"] is None or "bindings" in report["listeners"]
    assert report["workspace"]["markers"] == ["pyproject.toml"]
    assert {row["name"] for row in report["commands"]} >= {"gh", "uv", "playwright", "actionlint"}
    assert "never-read" not in json.dumps(report)


def test_minimal(capsys):
    assert yourself.main(["--minimal"]) == 0
    assert "Tools" not in capsys.readouterr().out


def test_bad_directory(capsys, tmp_path):
    assert yourself.main(["--directory", str(tmp_path / "absent")]) == 2
    assert capsys.readouterr().err == "FileNotFoundError\n"


@pytest.mark.parametrize("status", ["timeout", "execution_error", "nonzero_exit", "unrecognized_version"])
def test_partial(monkeypatch, capsys, status, fake_python_packages):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": []},
        "commands": [{"status": status, "name": "git", "available": True, "version": None}],
    })
    assert yourself.main(["--versions"]) == 2


def test_workspace_error(monkeypatch, capsys, fake_python_packages):
    monkeypatch.setattr(yourself, "diagnose", lambda **kwargs: {
        **yourself.collect(), "workspace": {"errors": ["permission"]}, "commands": [],
    })
    assert yourself.main([]) == 2


def test_copy_one_file_execution(tmp_path, fake_package_cli):
    target = tmp_path / "tool.py"
    target.write_bytes((ROOT / "yourself.py").read_bytes())
    proc = fake_package_cli(target, "--format", "json", cwd=tmp_path, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert proc.json()["workspace"] is not None


def test_copy_one_file_minimal_entry(tmp_path):
    target = tmp_path / "standalone.py"
    target.write_bytes((ROOT / "yourself.py").read_bytes())
    proc = run_cli(target, "--minimal", "--format", "json", python_options=("-S",), cwd=tmp_path, timeout=10)
    assert proc.returncode == 0, proc.stderr
    report = proc.json()
    assert report["os"]["system"] and report["runtime"]["version"]
    assert "python_packages" not in report and report["directory"] is None
    assert list(tmp_path.iterdir()) == [target]  # no siblings or __pycache__ required


# ── scripts/yourself_cli.py error contract: synthetic failures, no host assumptions ───────────

spec = importlib.util.spec_from_file_location('report_cli', ROOT / 'scripts/yourself_cli.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)



def run(*args):
    return run_cli(
        ROOT / 'scripts/yourself_cli.py',
        *args,
        python_options=('-S',),
        timeout=20,
    )


def test_success_and_help(tmp_path, monkeypatch):
    result = run('--format', 'json', '--directory', str(tmp_path), '--sample-size', '0')
    assert result.returncode == 0 and result.stderr == ''
    report = json.loads(result.stdout)
    assert report['directory']['sample'] == [] and 'error' not in report
    assert run().stdout.startswith('#')
    help_text = run('--help')
    assert help_text.returncode == 0
    original = cli.Parser.parse_args
    def inspect_parser(parser, *args, **kwargs):
        assert len(parser._actions) == 9
        assert all(action.help for action in parser._actions)
        return original(parser, *args, **kwargs)
    monkeypatch.setattr(cli.Parser, 'parse_args', inspect_parser)
    assert cli.main(['--sample-size=0']) == 0
    for flag in ('format', 'directory', 'sample-size', 'include-host', 'diagnose', 'versions', 'ports', 'command'):
        assert '--' + flag in help_text.stdout
    assert '3 partial' in help_text.stdout


@pytest.mark.parametrize('args', [
    ['--sample-size', '-1'], ['--sample-size', 'SECRET'], ['--sample-size'],
    ['--command', 'SECRET'], ['--SECRET'], ['--format', 'SECRET'],
])
@pytest.mark.parametrize('json_args', [[], ['--format', 'json'], ['--format=json']])
def test_invalid_input_is_safe(args, json_args, monkeypatch):
    monkeypatch.setenv('PRIVATE_TOKEN', 'SECRET')
    result = run(*args, *json_args)
    assert result.returncode == 2
    assert 'SECRET' not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr
    if json_args:
        assert json.loads(result.stdout)['error']['code'] == 'invalid_input'
        assert result.stderr == ''
    else:
        assert result.stdout == '' and len(result.stderr.splitlines()) == 1


@pytest.mark.parametrize('diagnose', [[], ['--diagnose']])
def test_invalid_directory(tmp_path, diagnose):
    file = tmp_path / 'file'
    file.touch()
    for path in (tmp_path / 'missing-SECRET', file):
        result = run('--format=json', '--directory', str(path), *diagnose)
        assert result.returncode == 2
        assert json.loads(result.stdout)['error']['code'] == 'invalid_input'
        assert str(path) not in result.stdout


@pytest.mark.parametrize('json_mode', [False, True])
def test_execution_failure(monkeypatch, capsys, json_mode):
    def fail(**kwargs):
        raise PermissionError('SECRET environment value')
    monkeypatch.setattr(cli.yourself, 'collect', fail)
    assert cli.main(['--format', 'json'] if json_mode else []) == 1
    out = capsys.readouterr()
    assert 'SECRET' not in out.out + out.err
    if json_mode:
        assert json.loads(out.out)['error']['code'] == 'execution_failed'
    else:
        assert out.out == '' and 'execution_failed' in out.err


@pytest.mark.parametrize('source', ['directory', 'workspace', 'listeners', 'commands'])
@pytest.mark.parametrize('json_mode', [False, True])
def test_partial_report(monkeypatch, capsys, source, json_mode):
    facts = cli.yourself.collect()
    if source == 'directory':
        facts[source] = {'path': '.', 'counts': {}, 'sample': [], 'errors': [{'reason': 'PermissionError'}]}
    elif source == 'commands':
        facts[source] = [{'name': 'python', 'available': True, 'version': None, 'status': 'timeout'}]
    else:
        facts[source] = {'errors': [{'reason': 'PermissionError'}]}
    monkeypatch.setattr(cli.yourself, 'collect', lambda **kwargs: facts)
    assert cli.main(['--format=json'] if json_mode else []) == 3
    out = capsys.readouterr()
    if json_mode:
        report = json.loads(out.out)
        assert report[source] == facts[source]
        assert report['error']['code'] == 'partial_result'
        assert out.err == ''
    else:
        assert out.out and 'partial_result' in out.err


def test_legacy_abbreviation():
    result = run('--fo=json', '--sample-s=0')
    assert result.returncode == 0
    assert 'error' not in json.loads(result.stdout)
    bad = run('--fo=json', '--sample-s=-1')
    assert bad.returncode == 2 and json.loads(bad.stdout)['error']


def test_symlink_root(tmp_path):
    link = tmp_path / 'link'
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('symlink unavailable')
    result = run('--format=json', '--directory', str(link))
    assert result.returncode == 2
    assert json.loads(result.stdout)['error']['code'] == 'invalid_input'


def test_render_failure(monkeypatch, capsys):
    def fail(facts):
        raise ValueError('SECRET rendering data')
    monkeypatch.setattr(cli.yourself, 'to_json', fail)
    assert cli.main(['--format=json']) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)['error']['code'] == 'execution_failed'
    assert 'SECRET' not in out.out + out.err


@pytest.mark.parametrize('flush_only', [False, True])
def test_output_failure(monkeypatch, capsys, flush_only):
    class BrokenOutput:
        def write(self, value):
            if not flush_only:
                raise OSError('SECRET stream path')
        def flush(self):
            raise OSError('SECRET stream path')
    monkeypatch.setattr(cli.sys, 'stdout', BrokenOutput())
    assert cli.main(['--format=json']) == 1
    out = capsys.readouterr()
    assert 'SECRET' not in out.err
    assert 'execution_failed' in out.err


def test_real_buffered_output_failure():
    if not Path('/dev/full').exists():
        pytest.skip('requires a full output device')
    with open('/dev/full', 'w') as output:
        result = subprocess.run([sys.executable, '-S', str(ROOT / 'scripts/yourself_cli.py'),
                                 '--format=json'], stdout=output, stderr=subprocess.PIPE,
                                text=True, timeout=20)
    assert result.returncode == 1
    assert result.stderr == 'error: execution_failed: Could not write the report\n'
