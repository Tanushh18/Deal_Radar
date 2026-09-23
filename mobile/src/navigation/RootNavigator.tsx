import { isPublicMode } from '../native/session';
import { createBottomTabNavigator, useBottomTabBarHeight } from '@react-navigation/bottom-tabs';
import { type ComponentType } from 'react';
import { View } from 'react-native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import Svg, { Circle, Path } from 'react-native-svg';

import { GlassTabBarBackground } from '../components';
import { useTheme } from '../theme';
import * as S from './screens';
import SetupScreen from './SetupScreen';
import type { MainTabParamList, RootStackParamList } from './types';

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tabs = createBottomTabNavigator<MainTabParamList>();

type IconProps = { color: string; size: number };
const stroke = (color: string) => ({
  stroke: color,
  strokeWidth: 2,
  fill: 'none',
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
});

function DealsIcon({ color, size }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z" {...stroke(color)} />
      <Circle cx={7.5} cy={7.5} r={1.5} fill={color} />
    </Svg>
  );
}
function AlertsIcon({ color, size }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0" {...stroke(color)} />
    </Svg>
  );
}
function ChannelsIcon({ color, size }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Circle cx={12} cy={12} r={9} {...stroke(color)} />
      <Circle cx={12} cy={12} r={5} {...stroke(color)} />
      <Circle cx={12} cy={12} r={1.5} fill={color} />
    </Svg>
  );
}
function SavedIcon({ color, size }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" {...stroke(color)} />
    </Svg>
  );
}

// The glass tab bar floats (position: absolute); keep each tab's content clear of it.
function aboveTabBar<P extends object>(Screen: ComponentType<P>): ComponentType<P> {
  function Inset(props: P) {
    const h = useBottomTabBarHeight();
    return (
      <View style={{ flex: 1, paddingBottom: h }}>
        <Screen {...props} />
      </View>
    );
  }
  Inset.displayName = `AboveTabBar(${Screen.displayName ?? Screen.name ?? 'Screen'})`;
  return Inset;
}

const DealsTab = aboveTabBar(S.DealsScreen);
const SavedTab = aboveTabBar(S.SavedScreen);
const AlertsTab = aboveTabBar(S.AlertsScreen);
const ChannelsTab = aboveTabBar(S.ChannelsScreen);
const AccountTab = aboveTabBar(S.AccountScreen);

function AccountIcon({ color, size }: IconProps) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Circle cx={12} cy={8} r={4} {...stroke(color)} />
      <Path d="M4 21a8 8 0 0 1 16 0" {...stroke(color)} />
    </Svg>
  );
}

function MainTabs() {
  const { c } = useTheme();
  return (
    <Tabs.Navigator
      backBehavior="firstRoute"
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: c.accent,
        tabBarInactiveTintColor: c.text3,
        tabBarStyle: { position: 'absolute', backgroundColor: 'transparent', borderTopWidth: 0, elevation: 0 },
        tabBarBackground: () => <GlassTabBarBackground />,
        sceneStyle: { backgroundColor: c.bg },
      }}
    >
      <Tabs.Screen name="Deals" component={DealsTab} options={{ tabBarIcon: DealsIcon }} />
      <Tabs.Screen name="Saved" component={SavedTab} options={{ tabBarIcon: SavedIcon }} />
      {/* Alerts and channel picking need a Telegram account; public-mode guests browse only. */}
      {!isPublicMode() && <Tabs.Screen name="Alerts" component={AlertsTab} options={{ tabBarIcon: AlertsIcon }} />}
      {!isPublicMode() && <Tabs.Screen name="Channels" component={ChannelsTab} options={{ tabBarIcon: ChannelsIcon }} />}
      <Tabs.Screen name="Account" component={AccountTab} options={{ tabBarIcon: AccountIcon }} />
    </Tabs.Navigator>
  );
}

export type RootNavigatorProps = {
  initialRouteName: keyof RootStackParamList;
  onServerSaved: (url: string) => void;
};

export default function RootNavigator({ initialRouteName, onServerSaved }: RootNavigatorProps) {
  const { c } = useTheme();
  return (
    <Stack.Navigator
      initialRouteName={initialRouteName}
      screenOptions={{
        headerShown: false,
        headerStyle: { backgroundColor: c.bg },
        headerTintColor: c.text,
        headerTitleStyle: { color: c.text },
        headerShadowVisible: false,
        contentStyle: { backgroundColor: c.bg },
        animation: 'slide_from_right',
      }}
    >
      <Stack.Screen name="Setup">
        {(props) => <SetupScreen {...props} onSaved={onServerSaved} />}
      </Stack.Screen>
      <Stack.Screen name="Login" component={S.LoginScreen} />
      <Stack.Screen name="Main" component={MainTabs} />
      <Stack.Screen name="DealDetail" component={S.DealDetailScreen} options={{ title: 'Deal' }} />
      <Stack.Screen
        name="Filters"
        component={S.FiltersScreen}
        options={{ presentation: 'modal', title: 'Filters', animation: 'slide_from_bottom' }}
      />
      <Stack.Screen name="Search" component={S.SearchScreen} options={{ animation: 'fade' }} />
      <Stack.Screen name="Settings" component={S.SettingsScreen} options={{ title: 'Settings' }} />
      <Stack.Screen name="Website" component={S.WebsiteScreen} options={{ title: 'DealRadar web' }} />
      <Stack.Screen name="CheckPrice" component={S.CheckPriceScreen} />
    </Stack.Navigator>
  );
}
