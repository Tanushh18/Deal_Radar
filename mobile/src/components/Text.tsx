import React, { createContext, useContext } from 'react';
import {
  StyleSheet,
  Text as RNText,
  TextInput as RNTextInput,
  type StyleProp,
  type TextInputProps,
  type TextProps,
  type TextStyle,
} from 'react-native';

import { MONO_FAMILY, fontFor, fontsReady } from '../theme/fonts';

/**
 * Drop-in replacements for React Native's Text/TextInput that pick the app
 * typeface. Custom fonts on Android ignore fontWeight (each weight is its own
 * file), so the weight is folded into the family name here.
 */

const InsideText = createContext(false);

function withFont(style: StyleProp<TextStyle>, nested: boolean): StyleProp<TextStyle> {
  if (!fontsReady()) return style;
  const flat = StyleSheet.flatten(style) ?? {};
  const mono = flat.fontFamily === MONO_FAMILY || flat.fontFamily === 'mono';
  if (flat.fontFamily && !mono) return style; // an explicit family wins
  // A nested span with no weight of its own inherits its parent's face.
  if (nested && !mono && flat.fontWeight == null) return style;
  return [style, { fontFamily: fontFor(flat.fontWeight, mono), fontWeight: 'normal' }];
}

export function Text({ style, children, ...rest }: TextProps & { ref?: React.Ref<RNText> }) {
  const nested = useContext(InsideText);
  return (
    <RNText {...rest} style={withFont(style, nested)}>
      {nested ? children : <InsideText.Provider value>{children}</InsideText.Provider>}
    </RNText>
  );
}

export function TextInput({ style, ...rest }: TextInputProps & { ref?: React.Ref<RNTextInput> }) {
  return <RNTextInput {...rest} style={withFont(style, false)} />;
}

// Instance types, so `useRef<TextInput>(null)` keeps working with the wrappers.
export type Text = RNText;
export type TextInput = RNTextInput;
