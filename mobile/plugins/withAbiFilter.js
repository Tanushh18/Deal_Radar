const { withAppBuildGradle, withGradleProperties } = require('@expo/config-plugins');

/**
 * Preview (sideloaded) APKs only ever run on the dev's own phone, so there's
 * no reason to ship all 4 native ABIs in one "universal" APK — that's what
 * blows a ~25-30MB app up to 100MB+. Restrict preview builds to arm64-v8a,
 * which covers virtually every Android device since ~2017. Production stays
 * untouched: it ships as an app-bundle (.aab) and Play Store already delivers
 * each device only its matching architecture.
 *
 * Two separate knobs have to agree for this to actually shrink the APK:
 * - abiFilters (below) restricts only this app module's own native code.
 * - reactNativeArchitectures (gradle.properties) restricts Hermes + RN core
 *   + every autolinked native module (reanimated, svg, notifee, webview...),
 *   which is where most of the size actually lives. Setting only the first
 *   one barely moves the number.
 */
module.exports = function withAbiFilter(config) {
  if (process.env.EAS_BUILD_PROFILE !== 'preview') return config;

  config = withAppBuildGradle(config, (config) => {
    if (config.modResults.contents.includes('abiFilters')) return config;
    config.modResults.contents = config.modResults.contents.replace(
      /defaultConfig\s*{/,
      `defaultConfig {\n        ndk {\n            abiFilters "arm64-v8a"\n        }`
    );
    return config;
  });

  return withGradleProperties(config, (config) => {
    const key = 'reactNativeArchitectures';
    const filtered = config.modResults.filter((item) => !(item.type === 'property' && item.key === key));
    filtered.push({ type: 'property', key, value: 'arm64-v8a' });
    config.modResults = filtered;
    return config;
  });
};
