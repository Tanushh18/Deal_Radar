import React from 'react';

import { ThemeProvider } from '../theme';
import { DealActionsProvider } from './DealActions';
import { DealsFilterProvider } from './DealsFilterContext';
import { SavedProvider } from './Saved';
import { ToastProvider } from './Toast';

/** Everything the screens need above the navigator: theme, shared deal filters, toasts, saved deals. */
export function AppProviders({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <DealsFilterProvider>
        <ToastProvider>
          <SavedProvider>
            <DealActionsProvider>{children}</DealActionsProvider>
          </SavedProvider>
        </ToastProvider>
      </DealsFilterProvider>
    </ThemeProvider>
  );
}
