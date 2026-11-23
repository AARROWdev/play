"""Library and names fixes from the baseline review (B2, B4, B8, B10-B14, B19, B23-B25, B31-B38, B43, B50, B56)."""
import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from play import library, names
from test_library import find, lib, make  # noqa: F401  (fixture)


# ------------------------------------------------------------------ names
@pytest.mark.parametrize('stem,title,year', [
    ('Blade.Runner.2049.2017.1080p.BluRay.x264', 'Blade Runner 2049', 2017),
    ('1917.2019.1080p.WEB-DL', '1917', 2019),
    ('Wonder.Woman.1984.2020.2160p.WEB-DL', 'Wonder Woman 1984', 2020),
    ('Death Race 2000 1975', 'Death Race 2000', 1975),
    ('Heat-1995', 'Heat', 1995),
    ('(500) Days of Summer (2009)', '(500) Days of Summer', 2009),
    ('Heat [1995]', 'Heat', 1995),
    ('Nosferatu.H.264.DDP5.1', 'Nosferatu', None),
    ('Heat [YTS.MX] 1995', 'Heat', 1995),
])
def test_movie_names(stem, title, year):
    assert names.movie(stem) == (title, year)


@pytest.mark.parametrize('rel,want', [
    (r'Ann Droid [S01E02].mkv', ('Ann Droid', 1, 2)),
    (r'Ann Droid (S01E02).mkv', ('Ann Droid', 1, 2)),
    (r'Ann Droid\Specials\S00E01.mkv', ('Ann Droid', 0, 1)),
    (r'Ann Droid\Season 1 (2019)\S01E01.mkv', ('Ann Droid', 1, 1)),
    (r'Ann Droid\Season 1 (2019)\E02.mkv', ('Ann Droid', 1, 2)),
    (r'Ann Droid\Specials\E03.mkv', ('Ann Droid', 0, 3)),
    (r'Doctor Who (1963)\Season 01\Doctor Who (1963) S01E01.mkv', ('Doctor Who (1963)', 1, 1)),
    (r'Doctor Who (2005)\Season 01\Doctor Who (2005) S01E01.mkv', ('Doctor Who (2005)', 1, 1)),
])
def test_episode_names(rel, want):
    assert names.episode(os.path.join(r'N:\M', rel), r'N:\M') == want


@pytest.mark.parametrize('stem,last', [('Ann Droid S01E01-E02', 2), ('Ann Droid S01E01E02', 2),
                                       ('Ann Droid S01E03-04', 4), ('Ann Droid S01E05', 5)])
def test_multi_episode_files(stem, last):
    i = names.episode_info(os.path.join(r'N:\M', stem + '.mkv'), r'N:\M')
    assert i['last'] == last


def test_family_word_unicode_and_display():
    assert names.family_word("Ocean's Eleven") == "ocean's"
    assert names.family_word("Ocean's Eleven", display=True) == "Ocean's"
    assert names.family_word('Pokémon Detective Pikachu') == 'pokémon'
    assert names.family_word('Amélie') == 'amélie'


@pytest.mark.parametrize('stem,near', [('Ann Droid E03', True), ('Ann Droid Season 1 Ep 6', True),
                                       ('Jungle Park 1993', False), ('Episode One', False)])
def test_near_miss_episode_names(stem, near):
    assert bool(names.looks_like_episode(stem)) is near


# ------------------------------------------------------------------ store safety
def test_unparseable_settings_are_kept_and_the_backup_used(tmp_path):
    lb = library.Library(str(tmp_path))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    lb.data['items']['abcd'] = {'kids': True}
    lb.save()                                      # second save: library.json.bak now holds the first
    lb.save()
    (tmp_path / 'library.json').write_text('{"folders": [,]}')     # a bad hand edit
    again = library.Library(str(tmp_path))
    assert again.data['items'] == {'abcd': {'kids': True}} and 'could not read' in again.load_problem
    assert any(n.startswith('library.json.unreadable-') for n in os.listdir(tmp_path))


def test_unparseable_settings_without_backup_start_empty_but_keep_the_file(tmp_path):
    (tmp_path / 'library.json').write_text('{"folders": [,]}')
    again = library.Library(str(tmp_path))
    assert again.folders == [] and 'empty settings' in again.load_problem
    assert any(n.startswith('library.json.unreadable-') for n in os.listdir(tmp_path))


def test_utf16_settings_file_is_read(tmp_path):
    (tmp_path / 'library.json').write_bytes(json.dumps({'folders': [{'path': 'D:/m'}]}).encode('utf-16'))
    assert library.Library(str(tmp_path)).folders == [{'path': os.path.abspath('D:/m'), 'kids': False}]


def test_lone_surrogate_name_doesnt_break_saving(lib, tmp_path):
    bad = str(tmp_path / 'home' / 'Bad \udc81 name.mkv')
    try:
        with open(bad, 'wb') as f:
            f.write(b'x' * 1000)
    except (OSError, UnicodeEncodeError):
        pytest.skip('this file system refuses the name')
    lib.scan()
    lib.save()
    assert any('Bad' in i['title'] for i in lib.current())


@pytest.mark.parametrize('port,skin,want_port,want_skin', [
    (70000, 'Ember', 8600, 'Ember'), ('²', 'Ember', 8600, 'Ember'), (True, 'Ember', 8600, 'Ember'),
    (80, 'Ember', 8600, 'Ember'), (8700, ['Blue'], 8700, 'Blue'), (8700, 'Plaid', 8700, 'Blue'),
    ('8700', 'Neon', 8700, 'Neon'), (8700, 'Violet', 8700, 'Violet')])
def test_bad_port_or_skin_in_settings_falls_back(tmp_path, port, skin, want_port, want_skin):
    """Written as UTF-8 (as PLAY writes it), so the value itself is what gets checked; a good value is kept."""
    (tmp_path / 'library.json').write_text(json.dumps({'port': port, 'skin': skin}, ensure_ascii=False),
                                           encoding='utf-8')
    lb = library.Library(str(tmp_path))
    assert lb.load_problem is None
    assert (lb.data['port'], lb.data['skin']) == (want_port, want_skin)


def test_set_value_refuses_bad_port_and_skin(lib):
    with pytest.raises(ValueError):
        lib.set_value('port', 70000)
    with pytest.raises(ValueError):
        lib.set_value('skin', 'Plaid')


# ------------------------------------------------------------------ scanning
def test_streaming_lookups_dont_wait_for_a_slow_walk(lib, monkeypatch):
    lib.scan()
    fp = find(lib, 'Jungle Park')['id']
    real = library.fingerprint
    started = threading.Event()

    def slow_walk(*a, **k):
        if not started.is_set():                   # the first file is slow: enough to hold the walk open
            started.set()
            time.sleep(1.5)
        return real(*a, **k)
    lib.fpcache.clear()                            # force fingerprinting, made slow
    monkeypatch.setattr(library, 'fingerprint', slow_walk)
    t = threading.Thread(target=lib.scan)
    t.start()
    started.wait(5)
    t0 = time.monotonic()
    assert lib.path_for(fp, rescan=False)
    lib.tv_view()
    assert time.monotonic() - t0 < 0.5
    t.join()


def test_nested_media_folder_isnt_a_copy(tmp_path):
    outer = tmp_path / 'Media'
    make(str(outer), 'Heat 1995.mkv')
    make(str(outer / 'Kids'), 'Toy Shelf.mp4')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(outer)}, {'path': str(outer / 'Kids'), 'kids': True}])
    ts = next(i for i in lb.items(0) if i['title'] == 'Toy Shelf')
    assert ts['problems'] == [] and ts['kids'] and not ts.get('copies')


def test_junction_loop_is_not_followed(tmp_path):
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'loop'), str(root)], capture_output=True)
    if r.returncode:
        pytest.skip('cannot make a junction here')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    heat = [i for i in lb.items(0) if i['title'] == 'Heat']
    assert len(heat) == 1 and not heat[0].get('copies')


def test_unreachable_folder_keeps_its_titles(lib, tmp_path, monkeypatch):
    lib.scan()
    before = {i['title'] for i in lib.current()}
    cache = dict(lib.fpcache)
    real = os.stat
    home = str(tmp_path / 'home')

    def stat(p, *a, **k):
        if os.path.normcase(str(p)) == os.path.normcase(home):
            raise OSError('NAS asleep')
        return real(p, *a, **k)
    monkeypatch.setattr(library.os, 'stat', stat)
    lib.scan()
    assert {i['title'] for i in lib.current()} == before and lib.unreachable == [home]
    assert all(k in lib.fpcache for k in cache)


def test_a_file_still_being_written_waits(lib, tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'SETTLE', 30.0)
    lib.scan()
    make(str(tmp_path / 'home'), 'Copying Now 2020.mkv')
    lib.scan()
    lib.scan()                                     # unchanged, but not for 30 s yet
    assert 'Copying Now' not in [i['title'] for i in lib.current()]
    later = library.time.monotonic() + 31
    monkeypatch.setattr(library.time, 'monotonic', lambda: later)
    lib.scan()                                     # the same size and time for 30 s: settled
    assert 'Copying Now' in [i['title'] for i in lib.current()]


def test_edits_never_walk_the_disk(lib, monkeypatch):
    lib.scan()
    ids = [find(lib, t)['id'] for t in ('Jungle Park', 'Jungle World')]
    calls = []
    monkeypatch.setattr(library, '_walk', lambda *a, **k: calls.append(1))
    lib.set_items(ids, kids=True)
    cid = lib.make_collection('JP', ids)
    lib.update_collection(cid, name='Dinos')
    lib.remove_from_collections(ids[:1])
    lib.delete_collection(cid)
    lib.dismiss('toy')
    assert calls == [] and lib.by_id(ids[0])['kids']


# ------------------------------------------------------------------ collections and suggestions
def test_episode_drops_out_of_its_collection(lib):
    ids = [find(lib, t)['id'] for t in ('Jungle Park', 'Jungle World')]
    cid = lib.make_collection('JP', ids)
    lib.set_item(ids[0], kind='episode', show='Dino Show', season=1, episode=1)
    assert [m['title'] for m in lib.collection_members(cid)] == ['Jungle World']
    assert lib.by_id(ids[0])['collection'] is None
    col = next(m for m in lib.tv_view()['movies'] if m['kind'] == 'collection')
    assert [m['title'] for m in col['items']] == ['Jungle World']


def test_offline_member_survives_a_collection_edit(tmp_path):
    """The USB drive with one film is unplugged when PLAY starts; editing the collection keeps that film, in its
    place. A film deleted for good (every folder readable) is dropped."""
    home, usb = tmp_path / 'home', tmp_path / 'usb'
    for rel in ['Jungle Park 1993.mp4', 'Jungle World 2015.mp4']:
        make(str(home), rel)
    make(str(usb), 'Jungle Park III 2001.mp4')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(home)}, {'path': str(usb)}])
    ids = {i['title']: i['id'] for i in lb.items(0)}
    order = [ids['Jungle Park'], ids['Jungle Park III'], ids['Jungle World']]
    cid = lb.make_collection('JP', order, order='custom')
    os.rename(str(usb), str(tmp_path / 'unplugged'))
    again = library.Library(lb.dir)                 # PLAY restarts with the drive gone
    again.scan()
    shown = [m['id'] for m in again.collection_members(cid)]
    assert ids['Jungle Park III'] not in shown and again.unreachable
    again.update_collection(cid, name='Dinos', members=shown, shown=shown, order='custom')
    assert again.data['collections'][cid]['members'] == order          # kept, and still second
    again.set_folders([{'path': str(home)}])                             # the drive is gone for good
    again.scan()
    again.update_collection(cid, members=shown, shown=shown)
    assert again.data['collections'][cid]['members'] == shown


def test_dismissed_folder_suggestion_isnt_reoffered_by_first_word(lib, tmp_path):
    for rel in ['Alien 1979.mkv', 'Aliens 1986.mkv']:
        make(str(tmp_path / 'home' / 'Alien Saga'), rel)
    lib.scan()
    key = next(s['key'] for s in lib.suggestions() if s['key'].startswith('folder:'))
    lib.dismiss(key)
    assert not any(s['key'] == 'alien' for s in lib.suggestions())


def test_stop_words_dont_make_a_folder_collection(lib, tmp_path):
    for rel in ['The Flea 1986.mkv', 'The Thing 1982.mkv']:
        make(str(tmp_path / 'home' / 'The Best'), rel)
    lib.scan()
    assert not any(s['key'].startswith('folder:') for s in lib.suggestions())


def test_suggestion_labels(lib):
    jur = next(s for s in lib.suggestions() if s['key'] == 'jungle')
    assert jur['label'] == 'Jungle'


# ------------------------------------------------------------------ problems
def test_multi_episode_file_isnt_a_gap(tmp_path):
    for rel in ['Ann Droid S01E01-E02.mkv', 'Ann Droid S01E03.mkv']:
        make(str(tmp_path / 'm'), rel)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    assert all(i['problems'] == [] for i in lb.items(0))


def test_near_miss_names_need_a_look(tmp_path):
    make(str(tmp_path / 'm'), 'Ann Droid E03.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    (it,) = lb.items(0)
    assert it['kind'] == 'movie' and 'no season' in it['problems'][0]


def test_same_name_shows_from_different_years_stay_apart(tmp_path):
    make(str(tmp_path / 'm' / 'Doctor Who (1963)' / 'Season 1'), 'Doctor Who (1963) S01E01.mkv')
    make(str(tmp_path / 'm' / 'Doctor Who (2005)' / 'Season 1'), 'Doctor Who (2005) S01E01.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    shows = [s['name'] for s in lb.tv_view()['shows']]
    assert shows == ['Doctor Who (1963)', 'Doctor Who (2005)']
    assert all(i['problems'] == [] for i in lb.current())


def test_by_id_is_an_index(lib):
    lib.scan()
    assert lib._by_id and all(lib._by_id[i['id']] is i for i in lib.current())


def test_release_key_is_the_one_rule():
    items = [{'title': 'B', 'year': None}, {'title': 'A', 'year': 2001}, {'title': 'C', 'year': 1990}]
    assert [i['title'] for i in sorted(items, key=library.release_key)] == ['C', 'A', 'B']


# ------------------------------------------------------------------ b6eeb8b review
def _junction(link, target):
    r = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    if r.returncode:
        pytest.skip('cannot make a junction here')


def test_junction_into_the_same_media_folder_isnt_a_copy(tmp_path):
    root = tmp_path / 'Media'
    make(str(root / 'A'), 'Heat 1995.mkv')
    (root / 'B').mkdir()
    _junction(root / 'B' / 'linkA', root / 'A')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    (heat,) = [i for i in lb.items(0) if i['title'] == 'Heat']
    assert not heat.get('copies') and heat['problems'] == []


def test_junction_to_another_drive_is_followed_and_kept_when_unplugged(tmp_path):
    root, films = tmp_path / 'Media', tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root.mkdir()
    _junction(root / 'More', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    assert [i['title'] for i in lb.items(0)] == ['Alien']
    cache = dict(lb.fpcache)
    os.rename(str(films), str(tmp_path / 'unplugged'))     # the junction now points nowhere
    lb.scan()
    assert [i['title'] for i in lb.current()] == ['Alien'] and lb.fpcache == cache


def test_movie_answers_a_season_folder_hint(tmp_path):
    make(str(tmp_path / 'm' / 'Show' / 'Season 1'), 'Pilot.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    (it,) = lb.items(0)
    assert it['kind'] == 'movie' and 'season folder' in it['problems'][0]
    lb.set_item(it['id'], kind='movie')
    assert lb.by_id(it['id'])['problems'] == []


def test_moving_a_double_episode_to_another_season_keeps_both(tmp_path):
    for rel in ['Show S01E01-E02.mkv', 'Show S02E03.mkv']:
        make(str(tmp_path / 'm'), rel)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    dbl = next(i for i in lb.items(0) if i['episode'] == 1)
    lb.set_item(dbl['id'], season=2)
    assert lb.by_id(dbl['id'])['episode_last'] == 2
    assert all(i['problems'] == [] for i in lb.current())


def test_edits_write_the_disk_outside_the_lock(lib, monkeypatch):
    real = os.replace
    held = []

    def replace(a, b):
        held.append(lib.lock._is_owned())
        return real(a, b)
    monkeypatch.setattr(library.os, 'replace', replace)
    jp = find(lib, 'Jungle Park')
    lib.set_item(jp['id'], kids=True)
    lib.make_collection('JP', [jp['id']])
    lib.dismiss('toy')
    assert held and not any(held)


def test_three_digit_episode_titles_are_migrated(tmp_path):
    (tmp_path / 'library.json').write_text(json.dumps({'items': {'abc': {'episode': 100, 'title': 'Show S01E100'}}}),
                                           encoding='utf-8')
    assert 'title' not in library.Library(str(tmp_path)).data['items']['abc']



# ------------------------------------------------------------------ 7015d01 review
def test_second_movie_click_keeps_the_answer(tmp_path):
    make(str(tmp_path / 'm' / 'Show' / 'Season 1'), 'Pilot.mkv')
    make(str(tmp_path / 'm'), 'Heat 1995.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    pilot = next(i for i in lb.items(0) if i['title'] == 'Pilot')
    heat = next(i for i in lb.current() if i['title'] == 'Heat')
    lb.set_item(pilot['id'], kind='movie')
    lb.set_items([pilot['id'], heat['id']], kind='movie')          # again, as part of a multi-select
    assert lb.by_id(pilot['id'])['problems'] == []
    assert 'kind' not in lb.data['items'].get(heat['id'], {})      # an unflagged movie stores nothing


def test_a_movie_in_a_specials_folder_gets_no_hint(tmp_path):
    make(str(tmp_path / 'm' / 'Specials'), '300 (2006).mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    (it,) = lb.items(0)
    assert it['kind'] == 'movie' and it['problems'] == []


def test_nested_link_targets_and_a_link_to_a_parent_walk_once(tmp_path):
    films = tmp_path / 'E' / 'Films'
    make(str(films), 'Heat 1995.mkv')
    make(str(films / 'Action'), 'Die Hard 1988.mkv')
    root = tmp_path / 'M' / 'Media'
    root.mkdir(parents=True)
    _junction(root / 'L2', films / 'Action')                     # the inner one first, by name order
    _junction(root / 'Z1', films)
    _junction(root / 'all', tmp_path / 'M')                       # a link back up, above the media folder
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    its = lb.items(0)
    assert sorted(i['title'] for i in its) == ['Die Hard', 'Heat']
    assert all(not i.get('copies') for i in its)


def test_offline_directory_symlink_keeps_its_titles(tmp_path):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    root.mkdir()
    try:
        os.symlink(str(films), str(root / 'More'), target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks need Developer Mode or admin here')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    assert [i['title'] for i in lb.items(0)] == ['Alien']
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    assert [i['title'] for i in lb.current()] == ['Alien'] and lb.unreachable


def test_unreachable_lists_offline_links(tmp_path):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    root.mkdir()
    _junction(root / 'More', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    lb.items(0)
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    assert str(root / 'More') in lb.unreachable                       # named by the link, as typed


# ------------------------------------------------------------------ 6b46edd review
def test_names_come_from_the_media_folder_through_links(tmp_path):
    other = tmp_path / 'OtherDrive'
    make(str(other / 'Breaking Bad' / 'Season 1'), 'E01.mkv', b'breaking bad 1' * 100)
    make(str(other / 'Season 4'), 'E02.mkv', b'lost 4x2' * 100)
    root = tmp_path / 'Media'
    make(str(root / 'Lost' / 'Season 1'), 'E01.mkv', b'lost 1x1' * 100)
    _junction(root / 'BB', other / 'Breaking Bad')
    _junction(root / 'Lost' / 'Season 4', other / 'Season 4')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    shows = {(i['show'], i['season']) for i in lb.items(0) if i['kind'] == 'episode'}
    assert shows == {('BB', 1), ('Lost', 1), ('Lost', 4)}
    assert all(str(root) in i['path'] for i in lb.current())          # paths as named in the media folder


def test_names_starting_with_a_dot_are_kept(tmp_path):
    make(str(tmp_path / 'm'), '...And Justice for All (1979).mkv')
    make(str(tmp_path / 'm'), '.hack Sign S01E01.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    titles = sorted(i['title'] for i in lb.items(0))
    assert len(titles) == 2 and any('Justice' in t for t in titles)


def test_a_kids_folder_link_wins_over_a_shared_target(tmp_path):
    films = tmp_path / 'E' / 'Films'
    make(str(films / 'Kids'), 'Toy Shelf.mp4')
    make(str(films), 'Heat 1995.mkv')
    movies, kids = tmp_path / 'Movies', tmp_path / 'Kids'
    movies.mkdir()
    kids.mkdir()
    _junction(movies / 'All', films)
    _junction(kids / 'Cartoons', films / 'Kids')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(movies)}, {'path': str(kids), 'kids': True}])
    ts = next(i for i in lb.items(0) if i['title'] == 'Toy Shelf')
    assert ts['kids'] and not ts.get('copies')
    assert [m['title'] for m in lb.tv_view(kids=True)['movies']] == ['Toy Shelf']


def test_an_unlistable_subfolder_isnt_reported_as_offline(tmp_path, monkeypatch):
    make(str(tmp_path / 'm' / 'System Volume Information'), 'x.mkv')
    make(str(tmp_path / 'm' / 'Locked'), 'y.mkv')
    make(str(tmp_path / 'm'), 'Heat 1995.mkv')
    real = os.scandir

    def scandir(p):
        if str(p).endswith('Locked'):
            raise PermissionError('denied')
        return real(p)
    monkeypatch.setattr(library.os, 'scandir', scandir)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    assert [i['title'] for i in lb.items(0)] == ['Heat'] and lb.unreachable == []


def test_an_upgraded_store_counts_autostart_as_offered(tmp_path):
    (tmp_path / 'library.json').write_text(json.dumps({'folders': []}), encoding='utf-8')
    assert library.Library(str(tmp_path)).data['autostart_offered'] is True
    fresh = tmp_path / 'new'
    assert library.Library(str(fresh)).data['autostart_offered'] is False


# ------------------------------------------------------------------ 0401def review
def test_appledouble_files_and_dot_links_are_skipped(tmp_path):
    root = tmp_path / 'm'
    make(str(root), 'Heat 1995.mkv')
    make(str(root), '._Heat 1995.mkv', b'apple' * 10)
    make(str(tmp_path / 'snap'), 'Old 1990.mkv')
    _junction(root / '.snapshots', tmp_path / 'snap')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    assert [i['title'] for i in lb.items(0)] == ['Heat']


def test_a_kids_link_into_another_media_folder_makes_those_kids(tmp_path):
    make(str(tmp_path / 'Movies' / 'Animated'), 'Toy Shelf.mp4')
    make(str(tmp_path / 'Movies'), 'Heat 1995.mkv')
    kids = tmp_path / 'Kids'
    kids.mkdir()
    _junction(kids / 'Cartoons', tmp_path / 'Movies' / 'Animated')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'Movies')}, {'path': str(kids), 'kids': True}])
    ts = next(i for i in lb.items(0) if i['title'] == 'Toy Shelf')
    assert ts['kids'] and not ts.get('copies')
    assert [m['title'] for m in lb.tv_view(kids=True)['movies']] == ['Toy Shelf']


def test_a_collection_keeps_films_while_a_subfolder_cant_be_read(tmp_path, monkeypatch):
    make(str(tmp_path / 'm' / 'Locked'), 'Alien 1979.mkv')
    make(str(tmp_path / 'm'), 'Heat 1995.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    ids = [i['id'] for i in lb.items(0)]
    cid = lb.make_collection('Faves', ids, order='custom')
    real = os.scandir

    def scandir(p):
        if str(p).endswith('Locked'):
            raise PermissionError('denied')
        return real(p)
    monkeypatch.setattr(library.os, 'scandir', scandir)
    lb2 = library.Library(lb.dir)
    lb2.scan()                                     # first scan: Locked can't be read, Alien isn't known
    shown = [m['id'] for m in lb2.collection_members(cid)]
    lb2.update_collection(cid, members=shown, shown=shown)   # an edit while it's unreadable
    assert lb2.data['collections'][cid]['members'] == ids


def test_a_shared_target_is_fingerprinted_once(tmp_path, monkeypatch):
    films = tmp_path / 'E'
    make(str(films), 'Heat 1995.mkv')
    a, b = tmp_path / 'A', tmp_path / 'B'
    a.mkdir()
    b.mkdir()
    _junction(a / 'x', films)
    _junction(b / 'y', films)
    calls = []
    real_fp = library.fingerprint
    monkeypatch.setattr(library, 'fingerprint', lambda p, n: calls.append(p) or real_fp(p, n))
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(a)}, {'path': str(b)}])
    assert [i['title'] for i in lb.items(0)] == ['Heat'] and len(calls) == 1


def test_autostart_offered_for_a_broken_store(tmp_path):
    (tmp_path / 'library.json').write_text('{not json', encoding='utf-8')
    assert library.Library(str(tmp_path)).data['autostart_offered'] is True


# ------------------------------------------------------------------ b44a105 review
def test_a_locked_file_keeps_its_collection_place(tmp_path, monkeypatch):
    make(str(tmp_path / 'm'), 'Alien 1979.mkv')
    make(str(tmp_path / 'm'), 'Heat 1995.mkv', b'heat' * 100)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    ids = [i['id'] for i in lb.items(0)]
    cid = lb.make_collection('Faves', ids, order='custom')
    for f in os.listdir(lb.dir):
        if f.startswith('fingerprints'):
            os.remove(os.path.join(lb.dir, f))                 # the fingerprint cache is lost
    real = library.fingerprint

    def fp(p, n):
        if 'Alien' in p:
            raise PermissionError('in use')
        return real(p, n)
    monkeypatch.setattr(library, 'fingerprint', fp)
    lb2 = library.Library(lb.dir)
    lb2.scan()
    shown = [m['id'] for m in lb2.collection_members(cid)]
    lb2.update_collection(cid, members=shown, shown=shown)
    assert lb2.data['collections'][cid]['members'] == ids


def test_a_kids_link_to_a_folder_above_another_media_folder(tmp_path):
    make(str(tmp_path / 'Media' / 'Movies'), 'Toy Shelf.mp4')
    kids = tmp_path / 'Kids'
    kids.mkdir()
    _junction(kids / 'All', tmp_path / 'Media')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'Media' / 'Movies')}, {'path': str(kids), 'kids': True}])
    (ts,) = lb.items(0)
    assert ts['kids'] and not ts.get('copies')


def test_a_media_folder_inside_a_kids_folder_is_kids(tmp_path):
    make(str(tmp_path / 'Kids' / 'Shows'), 'Bluey S01E01.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'Kids'), 'kids': True}, {'path': str(tmp_path / 'Kids' / 'Shows')}])
    (ep,) = lb.items(0)
    assert ep['kids']


# ------------------------------------------------------------------ b01b460 review
def test_switching_kids_on_and_off_follows_a_link_into_another_folder(tmp_path):
    make(str(tmp_path / 'Movies' / 'Animated'), 'Toy Shelf.mp4')
    kids = tmp_path / 'Kids'
    kids.mkdir()
    _junction(kids / 'Cartoons', tmp_path / 'Movies' / 'Animated')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'Movies')}, {'path': str(kids)}])
    assert not lb.items(0)[0]['kids']
    lb.set_folders([{'path': str(tmp_path / 'Movies')}, {'path': str(kids), 'kids': True}])
    lb.scan()
    assert lb.current()[0]['kids']


def test_an_error_that_lasts_stops_holding_collections(tmp_path, monkeypatch):
    make(str(tmp_path / 'm' / 'Locked'), 'x.mkv')
    make(str(tmp_path / 'm'), 'Heat 1995.mkv')
    real = os.scandir

    def scandir(p):
        if str(p).endswith('Locked'):
            raise PermissionError('denied')
        return real(p)
    monkeypatch.setattr(library.os, 'scandir', scandir)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'm')}])
    lb.items(0)
    assert lb._incomplete
    t = library.time.time()
    lb2 = library.Library(lb.dir)                  # PLAY restarts: the clock is kept in library.json
    monkeypatch.setattr(library.time, 'time', lambda: t + library.ERROR_GRACE + 1)
    lb2.scan()
    assert not lb2._incomplete
    lb2.scan()
    assert not lb2._incomplete                     # and it stays that way


# ------------------------------------------------------------------ 6f019bc review
def test_a_new_kids_link_counts_without_a_restart(tmp_path):
    make(str(tmp_path / 'Movies' / 'Animated'), 'Toy Shelf.mp4')
    kids = tmp_path / 'Kids'
    kids.mkdir()
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'Movies')}, {'path': str(kids), 'kids': True}])
    assert not lb.items(0)[0]['kids']
    _junction(kids / 'Cartoons', tmp_path / 'Movies' / 'Animated')
    lb.scan()
    assert lb.current()[0]['kids']


def test_an_offline_link_stops_holding_collections_after_a_day(tmp_path, monkeypatch):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    _junction(root / 'More', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    lb.items(0)
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    assert lb._incomplete and lb.unreachable
    t = library.time.time()
    monkeypatch.setattr(library.time, 'time', lambda: t + library.ERROR_GRACE + 1)
    lb.scan()
    assert not lb._incomplete and lb.unreachable   # still named in the notice, no longer holding things back


def test_an_unreached_media_folder_keeps_its_error_clock(tmp_path, monkeypatch):
    make(str(tmp_path / 'nas' / 'Locked'), 'x.mkv')
    make(str(tmp_path / 'nas'), 'Heat 1995.mkv')
    real = os.scandir

    def scandir(p):
        if str(p).endswith('Locked'):
            raise PermissionError('denied')
        return real(p)
    monkeypatch.setattr(library.os, 'scandir', scandir)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'nas')}])
    lb.items(0)
    first = dict(lb.data['unreadable_since'])
    assert first
    os.rename(str(tmp_path / 'nas'), str(tmp_path / 'asleep'))
    lb.scan()                                      # the NAS sleeps
    assert lb.data['unreadable_since'] == first



# ------------------------------------------------------------------ 6ae30f6 review
def _offline_link_setup(tmp_path):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    _junction(root / 'More', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    lb.items(0)
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    return lb, root


def test_an_offline_link_keeps_its_clock_across_scans(tmp_path):
    lb, root = _offline_link_setup(tmp_path)
    first = dict(lb.data['unreadable_since'])
    assert first
    lb.scan()
    lb.scan()
    assert lb.data['unreadable_since'] == first


def test_a_future_or_broken_clock_doesnt_stick(tmp_path, monkeypatch):
    (tmp_path / 'library.json').write_text(json.dumps({'unreadable_since': {
        'a': True, 'b': 'x', 'c': 1e999, 'd': 5.0}}), encoding='utf-8')
    assert library.Library(str(tmp_path)).data['unreadable_since'] == {'d': 5.0}
    lb, root = _offline_link_setup(tmp_path / 'x')
    key = next(iter(lb.data['unreadable_since']))
    lb.data['unreadable_since'][key] = library.time.time() + 10 * 365 * 86400   # set ahead, then the clock fixed
    t = library.time.time()
    monkeypatch.setattr(library.time, 'time', lambda: t + library.ERROR_GRACE + 1)
    lb.scan()
    assert lb._incomplete                           # counted from now, not from the future
    monkeypatch.setattr(library.time, 'time', lambda: t + 2 * library.ERROR_GRACE + 2)
    lb.scan()
    assert not lb._incomplete


def test_a_removed_media_folder_forgets_its_clocks(tmp_path):
    lb, root = _offline_link_setup(tmp_path)
    assert lb.data['unreadable_since']
    other = tmp_path / 'Other'
    make(str(other), 'Up 2009.mkv')
    lb.set_folders([{'path': str(other)}])
    lb.scan()
    assert lb.data['unreadable_since'] == {}


def test_an_offline_media_folder_link_is_named_once(tmp_path):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    _junction(root / 'Films', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}, {'path': str(root / 'Films')}])
    lb.items(0)
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    assert len([u for u in lb.unreachable if u.lower().endswith('films')]) == 1
    assert not any(k.endswith('films') for k in lb.data['unreadable_since'])



# ------------------------------------------------------------------ 894cf49 review
def test_a_huge_number_in_the_settings_doesnt_stop_play(tmp_path):
    (tmp_path / 'library.json').write_text('{"unreadable_since": {"a": 1' + '0' * 400 + ', "b": 7}}',
                                           encoding='utf-8')
    assert library.Library(str(tmp_path)).data['unreadable_since'] == {'b': 7.0}


def test_an_offline_link_that_is_also_a_media_folder_keeps_its_titles(tmp_path):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    _junction(root / 'Films', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    assert sorted(i['title'] for i in lb.items(0)) == ['Alien', 'Heat']
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    lb.set_folders([{'path': str(root)}, {'path': str(root / 'Films')}])   # added while it is offline
    lb.scan()
    assert sorted(i['title'] for i in lb.current()) == ['Alien', 'Heat']
    lb.scan()
    assert sorted(i['title'] for i in lb.current()) == ['Alien', 'Heat']


def _locked_scandir(monkeypatch):
    real = os.scandir

    def scandir(p):
        if str(p).endswith('Locked'):
            raise PermissionError('denied')
        return real(p)
    monkeypatch.setattr(library.os, 'scandir', scandir)


def test_a_clock_under_a_link_that_goes_offline_is_kept(tmp_path, monkeypatch):
    other = tmp_path / 'Other'
    make(str(other / 'Locked'), 'x.mkv')
    make(str(other), 'Alien 1979.mkv')
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    _junction(root / 'More', other)
    _locked_scandir(monkeypatch)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    lb.items(0)
    locked = [k for k in lb.data['unreadable_since'] if k.endswith('locked')]
    assert locked
    os.rename(str(other), str(tmp_path / 'gone'))
    lb.scan()
    assert all(k in lb.data['unreadable_since'] for k in locked)


def test_a_clock_in_a_nested_media_folder_that_is_away_is_kept(tmp_path, monkeypatch):
    movies, nas = tmp_path / 'Movies', tmp_path / 'NAS4K'
    make(str(movies), 'Heat 1995.mkv')
    make(str(nas / 'Locked'), 'x.mkv')
    make(str(nas), 'Alien 1979.mkv', b'alien' * 50)
    _junction(movies / '4K', nas)
    _locked_scandir(monkeypatch)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(movies)}, {'path': str(movies / '4K')}])
    lb.items(0)
    locked = dict(lb.data['unreadable_since'])
    assert locked
    os.rename(str(nas), str(tmp_path / 'asleep'))  # the NAS sleeps: 4K is there, but leads nowhere
    lb.scan()
    assert lb.data['unreadable_since'] == locked and lb.missing == []


def test_a_media_folder_with_a_trailing_slash_in_the_settings(tmp_path):
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    (tmp_path / 's').mkdir()
    (tmp_path / 's' / 'library.json').write_text(json.dumps({'folders': [{'path': str(root) + '\\\\'}]}),
                                                 encoding='utf-8')
    lb = library.Library(str(tmp_path / 's'))
    assert lb.folders[0]['path'] == str(root)



# ------------------------------------------------------------------ 6fb1782 review
def test_an_unreadable_subfolder_added_as_a_media_folder_keeps_its_titles(tmp_path, monkeypatch):
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv')
    make(str(root / 'Locked'), 'Alien 1979.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root)}])
    assert sorted(i['title'] for i in lb.items(0)) == ['Alien', 'Heat']
    _locked_scandir(monkeypatch)
    lb.scan()
    lb.set_folders([{'path': str(root)}, {'path': str(root / 'Locked')}])
    lb.scan()
    assert sorted(i['title'] for i in lb.current()) == ['Alien', 'Heat']


def test_a_kids_link_that_is_also_its_own_folder_makes_its_films_kids(tmp_path):
    make(str(tmp_path / 'Movies' / 'Animated'), 'Toy Shelf.mp4')
    kids = tmp_path / 'Kids'
    kids.mkdir()
    _junction(kids / 'Cartoons', tmp_path / 'Movies' / 'Animated')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(kids), 'kids': True}, {'path': str(kids / 'Cartoons')}])
    (ts,) = lb.items(0)
    assert ts['kids']



# ------------------------------------------------------------------ 810cdfe review
def _media_with_offline_films(tmp_path, folders_before):
    films = tmp_path / 'OtherDrive'
    make(str(films), 'Alien 1979.mkv')
    make(str(films / 'Sub'), 'Brazil 1985.mkv', b'brazil' * 50)
    root = tmp_path / 'Media'
    make(str(root), 'Heat 1995.mkv', b'heat' * 50)
    _junction(root / 'Films', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(root / p) if p else str(root)} for p in folders_before])
    lb.items(0)
    os.rename(str(films), str(tmp_path / 'gone'))
    lb.scan()
    return lb, root


def titles(lb):
    return sorted(i['title'] for i in lb.current())


def test_case_c_switching_to_the_parent_folder_while_offline(tmp_path):
    lb, root = _media_with_offline_films(tmp_path, ['Films'])
    assert titles(lb) == ['Alien', 'Brazil']
    lb.set_folders([{'path': str(root)}])
    lb.scan()
    assert titles(lb) == ['Alien', 'Brazil', 'Heat']


def test_case_d_removing_the_offline_folder(tmp_path):
    lb, root = _media_with_offline_films(tmp_path, ['', 'Films'])
    assert titles(lb) == ['Alien', 'Brazil', 'Heat']
    lb.set_folders([{'path': str(root)}])
    lb.scan()
    assert titles(lb) == ['Alien', 'Brazil', 'Heat']


def test_case_e_a_title_stays_with_the_deepest_folder(tmp_path):
    lb, root = _media_with_offline_films(tmp_path, ['', r'Films\Sub'])
    assert titles(lb) == ['Alien', 'Brazil', 'Heat']
    lb.set_folders([{'path': str(root)}, {'path': str(root / 'Films' / 'Sub')}, {'path': str(root / 'Films')}])
    lb.scan()
    assert titles(lb) == ['Alien', 'Brazil', 'Heat']
    sub = os.path.normcase(str(root / 'Films' / 'Sub'))
    films = os.path.normcase(str(root / 'Films'))
    assert [os.path.basename(e[1]) for e in lb._raw[sub]] == ['Brazil 1985.mkv']
    assert [os.path.basename(e[1]) for e in lb._raw[films]] == ['Alien 1979.mkv']



# ------------------------------------------------------------------ 8dd6488 review
def test_removing_a_folder_a_link_leads_to_while_it_is_offline(tmp_path):
    other = tmp_path / 'Other'
    make(str(other), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', other)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(other)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(other), str(tmp_path / 'gone'))
    lb.scan()
    lb.set_folders([{'path': str(media)}])                 # the Other folder was redundant: removed
    lb.scan()
    assert titles_now(lb) == ['Alien', 'Heat']
    alien = next(i for i in lb.current() if i['title'] == 'Alien')
    assert os.path.normcase(str(media / 'Films')) in os.path.normcase(alien['path'])


def test_a_deleted_media_folder_leaves_no_ghosts(tmp_path):
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    make(str(media / 'Films'), 'Alien 1979.mkv')
    make(str(tmp_path), 'single.mkv', b'single' * 50)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Films')}, {'path': str(tmp_path / 'single.mkv')}])
    assert titles_now(lb) == ['Alien', 'Heat']
    shutil.rmtree(str(media / 'Films'))
    lb.scan()
    assert titles_now(lb) == ['Heat']


def titles_now(lb):
    lb.scan()
    return sorted(i['title'] for i in lb.current())



# ------------------------------------------------------------------ 2216030 review
def test_case_f_two_links_to_one_place_one_removed(tmp_path):
    other = tmp_path / 'Other'
    make(str(other), 'Alien 1979.mkv')
    a, media = tmp_path / 'A', tmp_path / 'Media'
    a.mkdir()
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(a / 'F', other)
    _junction(media / 'Films', other)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(a)}, {'path': str(media)}, {'path': str(other)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    lb.set_folders([{'path': str(media)}, {'path': str(other)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(other), str(tmp_path / 'gone'))
    lb.scan()
    lb.set_folders([{'path': str(media)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    assert all(os.path.normcase(str(a)) not in k for k in lb._links)


def test_case_g_a_link_into_a_link_keeps_a_clean_path(tmp_path):
    store = tmp_path / 'Store'
    make(str(store / 'Movies' / 'Films'), 'Alien 1979.mkv')
    j = tmp_path / 'J'
    _junction(j, store / 'Movies' / 'Films')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'All', store)
    lb = library.Library(str(tmp_path / 'store-settings'))
    lb.set_folders([{'path': str(media)}, {'path': str(j)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(store), str(tmp_path / 'gone'))
    lb.scan()
    lb.set_folders([{'path': str(media)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    alien = next(i for i in lb.current() if i['title'] == 'Alien')
    assert alien['path'] == str(media / 'All' / 'Movies' / 'Films' / 'Alien 1979.mkv')   # names as written


def test_case_h_a_deleted_parent_leaves_no_ghosts(tmp_path):
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    make(str(media / 'Kids' / 'Cartoons'), 'Up 2009.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids' / 'Cartoons')}])
    assert titles_now(lb) == ['Heat', 'Up']
    shutil.rmtree(str(media / 'Kids'))
    assert titles_now(lb) == ['Heat']
    assert lb.unreachable == [] and not lb._incomplete
    assert lb.missing == [str(media / 'Kids' / 'Cartoons')]


def test_a_file_where_a_media_folder_was(tmp_path):
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    make(str(media / 'Films'), 'Alien 1979.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Films')}])
    assert titles_now(lb) == ['Alien', 'Heat']
    shutil.rmtree(str(media / 'Films'))
    (media / 'Films').write_bytes(b'not a folder')
    assert titles_now(lb) == ['Heat']
    assert library._gone(str(media / 'Films'), {os.path.normcase(str(media))})


def test_a_folder_behind_an_offline_link_is_not_deleted(tmp_path):
    other = tmp_path / 'Other'
    make(str(other / 'Sub'), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    media.mkdir()
    _junction(media / 'Films', other)
    os.rename(str(other), str(tmp_path / 'gone'))
    assert not library._gone(str(media / 'Films' / 'Sub'), {os.path.normcase(str(media))})



# ------------------------------------------------------------------ c4b35e1 review
def test_a_deleted_kids_folder_stops_making_films_kids(tmp_path):
    other = tmp_path / 'Other'
    make(str(other), 'Up 2009.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    (media / 'Kids' / 'Cartoons').mkdir(parents=True)
    _junction(media / 'Kids' / 'Cartoons' / 'Link', other)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids' / 'Cartoons'), 'kids': True},
                    {'path': str(other)}])
    assert next(i for i in lb.items(0) if i['title'] == 'Up')['kids']      # Kids flows through the link
    os.rmdir(str(media / 'Kids' / 'Cartoons' / 'Link'))
    shutil.rmtree(str(media / 'Kids'))
    lb.scan()
    make(str(other), 'Brazil 1985.mkv', b'brazil' * 50)
    lb.scan()
    brazil = next(i for i in lb.current() if i['title'] == 'Brazil')
    assert not brazil['kids']


def test_a_renamed_media_folder_is_reported(tmp_path):
    media = tmp_path / 'Media'
    make(str(media / 'Kids'), 'Up 2009.mkv')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids')}])
    lb.items(0)
    os.rename(str(media / 'Kids'), str(media / 'Children'))
    lb.scan()
    assert lb.missing == [str(media / 'Kids')] and lb.unreachable == []


def test_a_clock_under_a_deleted_folder_is_dropped(tmp_path, monkeypatch):
    media = tmp_path / 'Media'
    make(str(media / 'Sub' / 'Locked'), 'x.mkv')
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _locked_scandir(monkeypatch)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Sub')}])
    lb.items(0)
    assert lb.data['unreadable_since']
    monkeypatch.undo()                             # (rmtree lists folders too)
    shutil.rmtree(str(media / 'Sub'))
    lb.scan()
    assert lb.data['unreadable_since'] == {}


def test_a_deleted_link_is_forgotten(tmp_path):
    other = tmp_path / 'Other'
    make(str(other), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', other)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}])
    lb.items(0)
    assert lb._links
    os.rmdir(str(media / 'Films'))
    lb.scan()
    assert lb._links == {}


def test_the_kept_titles_pool_is_reused_until_something_changes(tmp_path):
    lb, root = _offline_link_setup(tmp_path)
    first = lb._pool_cache[1]
    assert first is not None
    lb.scan()
    assert lb._pool_cache[1] is first                      # nothing changed: the same pool
    make(str(root), 'Up 2009.mkv', b'up' * 50)
    lb.scan()
    lb.scan()
    assert lb._pool_cache[1] is not first                  # a new title: built again



# ------------------------------------------------------------------ 0ac5404 review
def test_links_under_a_renamed_folder_are_forgotten(tmp_path):
    other = tmp_path / 'Other'
    make(str(other), 'Up 2009.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    (media / 'Kids').mkdir()
    _junction(media / 'Kids' / 'Link', other)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids'), 'kids': True}])
    lb.items(0)
    assert any(k.endswith(os.path.join('kids', 'link')) for k in lb._links)
    os.rename(str(media / 'Kids'), str(media / 'Children'))
    lb.scan()
    assert not any(k.endswith(os.path.join('kids', 'link')) for k in lb._links)


def test_the_pool_is_built_again_when_one_input_changes(tmp_path):
    other = tmp_path / 'OtherDrive'
    make(str(other), 'Alien 1979.mkv')
    media, a, b, e = tmp_path / 'Media', tmp_path / 'A', tmp_path / 'B', tmp_path / 'E'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    for d in (a, b, e):
        d.mkdir()
    _junction(media / 'More', other)
    _junction(tmp_path / 'R', a)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(tmp_path / 'R')}])
    lb.items(0)
    os.rename(str(other), str(tmp_path / 'gone'))
    lb.scan()
    lb.scan()
    steady = lb._pool_cache[1]
    lb.scan()
    assert lb._pool_cache[1] is steady                          # nothing changed: reused
    _junction(media / 'Extra', e)                               # a new (empty) link: only the links change
    lb.scan()
    after_link = lb._pool_cache[1]
    assert after_link is not steady
    lb.scan()
    assert lb._pool_cache[1] is after_link
    os.rmdir(str(tmp_path / 'R'))                               # the media folder R now leads elsewhere:
    _junction(tmp_path / 'R', b)                                # only its real location changes
    lb.scan()
    assert lb._pool_cache[1] is not after_link


def test_a_renamed_folder_with_a_sleeping_link_keeps_its_titles(tmp_path):
    nas = tmp_path / 'NAS'
    make(str(nas), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    (media / 'Kids').mkdir()
    _junction(media / 'Kids' / 'Link', nas)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids')}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(nas), str(tmp_path / 'asleep'))              # the NAS sleeps
    lb.scan()
    os.rename(str(media / 'Kids'), str(media / 'Children'))    # and the folder is renamed in Explorer
    assert titles_now(lb) == ['Alien', 'Heat']
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Children')}])   # Settings updated
    assert titles_now(lb) == ['Alien', 'Heat']



# ------------------------------------------------------------------ 41e603e review
def test_an_offline_link_keeps_the_target_it_had_online(tmp_path, monkeypatch):
    # A link to a mapped drive, as Windows does it: the link itself says Z:\Movies, online it resolves to the
    # \nas share behind Z:, offline realpath can only give back Z:\Movies.
    nas = tmp_path / 'NAS'
    make(str(nas), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', nas)
    films = os.path.normcase(str(media / 'Films'))
    real_readlink, real_realpath = library.os.readlink, library.os.path.realpath
    monkeypatch.setattr(library.os, 'readlink',
                        lambda p: r'Z:\Movies' if os.path.normcase(str(p)) == films else real_readlink(p))

    def realpath(p, *a, **k):
        if os.path.normcase(str(p)) == films and not os.path.isdir(str(nas)):
            return r'Z:\Movies'
        return real_realpath(p, *a, **k)
    monkeypatch.setattr(library.os.path, 'realpath', realpath)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(nas)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(nas), str(tmp_path / 'asleep'))
    lb.scan()
    lb.set_folders([{'path': str(media)}])           # the NAS folder is removed from Settings while it sleeps
    assert titles_now(lb) == ['Alien', 'Heat']
    assert lb._links[films][1] == os.path.normcase(str(nas))


def test_switching_a_folder_from_its_link_to_its_target_while_unplugged(tmp_path):
    films = tmp_path / 'Films'
    make(str(films), 'Alien 1979.mkv')
    _junction(tmp_path / 'J', films)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(tmp_path / 'J')}])
    assert titles_now(lb) == ['Alien']
    os.rename(str(films), str(tmp_path / 'unplugged'))
    assert titles_now(lb) == ['Alien']
    lb.scan()
    # Settings: from the link J to Films itself, while it's unplugged. Only the folder changes: both lead to
    # the same real place, and the last scan's titles are the same.
    lb.set_folders([{'path': str(films)}])
    assert titles_now(lb) == ['Alien']



# ------------------------------------------------------------------ 8f8e372 review
def test_a_rename_doesnt_make_a_false_copy(tmp_path):
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    make(str(media / 'Kids' / 'Sub'), 'Up 2009.mkv')
    _junction(media / 'Cartoons', media / 'Kids' / 'Sub')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids')}])
    assert titles_now(lb) == ['Heat', 'Up']
    os.rename(str(media / 'Kids'), str(media / 'Children'))
    assert titles_now(lb) == ['Heat', 'Up']
    up = next(i for i in lb.current() if i['title'] == 'Up')
    assert not up.get('copies')


def test_a_repointed_offline_junction_follows_its_new_target(tmp_path):
    d, e = tmp_path / 'D' / 'Films', tmp_path / 'E' / 'Films'
    make(str(d), 'Alien 1979.mkv')
    make(str(e), 'Brazil 1985.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', d)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(d)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rmdir(str(media / 'Films'))
    os.rename(str(e), str(tmp_path / 'E' / 'away'))         # E is unplugged ...
    _junction(media / 'Films', e)                            # ... and Films now points there
    lb.scan()
    assert lb._links[os.path.normcase(str(media / 'Films'))][1] == os.path.normcase(str(e))
    lb.set_folders([{'path': str(media)}])                   # D removed from Settings
    assert titles_now(lb) == ['Heat']                        # Alien isn't kept under Films any more



# ------------------------------------------------------------------ f6715c4 review
def test_an_unplugged_second_copy_keeps_the_title(tmp_path):
    media, usb = tmp_path / 'Media', tmp_path / 'USB'
    make(str(media), 'Alien 1979.mkv')
    make(str(usb), 'Alien 1979.mkv')                  # the same file, twice
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(usb)}])
    alien = next(i for i in lb.items(0) if i['title'] == 'Alien')
    assert alien['copies'] == 2
    os.rename(str(usb), str(tmp_path / 'unplugged'))
    assert titles_now(lb) == ['Alien', 'Heat']
    assert not next(i for i in lb.current() if i['title'] == 'Alien').get('copies')   # USB folder away: 1 here
    os.remove(str(media / 'Alien 1979.mkv'))          # "2 copies": the user deletes one
    assert titles_now(lb) == ['Alien', 'Heat']        # the other is on the unplugged drive
    assert not next(i for i in lb.current() if i['title'] == 'Alien').get('copies')


def test_a_link_to_a_moved_folder_keeps_its_titles(tmp_path):
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    make(str(media / 'Kids'), 'Up 2009.mkv')
    _junction(media / 'Cartoons', media / 'Kids')
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(media / 'Kids')}])
    assert titles_now(lb) == ['Heat', 'Up']
    os.rename(str(media / 'Kids'), str(tmp_path / 'KidsMoved'))   # moved out: Cartoons now leads nowhere
    assert titles_now(lb) == ['Heat', 'Up']



# ------------------------------------------------------------------ 42b0a45 review
def test_a_copy_that_plays_beats_one_on_an_unplugged_drive(tmp_path):
    usb, media, backup = tmp_path / 'USB', tmp_path / 'Media', tmp_path / 'Backup'
    make(str(usb), 'Alien 1979.mkv')
    make(str(backup), 'Alien 1979.mkv')
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'USB', usb)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(backup)}])
    assert next(i for i in lb.items(0) if i['title'] == 'Alien')['copies'] == 2
    os.rename(str(usb), str(tmp_path / 'unplugged'))
    lb.scan()
    alien = next(i for i in lb.current() if i['title'] == 'Alien')
    assert alien['path'] == str(backup / 'Alien 1979.mkv') and os.path.exists(alien['path'])
    assert not alien.get('copies')                  # the unplugged one isn't counted while it's away
    os.rename(str(tmp_path / 'unplugged'), str(usb))   # plugged back in
    lb.scan()
    alien = next(i for i in lb.current() if i['title'] == 'Alien')
    assert alien['copies'] == 2 and alien['path'] == str(media / 'USB' / 'Alien 1979.mkv')


def test_a_failed_readlink_offline_keeps_the_remembered_target(tmp_path, monkeypatch):
    nas = tmp_path / 'NAS'
    make(str(nas), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', nas)
    films = os.path.normcase(str(media / 'Films'))
    real_readlink, real_realpath = library.os.readlink, library.os.path.realpath
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(nas)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(nas), str(tmp_path / 'asleep'))

    def readlink(p):                                # offline the link can't be read now
        if os.path.normcase(str(p)) == films:
            raise OSError('cannot read the link now')
        return real_readlink(p)

    def realpath(p, *a, **k):                       # and realpath can only give the mapped-drive spelling
        return r'Z:\Movies' if os.path.normcase(str(p)) == films else real_realpath(p, *a, **k)
    monkeypatch.setattr(library.os, 'readlink', readlink)
    monkeypatch.setattr(library.os.path, 'realpath', realpath)
    lb.scan()
    lb.set_folders([{'path': str(media)}])           # the NAS folder is removed from Settings while it sleeps
    assert titles_now(lb) == ['Alien', 'Heat']


def test_a_link_first_read_without_its_raw_target_isnt_taken_as_repointed(tmp_path, monkeypatch):
    nas = tmp_path / 'NAS'
    make(str(nas), 'Alien 1979.mkv')
    media = tmp_path / 'Media'
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    _junction(media / 'Films', nas)
    films = os.path.normcase(str(media / 'Films'))
    real_readlink, real_realpath = library.os.readlink, library.os.path.realpath

    def readlink(p):                                # online once without an answer, offline it answers
        if os.path.normcase(str(p)) == films and os.path.isdir(str(nas)):
            raise OSError('busy')
        return r'Z:\Movies' if os.path.normcase(str(p)) == films else real_readlink(p)

    def realpath(p, *a, **k):
        if os.path.normcase(str(p)) == films and not os.path.isdir(str(nas)):
            return r'Z:\Movies'
        return real_realpath(p, *a, **k)
    monkeypatch.setattr(library.os, 'readlink', readlink)
    monkeypatch.setattr(library.os.path, 'realpath', realpath)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(nas)}])
    assert titles_now(lb) == ['Alien', 'Heat']
    os.rename(str(nas), str(tmp_path / 'asleep'))
    lb.scan()
    lb.set_folders([{'path': str(media)}])
    assert titles_now(lb) == ['Alien', 'Heat']


# ------------------------------------------------------------------ 90aa7e3 review
def test_a_film_on_two_unplugged_folders_stays_with_the_first(tmp_path):
    usb1, usb2, media = tmp_path / 'USB1', tmp_path / 'USB2', tmp_path / 'Media'
    make(str(usb1), 'Alien 1979.mkv')
    make(str(usb2), 'Alien 1979.mkv')
    make(str(media), 'Heat 1995.mkv', b'heat' * 50)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(media)}, {'path': str(usb1)}, {'path': str(usb2)}])
    alien = next(i for i in lb.items(0) if i['title'] == 'Alien')
    assert alien['copies'] == 2 and alien['path'] == str(usb1 / 'Alien 1979.mkv')
    os.rename(str(usb1), str(tmp_path / 'away1'))
    os.rename(str(usb2), str(tmp_path / 'away2'))
    lb.scan()
    assert lb.unreachable == [str(usb1), str(usb2)]           # both unplugged, not gone
    fp = alien['id']
    assert {(fp, str(usb1 / 'Alien 1979.mkv')), (fp, str(usb2 / 'Alien 1979.mkv'))} <= lb._kept   # both kept
    alien = next(i for i in lb.current() if i['title'] == 'Alien')
    assert alien['path'] == str(usb1 / 'Alien 1979.mkv')      # the first folder in Settings, as when found
    assert alien['folder'] == str(usb1)



# ------------------------------------------------------------------ 1a25fcb review
def test_an_older_kids_folder_tags_once_then_is_ordinary(tmp_path):
    kids = tmp_path / 'Kids'
    make(str(kids), 'Up 2009.mkv', b'up' * 50)
    store = tmp_path / 'store'
    store.mkdir()
    (store / 'library.json').write_text(json.dumps({'folders': [{'path': str(kids), 'kids': True}]}),
                                        encoding='utf-8')
    lb = library.Library(str(store))
    assert next(i for i in lb.items(0) if i['title'] == 'Up')['kids']     # its files: Kids, as before
    assert lb.folders[0]['kids'] is False                                   # ... and the folder is ordinary now
    make(str(kids), 'Heat 1995.mkv', b'heat' * 50)
    lb.scan()
    assert not next(i for i in lb.current() if i['title'] == 'Heat')['kids']   # a film added later isn't
    assert library.Library(str(store)).folders[0]['kids'] is False         # saved



def test_an_older_kids_folder_waits_for_a_full_read(tmp_path, monkeypatch):
    kids = tmp_path / 'Kids'
    make(str(kids), 'Up 2009.mkv', b'up' * 50)
    make(str(kids / 'Locked'), 'Cars 2006.mkv', b'cars' * 50)
    store = tmp_path / 'store'
    store.mkdir()
    (store / 'library.json').write_text(json.dumps({'folders': [{'path': str(kids), 'kids': True}]}),
                                        encoding='utf-8')
    real = os.scandir
    _locked_scandir(monkeypatch)
    lb = library.Library(str(store))
    lb.items(0)
    assert lb.folders[0]['kids'] is False                                   # retired after what it could read ...
    assert lb.data['kids_pending']                                          # ... the rest is remembered
    make(str(kids), 'Heat 1995.mkv', b'heat' * 50)                          # (a film added now isn't Kids)
    monkeypatch.setattr(library.os, 'scandir', real)                        # readable again
    lb.scan()
    assert next(i for i in lb.current() if i['title'] == 'Cars')['kids']    # the file it couldn't read: tagged
    assert not next(i for i in lb.current() if i['title'] == 'Heat')['kids']
    assert lb.data['kids_pending'] == []


def test_settings_saved_before_the_first_scan_keep_an_older_kids_folder(tmp_path):
    kids = tmp_path / 'Kids'
    make(str(kids), 'Up 2009.mkv', b'up' * 50)
    store = tmp_path / 'store'
    store.mkdir()
    (store / 'library.json').write_text(json.dumps({'folders': [{'path': str(kids), 'kids': True}]}),
                                        encoding='utf-8')
    lb = library.Library(str(store))
    lb.set_folders([{'path': str(kids), 'kids': False}])                    # Settings, before any scan
    assert next(i for i in lb.items(0) if i['title'] == 'Up')['kids']      # its files still get tagged once
    make(str(kids), 'Heat 1995.mkv', b'heat' * 50)
    lb.scan()
    assert not next(i for i in lb.current() if i['title'] == 'Heat')['kids']
