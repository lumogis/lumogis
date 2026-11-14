# Connecting Outlook.com with OAuth (operator setup)

Microsoft retired app passwords for personal Outlook.com / Hotmail / Live IMAP,
so those mailboxes now connect with a one-time **OAuth sign-in** instead of a
stored password. Lumogis uses the [OAuth 2.0 Device Authorization Grant][rfc8628]:
the household member is shown a short code, enters it on Microsoft's site in any
browser, and Lumogis receives a refresh token — no password ever touches Lumogis.

This is a **bring-your-own-app** model: you (the operator) register your own free
Microsoft OAuth application once, and every household member connects through it.
Nothing is registered with Lumogis or any third party.

> **Personal accounts only (v1).** The app is registered against Microsoft's
> `consumers` tenant, so it connects Outlook.com / Hotmail / Live mailboxes.
> Organisational Microsoft 365 (work/school) accounts are a separate registration
> and are not covered here.

> **Gmail is not offered in v1.** Google's device flow cannot grant the IMAP
> scope and requires a client secret, so it doesn't fit the device-flow
> public-client model. Setting `LUMOGIS_OAUTH_GOOGLE_CLIENT_ID` has no effect.

---

## 1. Register a Microsoft OAuth app

1. Sign in to the [Azure Portal → App registrations][appreg] with any Microsoft
   account (a free personal account works).
2. **New registration**:
   - **Name**: anything, e.g. `Lumogis (my household)`.
   - **Supported account types**: choose
     **Personal Microsoft accounts only**.
   - **Redirect URI**: leave empty — the device flow uses no redirect.
   - Click **Register**.
3. On the app's **Overview**, copy the **Application (client) ID** — this is the
   value you'll set as `LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID`.

## 2. Turn on the public-client device flow

1. In the app, open **Authentication**.
2. Under **Advanced settings → Allow public client flows**, set
   **"Enable the following mobile and desktop flows"** to **Yes**, then **Save**.

   The device flow is a *public client* — it authenticates with only the client
   id (no secret). This toggle is what permits it.

## 3. Add the IMAP + SMTP permissions

1. Open **API permissions → Add a permission → APIs my organization uses**, and
   search for **Office 365 Exchange Online** (or add the delegated permissions
   directly). Add these **delegated** scopes:
   - `IMAP.AccessAsUser.All` — read mail over IMAP.
   - `SMTP.Send` — send mail (only used if the member enables "send as me").
   - `offline_access` — issue a refresh token so the connection survives.
2. No admin consent is required for personal-account delegated scopes — each
   member consents for their own mailbox when they sign in.

The exact scope strings Lumogis requests are:

```
https://outlook.office.com/IMAP.AccessAsUser.All
https://outlook.office.com/SMTP.Send
offline_access
```

## 4. Configure Lumogis

Set the client id in your `.env` (see `.env.example`) and restart the
orchestrator:

```bash
LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID=00000000-0000-0000-0000-000000000000
```

The client id is **not a secret** — it identifies your app publicly. There is no
client-secret to store.

Once set, the **Connect Outlook.com** tile appears (enabled) in the guided
mailbox setup. Until it's set, that tile stays disabled with a short explanation
rather than a dead end.

---

## How a household member connects

1. In Lumogis, they pick **Outlook.com**, enter their mailbox address and a
   label, and choose **Continue to sign-in**.
2. Lumogis shows a short **user code** and a Microsoft sign-in link.
3. They open the link (on any device), sign in to Outlook.com, and enter the
   code. Lumogis polls in the background and connects automatically once approved.

Nothing to copy back — the code goes *to* Microsoft, and the mailbox appears in
Lumogis when Microsoft confirms.

## What Lumogis stores

- Only a **refresh token** (and short-lived access tokens minted from it), held
  in the same encrypted connector-credential store as every other mailbox
  secret. The member's Microsoft password is never seen or stored.
- A member can disconnect at any time; revoking the app under their
  [Microsoft account privacy settings][msprivacy] also cuts access immediately.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Outlook tile stays disabled | `LUMOGIS_OAUTH_MICROSOFT_CLIENT_ID` is unset or empty; set it and restart. |
| "Connecting Outlook.com isn't set up on this server yet" during connect | Same as above — the client id isn't visible to the orchestrator process. |
| Sign-in code expires before approval | Codes are short-lived; start again and approve promptly. |
| Sign-in works but the mailbox shows "verify on first sync" | The deployment routes IMAP through an egress proxy, so the live check is deferred to the first scheduled sync — this is expected, not an error. |
| `AADSTS7000218` / "public client flow" error at Microsoft | Step 2 (Allow public client flows) wasn't saved. |

[rfc8628]: https://datatracker.ietf.org/doc/html/rfc8628
[appreg]: https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade
[msprivacy]: https://account.microsoft.com/privacy
