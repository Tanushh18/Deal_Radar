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
| expo-constants / expo-device / expo-status-bar / expo-system-ui / expo-navigation-bar / expo-splash-screen / expo-build-properties | 57.x |

## Contracts provided by the infra side

- `src/native/session.ts`: `getServerUrl(): Promise<string>`, `onSignedIn(user)`, `onSignedOut()`.
- `src/native/config.ts`: `COLORS`, `joinUrl(base, path)`, `isOwnHost(base, url)`, `normalize(url)`.
- `src/navigation/types.ts`: `RootStackParamList`, `MainTabParamList`, and the `navigationRef`.
- Root stack routes: `Setup`, `Login`, `Main` (tabs: `Deals`, `Alerts`, `Channels`, `Account`),
  `DealDetail {id: string}`, `Filters` (modal), `Search`, `Settings`, `Website {path?: string}`.

## Root/navigation notes (infra)

- `App.tsx` wraps everything in `ThemeProvider` from `src/theme` (so `useTheme()` works everywhere,
  including Setup/boot screens), then `GestureHandlerRootView` + `SafeAreaProvider` + `NavigationContainer`
  (nav theme derived from `useTheme()`). `app.json` `userInterfaceStyle` is `automatic` so light mode works.
- Root stack `headerShown: false` by default; `DealDetail`, `Filters`, `Settings`, `Website` get a themed
  native header (override with `navigation.setOptions`). Tabs have no header; screens handle top safe area.
- `src/navigation/screens.ts` is the single switch between placeholders and `src/screens/index.ts`
  (named exports `DealsScreen`, `AlertsScreen`, … `WebsiteScreen`).
- Notification tap → `reset([Main, DealDetail {id}])`; no deal id → `Website {path}`.
- Call `onSignedOut()` before `POST /api/auth/logout` (it unregisters the push token with the cookie).
