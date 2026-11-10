import random
from types import SimpleNamespace; from unittest.mock import patch
import yourself


class Entries:
    def __init__(self,names):self.names=names
    def __enter__(self):
        return iter(SimpleNamespace(name=name,is_symlink=lambda:False,
                    is_dir=lambda **kw:False,is_file=lambda **kw:True) for name in self.names)
    def __exit__(self,*args):pass


def test_sample_matches_sorted_reference(tmp_path):
    rng=random.Random(20261104)
    names=[str(i) for i in range(200)]+['日本語','🙂','A','a']
    for _ in range(5):
        rng.shuffle(names)
        for limit in (0,1,10,100,300):
            with patch.object(yourself.os,'scandir',return_value=Entries(names)):
                result=yourself.directory_summary(tmp_path,sample_size=limit)
            assert result['sample']==sorted(names)[:limit]
            assert result['counts']=={'files':len(names),'directories':0,'symlinks':0,'other':0}
            assert result['errors']==[]
