import React from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import type { Offer } from '../api';
import { MONO_FAMILY, makeStyles, useTheme } from '../theme';
import { displayTitle, money, timeAgo } from './format';
import { Icon } from './Icon';
import { haptic, openExternal } from './native';
import { StoreLogo } from './StoreLogo';
import { Text } from './Text';
import { SectionHead } from './ui';

/**
 * "More offers" under a search: sales, round-ups and text-only deals our
 * scanners found that aren't full deal cards. Tapping opens the store.
 */
export function OffersSection({ q, offers, flat }: { q: string; offers: Offer[]; flat: boolean }) {
  const s = useStyles();
  if (!offers.length) return null;
  return (
    <View style={{ paddingTop: 22 }}>
      <SectionHead title="More offers" sub={`Sales and offers matching “${q}” from the last few days`} />
      <View style={[s.list, flat && { marginHorizontal: -16 }]}>
        {offers.map((o) => (
          <OfferRow key={o.id} offer={o} />
        ))}
      </View>
    </View>
  );
}

function OfferRow({ offer }: { offer: Offer }) {
  const t = useTheme();
  const s = useStyles();
  const store = offer.store ? offer.store.toUpperCase() : '';
  const price = offer.price ? `${offer.price_from ? 'from ' : ''}${money(offer.price)}` : '';
  return (
    <Pressable
      onPress={() => {
        haptic.select();
        void openExternal(offer.url);
      }}
      accessibilityRole="link"
      accessibilityLabel={`${displayTitle(offer.title)}${price ? `, ${price}` : ''}${offer.store ? `, on ${offer.store}` : ''}`}
      style={({ pressed }) => [s.row, pressed && { backgroundColor: t.c.surface2 }]}
    >
      <View style={s.logo}>{offer.store ? <StoreLogo store={offer.store} size={30} /> : <Icon name="tag" size={16} color={t.c.text3} />}</View>
      <View style={{ flex: 1, gap: 3 }}>
        <Text numberOfLines={2} style={s.title}>
          {displayTitle(offer.title)}
        </Text>
        <View style={s.metaRow}>
          {price ? <Text style={s.price}>{price}</Text> : null}
          <Text numberOfLines={1} style={s.meta}>
            {[store, offer.marketplace ? 'SEARCH THERE' : timeAgo(offer.posted_at).toUpperCase()].filter(Boolean).join(' · ')}
          </Text>
        </View>
      </View>
      <Icon name="external" size={15} color={t.c.text3} />
    </Pressable>
  );
}

const useStyles = makeStyles((t) => ({
  list: { borderTopWidth: StyleSheet.hairlineWidth, borderColor: t.c.borderStrong, marginTop: 8 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: t.c.borderStrong,
  },
  logo: { width: 30, height: 30, alignItems: 'center', justifyContent: 'center' },
  title: { color: t.c.text, fontSize: 14, fontWeight: '500', lineHeight: 19 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  price: { color: t.c.text, fontSize: 13, fontWeight: '700' },
  meta: { flexShrink: 1, color: t.c.text3, fontSize: 10.5, fontFamily: MONO_FAMILY, letterSpacing: 0.3 },
}));
