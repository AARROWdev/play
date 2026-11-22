# Naming your files for PLAY

PLAY reads everything it needs from file and folder names. It never renames, moves or changes your files, so the
name you give a file is what PLAY works from. You can also fix anything by hand in the PLAY window (title, movie or
show, season and episode, Kids, Hide, collection), and that sticks even if you rename or move the file later.

You don't need any particular folder layout. Put everything in one media folder, or several, organised however you
like. PLAY sorts it out from the names.

## Movies

**Recommended: `Title (Year).ext`**

| File name | Read as (title · year) |
|---|---|
| `Jungle Park (1993).mp4` | Jungle Park · 1993 |
| `The Flea 1986.mkv` | The Flea · 1986 |
| `Heat.1995.1080p.BluRay.x264.mkv` | Heat · 1995 |
| `Toy Shelf.mp4` | Toy Shelf (no year) |

- The year is optional, but it's what puts a collection in release order. Without years, a collection sorts by title.
- Download junk after the title is ignored: `1080p`, `2160p`, `BluRay`, `WEB-DL`, `x264`, `HEVC`, `[group]` tags and
  so on.
- A title that is also a year needs the real year after it: `1917 (2019).mkv`, `2001 A Space Odyssey (1968).mkv`.
- A title that ends in a number that could be a year, like **Death Race 2000**, should always have its real year:
  `Death Race 2000 (1975).mkv`. Otherwise PLAY reads 2000 as the year.
- A leading "The", "A" or "An" is ignored for sorting, so The Flea sorts under F.

## TV shows

Any of these make a file an episode. The show name is what comes before the season and episode.

| File name | Show · Season · Episode |
|---|---|
| `Ann Droid S01E02.mkv` | Ann Droid · 1 · 2 |
| `Ann Droid - S1E2.mkv` | Ann Droid · 1 · 2 |
| `Ann.Droid.S01E02.1080p.WEB-DL.mkv` | Ann Droid · 1 · 2 |
| `Ann Droid 1x02.mkv` | Ann Droid · 1 · 2 |
| `Ann Droid Season 1 Episode 2.mkv` | Ann Droid · 1 · 2 |

Season folders work too. The show name comes from the folder above the season folder:

```
Ann Droid/
    Season 1/
        E01.mkv              -> Ann Droid · 1 · 1
        02 The Return.mkv    -> Ann Droid · 1 · 2
    Season 2/
        Ann Droid S02E01.mkv
```

- Spell the show name the same way on every episode. Capital letters don't matter.
- Two different shows with the same name (a remake) stay apart when you add the year: `Doctor Who (1963) S01E01.mkv`
  and `Doctor Who (2005) S01E01.mkv`, or folders `Doctor Who (1963)/Season 1/` (the folder's year counts even when
  the file names leave it out).
- A `Specials` folder is season 0 for files named like `S00E01` or `E01` (a plain `01 Interview.mkv` there stays a
  movie), and `Season 1 (2019)` or `Season 01 [1080p]` work as season folders.
- A file with two episodes in it, `Show S01E01-E02.mkv`, counts as both.
- A name PLAY can't read as an episode but that looks like one (`Show E03.mkv`, `Show Season 1 Ep 6.mkv`) is listed
  under **Needs a look** with how to fix it.
- PLAY tells you when a season is missing an episode ("Season 1 is missing episode 3") or has one twice. Look under
  **Needs a look** in the PLAY window.

## Kids

Anything tagged **Kids** shows on the KIDS side of the TV, and nothing else does. HOME shows everything.

- Tag titles in the PLAY window: select them and click **Kids**.
- Or, in Settings, tick **Kids** next to a media folder. Files PLAY finds there start out tagged Kids, and keep the
  tag if you move them somewhere else later.

## Collections (franchises)

A collection shows on the TV as one entry, `Jungle Park · 7 films`. Select it to see the films in order.

- **PLAY suggests them.** When several movies start with the same word (Jungle Park, Jungle World...) or sit
  together in a subfolder named after them (`Alien Saga/`), PLAY shows "Looks like a collection". Click **Make
  collection**, or **Not now** to stop it asking.
- **Make your own.** Select movies, then choose **New collection…** in the Collection box.
- **Order:** by release year (from the names) or your own order: double-click the collection and drag the films.

## What plays on a Roku

Roku plays most modern files. If a file won't play, it's usually the format inside it, not PLAY.

| Plays | Usually doesn't |
|---|---|
| MP4, MKV, MOV (`.mp4` `.m4v` `.mkv` `.mov`) | AVI, WMV, FLV, MPG, WebM, TS (`.ts` `.m2ts`) |
| Video: H.264 (AVC), H.265 (HEVC) on 4K Rokus | Video: MPEG-4 Part 2 (DivX/Xvid), VC-1 |
| Audio: AAC, AC3 (Dolby Digital), MP3 | Audio: DTS on most Rokus (it passes through to a receiver that supports it) |

PLAY flags AVI, WMV, FLV, MPG, WebM and TS files under **Needs a look**. To check what's inside a file, open it in
[MediaInfo](https://mediaarea.net/MediaInfo). To fix one, remux or convert it to MP4 (H.264 + AAC) with a tool such as
HandBrake.

## Adding new files

Drop them into a media folder. With PLAY's window open it looks every few seconds, from the tray every 30 seconds;
a new file is listed once it has stopped changing for 10 seconds (so a file still being copied isn't half-read).
The TV shows it the next time you open HOME or KIDS. Nothing needs restarting.
