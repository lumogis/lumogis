// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ChevronDown } from "lucide-react";

import { useAuth } from "../../auth/AuthProvider";
import { PageSection } from "../../components/page/PageSection";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../../components/ui/collapsible";
import { parseFilenameFromContentDisposition } from "../../util/contentDisposition";

interface SectionSummary {
  name: string;
  kind: string;
  row_count: number;
}

export function MeExportView(): JSX.Element {
  const { client } = useAuth();
  const [status, setStatus] = useState<string | null>(null);

  const inv = useQuery({
    queryKey: ["me", "data-inventory"],
    queryFn: () => client.getJson<SectionSummary[]>("/api/v1/me/data-inventory"),
  });

  async function doExport(): Promise<void> {
    setStatus(null);
    let objectUrl: string | null = null;
    try {
      const res = await client.fetchOnce("/api/v1/me/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (res.status === 413) {
        setStatus("Archive too large; contact your admin.");
        return;
      }
      if (res.status === 401) {
        setStatus("Session expired; please refresh and try again.");
        return;
      }
      if (!res.ok) {
        setStatus(`Export failed (${res.status})`);
        return;
      }
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const cd = res.headers.get("Content-Disposition");
      const name = parseFilenameFromContentDisposition(cd) ?? "export.zip";
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = name;
      a.click();
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      setStatus("Download started.");
    } catch {
      setStatus("Session expired; please refresh and try again.");
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    }
  }

  return (
    <PageSection
      title="Download my data"
      description="Download a ZIP file with a copy of what Lumogis holds for you: your notes, documents, mail summaries, settings and more."
    >
      <div className="flex flex-col gap-3">
        <div>
          <Button type="button" onClick={() => void doExport()}>
            Download ZIP
          </Button>
        </div>
        {status ? (
          <Alert role="status">
            <AlertDescription>{status}</AlertDescription>
          </Alert>
        ) : null}
        {inv.isSuccess ? (
          <Collapsible>
            <CollapsibleTrigger
              render={<Button type="button" variant="ghost" size="sm" className="-ml-2 group/trigger" />}
            >
              <ChevronDown
                aria-hidden="true"
                className="transition-transform group-aria-expanded/trigger:rotate-180"
              />
              What&apos;s included
            </CollapsibleTrigger>
            <CollapsibleContent keepMounted className="pt-2">
              <ul role="list" className="m-0 list-none p-0 text-sm">
                {inv.data.map((s) => (
                  <li key={s.name}>
                    {s.name} ({s.kind}): {s.row_count}
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
        ) : null}
      </div>
    </PageSection>
  );
}
