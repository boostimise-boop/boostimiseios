# WhatsApp Web selectors used by this tool

WhatsApp Web has no public API; this tool automates the real web UI the same
way a person would use it. The DOM is not documented and can change without
notice, so if fetching stops working, this is the first place to look.

All selectors below were verified against the live site in **September 2026**.
They favour element `id`s, `data-testid`s and ARIA roles over visible text,
which is localized.

## How images are enumerated (important)

The message list **cannot** be used to find images. WhatsApp virtualizes
off-screen messages: the row keeps its `data-id` but its contents -- the
thumbnail included -- are stripped out of the DOM. A scan of the message
list therefore only ever sees whatever is on screen at that instant, and
repeated scans of the same 99 rows returned 18 images once and 2 another
time.

Instead, the tool opens the newest image in WhatsApp's own **media viewer**
and walks backwards with the viewer's **Previous** button. The viewer is
backed by the chat's full media collection, so it steps through every image
in the chat regardless of what the message list has loaded. This was
verified walking from 2026-09-07 back to 2026-08-19 with only *one*
thumbnail present in the DOM.

Dates come from the **download filename** (`WhatsApp Image 2026-09-07 at
11.49.33 PM.jpeg`), which WhatsApp derives from the message itself. This is
the only reliable date source: image messages show only a time (`11:49 PM`)
in the DOM, and the date separators above them are localized text.

| Purpose | Selector | Where used |
|---|---|---|
| Chat list sidebar container | `#pane-side` | `list_chats`, `open_chat` |
| Search input (tried in order) | `input[data-tab='3']` **(current -- a plain `<input>`, not a contenteditable div)**, `[data-testid='chat-list-search-container'] input`, `div[contenteditable='true'][data-tab='3']` | `open_chat` (`_SEARCH_BOX_SELECTORS`) |
| Chat/search-result row (tried in order) | `[data-testid='cell-frame-container']` **(current)**, `div[role='listitem']` (obsolete), `div[role='row']` | `open_chat` (`_ROW_SELECTORS`) |
| Chat/contact display name | `span[title]` inside `#pane-side` | `list_chats`, `open_chat` |
| Open conversation pane | `#main` | `open_chat` |
| Message list scroll container | `[data-testid='conversation-panel-messages']` (nearest scrollable ancestor) | `_scroll_up` |
| Image thumbnail in a message | `[data-testid='image-thumb']` | `_open_newest_image` |
| Media viewer modal | `[data-testid='media-viewer-modal']` | `_open_newest_image`, `_close_viewer` |
| Older-media button in the viewer (localized label; `aria-disabled="true"` at the oldest item) | `[aria-label='Previous']` | `_advance`, `_VIEWER_STATE_JS` |
| Newer-media button in the viewer (localized label), used only to find the starting point | `[aria-label='Next']` | `_seek_forward`, `_advance(forward=True)`, `_VIEWER_STATE_JS` |
| Video currently shown in viewer | `[data-testid='media-viewer-modal'] video` | `_VIEWER_STATE_JS` (videos are stepped over without downloading) |
| Current image in the viewer (its `src` changes when the viewer moves) | `[data-testid='media-canvas-img']`, `[data-testid='media-image'] img` | `_VIEWER_STATE_JS` (movement detection) |
| Viewer header: sender | `[data-testid='media-viewer-modal'] [data-testid='cell-frame-title']` (e.g. `You`, `Hira`) | `_VIEWER_STATE_JS` (CSV sender column) |
| Viewer header: date/time, minute precision | `[data-testid='media-viewer-modal'] [data-testid='cell-frame-secondary']` (e.g. `9/7/2026 at 11:49 PM`) | `_VIEWER_STATE_JS` (pre-filter, resume) |
| Download button in the viewer toolbar (one-to-one chats) | `[data-icon='ic-download']` **(current; was `download`)**, `[aria-label='Download']`, `[data-icon='download']` | `_trigger_download` (`_DOWNLOAD_BTN_SELECTOR`) |
| Overflow ("three dots") button, which holds Download in **group** chats | `[aria-label='Menu']`, `[data-icon='menu']`, `[data-icon='more-refreshed']` inside the modal | `_trigger_download` (`_MENU_BTN_SELECTOR`) |
| Download item inside that menu (rendered outside the modal) | `[role='menu'] [data-icon='ic-download']`, `[role='menu'] [aria-label='Download']` | `_trigger_download` (`_MENU_DOWNLOAD_SELECTOR`) |

## Long runs

- **The viewer header is used to skip, the filename is used to decide.**
  The header's `9/7/2026 at 11:49 PM` is read without downloading anything,
  so items certainly outside the range (with a 1-2 minute safety margin)
  and all videos are stepped over for free -- this is how the walk gets from
  the chat's newest image back to the To time. Anything near a boundary is
  downloaded and judged by the filename's exact seconds. `Today`,
  `Yesterday` and weekday headers are exact. A numeric date is ambiguous
  (month-first vs day-first) until it is compared with a downloaded file;
  until then both readings are kept (minus any in the future) and an item is
  skipped only if both are outside the range. If the header and a file ever
  disagree the header stops being trusted and every image is checked by
  download instead (slower, still exact).
- **Skipping needs the file, not just the manifest.** An image is only
  skipped as a duplicate if its hash is in the manifest *and* its file is
  still in the chat's folder, so deleted or moved files are saved again.
- **The walk must start after the To time.** The first image is opened from
  whatever thumbnail is rendered, which is *not* necessarily the newest --
  if the chat was left scrolled up, the last rendered thumbnail is from
  there, and walking back from it silently missed every newer image in the
  range. So the chat is scrolled to its newest message first, and after the
  viewer opens it steps **Next** until an item is certainly after the To
  time (or Next is disabled at the newest image).
- **Nothing waits a fixed time.** After clicking Previous the walk waits
  (up to 60 s, clicking again once) until the viewer's image actually
  changes, so slow loads can't cause a double download or a skipped image.
- **Downloads retry**: up to 3 attempts, 30 s for the button, 180 s for the
  download to start.
- **The viewer is reopened if it closes or freezes**: the walk scrolls the
  chat to its newest message, reopens the newest image, and fast-forwards
  (header only, no downloads) back to where it was. Up to 5 recoveries per
  50 successful steps.
- **No step cap.** The walk ends only at the start of the range, at the
  oldest media in the chat, on Stop, or after repeated unrecoverable errors.

## Behaviours that bit us (don't regress these)

- **Never press Escape after opening a chat.** In current WhatsApp Web,
  Escape closes the conversation that was just opened. This made every
  fetch report "no message rows found". Escape is only safe for closing the
  media viewer, which `_close_viewer` does.
- **Read the chat list in one snapshot.** The sidebar re-renders while it
  scrolls; reading titles element by element (`.all()` then
  `get_attribute`) waited 60 s on a row that had vanished and failed the
  whole Refresh Chats. `list_chats` uses a single `evaluate_all`.
- **Park the mouse before clicking the search box.** Playwright's pointer
  starts at (0, 0), which hovers the left nav rail; its tooltip then
  intercepts clicks on the search box. `_park_mouse` moves it to the middle
  of the page first.
- **Scroll the list from JavaScript, not the mouse wheel.** After the media
  viewer closes, wheel events over `#main` stop reaching the list and
  history silently stops loading.
- **Filenames are not unique.** Images sent in the same second share a
  filename but are different files, so downloads are de-duplicated by
  SHA-256 of their contents and saved with a hash suffix.
- **Contents are not unique either.** The same picture is often sent twice
  (e.g. 12:21:48 and again at 12:23:00). Within a run a message is
  identified by hash *and* timestamp; keying on the hash alone made the walk
  treat the second copy as "already seen" and drop it silently -- even when
  the first copy was outside the range and never saved.
- **Download hides in the overflow menu in group chats.** A group adds
  "Reply privately" and "Report <sender>" to the viewer toolbar, which
  pushes Download into the three-dots menu -- the toolbar then has no
  download icon at all (and in that layout the modal carries no `data-icon`
  attributes either). `_trigger_download` falls back to opening the menu.
  Symptom when this breaks: the status sits on "Checking ..." and nothing
  saves, while other chats work.

## If something breaks

1. Open WhatsApp Web in a normal Chrome/Edge tab, right-click the relevant
   element (an image, the search box, the viewer's download button) and
   choose **Inspect**.
2. Compare the real attributes against the table above and update
   `fetcher.py`. For lists like `_SEARCH_BOX_SELECTORS` and
   `_ROW_SELECTORS`, add the new candidate at the *front* rather than
   replacing the list -- the old entries stay as fallbacks.
3. The app's Log panel names the failing step ("Could not locate the
   WhatsApp Web search box", "Found no images in this chat to start from",
   "Timed out downloading an image") instead of failing silently.
