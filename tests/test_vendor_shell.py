from pathlib import Path
import subprocess
import shutil
import shlex
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(('step_name', 'mode'), [
    ('Exercise canonical GHI enrollment', 'enroll'),
    ('Update public vendor files for this run', 'promote'),
])
def test_failed_vendor_operation_does_not_execute_check(tmp_path, step_name, mode):
    import yaml
    ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
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
