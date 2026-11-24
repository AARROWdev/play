# Installing PLAY, step by step

PLAY has two parts: a program on your Windows PC that holds your movies, and an app on each Roku TV that plays
them. Setting it up takes about 15 minutes the first time, most of it on the TV.

**You need**
- A Windows 10 or 11 PC (64-bit) on the same home network (Wi-Fi or cable) as the TVs.
- A Roku TV or Roku player, and its remote.
- Your movies and shows as files (MKV, MP4 or MOV play best).

---

## Part 1: the PC

1. **Download** `PLAYSetup-<version>.exe` from the
   [latest release](https://github.com/AARROWdev/play/releases/latest).
2. **Run it.** PLAY isn't code-signed yet, so Windows may show **"Windows protected your PC"**. Click
   **More info**, then **Run anyway**.
3. **Click through the installer.** It may ask for permission to make changes: click **Yes**. It lets PLAY
   through Windows Firewall on private networks only.
4. **Make sure Windows calls your home network Private.** Open **Settings › Network & internet**, click your
   network, and set **Network profile type** to **Private**. (On a Public network the TVs can't reach the PC.)
5. **PLAY opens Settings the first time.** It starts with one media folder, `Videos\PLAY`. Click **Save**.
   - Your films are somewhere else already (another drive, a NAS)? Click **Add folder…** and pick that folder
     too. **Make default** chooses where new media goes.
6. **Add your movies.** Click **Add media…**, pick the files, and confirm. PLAY *copies* them into your default
   folder (episodes go into `Show\Season 1` folders for you). Tick **Mark them Kids** for children's titles.

PLAY now lives in the tray (by the clock) and starts with Windows, so the TVs can always reach it.

> **Naming tip:** `Title (Year).mkv` for movies and `Show Name S01E02.mkv` for episodes read best. See
> [NAMING.md](../NAMING.md). Anything PLAY reads wrong can be fixed in its window.

---

## Part 2: each Roku TV (once per TV)

The PLAY TV app isn't in Roku's channel store; PLAY puts it on your TV itself. For that, the TV's
**Developer Mode** has to be on. It's a setting Roku builds into every TV.

### Turn on Developer Mode

1. Stand in front of the TV with its Roku remote, and make sure the TV is on the Roku home screen.
2. Press, in order: **Home Home Home, Up Up, Right, Left, Right, Left, Right**.
3. The **Developer Settings** screen opens. Write down the **IP address** it shows (like `192.168.1.50`): you'll
   check it against PLAY in a minute.
4. Choose **Enable installer and restart**, and accept the license agreement.
5. **Choose a developer password** and write it down. PLAY asks for it once.
6. The TV restarts.

(Nothing appears? Press the buttons a little slower and try again. It has to start from the home screen.)

### Put PLAY on the TV

1. On the PC, in PLAY's window, click **Set up TVs** (bottom left; it's also in the tray icon's menu).
2. PLAY searches your network and lists the Roku TVs it finds, by name.
3. Click your TV. Under it, PLAY shows its IP address: **check it matches** the one on the TV's screen (or on the
   TV: **Settings › Network › About**). With several TVs, this is how you know which one you're setting up.
4. Its status should read **Ready to install**. Click **Install on this TV** and type the developer password.
5. About 10 seconds later PLAY opens on the TV. It finds the PC by itself.

Do the same for every TV. Run **Set up TVs** again any time to add a TV, or to update the TV app after a PLAY
update.

---

## Part 3: watching

On the TV, open **PLAY** from the home screen (it's at the end of your apps) and pick **HOME** or **KIDS**.
KIDS shows only what's marked Kids.

In PLAY's window on the PC you can:
- **Mark titles Kids:** select them and press **Kids** on the right, or open the **Kids** view and click
  **+ Add to Kids…** to pick from everything.
- **Make collections** (like "Comedy", or a film series in release order): **New collection…**
- **Fix a title or year:** select it and type on the right.

---

## If something doesn't work

| What you see | What to do |
|---|---|
| **Set up TVs finds no TV** | The TV must be on and on the same network as the PC. Or add it by address: on the TV, **Settings › Network › About** shows its IP; click **Add by address…** |
| **"Developer Mode is off"** | Do *Turn on Developer Mode* above on that TV, then click **Search again**. |
| **"Needs a setting changed"** | On the TV: **Settings › System › Advanced system settings › Control by mobile apps**, set **Network access** to **Default**, then **Search again**. |
| **"Install didn't work"**, with *"The TV did not accept that developer password"* | Type the developer password you set on that TV (PLAY asks again). Forgot it? On the TV, press the remote sequence again: the screen now offers **Disable installer and restart**. Choose it; when the TV is back, press the sequence once more and choose **Enable installer and restart** with a new password. |
| **The TV app can't find the PC** | Check Windows calls your network **Private** (Part 1, step 4), and that PLAY is running (its icon is in the tray). The TV app also lets you type the PC's address: PLAY's window shows it at the bottom. |
| **A title says "Check" under Plays on Roku** | Roku can't play that file type (AVI, for example). Convert it to MP4 or MKV. |
| **Another developer app was on the TV** | A Roku holds one developer app at a time: installing PLAY replaces it (Set up TVs asks first). |

**Uninstalling:** Windows **Settings › Apps › PLAY › Uninstall**. Your media isn't touched. Your Kids marks and
collections stay in `%APPDATA%\PLAY\` (delete that folder to remove them too).
