# DealRadar — Android app (Expo)

A native React Native app (Expo SDK 57, RN 0.86, New Architecture) that talks
to the DealRadar FastAPI server's JSON API. It replaces the old Kotlin WebView
shell in `legacy/android-kotlin/`.

- Package id `com.dealradar.app`, name **DealRadar**
- Default server: `https://dealradar-k2hb.onrender.com` (changeable in-app)
- Auth: the server's `tgdeals_session` cookie. On Android, RN `fetch` goes
  through OkHttp + `ForwardingCookieHandler`, which is backed by
  `android.webkit.CookieManager` — the same persistent jar a WebView uses — so
  the session survives restarts and is shared with the optional "Open website"
  WebView screen. All API calls use `credentials: 'include'`.

## Layout

| Path | What |
|---|---|
| `App.tsx` | Root: boot (server reachability → public `Main`), offline screen (Retry / Change server), visitor session, foreground feed polling, share/shortcut/notification routing, back-twice-to-exit |
| `src/navigation/` | Root native-stack + bottom tabs, route types, `Setup` screen, placeholders |
| `src/native/config.ts` | Server URL storage (AsyncStorage), URL normalisation (`localhost` → `10.0.2.2`, bare host → https except LAN), `/api/ping` probe, brand colours |
| `src/native/session.ts` | `getServerUrl()`, `startVisitorSession()`, `enableNotifications()`, legacy `onSignedIn/onSignedOut` |
| `src/native/device.ts` | `getDeviceId()`, `registerDevice()` |
| `src/native/notifications.ts` | Channel `deal-alerts`, permission, Expo push token, Notifee rich display, device-feed poller, push dedupe |
| `src/native/backgroundTask.ts` | Module-scope tasks: feed poll (≥15 min), push-received task, Notifee background press handler |
| `src/native/deepLinks.ts` | `routeTo()` for notification taps, share intents, shortcuts (held until the navigator is ready) |
| `src/native/shareIntent.ts`, `quickActions.ts`, `updates.ts` | Share → `CheckPrice`, launcher shortcuts, OTA update check |
| `src/screens/`, `src/components/`, `src/api/`, `src/theme/` | The native UI screens |
| `scripts/make-mono-icon.js` | Regenerates the white-on-transparent notification/monochrome icons |

## Run in development

Requires **Node ≥ 22.13** (Expo SDK 57). With nvm: `nvm install 22 && nvm use 22`.

```bash
cd mobile
npm install
npx expo start            # Expo Go / dev client
```

**Expo Go limitation:** Expo Go cannot receive notifications on Android since
SDK 53 and cannot run `expo-background-task`. Everything notification-related
needs a development build or the APK:

```bash
npx expo run:android      # builds + installs a debug dev build on a device/emulator
```

Setup screen presets: *Live server*, *Local (emulator)* = `http://10.0.2.2:8765`
(the host machine as seen from the Android emulator), *Local (Wi-Fi)* =
`http://192.168.x.x:<port>`. Cleartext http is enabled (`expo-build-properties`
→ `usesCleartextTraffic`) for these LAN/emulator servers. Change it later from
Settings → *Change server*, or from the offline screen.

## Build the APK locally (no Expo account needed)

```bash
cd mobile
# JDK 17 or 21. JDK 24+ (incl. current Android Studio's bundled JBR 25) fails the
# CMake configure step with "A restricted method in java.lang.System has been called".
# On this Mac a Gradle-provisioned Temurin 17 works:
export JAVA_HOME="$HOME/.gradle/jdks/eclipse_adoptium-17-aarch64-os_x.2/jdk-17.0.20.1+1/Contents/Home"
export ANDROID_HOME=~/Library/Android/sdk
npx expo prebuild --platform android --clean
cd android
./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a
cp app/build/outputs/apk/release/app-release.apk ../dist/DealRadar.apk
```

The release build is signed with the debug keystore — fine for sideloading
(`adb install -r dist/DealRadar.apk`), not for the Play Store. Drop the
`-PreactNativeArchitectures` flag to also build armeabi-v7a/x86/x86_64 (slower,
larger APK). `android/` is generated — configure native behaviour via
`app.json` and config plugins, never by hand.

## Visitor device (no login)

The app opens straight to deals in public mode. `startVisitorSession()`
(`src/native/session.ts`, called by `App.tsx` once the server answers):

- `getDeviceId()` (`src/native/device.ts`) — `app_<uuid4>`, stored in
  AsyncStorage `dr-device-id`. Screens use it for saved deals, price alerts, follows.
- `POST /api/devices/register {device_id, platform, push_token?, digest?, digest_hour?}`
  on every start; `registerDevice({digest, digest_hour})` changes digest prefs.
- ~6 s after the first launch it asks for `POST_NOTIFICATIONS` (Android 13+),
  once. `enableNotifications()` re-asks from a Settings toggle.
- If an Expo push token can be obtained (needs FCM, see below) it is registered,
  and re-registered when `addPushTokenListener` reports a new token.

## Notifications

Channel **`deal-alerts`**: high importance (heads-up), default sound,
vibration, lights, brand colour `#2563eb`, small icon `notification_icon`
(monochrome). Created by Notifee; it is also the FCM default channel, so
remote pushes land in the same channel.

**Rich display (Notifee `@notifee/react-native` 9.1.8, BigPictureStyle).**
Collapsed: title, body and the product thumbnail on the right (`largeIcon`).
Expanded: the full product image (`picture`, large icon hidden) plus a
**View deal** action. Items without an image use BigTextStyle. If Notifee's
native module is missing (Expo Go) it falls back to a plain expo-notifications
banner. No Notifee config plugin is used: Android autolinks it and its
`build.gradle` adds its own local maven repo.

**Device feed.** On every foreground and from an `expo-background-task` job
(≥ 15 min, OS-scheduled) the app calls
`GET /api/devices/feed?device_id=&since=<lastSeen>&limit=20` and shows each
new item (deduped by item id; `lastSeen` = response `now`). The first poll
on an install only sets the cursor. Items whose `deal_id` already arrived as
a remote push in the last 24 h are skipped (tracked from foreground receipt,
taps, pushes still in the shade, and the expo-notifications background task).

**Remote pushes (Expo).** Displayed by expo-notifications/FCM when the app is
backgrounded: title, body and — with `richContent.image` — the image. While
the app is in the foreground, a push with `data.image_url` is re-shown via
Notifee so it gets the same big-picture layout. For the full Myntra layout
while backgrounded too, the server would have to send data-only pushes and
let the app render them (not done yet).

**Taps** (Expo push, Notifee, warm or cold start, either the body or
"View deal") → `DealDetail {id: data.deal_id}` (fallback `?deal=` in
`data.url`, then `Website {path}`), pushed on top of `Main`.

Test the background job on a device: `adb shell cmd jobscheduler run -f com.dealradar.app <jobId>`
or call `BackgroundTask.triggerTaskWorkerForTestingAsync()` from a dev build.

### Enabling remote push (needs Firebase)

Without FCM credentials `getExpoPushTokenAsync` fails; the device then
registers token-less and relies on the feed poller.

1. Create a Firebase project, add an Android app with package
   `com.dealradar.app`, download **`google-services.json`** into `mobile/` and
   set `"android": { "googleServicesFile": "./google-services.json" }` in `app.json`.
2. Upload the **FCM v1 service-account key** to Expo:
   `npx eas-cli@latest credentials` → Android → Google Service Account → *FCM V1*.
3. Rebuild the APK (native change — an OTA update is not enough).

## Share to DealRadar

`expo-share-intent` adds a `text/*` SEND intent filter. Sharing a product
from Amazon/Flipkart/etc. opens the app on `CheckPrice {url}` (first http(s)
URL found in the shared text).

## Launcher shortcuts

Long-press the icon: *Search deals* → `Search`, *Saved deals* → `Saved`,
*Check a price* → `CheckPrice` (`expo-quick-actions`, dynamic shortcuts; icons
from `assets/shortcut-*.png`).

## Over-the-air updates

`expo-updates` with `runtimeVersion: {policy: "appVersion"}` and the EAS
`preview` build profile on channel `preview`. JS/asset-only changes ship
without a new APK:

```bash
cd mobile
EAS_NO_VCS=1 npx eas-cli@latest update --branch preview --message "what changed"
```

The app checks on launch and applies the update on the next cold start (or
immediately if it finishes downloading while the connecting screen is still
up). Anything native — new libraries, `app.json` plugin/permission changes,
icons — needs a new build **and** a `version` bump in `app.json` (which also
changes the runtime version, so old APKs never receive incompatible JS).

Build an APK on EAS:

```bash
EAS_NO_VCS=1 npx -y eas-cli@latest build -p android --profile preview --non-interactive --no-wait
```
