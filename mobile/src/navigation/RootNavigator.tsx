import { isPublicMode } from '../native/session';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import Svg, { Circle, Path } from 'react-native-svg';

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
        tabBarStyle: { backgroundColor: c.bg, borderTopColor: c.border },
        sceneStyle: { backgroundColor: c.bg },
      }}
    >
      <Tabs.Screen name="Deals" component={S.DealsScreen} options={{ tabBarIcon: DealsIcon }} />
      {/* Alerts and channel picking need a Telegram account; public-mode guests browse only. */}
      {!isPublicMode() && <Tabs.Screen name="Alerts" component={S.AlertsScreen} options={{ tabBarIcon: AlertsIcon }} />}
      {!isPublicMode() && <Tabs.Screen name="Channels" component={S.ChannelsScreen} options={{ tabBarIcon: ChannelsIcon }} />}
      <Tabs.Screen name="Account" component={S.AccountScreen} options={{ tabBarIcon: AccountIcon }} />
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
    </Stack.Navigator>
  );
}
