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
                                     progress=lambda i, n, name: seen.append((i, n, name)), fingerprints=True)
    assert failed == [] and seen == [(0, 2, 'A.mkv'), (1, 2, 'B.mkv')]
    assert sorted(os.listdir(dst)) == ['A [2].mkv', 'A.mkv', 'B.mkv'] and not os.listdir(src)
    assert open(dst / 'A.mkv', 'rb').read() == b'old'                       # the one already there is untouched
    assert all(len(fp) == 16 for _, fp in moved)


def test_a_file_already_in_the_folder_stays_put(tmp_path):
    make(str(tmp_path), 'A.mkv', b'a' * 100)
    moved, failed = media.move_files([str(tmp_path / 'A.mkv')], str(tmp_path))
    assert moved[0][0] == str(tmp_path / 'A.mkv') and os.listdir(tmp_path) == ['A.mkv']


def test_a_file_that_cant_be_moved_is_reported(tmp_path):
    moved, failed = media.move_files([str(tmp_path / 'missing.mkv')], str(tmp_path / 'dst'))
    assert moved == [] and failed and failed[0][0].endswith('missing.mkv')



def _no_rename(monkeypatch):
    """As across drives (or when Windows won't rename an open file): the move has to copy."""
    real = os.rename

    def rename(a, b):
        if not str(a).endswith(media.PART):
            raise OSError(17, 'not the same device')
        return real(a, b)
    monkeypatch.setattr(media.os, 'rename', rename)


def test_a_copy_that_fails_leaves_nothing_behind(tmp_path, monkeypatch):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 1000)
    _no_rename(monkeypatch)

    def half_copy(a, b):
        open(b, 'wb').write(b'half')
        raise OSError(28, 'There is not enough space on the disk')
    monkeypatch.setattr(media.shutil, 'copy2', half_copy)
    moved, failed = media.move_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(tmp_path / 'dst'))
    assert moved == [] and 'space' in failed[0][1]
    assert not (tmp_path / 'dst').exists() and os.path.exists(tmp_path / 'src' / 'Up 2009.mkv')   # no leftovers


def test_an_original_that_cant_be_removed_leaves_no_second_copy(tmp_path, monkeypatch):
    src = tmp_path / 'src' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    _no_rename(monkeypatch)
    real_remove = os.remove

    def remove(p):
        if os.path.normcase(str(p)) == os.path.normcase(str(src)):
            raise PermissionError(13, 'in use')
        return real_remove(p)
    monkeypatch.setattr(media.os, 'remove', remove)
    moved, failed = media.move_files([str(src)], str(tmp_path / 'dst'))
    assert moved == [] and failed and src.exists() and not (tmp_path / 'dst').exists()


def test_across_drives_it_copies_then_removes(tmp_path, monkeypatch):
    src = tmp_path / 'src' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    _no_rename(monkeypatch)
    moved, failed = media.move_files([str(src)], str(tmp_path / 'dst'))
    assert failed == [] and not src.exists() and os.listdir(tmp_path / 'dst') == ['Up 2009.mkv']


def test_episodes_keep_their_show_and_season(tmp_path):
    make(str(tmp_path / 'Downloads' / 'Bluey' / 'Season 1'), 'E01.mkv', b'b1' * 100)
    make(str(tmp_path / 'Downloads'), 'Ann Droid S02E03.mkv', b'a' * 100)
    dst = tmp_path / 'PLAY'
    moved, failed = media.move_files([str(tmp_path / 'Downloads' / 'Bluey' / 'Season 1' / 'E01.mkv'),
                                      str(tmp_path / 'Downloads' / 'Ann Droid S02E03.mkv')], str(dst))
    assert failed == []
    assert os.path.exists(dst / 'Bluey' / 'Season 1' / 'E01.mkv')
    assert os.path.exists(dst / 'Ann Droid' / 'Season 2' / 'Ann Droid S02E03.mkv')


def test_files_already_in_any_media_folder_stay_put(tmp_path):
    other = tmp_path / 'Other'
    make(str(other / 'Show' / 'Season 1'), 'E01.mkv', b'x' * 100)
    moved, failed = media.move_files([str(other / 'Show' / 'Season 1' / 'E01.mkv')], str(tmp_path / 'PLAY'),
                                     roots=[str(tmp_path / 'PLAY'), str(other)])
    assert failed == [] and moved[0][0] == str(other / 'Show' / 'Season 1' / 'E01.mkv')
    assert not (tmp_path / 'PLAY').exists()


def test_non_video_files_are_left_alone(tmp_path):
    make(str(tmp_path / 'src'), 'notes.txt', b'hi')
    moved, failed = media.move_files([str(tmp_path / 'src' / 'notes.txt')], str(tmp_path / 'dst'))
    assert moved == [] and 'not a video' in failed[0][1] and (tmp_path / 'src' / 'notes.txt').exists()


def test_a_fingerprint_that_fails_still_counts_as_moved(tmp_path, monkeypatch):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 100)
    monkeypatch.setattr(media.library, 'fingerprint', lambda *a: (_ for _ in ()).throw(PermissionError('av')))
    moved, failed = media.move_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(tmp_path / 'dst'),
                                     fingerprints=True)
    assert failed == [] and moved[0][1] is None


def test_a_second_copy_keeps_its_title_on_the_tv(tmp_path):
    from play import names
    make(str(tmp_path / 'dst'), 'Up.mkv', b'one')
    make(str(tmp_path / 'src'), 'Up.mkv', b'two')
    moved, failed = media.move_files([str(tmp_path / 'src' / 'Up.mkv')], str(tmp_path / 'dst'))
    assert os.path.basename(moved[0][0]) == 'Up [2].mkv' and names.movie('Up [2]')[0] == 'Up'



# ------------------------------------------------------------------ 1104925 review
def test_a_read_only_file_isnt_moved_and_leaves_nothing(tmp_path, monkeypatch):
    import stat
    src = tmp_path / 'disc' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    os.chmod(src, stat.S_IREAD)
    try:
        _no_rename(monkeypatch)
        moved, failed = media.move_files([str(src)], str(tmp_path / 'dst'))
        assert moved == [] and 'read-only' in failed[0][1] and src.exists()
        assert not (tmp_path / 'dst').exists()                               # nothing copied first
    finally:
        os.chmod(src, stat.S_IWRITE)


def test_a_read_only_copy_is_still_cleaned_up(tmp_path, monkeypatch):
    import stat
    src = tmp_path / 'src' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    _no_rename(monkeypatch)
    real_copy = media.shutil.copy2

    def copy_read_only(a, b):                       # copy2 carries the read-only bit over
        real_copy(a, b)
        os.chmod(b, stat.S_IREAD)
    monkeypatch.setattr(media.shutil, 'copy2', copy_read_only)
    real_remove = os.remove

    def remove(p):
        if os.path.normcase(str(p)) == os.path.normcase(str(src)):
            raise PermissionError(13, 'in use')
        return real_remove(p)
    monkeypatch.setattr(media.os, 'remove', remove)
    moved, failed = media.move_files([str(src)], str(tmp_path / 'dst'))
    assert moved == [] and failed and src.exists() and not (tmp_path / 'dst').exists()


def test_leftover_part_files_are_swept(tmp_path):
    dst = tmp_path / 'dst'
    make(str(dst), '.abc' + media.PART, b'half')
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 100)
    media.move_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(dst))
    assert os.listdir(dst) == ['Up 2009.mkv']


def test_a_show_named_like_a_device_gets_a_usable_folder(tmp_path):
    make(str(tmp_path / 'src'), 'Con S01E01.mkv', b'c' * 100)
    moved, failed = media.move_files([str(tmp_path / 'src' / 'Con S01E01.mkv')], str(tmp_path / 'PLAY'))
    assert failed == [] and os.path.exists(tmp_path / 'PLAY' / 'Con_' / 'Season 1' / 'Con S01E01.mkv')
