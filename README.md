# PLAY

**Your own movies and shows on your Roku, from your Windows PC.** No accounts, no cloud, no transcoding: PLAY serves
the files straight from your drives to the TVs in your house.

![The PLAY library window](docs/library.png)

- **One library, tagged your way.** Point PLAY at your media folders. Kids, movie or show, hidden and collections are
  tags inside PLAY, not folders, and they follow a file if you rename or move it. Your files are never renamed,
  moved or changed.
- **Shows grouped for you.** `Show S01E02`, `1x02`, `Season 1 Episode 2` and `Show/Season 1/E02` all work. PLAY spots
  missing and doubled episodes.
- **Collections.** Seven Jungle films show on the TV as one entry, in release order or your own. PLAY suggests
  them; you confirm.
- **HOME and KIDS.** The KIDS side of the TV shows only what's tagged Kids.
- **The TV finds the PC by itself.** No IP addresses to type (there's a fallback if your network blocks it).
- **Resume** where you stopped, per TV, and search across everything.
- **Five skins** (Blue, Ember, Neon, Violet, Crimson), shared with [Bigfoot](https://github.com/AARROWdev/bigfoot) and
  [Cratedig](https://github.com/AARROWdev/cratedig).

## Set up
1. **On the PC:** install **PLAYSetup-&lt;version&gt;.exe** from the
   [latest release](https://github.com/AARROWdev/play/releases/latest) (Windows 10/11, 64-bit). On first run, add
   the folder or folders your movies and shows are in. PLAY lives in the tray and starts with Windows, so the TVs can
   always reach it.
2. **On the Roku:** add the PLAY channel (see the release notes for the channel link), open it, and pick HOME or KIDS.
   It finds the PC on its own.

The app isn't code-signed yet, so Windows may show "Windows protected your PC": click **More info**, then
**Run anyway**. The installer lets PLAY through Windows Firewall on private networks only: if the TV can't find
the PC, check that Windows calls your home network **Private** (Settings > Network & internet > your network).

"Start PLAY with Windows" applies to the Windows account that runs it. If someone else's administrator password
was used to install, turn it on once from PLAY's Settings while signed in as yourself.

**How to name files:** see [NAMING.md](NAMING.md). In short: `Title (Year).mkv` for movies and
`Show Name S01E02.mkv` for episodes. Anything PLAY gets wrong can be fixed in the window.

**Uninstall:** Settings > Apps > PLAY > Uninstall. This removes the firewall rule and the start-with-Windows entry.
If you turned on "Start PLAY with Windows" from Settings while signed in as a different Windows user than the one
uninstalling, switch it off there first (Windows only lets the uninstaller change its own account's entry).
Your tags and collections stay in `%APPDATA%\PLAY\` (delete the folder to remove them too). Your media is untouched.

## How it works
- The PLAY program runs a small web server on port 8600 (changeable in Settings). The TV asks it for the HOME or
  KIDS list and streams files with ordinary HTTP range requests, which is how the Roku player seeks.
- Only files found in your media folders can be streamed. The TV asks for a file by an id, never by a path.
- The TV finds the PC with a UDP broadcast on port 8601 that PLAY answers.
- Each file is recognised by a fingerprint of its size and its first and last 64 KB, so tags follow it through
  renames and moves without PLAY ever writing to it.

## Build
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller pytest requests
winget install JRSoftware.InnoSetup   # installer compiler
.\build.ps1   # -> dist\PLAYSetup-<ver>.exe, dist\PLAY-<ver>-portable-win64.zip, dist\PLAY-roku.zip
```
`python play_main.py` runs it from source. `python -m pytest tests` runs the tests; they use temporary folders and
never touch your settings or media. `python tools\roku_build.py <tv-ip>` sideloads the Roku app onto a TV in
developer mode.
