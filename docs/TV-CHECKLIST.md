# PLAY on the TV: test checklist

Run this on a real Roku after installing a new build (the automated tests cover the PC side; a Roku can only be
driven by hand). Tick each line; anything that fails goes back as a bug with the step number.

## Finding the PC
1. First open of the channel: "Looking for PLAY on your network…", then straight to HOME / KIDS (one PC found).
2. With PLAY running, pick HOME right after starting PLAY on the PC (or after adding a big folder): "PLAY is
   reading your folders on the PC…", then the list appears by itself.
3. Quit PLAY on the PC (tray > Quit PLAY), pick HOME: within about 15 s "Can't reach PLAY … Is the PC on, with the
   PLAY program running?". OK tries again; `*` searches again.
4. While a list is loading (step 3, before the message), press `*`: the search for the PC starts.
5. With PLAY still quit, `*` on the menu: "PLAY wasn't found", with "Search again" and "Enter the PC's address
   myself".
6. Start PLAY again. Read the PC's address at the bottom of PLAY's window ("Serving at 192.168.x.x:8600"). On the
   TV choose "Enter the PC's address myself" and type it: once as just the address, once with ":8600", once as
   "http://<address>/". Each time the menu appears and HOME loads. Typing "hello there" says it isn't an address.
7. `*` on the menu: the PC is found again.

## Browsing
8. HOME > MOVIES: titles in A-Z order (The/A/An ignored), each with its year, like "Jungle Park   (1993)";
   collections show as one row "Name · N films".
9. Open a collection: its films in release order (or the custom order set on the PC); Back returns to MOVIES.
10. HOME > SHOWS > a show > a season: episodes in order; a Specials folder shows as "Specials"; Back walks up one
    level each time.
11. KIDS shows only Kids-tagged titles; a title untagged on the PC disappears from KIDS after reopening KIDS.
12. A title hidden on the PC is gone from HOME and KIDS.
13. Search: press Up from the top row to highlight the search bar, then OK (or press `*` in a list). It finds
    movies, films inside collections and episodes.
14. Back during "Loading…" returns to the menu (doesn't exit the channel).
15. Open search from the bar, press Back on the keyboard: the search bar is highlighted again. Open it with `*`
    from the middle of a list, press Back: that list row is highlighted again.

## Playing
16. Play a movie, seek forward and back with the remote: playback continues after each seek.
17. Resume: pick a title with no "resume at" mark. Play at least 1 minute (resume marks need 30 s or more, and are
    dropped in the last 2 minutes of a film), note the time, then Pause and Back: the row shows "resume at" that
    time. Select it: "Resume from …" and "Start over" both work. After "Resume from", rewind past the resume point:
    it rewinds.
18. Home survives: pick another title with no mark, play to about 2:00, note the exact time, press Home (not
    Back), reopen the channel: it offers "Resume from" within 30 s of the noted time.
19. Play to the end: the resume mark is gone.
20. A file PLAY flags "Check" on the PC (e.g. AVI): the TV says "This file can't play on this TV" and stays on the
    list. A title that had a resume mark keeps it after such an error.
21. A .mov file (e.g. from a phone): plays.
22. Rename or move a file on the PC while the channel is open, then play it: it still plays (same title, same resume).

## Skins
23. Change the skin in PLAY's Settings, reopen HOME on the TV: the highlight colour follows the skin.
