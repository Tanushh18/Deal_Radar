/**
 * Loads one page in an invisible in-app browser and hands back its HTML.
 *
 * Used when a plain request for a BuyHatke page is challenged by its
 * Cloudflare check: a real browser engine passes that check the same way it
 * does when the user taps "Price history & stock". Waits through the
 * challenge's own reload(s), gives up after TIMEOUT_MS.
 */
import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';

const TIMEOUT_MS = 25_000;

// Runs after every page load in the hidden browser, including the one the
// challenge redirects to once it passes.
const SEND_HTML = `
(function () {
  function send() {
    try { window.ReactNativeWebView.postMessage(JSON.stringify({ url: location.href, html: document.documentElement.outerHTML })); } catch (e) {}
  }
  if (document.readyState === 'complete') send(); else window.addEventListener('load', send);
  setTimeout(send, 4000);
})();
true;`;

type Props = {
  url: string;
  /** Return true once the page is the one you wanted; false to keep waiting. */
  onPage: (html: string, pageUrl: string) => boolean;
  onGiveUp: () => void;
};

export function HiddenPageReader({ url, onPage, onGiveUp }: Props) {
  const done = useRef(false);
  const onPageRef = useRef(onPage);
  const onGiveUpRef = useRef(onGiveUp);
  onPageRef.current = onPage;
  onGiveUpRef.current = onGiveUp;

  useEffect(() => {
    const timer = setTimeout(() => {
      if (done.current) return;
      done.current = true;
      onGiveUpRef.current();
    }, TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [url]);

  const onMessage = (e: WebViewMessageEvent) => {
    if (done.current) return;
    try {
      const { url: pageUrl, html } = JSON.parse(e.nativeEvent.data) as { url: string; html: string };
      if (onPageRef.current(html, pageUrl)) done.current = true;
    } catch {
      /* not our message */
    }
  };

  return (
    <View pointerEvents="none" style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden' }}>
      <WebView
        source={{ uri: url }}
        injectedJavaScript={SEND_HTML}
        onMessage={onMessage}
        onError={() => {
          if (done.current) return;
          done.current = true;
          onGiveUpRef.current();
        }}
        javaScriptEnabled
        domStorageEnabled
        // Only BuyHatke pages (plus Cloudflare's check itself); never follow it anywhere else.
        originWhitelist={['https://buyhatke.com', 'https://www.buyhatke.com', 'https://challenges.cloudflare.com']}
        onShouldStartLoadWithRequest={(req) =>
          /^https:\/\/((www\.)?buyhatke\.com|challenges\.cloudflare\.com)\//.test(req.url) || req.url === 'about:blank'
        }
        setSupportMultipleWindows={false}
        mediaPlaybackRequiresUserAction
        style={{ width: 1, height: 1 }}
      />
    </View>
  );
}
