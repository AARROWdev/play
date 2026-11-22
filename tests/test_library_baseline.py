"""Library and names fixes from the baseline review (B2, B4, B8, B10-B14, B19, B23-B25, B31-B38, B43, B50, B56)."""
import json
import os
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
    assert library.Library(str(tmp_path)).folders == [{'path': 'D:/m', 'kids': False}]


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
    assert any('otherdrive' in u.lower() for u in lb.unreachable)
