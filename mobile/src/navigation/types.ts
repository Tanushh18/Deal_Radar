import { createNavigationContainerRef, type NavigatorScreenParams } from '@react-navigation/native';

export type MainTabParamList = {
  Deals: undefined;
  Alerts: undefined;
  Channels: undefined;
  Account: undefined;
};

export type RootStackParamList = {
  Setup: { fromSettings?: boolean } | undefined;
  Login: undefined;
  Main: NavigatorScreenParams<MainTabParamList> | undefined;
  DealDetail: { id: string };
  Filters: undefined;
  Search: undefined;
  Settings: undefined;
  Website: { path?: string } | undefined;
};

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace ReactNavigation {
    interface RootParamList extends RootStackParamList {}
  }
}
