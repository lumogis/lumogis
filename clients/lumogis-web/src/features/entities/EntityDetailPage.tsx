// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Entity detail route contract for LUM-161 (LUM-160).

import { Link, useParams } from "react-router-dom";

import { useAuth } from "../../auth/AuthProvider";
import { EntityCardPanel } from "../memory/EntityCard";

export function EntityDetailPage(): JSX.Element {
  const { entityId } = useParams<{ entityId: string }>();
  const { client } = useAuth();

  if (!entityId) {
    return <p role="alert">Missing entity id.</p>;
  }

  return (
    <section className="lumogis-entity-detail" data-testid="entity-detail-page">
      <p>
        <Link to="/search">← Search</Link>
      </p>
      <EntityCardPanel entityId={entityId} client={client} />
    </section>
  );
}
