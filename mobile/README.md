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
| `App.tsx` | Root: boot/auth gate (`GET /api/auth/me` → `Main` or `Login`), offline screen (Retry / Change server), foreground polling, back-twice-to-exit, notification-tap routing |
| `src/navigation/` | Root native-stack + bottom tabs, route types, `Setup` screen, placeholders |
| `src/native/config.ts` | Server URL storage (AsyncStorage), URL normalisation (`localhost` → `10.0.2.2`, bare host → https except LAN), `/api/ping` probe, brand colours |
| `src/native/session.ts` | Contract for screens: `getServerUrl()`, `onSignedIn(user)`, `onSignedOut()` |
| `src/native/notifications.ts` | Channel `deal-alerts`, permission, Expo push token, `/api/notifications` poller |
| `src/native/backgroundTask.ts` | `expo-background-task` job (≥15 min) running the poller |
| `src/native/deepLinks.ts` | Notification tap → `DealDetail {id}` (cold start too) |
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

## Notifications

Channel id **`deal-alerts`** (high importance). After sign-in
(`onSignedIn`), the app asks for `POST_NOTIFICATIONS` (Android 13+), then:

1. **Remote push (when configured):** reads `extra.eas.projectId` from
   `app.json`; if present, gets an Expo push token and `POST`s it to
   `/api/push/register` (`{token, platform: 'android'}`) with the session cookie.
   No projectId, or no FCM credentials in the build → skipped silently.
2. **Polling fallback (works today, no Firebase/EAS):** on every app foreground
   and from a background task (min 15 min, OS-scheduled, survives reboot)
   the app calls `GET /api/notifications?since=<lastSeen>` and posts one local
   notification per new item (deduped by id; `lastSeen` = the response's
   `now`). The first poll after sign-in only sets the cursor, so old alerts
   don't burst in. With push active, polls only advance the cursor.

Tapping a notification (push or local, warm or cold start) opens
`DealDetail` for `data.deal_id` (falling back to `?deal=` in `data.url`, then
to the `Website` screen at that path). `onSignedOut()` unregisters the push
token (call it *before* `/api/auth/logout`), stops polling and clears the
cursor.

Test the background job on a device: `adb shell cmd jobscheduler run -f com.dealradar.app <jobId>`
or call `BackgroundTask.triggerTaskWorkerForTestingAsync()` from a dev build.
Server-side test: `POST /api/notifications/test` while signed in, then
foreground the app.

### Enabling true remote push later

The server already sends via the Expo Push API to Expo tokens. To make tokens
available:

1. `npx eas-cli@latest login` and `npx eas-cli@latest init` in `mobile/` —
   this creates an EAS project and writes `extra.eas.projectId` into `app.json`.
2. Create a Firebase project, add an Android app with package
   `com.dealradar.app`, download **`google-services.json`** into `mobile/` and
   set `"android": { "googleServicesFile": "./google-services.json" }` in
   `app.json`.
3. Upload the **FCM v1 service-account key** to Expo:
   `npx eas-cli@latest credentials` → Android → Google Service Account →
   *FCM V1* (or via the expo.dev project page).
4. Rebuild (`expo prebuild --clean` + Gradle, or `eas build -p android`). On
   next sign-in the app registers its token and the server pushes instantly;
   polling then just keeps the cursor in step.
