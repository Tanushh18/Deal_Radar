import React from 'react';

import { ThemeProvider } from '../theme';
import { DealsFilterProvider } from './DealsFilterContext';
import { ToastProvider } from './Toast';

/** Everything the screens need above the navigator: theme, shared deal filters, toasts. */
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <DealsFilterProvider>
        <ToastProvider>{children}</ToastProvider>
      </DealsFilterProvider>
    </ThemeProvider>
  );
}
