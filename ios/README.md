# Boostimise for iPhone

The iPhone version of Boostimise, the same app as `android/`: same Google account, same quota,
plans and dashboard as boostimise.com, and the same WhatsApp tools.

| Menu | What it does on the iPhone |
|---|---|
| **Photo Enhancer** | Pick photos (Photos or Files, or *Open in Boostimise*), Enhance, compare before / after, download PNG / JPG or all as ZIP. Saved to **Photos** and to *Files › On My iPhone › Boostimise › Enhanced*. iPhone photos (HEIC) are sent as JPEG. |
| **Fetch by Code** | Finds each code's photos in your WhatsApp chats and saves them as `2-558 Realme C85.jpg` in *Files › Boostimise › Fetch by Code*. *A chat for each list* works as on Android. |
| **Reaction List** | The codes reacted with your shop's reactions (❌, 📵, 😥 until you change them in *Shop settings*, with what each one means) or the emojis you type under *Custom*, with the phone number from the order message. *Copy list* / *Save .txt*. *Fetch: List + photos* also saves their reacted photos as `2-558 Realme C85.jpg` in *Files › Boostimise › Reaction List*. |
| **Model Detect** | Finds each code's photos and writes the phone model next to the code: names written in the chat, text printed in the photos (read on the iPhone with Apple's text recognition), or Claude (your API key, kept in the Keychain). |
| **Pathao Auto-fill** | Reads the order message of each code and fills Pathao's New Delivery form (merchant.pathao.com, inside the app). You check it and tap Save; the next code fills itself. **Check Pathao** marks the codes of the list that already have a Pathao order (the code is its Merchant Order ID, counting only the prefix's current round of codes) and writes each one's phone next to it in the list. |
| **WhatsApp** | The one WhatsApp Web every tool uses. |
| **Dashboard / Plans** | As on the website. |
| **Shop settings** | Your reactions and what they mean, and the WhatsApp chat of each code list. Kept in your Boostimise account, so the website and the Windows app use the same (Core/Shop). |

The tools read chats through WhatsApp Web's own modules with the same scripts as the Windows and
Android apps (`android/app/src/main/assets/*.js`, shared, not copied), so they never scroll or tap.

## Differences from Android

- **Keep Boostimise open while a tool reads chats.** iOS pauses apps in the background (there is no
  "foreground service"). While a tool works the screen stays on; if you switch away, a notification
  says to come back, and the tool goes on when you do.
- **Files** go to the Files app (*On My iPhone › Boostimise*), not to a Pictures folder. The *Open*
  button under the results opens the folder.
- **No "Share → Boostimise" from WhatsApp yet** (that needs a separate share extension). Use
  *Upload Images*, or save the photos to Files and pick them with *From Files*.

## Linking WhatsApp (once)

1. Open **WhatsApp** in the menu. On the WhatsApp Web page tap **Log in with phone number** and
   enter your WhatsApp number: an 8-letter code appears.
2. In WhatsApp on the phone that has your WhatsApp: **Settings › Linked devices › Link a device ›
   Link with phone number instead**, type the code.

It is one more of WhatsApp's linked devices; the PC and Android links keep working.

## Sign-in

*Continue with Google* opens a sign-in sheet in the app. Google comes back to
`http://127.0.0.1:53214/auth/callback` (a tiny server inside the app, the same address the Windows
and Android apps use), so **no new Supabase setting is needed**. The app sends
`X-Client-Info: boostimise-ios/<version>` and records enhancements with device type `mobile`.

## Updates and access (admin panel)

As on Android (`Core/AppGuard.swift`): the iPhone checks in on start, when it comes back to the
front and every 30 minutes, shows up on the admin panel's **Phone installs** page, and obeys
**Releases & access › iPhone** (required version, blocked version, blocked iPhone, app switched
off; a newer release shows a banner). The id is a hash of a random id kept in the Keychain, so it
stays the same after reinstalling. For a new build raise `MARKETING_VERSION` and
`CURRENT_PROJECT_VERSION` in `project.yml`, then add the release on the iPhone tab.

## Build it without a Mac (GitHub Actions)

Apple only builds iPhone apps on a Mac, so GitHub's Macs do it:

1. Put this `boostimise` folder in a **private** GitHub repository (GitHub Desktop: *Add local
   repository* › *create a repository* › *Publish*, keep "private" ticked). The workflow is
   `.github/workflows/ios.yml`; it needs `ios/`, `android/app/src/main/assets/` and `.github/`.
2. On github.com open the repository › **Actions** › **iOS app**. It runs on every push (or press
   *Run workflow*). After ~10 minutes the run has an artifact **Boostimise-ipa**: download it and
   unzip it to get `Boostimise.ipa`.
3. If the run fails, its **build-log** artifact has the Xcode errors.

## Install it on the iPhone (from Windows)

The .ipa is unsigned; [Sideloadly](https://sideloadly.io) signs it with your Apple ID while
installing (a free Apple ID is enough):

1. Install iTunes (from apple.com, not the Microsoft Store version) and Sideloadly on the PC.
2. Connect the iPhone by USB, unlock it, tap *Trust*.
3. In Sideloadly drop `Boostimise.ipa`, enter your Apple ID, press **Start**.
4. On the iPhone: **Settings › General › VPN & Device Management** › your Apple ID › *Trust*, and
   **Settings › Privacy & Security › Developer Mode** › on (the iPhone restarts).

With a free Apple ID the app stops opening after **7 days**: install it again with Sideloadly (your
sign-in, WhatsApp link and settings stay). A paid Apple Developer account ($99/year) gives one
year, and TestFlight / the App Store.

## Build it on a Mac

```
brew install xcodegen
cd ios && xcodegen generate && open Boostimise.xcodeproj
```

In Xcode: *Boostimise* target › *Signing & Capabilities* › choose your Team, connect the iPhone,
press Run. iOS 17 or newer.

## Code

```
project.yml                 XcodeGen project (the .xcodeproj is generated, not kept)
Boostimise/Info.plist       URL scheme, Files sharing, photo permission, App-Bound Domains
Boostimise/Sources/Core     Supabase, account and sign-in, Realtime, storage, code list + order parsing
.../WA                      the shared WhatsApp Web (WaEngine) and the "tools at work" state (Jobs)
.../Tools                   one file per tool, ported from android/.../tools
.../UI                      the screens (SwiftUI)
```

One dependency: [PhoneNumberKit](https://github.com/marmelroy/PhoneNumberKit) (the mobile number
form, like libphonenumber on Android).
