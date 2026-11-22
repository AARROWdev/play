"""Reading movie and episode details from file names. Pure functions, no file access."""
import os
import re

VIDEO_EXTS = {'.mkv': 'mkv', '.mp4': 'mp4', '.m4v': 'mp4', '.mov': 'mov', '.avi': 'avi', '.ts': 'ts',
              '.webm': 'webm', '.wmv': 'wmv', '.mpg': 'mpg', '.mpeg': 'mpg', '.flv': 'flv', '.m2ts': 'ts'}
# Containers the Roku player opens. Others are listed but flagged; the codecs inside still matter (see the guide).
ROKU_OK = {'mkv', 'mp4', 'mov', 'ts'}

# Release junk that ends a title: quality, source, codec, audio, group tags.
_JUNK = re.compile(r'\b(2160p|1080[pi]|720p|480p|4k|uhd|hdr10?|dv|blu-?ray|bdrip|brrip|web-?dl|web-?rip|webrip|hdtv|dvdrip|'
                   r'remux|x26[45]|h\.?26[45]|hevc|avc|aac|ac3|dts|ddp?5\.1|atmos|proper|repack|extended|unrated|'
                   r'imax|10bit)\b.*$', re.I)
_YEAR = re.compile(r'(?:^|[\s(\[])((?:19|20)\d\d)(?:[\s)\]]|$)')
# Episode forms, tried in order. Each captures (show, season, episode) where present.
_EP_FORMS = [
    re.compile(r'^(?P<show>.*?)[\s\-]*\bS(?P<s>\d{1,2})[\s\-]*E(?P<e>\d{1,3})(?!\d)', re.I),             # Show S01E02, S1 E2
    re.compile(r'^(?P<show>.*?)[\s\-]*\b(?P<s>\d{1,2})x(?P<e>\d{2,3})(?!\d)', re.I),                      # Show 1x02
    re.compile(r'^(?P<show>.*?)[\s\-]*\bSeason[\s\-]*(?P<s>\d{1,2})[\s\-,]*Episode[\s\-]*(?P<e>\d{1,3})(?!\d)', re.I),
]
_SEASON_DIR = re.compile(r'^(?:season|series|s)[\s\-_.]*(\d{1,2})$', re.I)
_EP_ONLY = [re.compile(r'^(?:E|Ep|Episode)[\s\-.]*(\d{1,3})(?!\d)', re.I), re.compile(r'^(\d{1,3})(?!\d)(?:\s|\-|$)')]


def clean(stem):
    """'Jungle.Park.1993.1080p.BluRay' -> 'Jungle Park 1993'. Keeps the year, drops release junk."""
    s = re.sub(r'[._]+', ' ', stem)
    s = re.sub(r'\[[^\]]*\]', ' ', s)                 # [group] tags
    s = _JUNK.sub('', s)
    s = re.sub(r'\s+', ' ', s).strip(' -')
    return s


def movie(stem):
    """(title, year or None) for a movie file name without extension."""
    s = clean(stem)
    years = list(_YEAR.finditer(s))
    if years:
        y = years[-1]
        before = s[:y.start(1)].strip(' -([')
        if before:                                    # '2001 A Space Odyssey' keeps 2001 as part of the title
            return before, int(y.group(1))
    return s.strip(' -'), None


def episode(path, root=None):
    """(show, season, episode) if the file is a TV episode, else None.
    Reads the file name first, then 'Season N' folders: Show/Season 1/E02.mkv or Show/Season 1/02 Pilot.mkv."""
    stem = os.path.splitext(os.path.basename(path))[0]
    name = clean(stem)
    for rx in _EP_FORMS:
        m = rx.match(name)
        if m:
            show = m.group('show').strip(' -')
            if not show:
                show = _show_from_dirs(path, root)
            if show:
                return _tidy_show(show), int(m.group('s')), int(m.group('e'))
    parent = os.path.basename(os.path.dirname(path))
    sm = _SEASON_DIR.match(parent.strip())
    if sm:
        for rx in _EP_ONLY:
            em = rx.match(name)
            if em:
                show = _show_from_dirs(path, root)
                if show:
                    return _tidy_show(show), int(sm.group(1)), int(em.group(1))
    return None


def _show_from_dirs(path, root):
    """The show folder above a 'Season N' folder (never above the library root)."""
    d = os.path.dirname(path)
    if _SEASON_DIR.match(os.path.basename(d).strip()):
        d = os.path.dirname(d)
    if root and os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(root)):
        return None
    return clean(os.path.basename(d)) or None


def _tidy_show(show):
    t, y = movie(show)          # 'Ann Droid (2026)' -> 'Ann Droid'
    return t or show


def sort_key(title):
    """Alphabetical, ignoring a leading The / A / An."""
    t = title.lower()
    for art in ('the ', 'a ', 'an '):
        if t.startswith(art) and len(t) > len(art):
            return t[len(art):]
    return t


def family_word(title):
    """First significant word of a title, for spotting franchises ('Jungle', 'Toy'). None if too short to trust."""
    words = re.findall(r"[a-z0-9']+", sort_key(title))
    if words and len(words[0]) >= 3 and not words[0].isdigit():
        return words[0]
    return None


def common_prefix_words(titles):
    split = [re.findall(r"[A-Za-z0-9':]+", t) for t in titles]
    out = []
    for group in zip(*split):
        if len({w.lower().rstrip(':') for w in group}) == 1:
            out.append(group[0].rstrip(':'))
        else:
            break
    return ' '.join(out)
