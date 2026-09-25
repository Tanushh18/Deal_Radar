const { withAppBuildGradle } = require('@expo/config-plugins');

/**
 * Preview (sideloaded) APKs only ever run on the dev's own phone, so there's
 * no reason to ship all 4 native ABIs in one "universal" APK — that's what
 * blows a ~25-30MB app up to 100MB+. Restrict preview builds to arm64-v8a,
 * which covers virtually every Android device since ~2017. Production stays
 * untouched: it ships as an app-bundle (.aab) and Play Store already delivers
 * each device only its matching architecture.
 */
module.exports = function withAbiFilter(config) {
  if (process.env.EAS_BUILD_PROFILE !== 'preview') return config;

  return withAppBuildGradle(config, (config) => {
    if (config.modResults.contents.includes('abiFilters')) return config;
    config.modResults.contents = config.modResults.contents.replace(
      /defaultConfig\s*{/,
      `defaultConfig {\n        ndk {\n            abiFilters "arm64-v8a"\n        }`
    );
    return config;
  });
};
