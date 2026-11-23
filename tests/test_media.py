"""Moving added media into the default folder."""
import os

from play import media
from test_library import make


def test_moved_into_the_folder_without_overwriting(tmp_path):
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    make(str(src), 'A.mkv', b'a' * 100)
    make(str(src), 'B.mkv', b'b' * 100)
    make(str(dst), 'A.mkv', b'old')
    seen = []
    moved, failed = media.move_files([str(src / 'A.mkv'), str(src / 'B.mkv')], str(dst),
                                     lambda i, n, name: seen.append((i, n, name)))
    assert failed == [] and seen == [(0, 2, 'A.mkv'), (1, 2, 'B.mkv')]
    assert sorted(os.listdir(dst)) == ['A (2).mkv', 'A.mkv', 'B.mkv'] and not os.listdir(src)
    assert open(dst / 'A.mkv', 'rb').read() == b'old'                       # the one already there is untouched
    assert all(len(fp) == 16 for _, fp in moved)


def test_a_file_already_in_the_folder_stays_put(tmp_path):
    make(str(tmp_path), 'A.mkv', b'a' * 100)
    moved, failed = media.move_files([str(tmp_path / 'A.mkv')], str(tmp_path))
    assert moved[0][0] == str(tmp_path / 'A.mkv') and os.listdir(tmp_path) == ['A.mkv']


def test_a_file_that_cant_be_moved_is_reported(tmp_path):
    moved, failed = media.move_files([str(tmp_path / 'missing.mkv')], str(tmp_path / 'dst'))
    assert moved == [] and failed and failed[0][0].endswith('missing.mkv')
