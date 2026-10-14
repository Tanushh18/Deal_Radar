import React from 'react';
import { Pressable, Text, View } from 'react-native';

import { COLORS } from '../native/config';

type State = { failed: boolean };

/**
 * Last line of defence: a render error anywhere below shows a "try again"
 * screen instead of closing the app. Retry remounts the tree from scratch.
 */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.warn('[app] render error:', (error as any)?.message ?? error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <View style={{ flex: 1, backgroundColor: COLORS.bg, alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <Text style={{ color: COLORS.text, fontSize: 18, fontWeight: '600', marginBottom: 8 }}>Something went wrong</Text>
        <Text style={{ color: COLORS.text2, textAlign: 'center', marginBottom: 20 }}>
          This screen hit a problem. Your saved deals are safe.
        </Text>
        <Pressable
          onPress={() => this.setState({ failed: false })}
          style={{ backgroundColor: COLORS.accent, paddingHorizontal: 24, paddingVertical: 12, borderRadius: 10 }}
        >
          <Text style={{ color: COLORS.accentText, fontWeight: '600' }}>Try again</Text>
        </Pressable>
      </View>
    );
  }
}
