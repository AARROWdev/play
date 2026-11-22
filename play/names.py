"""Reading movie and episode details from file names. Pure functions, no file access."""
import datetime
import os
import re

VIDEO_EXTS = {'.mkv': 'mkv', '.mp4': 'mp4', '.m4v': 'mp4', '.mov': 'mov', '.avi': 'avi', '.ts': 'ts',
              '.webm': 'webm', '.wmv': 'wmv', '.mpg': 'mpg', '.mpeg': 'mpg', '.flv': 'flv', '.m2ts': 'ts'}
# Containers the Roku player opens over the network (a .mov is sent as mp4). Others, MPEG-TS included, are listed
# but flagged; the codecs inside still matter (see the guide).
ROKU_OK = {'mkv', 'mp4', 'mov'}
# What the TV is told to play each container as (Roku has no "mov" stream format; mp4 covers .mov and .m4v)
STREAM_FORMAT = {'mov': 'mp4'}

# Release junk. A strong tag (resolution, source, codec) ends the title wherever it appears; the weak words below
# are real words too ("The Extended Family"), so they are only dropped when they trail the title.
_STRONG = re.compile(r'\b(2160p|1080[pi]|720p|480p|4k|uhd|blu-?ray|bdrip|brrip|web-?dl|web-?rip|hdtv|dvdrip|remux|'
                     r'x26[45]|h26[45]|hevc|10bit)\b.*$', re.I)
_WEAK = re.compile(r'(?:\s+(?:hdr10?|dv|avc|aac\d*|ac3|dts\d*|ddp?\d*|atmos|proper|repack|extended|unrated|imax|'
                   r'internal|limited))+\s*$', re.I)
# A year stands alone: start, a space, ( [ or a dash that doesn't follow a number before it; end, a space, ) ] or a
# dash not followed by a digit after it. So '2049 2017' gives both, but a date (2019-07-04) or a span (1994-2004)
# is not a year.
_YEAR = re.compile(r'(?:^|(?<=[\s(\[])|(?<=(?<!\d)-))((?:19|20)\d\d)(?=[\s)\]]|-(?!\d)|$)')
_EP_END = r'(?:-?E(?P<e2>\d{1,3})|-(?P<e3>\d{1,3}))?(?!\d)'   # S01E01-E02, S01E01E02, S01E01-02
# Episode forms, tried in order. Each captures (show, season, episode) where present.
_EP_FORMS = [
    re.compile(r'^(?P<show>.*?)[\s\-]*\bS(?P<s>\d{1,2})[\s\-]*E(?P<e>\d{1,3})' + _EP_END, re.I),     # S01E02, S1 E2
    re.compile(r'^(?P<show>.*?)[\s\-]*\b(?P<s>\d{1,2})x(?P<e>\d{2,3})(?!\d)', re.I),                  # 1x02
    re.compile(r'^(?P<show>.*?)[\s\-]*\bSeason[\s\-]*(?P<s>\d{1,2})[\s\-,]*Episode[\s\-]*(?P<e>\d{1,3})(?!\d)', re.I),
]
# 'Season 1', 'Series 01', 'S1', with anything after the number: 'Season 1 (2019-2020)', 'Season 01 [1080p]'
_SEASON_DIR = re.compile(r'^(?:season|series|s)[\s\-_.]*(\d{1,2})(?!\d)(?:[\s\-_.(\[].*)?$', re.I)
_SPECIALS_DIR = re.compile(r'^specials?$', re.I)
_EP_E = re.compile(r'^(?:E|Ep|Episode)[\s\-.]*(\d{1,3})(?!\d)', re.I)     # 'E02', 'Episode 2'
_EP_NUM = re.compile(r'^(\d{1,3})(?!\d)(?:\s|\-|$)')                       # '02 Pilot'
_EP_MARK = r'S\d{1,2}\s*-?\s*E\d{1,3}(?:-?E?\d{1,3})?|\d{1,2}x\d{2,3}'
# a bracketed year or episode marker is kept (unwrapped); any other [group] tag is dropped
_KEEP_BRACKETS = re.compile(r'[\[(]\s*((?:19|20)\d\d|' + _EP_MARK + r')\s*[\])]', re.I)


def _light(stem):
    """Dots and underscores to spaces, codec dots joined, bracketed years/episode markers unwrapped, other [tags]
    dropped. No junk cut yet: an episode marker after 'H.264' must survive."""
    s = re.sub(r'(?i)\bh\.(26[45])\b', r'h\1', stem)            # H.264 / DDP5.1 / DTS5.1 before dots become spaces
    s = re.sub(r'(?i)\b(ddp?|aac|dts)(\d)\.(\d)\b', r'\1\2\3', s)
    s = re.sub(r'[._]+', ' ', s)
    s = _KEEP_BRACKETS.sub(r' \1 ', s)
    s = re.sub(r'\[[^\]]*\]', ' ', s)                             # [group] tags
    return re.sub(r'\s+', ' ', s).strip()


def clean(stem):
    """'Jungle.Park.1993.1080p.BluRay' -> 'Jungle Park 1993'. Keeps the year, drops release junk."""
    s = _STRONG.sub('', _light(stem))
    s = re.sub(r'\s+', ' ', s).strip(' -')
    s = _WEAK.sub('', ' ' + s).strip(' -')
    return s


def movie(stem):
    """(title, year or None) for a movie file name without extension."""
    s = clean(stem)
    latest = datetime.date.today().year + 1      # 'Blade Runner 2049' has no year
    years = [y for y in _YEAR.finditer(s) if 1900 <= int(y.group(1)) <= latest]
    if years:
        y = years[-1]
        before = s[:y.start(1)].rstrip(' -([')
        if before:                                    # '2001 A Space Odyssey' keeps 2001 as part of the title
            return before, int(y.group(1))
    return s.strip(' -'), None


def season_of_dir(name):
    """'Season 1', 'Series 01', 'S1', 'Season 1 (2019)' -> 1; 'Specials' -> 0; anything else -> None."""
    name = name.strip()
    m = _SEASON_DIR.match(name)
    if m:
        return int(m.group(1))
    return 0 if _SPECIALS_DIR.match(name) else None


def episode_info(path, root=None):
    """{'show', 'season', 'episode', 'last'} for a TV episode file, else None. 'last' is the final episode of a
    multi-episode file (S01E01-E02), otherwise the same as 'episode'.
    Reads the file name first, then season folders: Show/Season 1/E02.mkv or Show/Season 1/02 Pilot.mkv. A
    'Specials' folder only counts with E-numbered or SxxEyy names, so 'Movies/Specials/300 (2006).mkv' stays a
    movie."""
    stem = os.path.splitext(os.path.basename(path))[0]
    name = _light(stem)
    for rx in _EP_FORMS:
        m = rx.match(name)
        if m:
            show = _strip_show(clean(m.group('show')))
            folder = _show_from_dirs(path, root)
            if not show:
                show = folder
            if show:
                e = int(m.group('e'))
                end = m.groupdict().get('e2') or m.groupdict().get('e3')
                last = int(end) if end and int(end) > e else e
                return {'show': _pick_show(_tidy_show(show), folder), 'season': int(m.group('s')), 'episode': e,
                        'last': last}
    season = season_of_dir(os.path.basename(os.path.dirname(path)))
    if season is not None:
        cleaned = clean(stem)
        em = _EP_E.match(cleaned) or (_EP_NUM.match(cleaned) if season > 0 else None)
        if em:
            show = _show_from_dirs(path, root)
            if show:
                e = int(em.group(1))
                return {'show': _tidy_show(show), 'season': season, 'episode': e, 'last': e}
    return None


def episode(path, root=None):
    """(show, season, episode) if the file is a TV episode, else None."""
    i = episode_info(path, root)
    return (i['show'], i['season'], i['episode']) if i else None


_NEAR_MISS = [
    (re.compile(r'\bSeason\s*\d{1,2}\b.*\bEp\.?\s*\d{1,3}\b', re.I),
     'Looks like an episode: name it like "Show S01E06" (or "Season 1 Episode 6")'),
    (re.compile(r'\bS\d{1,2}\s*[-x]?\s*\d{1,3}\b', re.I),
     'Looks like an episode: name it like "Show S01E02"'),
    (re.compile(r'(?:^|\s)(?:E|Ep\.?)\s*\d{1,3}(?:\s|$)', re.I),
     'Looks like an episode but has no season: name it like "Show S01E03"'),
]


def looks_like_episode(stem, in_season_folder=False):
    """A hint for names that were read as a movie but look like an episode ('Ann Droid E03', 'Season 1 Ep 6'),
    or None. A file in a season folder that couldn't be numbered gets a hint too."""
    s = clean(stem)
    for rx, hint in _NEAR_MISS:
        if rx.search(s):
            return hint
    if in_season_folder:
        return 'In a season folder but the name has no episode number: name it like "Show S01E03" or "E03"'
    return None


def _show_from_dirs(path, root):
    """The show folder above a season folder (never above the library root)."""
    d = os.path.dirname(path)
    if season_of_dir(os.path.basename(d)) is not None:
        d = os.path.dirname(d)
    if root and os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(root)):
        return None
    return _strip_show(clean(os.path.basename(d))) or None


def _strip_show(show):
    """Trim separators; a leading '(' stays ('(Un)Well')."""
    return show.strip(' -').rstrip(' -([')


def _pick_show(from_name, folder):
    """The show folder's spelling wins when it is the same show with a year: 'Doctor Who (1963)/Season 1/Doctor Who
    S01E01.mkv' is 'Doctor Who (1963)', so a show split across naming styles stays one show."""
    if folder:
        f = _tidy_show(folder)
        base_f, _ = movie(f.replace('(', ' ').replace(')', ' '))
        base_n, _ = movie(from_name.replace('(', ' ').replace(')', ' '))
        if base_f and base_f.lower() == base_n.lower() and f != from_name and f.endswith(')'):
            return f
    return from_name


def _tidy_show(show):
    """'Show REPACK' -> 'Show'. A year stays, written 'Show (Year)', so remakes don't merge: 'Doctor Who (1963)'
    and 'Doctor Who (2005)' are two shows."""
    show = _strip_show(_WEAK.sub('', ' ' + clean(show)))
    t, y = movie(show)
    if not t:
        return show
    return f'{t} ({y})' if y else t


def show_key(show):
    """How shows are grouped: capital letters don't matter."""
    return show.lower()


ARTICLES = ('the ', 'a ', 'an ')


def _strip_article(title):
    low = title.lower()
    for art in ARTICLES:
        if low.startswith(art) and len(low) > len(art):
            return title[len(art):]
    return title


def sort_key(title):
    """Alphabetical, ignoring a leading The / A / An."""
    return _strip_article(title).lower()


def plural_word(n, word):
    return word if n == 1 else word + 's'


def plural(n, word):
    return f'{n} {plural_word(n, word)}'


_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def family_word(title, display=False):
    """First significant word of a title, for spotting franchises ('jungle', "ocean's", 'pokémon'). None if too
    short to trust. display=True gives the word as written in the title ('Jungle')."""
    m = _WORD.search(_strip_article(title))
    if not m or len(m.group(0)) < 3 or m.group(0).isdigit():
        return None
    return m.group(0) if display else m.group(0).lower()


def legacy_family_word(title):
    """The key PLAY 1.0's first builds used (ASCII letters only), so a suggestion dismissed then stays dismissed."""
    words = re.findall(r"[a-z0-9']+", sort_key(title))
    if words and len(words[0]) >= 3 and not words[0].isdigit():
        return words[0]
    return None
