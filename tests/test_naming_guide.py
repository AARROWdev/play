"""Every example in NAMING.md, checked against the real name reader, so the guide can't drift from the code."""
import os
import re

import pytest

from play import names

GUIDE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'NAMING.md')


@pytest.mark.parametrize('stem,title,year', [
    ('Jungle Park (1993)', 'Jungle Park', 1993),
    ('The Flea 1986', 'The Flea', 1986),
    ('Heat.1995.1080p.BluRay.x264', 'Heat', 1995),
    ('Toy Shelf', 'Toy Shelf', None),
    ('1917 (2019)', '1917', 2019),
    ('2001 A Space Odyssey (1968)', '2001 A Space Odyssey', 1968),
    ('Death Race 2000 (1975)', 'Death Race 2000', 1975),
])
def test_movie_examples(stem, title, year):
    assert names.movie(stem) == (title, year)


@pytest.mark.parametrize('rel', ['Ann Droid S01E02.mkv', 'Ann Droid - S1E2.mkv', 'Ann.Droid.S01E02.1080p.WEB-DL.mkv',
                                 'Ann Droid 1x02.mkv', 'Ann Droid Season 1 Episode 2.mkv'])
def test_episode_examples(rel):
    assert names.episode(os.path.join(r'N:\M', rel), r'N:\M') == ('Ann Droid', 1, 2)


@pytest.mark.parametrize('rel,want', [
    (r'Ann Droid\Season 1\E01.mkv', ('Ann Droid', 1, 1)),
    (r'Ann Droid\Season 1\02 The Return.mkv', ('Ann Droid', 1, 2)),
    (r'Ann Droid\Season 2\Ann Droid S02E01.mkv', ('Ann Droid', 2, 1)),
])
def test_season_folder_examples(rel, want):
    assert names.episode(os.path.join(r'N:\M', rel), r'N:\M') == want


def test_every_table_row_in_the_guide_is_covered():
    text = open(GUIDE, encoding='utf-8').read()
    files = re.findall(r'^\| `([^`]+)` \|', text, re.M)
    covered = {'Jungle Park (1993).mp4', 'The Flea 1986.mkv', 'Heat.1995.1080p.BluRay.x264.mkv', 'Toy Shelf.mp4',
               'Ann Droid S01E02.mkv', 'Ann Droid - S1E2.mkv', 'Ann.Droid.S01E02.1080p.WEB-DL.mkv',
               'Ann Droid 1x02.mkv', 'Ann Droid Season 1 Episode 2.mkv'}
    assert set(files) == covered



def test_guide_tables_say_what_the_code_does():
    """Every '| `file` | expected |' row in NAMING.md, checked against the reader: the guide can't drift."""
    text = open(GUIDE, encoding='utf-8').read()
    rows = re.findall(r'^\| `([^`]+)` \| ([^|]+) \|', text, re.M)
    assert len(rows) >= 9
    for fname, want in rows:
        want = want.strip()
        stem = os.path.splitext(fname)[0]
        ep = names.episode(os.path.join(r'N:\M', fname), r'N:\M')
        if ' · ' in want and want.count(' · ') == 2:           # Show · Season · Episode
            show, s, e = want.split(' · ')
            assert ep == (show, int(s), int(e)), fname
        else:                                                   # Title · Year, or Title (no year)
            title, _, year = want.partition(' · ')
            title = title.split(' (no year)')[0].strip()
            assert ep is None and names.movie(stem) == (title, int(year) if year else None), fname
