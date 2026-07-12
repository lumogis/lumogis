// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis

import { ThemeToggle } from "../../components/ThemeToggle";

export function MeAppearanceView(): JSX.Element {
  return (
    <section className="lumogis-appearance">
      <h2>Appearance</h2>
      <p className="lumogis-appearance__lead">
        Choose how Lumogis looks on this device. System follows your operating system preference.
      </p>
      <dl className="lumogis-kv-list">
        <div className="lumogis-kv-row lumogis-kv-row--stack">
          <dt className="lumogis-kv-row__label">Theme</dt>
          <dd className="lumogis-kv-row__value">
            <ThemeToggle variant="segment" />
          </dd>
        </div>
      </dl>
    </section>
  );
}
