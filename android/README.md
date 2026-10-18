# Boostimise for Android

The phone version of Boostimise (the Windows app one folder up). Same Google account, same quota,
plans and dashboard as boostimise.com, and the same WhatsApp tools.

| Menu | What it does on the phone |
|---|---|
| **Photo Enhancer** | Pick photos (or *Share → Boostimise* from WhatsApp / the gallery), Enhance, compare before / after, download PNG / JPG or all as ZIP. Saved in *Pictures/Boostimise/Enhanced*. |
| **Fetch by Code** | Finds each code's photos in your WhatsApp chats and saves them as `2-558 Realme C85.jpg` in *Pictures/Boostimise/Fetch by Code*. With *A chat for each list* the 6- codes are searched only in the 6- list's chat ("Customise Phone Case (6-)"), the 5a- codes in the 5a- chat …, filled in from chat names and changeable. |
| **Reaction List** | The codes reacted with ❌, 📵, 😥 or the emojis you type under *Custom*, with the phone number from the order message. *Copy list* / *Save .txt*. *Fetch: List + photos* also saves their reacted photos as `2-558 Realme C85.jpg` in *Pictures/Boostimise/Reaction List*. |
| **Model Detect** | Finds each code's photos and writes the phone model next to the code: names written in the chat, text printed in the photos (read on the phone), or Claude (your API key). |
| **Pathao Auto-fill** | Reads the order message of each code and fills Pathao's New Delivery form (merchant.pathao.com, inside the app). You check it and tap Save; the next code fills itself. **Check Pathao** marks the codes of the list that already have a Pathao order (the code is its Merchant Order ID, counting only the prefix's current round of codes) and writes each one's phone next to it in the list. |
| **WhatsApp** | The one WhatsApp Web every tool uses. |
| **Dashboard / Plans** | As on the website. |

## Linking WhatsApp (once)

The app runs WhatsApp Web inside itself, linked to your WhatsApp like a PC (it is one more of
WhatsApp's *Linked devices*; the PC app's link keeps working).

1. Open **WhatsApp** in the menu. On the WhatsApp Web page tap **Log in with phone number**, enter
   this phone's WhatsApp number: an 8-letter code appears.
2. In the WhatsApp app: **⋮ → Linked devices → Link a device → Link with phone number instead**, type the code.

The tools read chats through WhatsApp Web's own modules (the Windows app's `wa_bridge.js` and
`wa_bridge_reactions.js`, unchanged, plus `boost_bridge.js`), so they never scroll or tap in WhatsApp.
Keep Boostimise open while a tool reads chats; a notification shows while one is working.

## Sign-in

*Continue with Google* opens Chrome; Google comes back to the app on
`http://127.0.0.1:53214/auth/callback`, the address the Windows app already uses, so **no new
Supabase setting is needed**. The phone app sends `X-Client-Info: boostimise-android/<version>`:
the database's desktop version lock does not apply to it (it is treated like the website), and
enhancements are recorded with device type `mobile`.

## Build

Needs JDK 17 and the Android SDK (platform 35, build-tools 35). On this PC they are in
`%LOCALAPPDATA%\Programs\Microsoft\jdk-17.0.10.7-hotspot` and `%LOCALAPPDATA%\Android\Sdk`
(`local.properties`). Then double-click **`build-apk.bat`** → `Boostimise.apk`.

Debug build: `gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`.

The release APK is signed with the debug key so it installs straight away (copy it to the phone and
open it; allow "install unknown apps"). For the Play Store, make your own key and set it in
`app/build.gradle.kts`.

Android 10 or newer.

## Code

```
app/src/main/assets/        wa_bridge*.js (from the Windows app), boost_bridge.js, phone_models.tsv
app/src/main/java/.../core  Supabase, account and sign-in, Realtime, storage, code list + order parsing
.../wa                      the shared WhatsApp Web (WaEngine) and the "tools at work" notification
.../tools/*                 one folder per tool, ported from tools/* of the Windows app
.../ui                      the screens (Jetpack Compose)
```
