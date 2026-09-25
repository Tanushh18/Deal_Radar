import { useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { api } from '../api';
import type { SaleEvent } from '../api/types';
import { useTheme } from '../theme';
import { Txt } from './ui';

/**
 * Upcoming store sales (Big Billion Days, Great Indian Festival…), fetched
 * once on mount. Renders nothing while loading or if there are none —
 * never an empty strip taking up space.
 */
export function SaleEventsRail() {
  const t = useTheme();
  const [events, setEvents] = useState<SaleEvent[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.saleEvents
      .list()
      .then((r) => !cancelled && setEvents(r.events))
      .catch(() => !cancelled && setEvents([]));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!events || !events.length) return null;

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
      {events.map((e) => (
        <SaleCard key={e.id} event={e} />
      ))}
    </ScrollView>
  );
}

function countdown(startsAt: number | null): string {
  if (!startsAt) return '';
  const days = Math.round((startsAt * 1000 - Date.now()) / 86_400_000);
  if (days <= 0) return 'Live now';
  if (days === 1) return 'Tomorrow';
  return `In ${days}d`;
}

function fmtDate(ts: number | null): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function SaleCard({ event }: { event: SaleEvent }) {
  const t = useTheme();
  return (
    <View
      style={{
        width: 220,
        padding: 14,
        borderRadius: t.r.md,
        backgroundColor: t.c.accentSoft,
        borderWidth: 1,
        borderColor: t.c.accentLine,
        gap: 4,
      }}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
        <Txt variant="label" color={t.c.accent}>
          {event.store || 'Sale'}
        </Txt>
        <Txt variant="fine" weight="700" color={t.c.text2}>
          {countdown(event.starts_at)}
        </Txt>
      </View>
      <Txt variant="h3" numberOfLines={2}>
        {event.name}
      </Txt>
      {event.hype ? (
        <Txt variant="small" numberOfLines={3}>
          {event.hype}
        </Txt>
      ) : null}
      <Txt variant="fine">
        {event.approximate ? 'Approx. ' : ''}
        {fmtDate(event.starts_at)}
        {event.ends_at ? `–${fmtDate(event.ends_at)}` : ''}
      </Txt>
    </View>
  );
}
