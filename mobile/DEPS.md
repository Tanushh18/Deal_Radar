# Installed dependencies (Expo SDK 57, RN 0.86, React 19.2, New Architecture)

Only the infra agent runs `npx expo install`. Ask for anything missing.

| Package | Version |
|---|---|
| expo | ~57.0.24 |
| react / react-native | 19.2.3 / 0.86.3 |
| @react-navigation/native | ^7.4.1 |
| @react-navigation/native-stack | ^7.19.2 |
| @react-navigation/bottom-tabs | ^7.19.2 |
| react-native-screens | ~4.26.0 |
| react-native-safe-area-context | ~5.7.0 |
| react-native-gesture-handler | ~2.32.0 |
| react-native-reanimated / react-native-worklets | 4.5.1 / 0.10.1 (babel-preset-expo wires the plugin; no babel.config.js needed) |
| @react-native-async-storage/async-storage | 2.2.0 |
| expo-image | ~57.0.5 |
| expo-linear-gradient | ~57.0.2 |
| react-native-svg | 15.15.4 |
| expo-haptics | ~57.0.3 |
| expo-web-browser | ~57.0.3 |
| expo-clipboard | ~57.0.2 |
| react-native-webview | 13.16.1 |
| expo-notifications / expo-background-task / expo-task-manager | ~57.0.20 / ~57.0.19 / ~57.0.19 |
| expo-blur | ~57.0.3 (`BlurView` for glass; on Android pass `experimentalBlurMethod="dimezisBlurView"` for real blur, else it is a tinted view) |
| @react-native-community/netinfo | 12.0.1 (`useNetInfo()` / `NetInfo.addEventListener` for offline detection) |
| expo-linking | ~57.0.10 |
| expo-updates | ~57.0.23 (infra only; OTA via `eas update --branch preview`) |
| @notifee/react-native + @evennit/notifee-expo-plugin | ^9.1.8 / ^3.8.0 (infra only; rich notifications) |
| expo-share-intent | ^8.0.1 (infra only; share-to-DealRadar → `CheckPrice {url}`) |
| expo-quick-actions | ^6.0.2 (infra only; launcher shortcuts) |
| expo-constants / expo-device / expo-status-bar / expo-system-ui / expo-navigation-bar / expo-splash-screen / expo-build-properties | 57.x |

## Contracts provided by the infra side

- `src/native/session.ts`: `getServerUrl(): Promise<string>`, `enableNotifications(): Promise<boolean>`
  (for a Settings "notifications" toggle), legacy `onSignedIn(user)`, `onSignedOut()`.
- `src/native/device.ts`: `getDeviceId(): Promise<string>` (`app_<uuid>`, AsyncStorage `dr-device-id`),
  `registerDevice({digest?, digest_hour?})` to change digest prefs (returns the server `device` or null).
- `src/native/config.ts`: `COLORS`, `joinUrl(base, path)`, `isOwnHost(base, url)`, `normalize(url)`.
- `src/navigation/types.ts`: `RootStackParamList`, `MainTabParamList`, and the `navigationRef`.
- Root stack routes: `Setup`, `Login`, `Main` (tabs: `Deals`, `Alerts`, `Channels`, `Account`),
  `DealDetail {id: string}`, `Filters` (modal), `Search`, `Settings`, `Website {path?: string}`,
  `CheckPrice {url?: string}` (share-to-DealRadar + shortcut; export `CheckPriceScreen` from `src/screens/index.ts`),
  `Saved` (launcher shortcut; export `SavedScreen`). Until exported, `src/navigation/screens.ts` uses placeholders.

## Root/navigation notes (infra)

- `App.tsx` wraps everything in `ThemeProvider` from `src/theme` (so `useTheme()` works everywhere,
  including Setup/boot screens), then `GestureHandlerRootView` + `SafeAreaProvider` + `NavigationContainer`
  (nav theme derived from `useTheme()`). `app.json` `userInterfaceStyle` is `automatic` so light mode works.
- Root stack `headerShown: false` by default; `DealDetail`, `Filters`, `Settings`, `Website` get a themed
  native header (override with `navigation.setOptions`). Tabs have no header; screens handle top safe area.
- `src/navigation/screens.ts` is the single switch between placeholders and `src/screens/index.ts`
  (named exports `DealsScreen`, `AlertsScreen`, … `WebsiteScreen`).
- Notification tap → push `DealDetail {id}` on top of `Main`; no deal id → `Website {path}`. Share intent → `CheckPrice {url}`.
- Call `onSignedOut()` before `POST /api/auth/logout` (it unregisters the push token with the cookie).
