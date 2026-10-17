import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yourself


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
    class Entries:
        def __enter__(self):
            return iter([entry])
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(yourself.os, 'scandir', lambda path: Entries())
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
