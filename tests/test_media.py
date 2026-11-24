"""Copying added media into the default folder (the originals are never touched)."""
import os
import stat

from play import library, media
from test_library import make


def test_copied_into_the_folder_without_overwriting_and_the_originals_stay(tmp_path):
    src, dst = tmp_path / 'src', tmp_path / 'dst'
    make(str(src), 'A.mkv', b'a' * 100)
    make(str(src), 'B.mkv', b'b' * 100)
    make(str(dst), 'A.mkv', b'old')
    seen = []
    added, failed = media.copy_files([str(src / 'A.mkv'), str(src / 'B.mkv')], str(dst),
                                     progress=lambda i, n, name: seen.append((i, n, name)), fingerprints=True)
    assert failed == [] and seen == [(0, 2, 'A.mkv'), (1, 2, 'B.mkv')]
    assert sorted(os.listdir(dst)) == ['A [2].mkv', 'A.mkv', 'B.mkv']
    assert sorted(os.listdir(src)) == ['A.mkv', 'B.mkv']                     # the originals are still there
    assert open(dst / 'A.mkv', 'rb').read() == b'old'                       # the one already there is untouched
    assert open(dst / 'A [2].mkv', 'rb').read() == b'a' * 100
    assert all(len(fp) == 16 for _, fp in added)


def test_a_file_already_in_the_folder_stays_put(tmp_path):
    make(str(tmp_path), 'A.mkv', b'a' * 100)
    added, failed = media.copy_files([str(tmp_path / 'A.mkv')], str(tmp_path))
    assert added[0][0] == str(tmp_path / 'A.mkv') and os.listdir(tmp_path) == ['A.mkv']


def test_a_missing_file_says_so(tmp_path):
    added, failed = media.copy_files([str(tmp_path / 'gone.mkv')], str(tmp_path / 'dst'))
    assert added == [] and 'not there' in failed[0][1] and not (tmp_path / 'dst').exists()


def test_a_copy_that_fails_leaves_nothing_behind(tmp_path, monkeypatch):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 1000)

    def half_copy(a, b):
        open(b, 'wb').write(b'half')
        raise OSError(28, 'There is not enough space on the disk')
    monkeypatch.setattr(media.shutil, 'copy2', half_copy)
    added, failed = media.copy_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(tmp_path / 'dst'))
    assert added == [] and 'space' in failed[0][1]
    assert not (tmp_path / 'dst').exists() and os.path.exists(tmp_path / 'src' / 'Up 2009.mkv')   # no leftovers


def test_the_original_is_only_read(tmp_path, monkeypatch):
    src = tmp_path / 'src' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    touched = []
    for name in ('remove', 'unlink', 'rename', 'replace'):
        real = getattr(os, name)

        def spy(*a, _real=real, _name=name, **k):
            if os.path.normcase(str(a[0])) == os.path.normcase(str(src)):
                touched.append(_name)
            return _real(*a, **k)
        monkeypatch.setattr(media.os, name, spy)
    added, failed = media.copy_files([str(src)], str(tmp_path / 'dst'))
    assert failed == [] and touched == [] and src.exists()


def test_a_read_only_original_is_copied_and_the_copy_is_writable(tmp_path):
    src = tmp_path / 'disc' / 'Up 2009.mkv'
    make(str(src.parent), src.name, b'u' * 1000)
    os.chmod(src, stat.S_IREAD)
    try:
        added, failed = media.copy_files([str(src)], str(tmp_path / 'dst'))
        copy = tmp_path / 'dst' / 'Up 2009.mkv'
        assert failed == [] and copy.exists() and os.access(copy, os.W_OK) and src.exists()
    finally:
        os.chmod(src, stat.S_IWRITE)


def test_a_file_already_in_the_library_isnt_copied_again(tmp_path):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 1000)
    src = str(tmp_path / 'src' / 'Up 2009.mkv')
    fp = library.fingerprint(src, os.path.getsize(src))
    added, failed = media.copy_files([src], str(tmp_path / 'dst'), known={fp})
    assert added == [] and 'already in your library' in failed[0][1] and not (tmp_path / 'dst').exists()


def test_the_same_file_picked_twice_is_copied_once(tmp_path):
    make(str(tmp_path / 'a'), 'Up 2009.mkv', b'u' * 1000)
    make(str(tmp_path / 'b'), 'Up 2009.mkv', b'u' * 1000)            # the same film in two places
    added, failed = media.copy_files([str(tmp_path / 'a' / 'Up 2009.mkv'), str(tmp_path / 'b' / 'Up 2009.mkv')],
                                     str(tmp_path / 'dst'))                # (an empty library, Kids not ticked)
    assert len(added) == 1 and 'already in your library' in failed[0][1]
    assert os.listdir(tmp_path / 'dst') == ['Up 2009.mkv']


def test_needed_space_adds_up_the_files(tmp_path):
    make(str(tmp_path), 'A.mkv', b'a' * 100)
    make(str(tmp_path), 'B.mkv', b'b' * 50)
    assert media.needed_space([str(tmp_path / 'A.mkv'), str(tmp_path / 'B.mkv'), str(tmp_path / 'gone.mkv')]) == 150


def test_episodes_keep_their_show_and_season(tmp_path):
    make(str(tmp_path / 'Downloads' / 'Bluey' / 'Season 1'), 'E01.mkv', b'b1' * 100)
    make(str(tmp_path / 'Downloads'), 'Ann Droid S02E03.mkv', b'a' * 100)
    dst = tmp_path / 'PLAY'
    added, failed = media.copy_files([str(tmp_path / 'Downloads' / 'Bluey' / 'Season 1' / 'E01.mkv'),
                                      str(tmp_path / 'Downloads' / 'Ann Droid S02E03.mkv')], str(dst))
    assert failed == []
    assert os.path.exists(dst / 'Bluey' / 'Season 1' / 'E01.mkv')
    assert os.path.exists(dst / 'Ann Droid' / 'Season 2' / 'Ann Droid S02E03.mkv')
    assert os.path.exists(tmp_path / 'Downloads' / 'Ann Droid S02E03.mkv')   # (still in Downloads)


def test_files_already_in_any_media_folder_stay_put(tmp_path):
    other = tmp_path / 'Other'
    make(str(other / 'Show' / 'Season 1'), 'E01.mkv', b'x' * 100)
    added, failed = media.copy_files([str(other / 'Show' / 'Season 1' / 'E01.mkv')], str(tmp_path / 'PLAY'),
                                     roots=[str(tmp_path / 'PLAY'), str(other)])
    assert failed == [] and added[0][0] == str(other / 'Show' / 'Season 1' / 'E01.mkv')
    assert not (tmp_path / 'PLAY').exists()


def test_non_video_files_are_left_alone(tmp_path):
    make(str(tmp_path / 'src'), 'notes.txt', b'hi')
    added, failed = media.copy_files([str(tmp_path / 'src' / 'notes.txt')], str(tmp_path / 'dst'))
    assert added == [] and 'not a video' in failed[0][1] and (tmp_path / 'src' / 'notes.txt').exists()


def test_a_fingerprint_that_fails_still_counts_as_added(tmp_path, monkeypatch):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 100)
    monkeypatch.setattr(media.library, 'fingerprint', lambda *a: (_ for _ in ()).throw(PermissionError('av')))
    added, failed = media.copy_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(tmp_path / 'dst'),
                                     fingerprints=True)
    assert failed == [] and added[0][1] is None


def test_a_second_copy_keeps_its_title_on_the_tv(tmp_path):
    from play import names
    make(str(tmp_path / 'dst'), 'Up.mkv', b'one')
    make(str(tmp_path / 'src'), 'Up.mkv', b'two')
    added, failed = media.copy_files([str(tmp_path / 'src' / 'Up.mkv')], str(tmp_path / 'dst'))
    assert os.path.basename(added[0][0]) == 'Up [2].mkv' and names.movie('Up [2]')[0] == 'Up'


def test_leftover_part_files_are_swept(tmp_path):
    import time
    dst = tmp_path / 'dst'
    make(str(dst), '.old' + media.PART, b'half')
    make(str(dst), '.now' + media.PART, b'another PLAY is copying this one')
    old = time.time() - media.STALE_PART - 60
    os.utime(dst / ('.old' + media.PART), (old, old))
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 100)
    media.copy_files([str(tmp_path / 'src' / 'Up 2009.mkv')], str(dst))
    assert sorted(os.listdir(dst)) == ['.now' + media.PART, 'Up 2009.mkv']     # only the stale one goes


def test_a_show_named_like_a_device_gets_a_usable_folder(tmp_path):
    make(str(tmp_path / 'src'), 'Con S01E01.mkv', b'c' * 100)
    added, failed = media.copy_files([str(tmp_path / 'src' / 'Con S01E01.mkv')], str(tmp_path / 'PLAY'))
    assert failed == [] and os.path.exists(tmp_path / 'PLAY' / 'Con_' / 'Season 1' / 'Con S01E01.mkv')



def test_the_same_path_picked_twice_is_copied_once(tmp_path):
    make(str(tmp_path / 'a'), 'Up 2009.mkv', b'u' * 1000)
    p = str(tmp_path / 'a' / 'Up 2009.mkv')
    added, failed = media.copy_files([p, p], str(tmp_path / 'dst'))
    assert len(added) == 1 and os.listdir(tmp_path / 'dst') == ['Up 2009.mkv']


def test_a_file_already_in_a_media_folder_is_listed_not_refused(tmp_path):
    lib_dir = tmp_path / 'PLAY'
    make(str(lib_dir), 'Up 2009.mkv', b'u' * 1000)
    p = str(lib_dir / 'Up 2009.mkv')
    fp = library.fingerprint(p, os.path.getsize(p))
    added, failed = media.copy_files([p], str(lib_dir), roots=[str(lib_dir)], fingerprints=True, known={fp})
    assert failed == [] and added == [(p, fp)]                  # listed where it is, so it can be marked Kids


def test_needed_space_counts_only_what_gets_copied(tmp_path):
    make(str(tmp_path / 'PLAY'), 'In.mkv', b'i' * 500)
    make(str(tmp_path / 'src'), 'Out.mkv', b'o' * 100)
    make(str(tmp_path / 'src'), 'notes.txt', b'n' * 900)
    assert media.needed_space([str(tmp_path / 'PLAY' / 'In.mkv'), str(tmp_path / 'src' / 'Out.mkv'),
                               str(tmp_path / 'src' / 'notes.txt')], roots=[str(tmp_path / 'PLAY')]) == 100



def test_a_hidden_film_picked_again_says_its_hidden(tmp_path):
    make(str(tmp_path / 'src'), 'Up 2009.mkv', b'u' * 1000)
    p = str(tmp_path / 'src' / 'Up 2009.mkv')
    fp = library.fingerprint(p, os.path.getsize(p))
    added, failed = media.copy_files([p], str(tmp_path / 'dst'), known={fp}, hidden={fp})
    assert added == [] and 'hidden' in failed[0][1]


def test_needed_space_counts_a_path_picked_twice_once(tmp_path):
    make(str(tmp_path), 'A.mkv', b'a' * 100)
    assert media.needed_space([str(tmp_path / 'A.mkv')] * 2) == 100
