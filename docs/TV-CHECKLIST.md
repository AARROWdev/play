# PLAY on the TV: test checklist

Run this on a real Roku after installing a new build (the automated tests cover the PC side; a Roku can only be
driven by hand). Tick each line; anything that fails goes back as a bug with the step number.

## Finding the PC
1. First open of the channel: "Looking for PLAY on your network…", then straight to HOME / KIDS (one PC found).
2. Quit PLAY on the PC (tray > Quit), reopen the channel, pick HOME: within a few seconds "Can't reach PLAY",
   OK tries again, `*` searches again.
3. With PLAY quit, `*` on the menu: "PLAY wasn't found", with "Search again" and "Enter the PC's address myself".
4. Enter the address by hand (the IP from `ipconfig`, no port): the menu appears and lists load.
5. Start PLAY again, `*` on the menu: the PC is found again.

## Browsing
6. HOME > MOVIES: titles in A-Z order (The/A/An ignored), each with its year; collections show as one row
   "Name · N films".
7. Open a collection: its films in release order (or the custom order set on the PC); Back returns to MOVIES.
8. HOME > SHOWS > a show > a season: episodes in order; Back walks up one level each time.
9. KIDS shows only Kids-tagged titles; a title untagged on the PC disappears from KIDS after reopening KIDS.
10. A title hidden on the PC is gone from HOME and KIDS.
11. Search (Up from the top row, or `*` in a list): finds movies, films inside collections and episodes.
12. Back during "Loading…" returns to the menu (doesn't exit the channel).

## Playing
13. Play a movie, seek forward and back with the remote: playback continues after each seek.
14. Pause, Back: the list shows "resume at …"; select it: "Resume from …" / "Start over" both work.
15. Play, then press Home (not Back), reopen the channel: the title still offers "Resume from …".
16. Play to the end: the resume mark is gone.
17. A file PLAY flags "Check" on the PC (e.g. AVI): the TV says "This file can't play on this TV" and stays on the
    list.
18. Rename or move a file on the PC while the channel is open, then play it: it still plays (same title, same resume).

## Skins
19. Change the skin in PLAY's Settings, reopen HOME on the TV: the highlight colour follows the skin.
