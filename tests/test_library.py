"""Library engine: names, shows, collections, tags. Uses small generated files in a temp folder only."""
import os

import pytest

from play import library, names


# ------------------------------------------------------------------ names
@pytest.mark.parametrize('stem,title,year', [
    ('Jungle Park 1993', 'Jungle Park', 1993),
    ('Jungle.Park.1993.1080p.BluRay.x264', 'Jungle Park', 1993),
    ('Termodroid 2 Reckoning Day 1991', 'Termodroid 2 Reckoning Day', 1991),
    ('The Flea (1986)', 'The Flea', 1986),
    ('Toy Shelf', 'Toy Shelf', None),
    ('Toy Shelf 2', 'Toy Shelf 2', None),
    ('2001 A Space Odyssey 1968', '2001 A Space Odyssey', 1968),
    ('1917', '1917', None),
    ('Blade Runner 2049 (2017) [YTS]', 'Blade Runner 2049', 2017),
])
def test_movie_names(stem, title, year):
    assert names.movie(stem) == (title, year)


@pytest.mark.parametrize('path,want', [
    (r'N:\M\Ann Droid S01E02.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid - S1E2.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann.Droid.S01E02.1080p.WEB-DL.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid S01 E02 Pilot.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid 1x02.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid Season 1 Episode 2.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid (2026)\Season 1\E02.mkv', ('Ann Droid', 1, 2)),
    (r'N:\M\Ann Droid\Season 2\03 The Return.mkv', ('Ann Droid', 2, 3)),
    (r'N:\M\Ann Droid\S01E04.mkv', ('Ann Droid', 1, 4)),
])
def test_episode_names(path, want):
    assert names.episode(path, r'N:\M') == want


@pytest.mark.parametrize('path', [
    r'N:\M\Jungle Park 1993.mp4', r'N:\M\Toy Shelf 2.mp4', r'N:\M\Season 1\E02.mkv',  # no show folder above
    r'N:\M\Ocean 11 2001.mkv',
])
def test_not_episodes(path):
    assert names.episode(path, r'N:\M') is None


def test_sort_ignores_articles():
    assert sorted(['The Flea', 'Mannequin', 'A Bug', 'Conor'], key=names.sort_key) == ['A Bug', 'Conor', 'The Flea', 'Mannequin']


# ------------------------------------------------------------------ library
def make(root, rel, data=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(data if data is not None else rel.encode() * 50)
    return p


@pytest.fixture
def lib(tmp_path):
    home, kids = tmp_path / 'home', tmp_path / 'kids'
    for rel in ['Jungle Park 1993.mp4', 'Jungle Park III 2001.mp4', 'Jungle World 2015.mp4',
                'Jungle Park II The Lost Valley 1997.mp4', 'Conor The Barbarian 1982.mkv',
                'Ann Droid S01E01.mkv', 'Ann Droid S01E02.mkv', 'Ann Droid S01E04.mkv', 'Old Home Video.avi']:
        make(str(home), rel)
    for rel in ['Toy Shelf.mp4', 'Toy Shelf 2.mp4']:
        make(str(kids), rel)
    lb = library.Library(str(tmp_path / 'store'))
    lb.set_folders([{'path': str(home)}, {'path': str(kids), 'kids': True}])
    return lb


def find(lb, title):
    return next(i for i in lb.items(0) if i['title'] == title)


def test_scan_reads_kinds_and_kids(lib):
    its = lib.items(0)
    assert len(its) == 11
    assert find(lib, 'Jungle Park')['year'] == 1993
    ep = find(lib, 'Ann Droid S01E02')
    assert (ep['kind'], ep['show'], ep['season'], ep['episode']) == ('episode', 'Ann Droid', 1, 2)
    assert find(lib, 'Toy Shelf')['kids'] and not find(lib, 'Conor The Barbarian')['kids']


def test_problems_flag_format_and_missing_episode(lib):
    assert any('AVI' in p for p in find(lib, 'Old Home Video')['problems'])
    assert find(lib, 'Ann Droid S01E04')['problems'] == ['Season 1 is missing episode 3']
    assert find(lib, 'Jungle Park')['problems'] == []


def test_tags_follow_a_renamed_file(lib, tmp_path):
    jp = find(lib, 'Jungle Park')
    lib.set_item(jp['id'], kids=True, title='JP')
    os.rename(jp['path'], str(tmp_path / 'home' / 'renamed.mp4'))
    moved = lib.by_id(jp['id']) if lib.scan() else None
    assert moved['file'] == 'renamed.mp4' and moved['kids'] and moved['title'] == 'JP'


def test_tags_survive_restart(lib):
    jp = find(lib, 'Jungle Park')
    lib.set_item(jp['id'], hidden=True)
    again = library.Library(lib.dir)
    assert again.by_id(jp['id'])['hidden'] is True


def test_override_back_to_auto_removes_it(lib):
    c = find(lib, 'Conor The Barbarian')
    lib.set_item(c['id'], kids=True)
    lib.set_item(c['id'], kids=False)
    assert c['id'] not in lib.data['items']


def test_movie_can_become_episode_and_back(lib):
    c = find(lib, 'Conor The Barbarian')
    lib.set_item(c['id'], kind='episode', show='Conor', season=1, episode=1)
    assert lib.by_id(c['id'])['kind'] == 'episode'
    lib.set_item(c['id'], kind='episode', show=None)      # incomplete: stays a movie
    assert lib.by_id(c['id'])['kind'] == 'movie'


def test_suggestion_then_collection_in_release_order(lib):
    sug = lib.suggestions()
    jur = next(s for s in sug if s['key'] == 'jungle')
    assert jur['name'] == 'Jungle Park' and len(jur['members']) == 4
    cid = lib.make_collection(jur['name'], jur['members'])
    assert [m['year'] for m in lib.collection_members(cid)] == [1993, 1997, 2001, 2015]
    assert not any(s['key'] == 'jungle' for s in lib.suggestions())


def test_custom_order_and_one_collection_per_movie(lib):
    ids = [find(lib, t)['id'] for t in ('Jungle World', 'Jungle Park')]
    a = lib.make_collection('A', ids, order='custom')
    assert [m['title'] for m in lib.collection_members(a)] == ['Jungle World', 'Jungle Park']
    b = lib.make_collection('B', ids[:1])
    assert [m['title'] for m in lib.collection_members(a)] == ['Jungle Park']
    assert lib.by_id(ids[0])['collection'] == b
    lib.update_collection(a, members=[])
    assert a not in lib.data['collections']


def test_dismissed_suggestion_stays_gone(lib):
    lib.dismiss('toy')
    assert not any(s['key'] == 'toy' for s in library.Library(lib.dir).suggestions())


def test_tv_view_home_and_kids(lib):
    jur = next(s for s in lib.suggestions() if s['key'] == 'jungle')
    lib.make_collection('Jungle Park', jur['members'])
    home = lib.tv_view(kids=False)
    titles = [m['title'] for m in home['movies']]
    assert 'Jungle Park' in titles and 'Jungle World' not in titles and 'Toy Shelf' in titles
    col = next(m for m in home['movies'] if m['kind'] == 'collection')
    assert len(col['items']) == 4 and col['items'][0]['url'].startswith('/media/')
    assert home['shows'][0]['name'] == 'Ann Droid'
    assert [e['episode'] for e in home['shows'][0]['seasons'][0]['episodes']] == [1, 2, 4]
    kids = lib.tv_view(kids=True)
    assert [m['title'] for m in kids['movies']] == ['Toy Shelf', 'Toy Shelf 2'] and kids['shows'] == []


def test_hidden_left_out_of_tv(lib):
    c = find(lib, 'Conor The Barbarian')
    lib.set_item(c['id'], hidden=True)
    assert 'Conor The Barbarian' not in [m['title'] for m in lib.tv_view()['movies']]


def test_copy_of_same_file_listed_once(lib, tmp_path):
    src = find(lib, 'Conor The Barbarian')['path']
    make(str(tmp_path / 'home' / 'backup'), 'Conor copy.mkv', open(src, 'rb').read())
    its = [i for i in lib.items(0) if 'Conor' in i['title']]
    assert len(its) == 1 and '2 copies of this file' in its[0]['problems']


def test_import_old_config(tmp_path):
    p = tmp_path / 'config.json'
    p.write_text('{"port": 8500, "libraries": {"HOME": ["N:\\\\H"], "KIDS": ["N:\\\\K"]}}')
    assert library.import_old_config(str(p)) == [{'path': 'N:\\H', 'kids': False}, {'path': 'N:\\K', 'kids': True}]


def test_bad_store_file_starts_clean(tmp_path):
    (tmp_path / 'library.json').write_text('not json')
    assert library.Library(str(tmp_path)).folders == []


# ------------------------------------------------------------------ review fixes (1348321)
@pytest.mark.parametrize('stem,title,year', [
    ('The Extended Family 2019', 'The Extended Family', 2019),
    ('Blade Runner 2049', 'Blade Runner 2049', None),
    ('Heat.1995.EXTENDED.1080p.BluRay', 'Heat', 1995),
    ('Proper Behaviour 2014 PROPER', 'Proper Behaviour', 2014),
])
def test_junk_words_only_trail(stem, title, year):
    assert names.movie(stem) == (title, year)


def test_repack_before_episode_marker():
    assert names.episode(r'N:\M\Show.REPACK.S01E02.mkv', r'N:\M') == ('Show', 1, 2)


def test_kids_tag_follows_a_file_out_of_the_kids_folder(lib, tmp_path):
    ts = find(lib, 'Toy Shelf')
    os.rename(ts['path'], str(tmp_path / 'home' / 'Toy Shelf.mp4'))
    lib.scan()
    assert lib.by_id(ts['id'])['kids'] is True
    assert 'Toy Shelf' in [m['title'] for m in lib.tv_view(kids=True)['movies']]


def test_untagging_kids_in_a_kids_folder_sticks(lib):
    ts = find(lib, 'Toy Shelf')
    lib.set_item(ts['id'], kids=False)
    lib.scan()
    assert lib.by_id(ts['id'])['kids'] is False
    assert library.Library(lib.dir).by_id(ts['id'])['kids'] is False


def test_same_file_in_home_and_kids_is_kids(lib, tmp_path):
    src = find(lib, 'Conor The Barbarian')['path']
    make(str(tmp_path / 'kids'), 'Conor.mkv', open(src, 'rb').read())
    assert find(lib, 'Conor The Barbarian')['kids'] is True


def test_episode_title_follows_edited_numbers(lib):
    ep = find(lib, 'Ann Droid S01E01')
    lib.set_item(ep['id'], show='Android', episode=7)
    it = lib.by_id(ep['id'])
    assert it['title'] == 'Android S01E07'
    tv = lib.tv_view()['shows']
    assert any(s['name'] == 'Android' and s['seasons'][0]['episodes'][0]['title'] == 'Android S01E07' for s in tv)
    lib.set_item(ep['id'], title='Pilot')
    assert lib.by_id(ep['id'])['title'] == 'Pilot'


def test_unknown_ids_rescan_at_most_every_10s(lib, monkeypatch):
    lib.scan()
    calls = []
    monkeypatch.setattr(lib, 'scan', lambda: calls.append(1))
    for _ in range(20):
        assert lib.path_for('0123456789abcdef') is None
    assert calls == []


def test_bad_value_types_in_store_files(tmp_path):
    (tmp_path / 'library.json').write_text('{"items": {"a": 5, "b": {"kids": true}}, "collections": {"x": 3, '
                                           '"y": {"name": 1}}, "dismissed": [1, "toy"], "folders": "nope"}')
    (tmp_path / 'fingerprints.json').write_text('{"k": 7, "j": [1, 2]}')
    lb = library.Library(str(tmp_path))
    assert lb.data['items'] == {'b': {'kids': True}} and lb.data['collections'] == {}
    assert lb.data['dismissed'] == ['toy'] and lb.fpcache == {} and lb.folders == []
    lb.scan()


def test_empty_collection_is_not_made(lib):
    assert lib.make_collection('Nothing', []) is None and lib.data['collections'] == {}


def test_subfolder_is_offered_as_collection(lib, tmp_path):
    for rel in ['Alien 1979.mkv', 'Aliens 1986.mkv', 'Prometheus 2012.mkv']:
        make(str(tmp_path / 'home' / 'Alien Saga'), rel)
    sug = next(s for s in lib.suggestions() if s['key'].startswith('folder:'))
    assert sug['name'] == 'Alien Saga' and len(sug['members']) == 3
    assert not any(s['key'] == 'alien' for s in lib.suggestions())
    lib.dismiss(sug['key'])
    assert not any(s['key'].startswith('folder:') for s in lib.suggestions())
