# Boostimise – all-in-one tools for Cover Canvas BD

<img src="assets/logo-full.png" width="220" align="right" alt="Boostimise logo">

One program with a sidebar. Each button opens one tool; a tool keeps working in the background
while you use another one (a green dot on its button means it is busy). Up to version 3.0 it was
called **CasePilot**, and up to 2.1 **CoverDesk**; updating keeps all settings and logins.

| Sidebar | What it does | Was |
|---|---|---|
| **Photo Enhancer** | AI photo enhancer and upscaler (2× / 4×, face and photo restoration), the same as [boostimise.com](https://boostimise.com): drop, pick or paste photos, compare before / after, download PNG, JPG or all as ZIP. | new (the website's enhancer) |
| **Fetch by Code** | Finds product photos in your WhatsApp chats by code and saves them (`2-558 Realme C85.jpg`). | `fetchbyrandomcode` (PixFetch) |
| **Fetch by Date** | Saves every photo sent in one chat between two dates and times (to the second), one folder per day, with a CSV log; photos saved before are skipped. | `wpimagefetch` (WhatsApp Image Fetcher) |
| **Reaction List** | Lists the codes reacted with ❌, 📵, 😥 (pick one or all three) or the emojis you type under *Custom*, with the phone number from the order message; *List + photos* also saves their reacted photos. | new (replaces Fetch by Reaction, `fetchimagesbyicon`) |
| **Model Detect** | Finds each code's photos in WhatsApp and writes the phone model next to the code. | `mobilemodeldetect` |
| **Rename by Match** | Renames the photos in a day folder by matching them with the photos posted in WhatsApp. | `photorenamebymatch` (PhotoMatch Renamer) |
| **Pathao Auto-fill** | Reads order messages in WhatsApp and fills Pathao's New Delivery form. | `dataentry` |
| **PhotoMatch** | Finds the photos in one folder that are missing from another folder, then copies them. | `photomatch` |
| **SnapRename** | Browse a folder's photos, rename them, drag them into sub-folders. | `photosrename` |
| **Dashboard** | Your enhancements today / this week / this month, per-day chart, plan and history - updates live. | new (the website's dashboard) |
| **Plans** | The free account and the paid plans; request a plan (an admin activates it). | new (the website's pricing) |

**Sign in with Google once, for every tool.** Boostimise opens on a *Sign in to Boostimise* screen;
*Continue with Google* opens your browser's Google account picker and comes back to the app by
itself (the same Boostimise account as on the website). The first time, the app asks for your
mobile number (every country code), like the website. After that it stays signed in, also after
a restart; the account menu at the bottom of the sidebar has Dashboard, Plans and *Sign out*.
Without internet the last signed-in account keeps working (the tools never lock you out because of
a network hiccup); an account suspended by an admin is locked at once, and signing out closes every tool.

**One WhatsApp login for every tool.** The first tool that needs WhatsApp opens one Microsoft Edge
window, and every tool works in it (Pathao Auto-fill uses it for Pathao too). Scan the QR code there
once and all tools are logged in, also after a restart. The window stays open when Boostimise closes;
if you close it, the next tool that needs it opens it again, still logged in. WhatsApp Web works in
only one tab, so two tools can't scroll it at the same moment: the second one says *Waiting for … to
finish with WhatsApp* and goes on by itself. (Fetch by Code reads WhatsApp without
scrolling, so it never waits; nor does Reaction List. Fetch by Date clicks through the photos, so it waits.)

Shortcuts: **Ctrl+1 … Ctrl+9** switch tools (Ctrl+1 is the Photo Enhancer, Ctrl+2 Fetch by Code …). The « button at the bottom of the sidebar makes it narrow.

**Any screen size.** The window fits small laptops (1024×600, 1366×768, also at 125 % / 150 % display
scaling) up to big monitors: the sidebar folds to icons by itself when the window is narrower than
about 1180 px, side-by-side panels go one above the other in a narrow window, rows of buttons wrap,
and when the window is smaller than a tool can shrink, the tool scrolls instead of being cut off.
The window opens at a size that fits the screen it is on.

## Install (for anyone)

Run **`Boostimise-Setup-<version>.exe`** and click through. Nothing else is needed: no Python, no
administrator password (it installs for the current user; an administrator can choose "for all
users" on the first page). It adds Boostimise to the Start menu, and to the desktop if ticked.
Windows 10 or 11, 64-bit. WhatsApp opens in Microsoft Edge (or Chrome), which is already on the PC.

The setup is not code-signed, so Windows may show *"Windows protected your PC"* the first time:
click **More info → Run anyway**.

To update, run the newer setup over the old one; settings and logins are kept. Updating from
CasePilot or CoverDesk replaces their Start-menu and desktop shortcuts with Boostimise ones (a
taskbar pin of the old name stops working: unpin it and pin Boostimise). Uninstall from
*Settings → Apps*; your settings and logins stay in `%LOCALAPPDATA%\CoverDesk` (delete that folder
to remove them too; it keeps the app's old name so that updates never lose a login).

Only one copy runs at a time; starting it again brings the open window to the front.

Every start is fresh: it opens on the first tool (the Photo Enhancer), with no code list loaded and no codes pasted from
last time (press *Load* or *Start* to read the list). File and folder paths, chat names, options and
the WhatsApp / Pathao logins are remembered.

## Run from the code (for development)

Double-click **`run.bat`**. The first time it sets up its own Python environment in `.venv`
(a few minutes, needs internet); after that it starts straight away. Python 3.10 or newer must be
installed.

## Make the installer

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php) once (or `winget install JRSoftware.InnoSetup`).
2. Run `run.bat` once, so `.venv` exists.
3. For a new release, raise `APP_VERSION` in `core\theme.py`.
4. Double-click **`build.bat`** (5–10 minutes). It makes the icon, packs the app with PyInstaller,
   runs the packed app's self-test (every tool must open, every library must load) and writes
   **`dist\Boostimise-Setup-<version>.exe`** – that one file is what you give people.

The name, publisher, version and colours are in `core\theme.py` (one style sheet there gives every
tool its look; the colours come from the logo: navy `#0f1d3a` and blue `#0a6cf0`); the logo is
`assets\logo.png`, 1024 px with a transparent background (`build.bat` puts it on a white tile and
turns it into `icon.ico` for the exe and the setup); `assets\logo-full.png` is the logo with the
"Boostimise" wordmark, transparent, shown on the sign-in pages.

## Where your data is

`<data>` is `%LOCALAPPDATA%\CoverDesk` in the installed app, and `data\` next to the code when
started with `run.bat`.

| Tool | Settings and history |
|---|---|
| Boostimise sign-in | `<data>\account` (`session.bin`, encrypted for your Windows user; delete it to sign out) |
| Photo Enhancer | `<data>\photo_enhancer` (where downloads are saved, pasted images while they are in the list) |
| WhatsApp + Pathao login (all tools) | `<data>\browser` (the shared Edge window's profile) |
| Fetch by Code | `<data>\fetch_by_code` |
| Fetch by Date | `<data>\fetch_by_date` (`downloads_manifest` = photos already saved; the photos go to *Save to*, `Downloads\WhatsApp Images` at first) |
| Reaction List | `<data>\reaction_list` |
| Model Detect | `<data>\model_detect` (result files and photos are saved next to your code list) |
| Pathao Auto-fill | `<data>\pathao_autofill` (`saved_codes.txt` = orders already saved) |
| Rename by Match | `%LOCALAPPDATA%\PhotoRenameByMatch` (undo logs in `rename-logs`) |
| PhotoMatch | `%LOCALAPPDATA%\PhotoMatch` (fingerprint cache) |
| SnapRename | `%LOCALAPPDATA%\SnapRename` (thumbnail cache) |

"Open data folder" at the bottom of the sidebar opens `<data>`. Problems are written to `<data>\app.log`.

Up to version 2.0.0 each tool had its own WhatsApp login. The first time the shared window opens,
Pathao Auto-fill's Edge login (`<data>\pathao_autofill\browser_profile_edge`) becomes the shared one.
The other old logins are no longer used: `<data>\fetch_by_code\wa_session`,
`<data>\fetch_by_reaction\wa_session`, `<data>\model_detect\wa_profile`, `<data>\pathao_autofill\browser_profile`
and `%LOCALAPPDATA%\PhotoRenameByMatch\whatsapp-profile` can be deleted, and their entries logged out on
the phone (WhatsApp › Linked devices).

To move from `run.bat` to the installed app without logging in again, close both, then copy
everything inside `data\` into `%LOCALAPPDATA%\CoverDesk`.

## Accounts, Supabase and the enhancement server

The app uses the website's own services (`core\config.py`): the Supabase project of boostimise.com
for accounts, the daily quota, plans, history and Realtime, and `https://bmapi.boostimise.com` for
the enhancement itself. Only Supabase's *publishable* key is in the app (the self-test fails the
build if a secret key ever ends up there); every write goes through the database functions in
`boostimise\supabase\schema.sql`, exactly like the website. Each enhancement is taken from the
account's daily quota before it is uploaded and recorded when it ends, so the Dashboard, the admin
panel and the limits see desktop and website enhancements alike.

**One Supabase setting is needed for Google sign-in:** Supabase dashboard -> Authentication ->
URL Configuration -> Redirect URLs -> add

    http://127.0.0.1:53214/auth/callback

(the app listens there for a moment while you sign in). Without it Google sends the browser to the
website instead of back to the app. Port 53214 must be free on the PC while signing in.

`BOOSTIMISE_SUPABASE_URL`, `BOOSTIMISE_SUPABASE_KEY` and `BOOSTIMISE_API_URL` (environment
variables) point the app at a test project or server instead.

## Folders

```
app.py            the window with the sidebar
run.bat           start from the code (sets up .venv the first time)
build.bat         make the installer (dist\Boostimise-Setup-<version>.exe)
requirements.txt  Python packages
assets\           the logo (logo.png, logo-full.png; icon.ico / icon.png are made from logo.png) and the style sheet's glyphs
core\             sidebar window, theme (look of every tool), layout helpers (ui.py), self-test,
                  sign-in and account (account.py, gate.py, supabase.py, realtime.py, net.py),
                  the website's look for the new pages (webui.py; assets\flags = country flags)
tools\            one folder per tool (the code)
packaging\        build script, PyInstaller recipe (Boostimise.spec), installer script (installer.iss)
data\             your settings and logins when run from the code (keep this when updating the code)
```

## Updates and remote control

On every start and every 30 minutes the app checks with the server (`core/guard.py`, `desktop_check_in` in
`boostimise/supabase/schema.sql`). The admin panel's **Releases & access** page decides the answer:

- **New release**: add the version (the same as `APP_VERSION` in `core/theme.py`) with its installer link; older
  copies show "Boostimise X is available".
- **Minimum version**: copies older than it show *Update required* instead of the tools until they are updated.
- **Block a version**, **block a PC** (on *PC installs*), or **switch the app off** for everyone.
- Offline the app keeps working for 72 hours after its last successful check (changeable), then asks for the internet.

The database also refuses outdated copies itself (every request carries `X-Client-Info: boostimise-desktop/<version>`),
so copies released before this check existed are locked out too: they show their *Account suspended* screen.

## Notes per tool

- **Photo Enhancer**: drop photos on the page, click *Upload Images*, or paste with Ctrl+V. Each photo
  waits until you press *Enhance photo* (tick *Enhance all photos at once* for the whole list); the AI
  analysis on the right recommends denoise / sharpen and shows the faces found. Finished photos open
  in the before / after slider: drag the divider, scroll to zoom (up to 1:1), double-click to inspect.
  *Download PNG / JPG* save into your Downloads folder (*Change* under the buttons picks another one);
  with several photos, *All as JPG / PNG (.zip)* saves them in one ZIP. If the enhancement server is
  offline the page says so and Enhance waits.

- **Reaction List**: pick ❌, 📵, 😥 or all three, or type your own emojis in the *Custom* box (👍 ❤️ 🔥 …, with or
  without spaces; 👍🏻 counts as 👍), then *Fetch list*. With the code box empty it lists every
  code that got the reaction in the chats read (*Search in*, *Look back*); with codes pasted, only those.
  A reaction counts on the message with the code, on its album, or on a photo the same person sent right
  after it; 😥 also counts 😢 (WhatsApp's built-in sad reaction). Messages naming more than 3 codes
  (lists, summaries) are left out. *Copy list* gives `1. 2-558 Realme C85 ❌ 01734484040`, one code per line.
  *Fetch: List + photos* also saves each listed code's reacted photos in *Save photos to* (`Pictures\Reaction List`
  at first) as `2-558 Realme C85.jpg`, like Fetch by Code: all photos of a code whose message has the reaction,
  otherwise only the photos with it. Codes already saved there are skipped; double-click a row to open its photos.
- **Fetch by Date**: pick the chat (or type its exact name), set *From* / *To* (or press *Today*, *Last 7 days* …)
  and press *Start*. It opens the chat's newest photo in WhatsApp's image viewer and steps back photo by photo,
  passing the newer ones without downloading them and saving the ones in the range, until it reaches one older
  than *From* (videos are passed). Photos are saved as `<Save to>\<chat>\<YYYY-MM-DD>\<date>_<time>_<hash>.jpg`,
  with `download_log.csv` in the chat's folder; a photo already in the folder is skipped, one deleted from it is
  saved again (*Forget saved photos* makes the next fetch save everything again). The date comes from the name
  WhatsApp gives the download, so it is exact; a photo without one is left out and named in the log. Leave the
  WhatsApp window alone while it runs (it clicks there); other tools that click in WhatsApp wait for it. If
  WhatsApp changes its page and the tool stops finding photos, `tools\fetch_by_date\SELECTORS.md` lists what it
  relies on.
- **Pathao Auto-fill**: click *Open / Connect browser*, log in to WhatsApp and Pathao in the shared Edge
  window once. F5 Start, F6 Pause, F8 Saved → Next, F9 Skip. *Keep this window on top* applies while this
  tool is the one shown. *Check Pathao* looks the loaded codes up in Pathao's orders (the code is an order's
  Merchant Order ID; only orders since the code prefix last started again from 1 count), marks the ones found
  as saved and writes their phone next to them in the list.
- **Model Detect**: *Identify phones with* can read written model names for free (chat text and text in
  the photos), or ask a local AI (Ollama, `ollama pull qwen2.5vl:7b`) or the Claude API (paid, needs a key).
- **PhotoMatch**: *AI match* runs on the Intel NPU / GPU / CPU through OpenVINO; *Visual match* needs nothing extra.
- **Rename by Match**: tick *Preview only* the first time; *Undo last rename* reverts the last run.
