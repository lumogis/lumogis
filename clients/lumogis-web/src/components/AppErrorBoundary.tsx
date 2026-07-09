// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// App-level React error boundary (LUM-211). Catches render-time crashes so the
// user gets a friendly, actionable fallback instead of a white screen. Network
// / query errors are handled per-surface with ErrorState; this is the
// last-resort net for unexpected render errors.

import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";

import { ErrorState } from "../features/_shared/ErrorState";

export interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  error: Error | null;
  // Bumped on reset so the recovered subtree remounts under a fresh key,
  // clearing any child state that contributed to the crash.
  resetCount: number;
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null, resetCount: 0 };

  static getDerivedStateFromError(error: Error): Partial<AppErrorBoundaryState> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface in the console for diagnostics; never render the raw error to the user.
    // (console.error is permitted by the no-console rule config — no disable needed.)
    console.error("AppErrorBoundary caught a render error:", error, info.componentStack);
  }

  // "Try again" clears the error and remounts the children fresh — recovers
  // from transient crashes. A deterministic error simply re-throws and we show
  // the fallback again (not an infinite loop); "Reload" is the hard reset.
  private handleReset = (): void => {
    this.setState((s) => ({ error: null, resetCount: s.resetCount + 1 }));
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <ErrorState
          title="Something went wrong"
          message="This page hit an unexpected error. Your data is safe."
          onRetry={this.handleReset}
          retryLabel="Try again"
          actions={[{ label: "Reload", onClick: () => window.location.reload() }]}
        />
      );
    }
    return <Fragment key={this.state.resetCount}>{this.props.children}</Fragment>;
  }
}
