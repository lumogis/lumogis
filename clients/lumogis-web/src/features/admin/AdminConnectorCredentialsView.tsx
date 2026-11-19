// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Connected services, Household view: the admin edits the sign-in details for one
// person, for the whole household, or for the whole system. Each tab renders the same
// MeConnectorsView against a different base path. Rebuilt on the shared components in
// LUM-993 plan B Pass 6.
import { useState } from "react";
import { useUser } from "../../auth/AuthProvider";
import { Alert, AlertDescription } from "../../components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { MeConnectorsView } from "../me/MeConnectorsView";
import { UserPicker } from "../_shared/UserPicker";

type Tab = "user" | "household" | "system";

const householdBase = "/api/v1/admin/connector-credentials/household";
const systemBase = "/api/v1/admin/connector-credentials/system";

export function AdminConnectorCredentialsView(): JSX.Element {
  const user = useUser();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("user");
  const [userId, setUserId] = useState("");

  const perUserPath =
    userId.length > 0 ? `/api/v1/admin/users/${encodeURIComponent(userId)}/connector-credentials` : null;

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="gap-4">
      <TabsList aria-label="Whose sign-in details">
        <TabsTrigger value="user">One person</TabsTrigger>
        <TabsTrigger value="household">Whole household</TabsTrigger>
        <TabsTrigger value="system">Whole system</TabsTrigger>
      </TabsList>
      <TabsContent value="user" className="flex flex-col gap-4">
        <UserPicker value={userId} onChange={setUserId} isAdmin={isAdmin} />
        {!userId && <p className="m-0 text-sm text-muted-foreground">Choose a person to see their connected services.</p>}
        {perUserPath && <MeConnectorsView basePath={perUserPath} />}
      </TabsContent>
      <TabsContent value="household" className="flex flex-col gap-4">
        <Alert>
          <AlertDescription>
            Household details apply to everyone in the household. Change them only when there is no value for the
            person or for the whole system.
          </AlertDescription>
        </Alert>
        <MeConnectorsView basePath={householdBase} />
      </TabsContent>
      <TabsContent value="system" className="flex flex-col gap-4">
        <Alert>
          <AlertDescription>
            System details apply to every person in Lumogis. Change them only when there is no value for the person or
            for the household.
          </AlertDescription>
        </Alert>
        <MeConnectorsView basePath={systemBase} />
      </TabsContent>
    </Tabs>
  );
}
