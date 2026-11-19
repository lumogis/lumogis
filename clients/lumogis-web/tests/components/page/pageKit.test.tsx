// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// The page kit every redesigned page is built from (LUM-993): InfoTip, PageHeader,
// ScopeSwitch and EmptyState.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const userState: { role: "admin" | "user" | null } = { role: "admin" };
vi.mock("../../../src/auth/AuthProvider", () => ({
  useUser: () => (userState.role ? { id: "u1", email: "a@example.org", role: userState.role } : null),
}));

import { EmptyState } from "../../../src/components/page/EmptyState";
import { InfoTip } from "../../../src/components/page/InfoTip";
import { PageHeader } from "../../../src/components/page/PageHeader";
import { ScopeSwitch, type Scope } from "../../../src/components/page/ScopeSwitch";
import { help } from "../../../src/copy/help";

describe("InfoTip", () => {
  it("is labelled, opens on click and on Enter, and closes on Escape with focus back", async () => {
    const user = userEvent.setup();
    render(<InfoTip about="Profile">Your sign-in details.</InfoTip>);
    const trigger = screen.getByRole("button", { name: "About Profile" });

    await user.click(trigger);
    expect(await screen.findByText("Your sign-in details.")).toBeVisible();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByText("Your sign-in details.")).toBeNull());
    expect(trigger).toHaveFocus();

    trigger.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Your sign-in details.")).toBeVisible();
  });

  it("puts its text in a dialog-like popup a screen reader can reach", async () => {
    const user = userEvent.setup();
    render(<InfoTip about="Role">Admins can invite people.</InfoTip>);
    await user.click(screen.getByRole("button", { name: "About Role" }));
    const popup = await screen.findByRole("dialog");
    expect(popup).toHaveTextContent("Admins can invite people.");
  });
});

describe("PageHeader", () => {
  it("renders exactly one h1, the purpose line, the (i) and the action", () => {
    render(
      <PageHeader
        title="Profile"
        description={help.profile.page}
        info={help.profile.about}
        action={<button type="button">Save</button>}
      />,
    );
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Profile");
    expect(screen.getByText(help.profile.page)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "About Profile" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("renders nothing when hidden (embedded views)", () => {
    const { container } = render(<PageHeader title="Connectors" hidden />);
    expect(container).toBeEmptyDOMElement();
  });

  it("passes the heading ref and tab index through (SourcesView focus handoff)", () => {
    const ref = { current: null as HTMLHeadingElement | null };
    render(<PageHeader title="Sources" headingRef={ref} headingTabIndex={-1} />);
    expect(ref.current).toBe(screen.getByRole("heading", { level: 1 }));
    expect(ref.current).toHaveAttribute("tabindex", "-1");
  });
});

function ControlledScope({ onChange }: { onChange?: (s: Scope) => void }): JSX.Element {
  const [value, setValue] = useState<Scope>("mine");
  return (
    <>
      <ScopeSwitch
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange?.(next);
        }}
      />
      <output data-testid="scope">{value}</output>
    </>
  );
}

describe("ScopeSwitch", () => {
  beforeEach(() => {
    userState.role = "admin";
  });

  it("renders nothing for a member or while signed out", () => {
    userState.role = "user";
    const { container, rerender } = render(<ScopeSwitch value="mine" onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    userState.role = null;
    rerender(<ScopeSwitch value="mine" onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("switches between Mine and Household for an admin", async () => {
    const user = userEvent.setup();
    const changes: Scope[] = [];
    render(<ControlledScope onChange={(s) => changes.push(s)} />);
    expect(screen.getByRole("group", { name: "Whose to show" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mine" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Household" }));
    expect(screen.getByTestId("scope")).toHaveTextContent("household");
    expect(screen.getByRole("button", { name: "Household" })).toHaveAttribute("aria-pressed", "true");

    // Pressing the active item again must not leave the switch with nothing chosen.
    await user.click(screen.getByRole("button", { name: "Household" }));
    expect(screen.getByTestId("scope")).toHaveTextContent("household");
    expect(changes).toEqual(["household"]);
  });

  it("has its own (i)", () => {
    render(<ScopeSwitch value="mine" onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "About Mine and Household" })).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renders its title, helper text and actions (button, in-app link, outside link)", () => {
    const onClick = vi.fn();
    render(
      <MemoryRouter>
        <EmptyState
          title="No documents yet"
          helperText="Add one to start."
          actions={[
            { label: "Upload", onClick, primary: true },
            { label: "Open Capture", href: "/capture" },
            { label: "Read the guide", href: "https://example.org/guide" },
          ]}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "No documents yet" })).toBeInTheDocument();
    expect(screen.getByText("Add one to start.")).toBeInTheDocument();
    const upload = screen.getByRole("button", { name: "Upload" });
    expect(upload).toHaveAttribute("data-variant", "primary");
    fireEvent.click(upload);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Open Capture" })).toHaveAttribute("href", "/capture");
    const outside = screen.getByRole("link", { name: "Read the guide" });
    expect(outside).toHaveAttribute("target", "_blank");
    expect(outside).toHaveAttribute("rel", "noopener noreferrer");
  });
});
