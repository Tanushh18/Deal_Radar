# 📡 DealRadar — Android app

The full DealRadar product as a standalone, runnable Android app: the same UI,
the same features, in an installable APK.

```
DealRadar-App/
├── server/     FastAPI backend + the DealRadar web UI  (Python)
└── android/    Android Studio project                  (Kotlin)
```

**The app defaults to the live deployment at
`https://dealradar-k2hb.onrender.com`, so you can install it and use your real
deals without running anything locally.** The `server/` folder is a complete
copy of that same backend for developing and testing against isolated data —
its own virtualenv, `.env`, SQLite database and freshly generated keys, sharing
nothing with the original `Telegram Automation` project or with production.

---

## How the app is put together

The Android app is a purpose-built shell around the DealRadar frontend, which
the server serves. That is deliberate, not a shortcut:

- **The UI is genuinely identical** — same HTML, CSS and JS, not a
  reimplementation that drifts from the web version.
- **Auth just works.** Sign-in is a session cookie set by the backend. Because
  the page and its `fetch` calls share one origin, nothing has to be proxied,
  re-signed, or re-implemented, and no CORS/`SameSite` workarounds are needed.
- **UI fixes ship without a new APK** — update the server, reopen the app.

What the shell adds natively, which a browser tab would otherwise provide:

| Behaviour | Where |
|---|---|
| First-run server setup + connection test against `/api/ping` | `SetupActivity.kt` |
| Hardware back → WebView history, then double-tap to exit | `MainActivity.kt` |
| Pull-to-refresh (armed only at scroll top, so it can't fight the page) | `MainActivity.kt` |
| "Buy now" / trending links → real browser, not trapped in the WebView | `onCreateWindow` |
| `tg://`, `upi://`, `mailto:` → the app that owns them | `shouldOverrideUrlLoading` |
| Persistent cookies flushed on pause, so you stay signed in | `onPause` |
| Offline / server-down state with retry + change-server | `activity_main.xml` |
| Downloads via the system DownloadManager | `startDownload` |
| Dark theme, splash and launcher icon matching the web design tokens | `res/values/` |
| "App settings" injected into the existing account dropdown | `NATIVE_GLUE_JS` |

---

## Run it

### 1. Add your Telegram API credentials

`server/.env` was created with fresh `SECRET_KEY` / `ADMIN_TOKEN`, but the
Telegram keys are blank — without them, sign-in is disabled and the login
screen says so. Get them at <https://my.telegram.org> → API development tools,
or copy the two lines from your existing project:

```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
```

### 2. Start the server

```bash
cd server
make android          # binds 0.0.0.0 and prints the address to enter in the app
```

Already installed — `make setup` is only needed on a fresh clone. Sanity check:

```bash
curl http://localhost:8000/api/ping
```

### 3. Open the Android project

Open **`android/`** in Android Studio (open that folder, not `DealRadar-App/`).
Gradle 8.9 + AGP 8.5.2, compileSdk 34, minSdk 24. Press **Run**.

Or from the command line:

```bash
cd android
./gradlew installDebug      # to a running emulator/device
./gradlew assembleDebug     # → app/build/outputs/apk/debug/app-debug.apk
```

### 4. Point the app at the server

The setup screen is **prefilled with the live server**, so the normal path is
just tapping **Connect** — steps 1–2 are only needed to run a local backend.

| Running against | Enter |
|---|---|
| Live deployment (default) | `https://dealradar-k2hb.onrender.com` |
| Local server + emulator | `http://10.0.2.2:8000` — "Local (emulator)" chip |
| Local server + physical device | `http://<your-mac-ip>:8000`, printed by `make android` |

Two inputs get corrected rather than failing obscurely: `localhost` becomes
`10.0.2.2` (on a phone, `localhost` is the phone), and a bare hostname gets
`https://` unless it is a LAN address — over `http` the backend's `Secure`
session cookie is dropped, which presents as signing in and instantly being
logged out. **Connect** verifies the server answers before saving, and allows
up to 70s for a free-tier instance to wake from sleep.

To change it later: account menu (avatar, top-right) → **App settings**.

### 5. Use it

Sign in with your Telegram phone number → the code arrives *in Telegram*, not
by SMS → pick your deal channels → **Save selection** kicks off the first sync.
Search, filters, price history, trending, and alerts all work as on the web.

---

## Automated UI testing

Flows live in `android/maestro/`. [Maestro](https://maestro.dev) is used rather
than Espresso because the entire UI is one WebView — Espresso is built for
native view hierarchies and would see almost nothing here.

```bash
curl -fsSL "https://get.maestro.mobile.dev" | bash    # no Homebrew needed
export PATH="$PATH:$HOME/.maestro/bin"

maestro test android/maestro/01-setup.yaml     # fresh install → connect → sign-in page
maestro test android/maestro/02-browse.yaml    # search, filters, detail modal, tabs
maestro studio                                 # interactive selector picker
```

`01-setup` clears state and runs from zero. `02-browse` deliberately does not:
Telegram sign-in needs a login code delivered to your Telegram app, which no
automation can obtain, so **sign in by hand once** and every later run reuses
that session cookie.

## Notes

**Google Sheets is off by default.** This instance runs SQLite-only so it can
never write over the sheet your original deployment owns. To enable it, create
a *new* sheet, share it with the service account, and fill `GOOGLE_SHEET_ID` +
`GOOGLE_SERVICE_ACCOUNT_B64` in `server/.env`.

**`PUBLIC_URL` must stay empty for local testing.** The session cookie is
marked `Secure` only when it starts with `https://`, and a `Secure` cookie is
silently dropped over plain http — which presents as "sign-in succeeds, then
immediately logs out."

**The release build is debug-signed** so `assembleRelease` produces an
installable APK without a keystore. Add a real `signingConfig` before shipping
anywhere real.

**Server API reference** and deployment instructions: `server/README.md`.

---

## Verified on this machine

- `server`: `make test` → all pipeline checks pass; `/api/ping`, `/api/health`,
  `/api/stats`, `/api/deals`, `/api/deals/categories` and the static UI all 200.
- `android`: `./gradlew assembleDebug` and `assembleRelease` both succeed
  (R8 + resource shrinking enabled on release).
- End-to-end on the `Pixel_7_API` emulator, twice:
  - **against the live server** — fresh install, setup screen prefilled with
    `https://dealradar-k2hb.onrender.com`, tapped **Connect**, URL persisted to
    `shared_prefs`, and the WebView rendered the real sign-in page.
  - **against a local server** — the local run's log confirms the WebView
    requesting `GET /`, `/assets/styles.css`, `/assets/app.js`, `/api/auth/me`
    and `/api/auth/config`, all `200`.

The emulator threw repeated *system* ANRs ("Process system isn't responding",
"System UI isn't responding") throughout — that is the emulator's own
`system_server`/SystemUI under memory pressure on a loaded machine, not this
app; no `AndroidRuntime` exception from `com.dealradar.app` at any point. It
should behave better launched from Android Studio with less running alongside.
