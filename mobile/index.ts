import { registerRootComponent } from 'expo';

// Must be imported before anything renders: defines the background polling
// task at module scope so a headless (background) JS start can find it.
import './src/native/backgroundTask';

import App from './App';

// registerRootComponent calls AppRegistry.registerComponent('main', () => App);
// It also ensures that whether you load the app in Expo Go or in a native build,
// the environment is set up appropriately
registerRootComponent(App);
