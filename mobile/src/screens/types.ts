import type { BottomTabNavigationProp } from '@react-navigation/bottom-tabs';
import type { CompositeNavigationProp, RouteProp } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

import type { MainTabParamList, RootStackParamList } from '../navigation/types';

export type { MainTabParamList, RootStackParamList };

export type RootNav = NativeStackNavigationProp<RootStackParamList>;

export type TabNav<T extends keyof MainTabParamList> = CompositeNavigationProp<
  BottomTabNavigationProp<MainTabParamList, T>,
  NativeStackNavigationProp<RootStackParamList>
>;

export type RootRoute<T extends keyof RootStackParamList> = RouteProp<RootStackParamList, T>;
