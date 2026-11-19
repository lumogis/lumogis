// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// AlertDialogAction ignores a second click within half a second (LUM-993 plan B
// review): a double-click must not run a delete or dismiss twice, while a dialog
// left open after a failure can still be confirmed again.

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlertDialog, AlertDialogAction, AlertDialogContent, AlertDialogTitle } from "../../../src/components/ui/alert-dialog";

afterEach(() => vi.useRealTimers());

describe("AlertDialogAction", () => {
  it("runs once on a double-click, and again after half a second", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const run = vi.fn();
    render(
      <AlertDialog open>
        <AlertDialogContent>
          <AlertDialogTitle>Delete it?</AlertDialogTitle>
          <AlertDialogAction onClick={run}>Delete</AlertDialogAction>
        </AlertDialogContent>
      </AlertDialog>,
    );
    const button = screen.getByRole("button", { name: "Delete" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(run).toHaveBeenCalledTimes(1);
    vi.setSystemTime(Date.now() + 600);
    fireEvent.click(button);
    expect(run).toHaveBeenCalledTimes(2);
  });
});
