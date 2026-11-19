// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { PageSection, Row } from "../../components/page/PageSection";
import { ThemeToggle } from "../../components/ThemeToggle";

export function MeAppearanceView(): JSX.Element {
  return (
    <PageSection
      title="Colour theme"
      description="Choose how Lumogis looks on this device. System follows your operating system preference."
    >
      <dl className="m-0">
        <Row label="Theme">
          {/* The pill toggle (ToggleGroup via SegmentedControl) stays as it was. */}
          <ThemeToggle variant="segment" />
        </Row>
      </dl>
    </PageSection>
  );
}
