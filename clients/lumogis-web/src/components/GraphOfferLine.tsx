// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-989 — one dismissible line in Admin → System status offering the knowledge
// graph. Admins only (the System status page is admin-only, so members never
// see it), and only when the server says the graph can be turned on, it is off,
// and the memory check does not say the machine is short. Dismissal is remembered
// on this device. It shares the Knowledge graph page's query (same key), so a
// System status load costs at most one status call.

import { Button } from "./ui/button";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { fetchKnowledgeGraphStatus, KNOWLEDGE_GRAPH_QUERY_KEY } from "../api/knowledgeGraph";
import { useAuth } from "../auth/AuthProvider";
import {
  GRAPH_OFFER_DISMISS_KEY,
  OFFER_LINE_TEXT,
} from "../features/admin/knowledgeGraphCopy";

const FIVE_MINUTES_MS = 5 * 60 * 1000;

function dismissed(): boolean {
  try {
    return localStorage.getItem(GRAPH_OFFER_DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function GraphOfferLine(): JSX.Element | null {
  const { client, status: authStatus } = useAuth();
  const [hidden, setHidden] = useState<boolean>(dismissed);
  const q = useQuery({
    // Same key as AdminKnowledgeGraphView: one cached answer for both.
    queryKey: KNOWLEDGE_GRAPH_QUERY_KEY,
    queryFn: () => fetchKnowledgeGraphStatus(client),
    staleTime: FIVE_MINUTES_MS,
    enabled: authStatus === "authenticated" && !hidden,
    retry: false,
  });

  const data = q.data;
  if (hidden || !data) return null;
  if (data.state !== "off" || !data.turn_on.available || data.memory.enough === false) return null;

  return (
    <p data-testid="graph-offer-line" className="m-0 max-w-2xl text-sm">
      {OFFER_LINE_TEXT} <Link to="/settings/knowledge-graph" className="text-accent-text underline underline-offset-2">Open Knowledge graph</Link>{" "}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => {
          try {
            localStorage.setItem(GRAPH_OFFER_DISMISS_KEY, "1");
          } catch {
            /* storage blocked: hide for this page view only */
          }
          setHidden(true);
        }}
      >
        Dismiss
      </Button>
    </p>
  );
}
