# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.14.0] — 2026-11-19

### Added

- **Dictation works in a new install from the Release files.** The files carry a local speech engine (Speaches) under an `stt` Compose profile, and `example.env` switches it on, so the **Dictate** button works from the first start with nothing to set up. Lumogis installs the speech model itself (one attempt at start-up, retries in the background, and a new attempt when a dictation finds it missing), and when something is wrong Dictate says why in words on the page: *"Speech-to-text is off on this Lumogis."*, *"The speech model is still downloading. Try again in a minute."*, or *"The speech engine isn't answering yet…"*. While you speak it shows **Listening…** with a level meter (a still dot if you prefer reduced motion). On a plain `http://` address other than `localhost` the browser withholds the microphone, so the button is greyed out and says that voice needs **Settings → Home address**. It costs about 3.3 GB of disk, a download of about 1.1 GB and up to about 0.6 GB of memory; the speech model comes from `huggingface.co` (see [TELEMETRY.md](TELEMETRY.md)). See [Speech-to-text](docs/deployment/quickstart.md#speech-to-text).
- **The first person to open Lumogis creates the admin account.** A new install opens on **Create the admin account**: enter an email and a password twice and you are signed in as the household admin, with nothing to type into `.env` first. This can be done once, so **open Lumogis straight after `docker compose up -d`**: until the admin exists, anyone who can reach the computer can create it (Lumogis logs a reminder every five minutes while it waits, and the quickstart says how to start again if someone else got there first). If Lumogis is still starting, the page says so and continues on its own. Two new public routes back it: `GET /api/v1/setup/status` and `POST /api/v1/setup/claim`.
- **"What Lumogis knows" page.** A new page in Lumogis Web (`/entities`, reached from **Search → Browse what Lumogis knows**) lists the people, organisations, projects, concepts and other entries Lumogis has learned, with a count for each kind. It works the same with the knowledge graph on or off. Open an entry to see what mentions it ("Mentioned in 3 documents, 2 chats…"): only items you can open are listed, and the owner of an entry can rename it and edit its other names with **Edit names**. Three new routes back it: `GET /api/v1/kg/entities`, `GET /api/v1/kg/entities/{id}/mentions` and `PATCH /api/v1/kg/entities/{id}`; the entry card also reports its `version`. See [What Lumogis knows](docs/guides/what-lumogis-knows.md).
- **Secure address at home.** **Settings → Home address** gives Lumogis a free `https://….homes.lumogis.app` name for use on the home network, with a real certificate that Caddy renews on its own. Phones and laptops on the home Wi-Fi can then install the app and use voice input and notifications, with nothing installed on them. The page checks the address with the admin before turning it on, shows the address with **Copy address** and **Check on this device**, and walks through the router setting when a router hides the name. Signed-in members see a notice once their device can open the address. Turning it on shares this computer's home network address with Lumogis's name service, and the name appears in public DNS and certificate logs; see [Secure address at home](docs/deployment/remote-access.md#secure-address-at-home). New routes: `GET /api/v1/admin/secure-address`, `POST …/enable`, `PUT …/lan-address`, `POST …/retry`, `POST …/disable` and `GET /api/v1/secure-address`; System status shows a secure-address row. A new install registers with no setting. The page looks for this computer's address when it opens, and on Docker Desktop says how to find it (`POST …/detect`) and recommends a fixed address for the computer, because Docker Desktop cannot notice when it changes. For up to 30 minutes after turning it on, the page says the name is new instead of blaming the router. Switching between test and real certificates needs only `docker compose up -d`, no Turn off and on: Lumogis restarts Caddy when it keeps serving the other certificate.
- **Correct what kind an entry is.** If Lumogis filed "Meier Pharmacy" as a person, open the entry, choose **This is wrong…** → **It's not a person, it's a…** and pick organisation, project or concept. The entry keeps its name, other names, documents and links; search follows at once and the map within a day. Lumogis remembers the correction for you only, so reading that name again does not bring the wrong kind back; change the entry back to undo it. If you already have an entry of that kind with the same name, Lumogis says so, offers **Merge with …** and links to it. `PATCH /api/v1/kg/entities/{id}` accepts `entity_type`.
- **Merge two entries that are the same, with undo.** Open the entry you want to keep, choose **This is wrong…** → **It's the same as…**, pick the other entry and merge. The entry you are on stays; the other entry's name becomes one of its other names. Every merge, including the ones Lumogis, the review queue or an admin make, is recorded, and the entry's new **History** section offers **Undo merge** (newest first): the other entry comes back with its documents, links, names and shared copy, the entry that stayed keeps what it gained since, and the pair is remembered as "not the same" so automatic tidying won't merge them again. After an undo you can move mentions added since to the restored entry. Four new routes: `POST /api/v1/kg/entities/{id}/merge`, `GET /api/v1/kg/entities/{id}/history`, `POST /api/v1/kg/merges/{id}/undo` and `POST /api/v1/kg/entities/{id}/mentions/move`. **Merges made before this release have no record and cannot be undone.** See [What Lumogis knows](docs/guides/what-lumogis-knows.md).
- **Delete an entry so Lumogis does not learn it again.** On an entry you own, choose **This is wrong…** → **Delete…**. The entry, its mentions, links, merge records, search entries, map node and household copy go, and its names go onto a list of **names Lumogis won't learn** (**Settings → Names Lumogis won't learn**), so reading the same document again cannot bring it back. Removing a name lets Lumogis learn it again and offers to re-read the documents that mentioned it. Entries another member shared get **Hide from my list** instead, which affects only your own view. New routes `DELETE /api/v1/kg/entities/{id}`, `POST` / `DELETE /api/v1/kg/entities/{id}/hide`, `GET /api/v1/kg/name-rules` and `POST /api/v1/kg/name-rules/remove`; migration 084; new `ENTITY_DELETED` hook and graph webhook event.
- **Turn the knowledge graph on with one click on a Release install.** The graph service is now published as a sixth image, `ghcr.io/lumogis/lumogis-graph`, and the Release `docker-compose.yml` carries it and FalkorDB (the engine-only `falkordb/falkordb-server` image, with no host port) under a `graph` Compose profile that does not run until it is switched on. **Settings → Knowledge graph** has **Turn on** and **Turn off** buttons for those installs: Lumogis writes the settings into `.env`, starts or stops the two containers through stack-control and restarts itself briefly; Turn off keeps the graph's data. The page shows "Starting…", "Stopping…" and, if Docker fails, "did not start" or "did not stop" with **Retry**. If the memory check says the machine is short, the admin can still turn it on after an explicit "I understand". Admins see the offer on that page while the graph is off, and one dismissible line in System status; members never do. New routes: `POST /api/v1/admin/knowledge-graph/enable` and `…/disable`; the status adds `install_kind`, `turn_on`, `turn_off` and `failed_code`. stack-control gains `POST /graph/start` and `/graph/stop`, and the `.env` writes in Core now go through one atomic, mode-preserving writer. Git checkouts keep the written steps.
- **Settings → Knowledge graph** explains the optional knowledge graph, says whether it is on, whether this machine has enough memory for it, and how to turn it on. The steps are also in the quickstart. The memory it asks for is now measured: about 2 GB on top of Lumogis's 8 GB, so about 10 GB in total (was a 12 GB estimate).

### Changed

- **Every release is now installed and checked automatically on clean amd64 and arm64 machines before it is published (release smoke).** The check installs the release from its two files, signs in as a first visitor, adds a document, asks one question with outside connections blocked, restarts, switches the knowledge graph on and off, updates from the previous release and goes back.
- **Speech-to-text: new error codes, a new default model and new diagnostics fields.** `POST /api/v1/voice/transcribe` and `POST /api/v1/captures/{id}/transcribe` answer 503 with `stt_model_missing` (the model is still being installed) or `stt_unreachable` (the engine does not answer) besides `stt_disabled` and `stt_processing_error`. The default `STT_MODEL` is now `Systran/faster-whisper-base.en` (it was `base`, which Speaches 0.8 does not accept); a checkout `.env` with a short name such as `STT_MODEL=base` now shows `model_state: unknown_model` in Admin diagnostics, so set the full id. `GET /api/v1/admin/diagnostics` adds `speech_to_text.model` and `speech_to_text.model_state`, and `transcribe_available` is true only once the model is `ready`. New optional setting `STT_MODEL_INSTALL_TIMEOUT_SEC` (default 900). **Upgrading:** an update does not switch speech-to-text on for an install you already have; [Turn on speech-to-text after an update](docs/deployment/upgrading.md#turn-on-speech-to-text-after-an-update) gives the four `.env` lines.
- **One stylesheet for Lumogis Web.** The old per-page styles (about 2,800 lines) are gone, and the standard browser reset is back for the whole app, so every page draws its buttons, fields, tables and spacing the same way. Buttons and other touch targets are now a full 44 px high on phones; before they came out at about 41 px. The sign-in page uses the same card as the other signed-out pages. On a phone, the kinds on **What Lumogis knows** no longer overlap the filter below them, and the downloaded AI models on **Health and backups** are listed one under another instead of in a table too wide for the screen. A quick double-click on a confirm button (such as **Delete** or **Dismiss**) no longer runs the action twice.
- **The main pages match the rest of Lumogis Web.** **Capture** has one **Save capture** button (or **Save on this device** while offline) and shows photos, audio and the transcript only when there are some; discarding a waiting capture asks first. In the **Library**, a document's **Delete** comes last and asks first, **Read again** is in a menu, and the file's location is under **Details**. Entries Lumogis knows show whether they are personal or shared once, with **Delete…** last in the **This is wrong…** menu; sharing an entry asks in a dialog. **Email** filters are pills, the keyboard shortcuts open in a proper dialog, and the message composer is a dialog that fills the screen on a phone, asking before it throws away an unsent message. **Approvals**, a failed send's **Dismiss**, sign-in, password reset, invitations, first-run setup and the welcome steps use the same buttons, fields and dialogs.
- **Clearer privacy, household and admin pages.** **Activity** describes each event in a sentence ("Signed in", "An online AI call was blocked by the privacy setting") with the technical code under **Details**, and its rows stack on a phone. **What Lumogis may do**, **Online or local AI** and **Shared with the household** use the same layout for you and for the household; stopping sharing asks first. **Members and invites** puts each member's actions in one menu, and asks before changing a role, turning someone off or deleting them; the forms open in proper dialogs. A turned-off member can now be turned back on from this page; before, the button for it was always greyed out. **Health and backups** starts with one line saying whether everything is running, then backups, services and disk space, with versions, commands and codes under **Details**; deleting a downloaded AI model asks in a dialog. **Email Lumogis sends from** now asks before removing the mailbox. **Home address**, **Better search ranking**, **Knowledge graph** and the developer pages use the same components.
- **Clearer settings pages for you and your connections.** **Connected services** shows one card per service under its plain name (such as "Paperless documents" or "Anthropic (Claude)") with its state, **Connect** or **Edit**, and **Test connection**; disconnecting is in a menu and asks first, and service codes, the raw JSON form and the test service are under **Details**. **AI services** lists your services and keys in plain words. **Notifications** groups this device, your phone (ntfy, in three steps) and what notifies you, with technical details folded away. **Connected AI apps** says **Create a key** instead of "Mint", shows the new key once in a dialog, and asks before revoking one. **Download my data** puts the download button first, with what's included folded below. **Documents, feeds and pages** lists each source with what kind it is, how often it is checked and when it was last checked, and asks before removing one. Removing a browser from notifications now asks in a dialog too.
- **Clearer email settings.** **Email accounts** now shows one card per mailbox, saying what Lumogis keeps (full messages, or subject and sender only), whether it may send, and what happens to attachments, with **Sync now** on the card and the other actions in a menu. Removing a mailbox asks first in a dialog. The list of sorted mail is no longer repeated there: it is on the **Email** page. **Email labels and rules** lists each label with a menu to rename it, turn it off, re-sort old mail or delete it; turning a label off or deleting it asks first and says that its mail moves to Other. A new label needs only its name, and rules read as sentences, such as "subject contains invoice → Bills & finance".
- **One Settings.** Settings and Admin are now one place, **Settings**, with pages grouped as You, Email and documents, Connections, Privacy and control, and, for household admins, Household and Advanced. Every page starts with its name, one line saying what it is for and an (i) with more. Pages that exist both for you and for the household, such as Connected AI apps or Activity, have a **Mine / Household** switch for admins. On a phone, Settings opens on a list of all pages. Pages meant for troubleshooting (Diagnostics, Tools and capabilities, Feature flags, Safety test bench) are hidden until an admin turns on **Show developer tools**; members no longer see Tools and capabilities. Old addresses under `/me`, `/admin` and `/audit` keep working and open the new page. Opening or reloading a `/settings/...` page shows Lumogis Web, while the older `/settings` API still answers apps that ask for JSON.
- **A new Chat home.** An empty chat now opens on "What can I help you with?" with the message box in the middle of the page. The model choice sits inside the message box, and the microphone appears only when speech-to-text is running. Your messages appear on the right and Lumogis's answers across the page, formatted: bold, lists, headings, code and links show as such instead of as `**` and `#` signs. Links in an answer open only for `http` and `https` addresses. Suggested questions show as small chips under the message box, or, while your library is empty, an offer to connect a source or upload a file. One line under the message box says what happens to your chats, with an (i) for more. The list of chats sits in a panel beside the chat on a computer and opens from **Chats** on a phone.
- **New navigation in Lumogis Web.** The bar across the top is gone. On a computer, a sidebar lists Chat, Search, Library and Capture, then Email once a mailbox is connected and Approvals once something that can act for you is connected, with the number of items waiting. Settings and your account menu (Profile, Look and feel, Sign out) sit at the bottom, and the sidebar can be collapsed to icons. On a phone, a tab bar shows Chat, Search, Library, Capture and More; More holds everything else, and when that would be only Settings, the fifth tab is Settings itself, with Sign out at the end of the Settings list. The tab bar steps aside while you type, so the keyboard does not push it over the message box. The sidebar and tab bar stay in place while the page scrolls. Notices such as "You're offline" appear at the top of the page. A new read-only route, `GET /api/v1/me/shell`, tells the app which of these to show.
- **Lumogis Web looks calmer and reads more easily.** Text is now in your device's own font, while headings and the Lumogis name keep the Lumogis typeface, with section headings inside a page smaller than the page title; only codes and technical details stay in a fixed-width font. Buttons, fields, switches and menus share one look in light and dark mode, with larger tap targets on phones. Amber text, field borders and the red delete buttons in dark mode were made easier to see, so they meet the usual contrast guidelines. Short messages, such as the one a member sees when opening an admin page, now appear as a small notice at the top of the screen.
- **Sign-in is on by default, and Lumogis generates its own secrets.** Both env templates now set `AUTH_ENABLED=true`. On the first start Lumogis writes `AUTH_SECRET`, the refresh secret and `LUMOGIS_CREDENTIAL_KEY` into `.env` (a value you set yourself is never replaced), so there is nothing to generate by hand. **Keep a copy of `.env`:** the credential key decrypts the passwords you save in Lumogis, and Lumogis warns at start-up if saved credentials no longer match the key. The quickstart's "Turn on sign-in" section is now "Sign-in" (the old link still lands there). The code default is unchanged: with `AUTH_ENABLED` unset sign-in is off, a developer and test setting. **Updating from 0.13.x:** an existing `.env` keeps its `AUTH_ENABLED` value. If it is unset or `false`, Lumogis Web shows only a page saying it needs sign-in: add `AUTH_ENABLED=true` to `.env` and run `docker compose up -d`, and the first person to open Lumogis then creates the admin account, which receives what was stored before.
- **`LUMOGIS_PUBLIC_ORIGIN` is optional.** Compose no longer sets it. When it is unset, Lumogis accepts the address in the browser, so a phone on the home network can sign in, stay signed in after a reload and sign out over plain HTTP. When it is set, it is still enforced exactly. Set it for a public name, a tunnel, or email password reset: emailed links are still built only from it. Invite links shown in **Settings → Members and invites** are now always complete addresses.
- **Entity extraction now fails closed when the list of names Lumogis won't learn cannot be read.** Before, an unreadable rules table meant corrections and deletions were silently ignored for that batch. Now the batch is retried (chat summaries), the document is re-read at the next rescan (its stored hash is cleared), and an AI tool's `add_memory` keeps the memory but skips its entries until the next one.
- **Caddy image for the secure address at home.** `lumogis-caddy` now includes the DNS module Caddy uses to get the household's certificate, and a new `caddy-home` volume holds the household's site (written by Core, read-only for Caddy). If that site ever stops validating, Caddy still starts and serves the home-network address without it, and Lumogis puts it back.
- **Merging entries.** A merge now keeps the other entry's name as another name of the entry that stays, so Lumogis recognises it in later documents. An automatic or admin merge that would change what your household sees (exactly one of the two entries is shared) is no longer made: Lumogis queues the pair for review, and the review queue and `POST /entities/merge` answer `409 sharing_differs`. `POST /entities/merge` (admin) now requires `user_id` in the body (it used to default to `default`). A shared entry's search record now names the shared row it belongs to after a merge, so the household chat finds it.
- Graph service MCP tools no longer take a `user_id` argument; a call that sends one gets a tool error. `graph.backfill` over MCP is refused while `GRAPH_ADMIN_TOKEN` is set. With sign-in off, `MCP_AUTH_TOKEN`, when set, is now enforced on the graph `/mcp` (before, only Core's `/mcp` checked it); with it unset the endpoint stays open as before.

### Removed

- **Single-user mode in Lumogis Web.** Lumogis Web now has one interface for one person and for a family. The "Sign-in is off" banner and the hidden account controls are gone. With `AUTH_ENABLED=false` (a developer and test setting) the sign-in page says that Lumogis Web needs sign-in and to set `AUTH_ENABLED=true`.

### Fixed

- **Restoring a backup no longer loses the knowledge graph.** The documented restore copied the graph's `dump.rdb` while FalkorDB was running and then restarted it; a running FalkorDB saves its current graph on the way down, over the restored file, so the graph came back empty without an error. Restore now refuses while FalkorDB is running, its message says to stop it, and the Backup and restore and Upgrading guides stop FalkorDB together with the orchestrator and Lumogis Web.
- **Core no longer asks to restart LibreChat on installs that do not run it.** Every Core start and every model change regenerated `librechat.yaml` and asked stack-control to restart LibreChat; on a Release install, which has no LibreChat, stack-control answered with an error and Core logged a restart that never happened. Both now run only when the `librechat` Compose profile is on, and Core logs the real outcome.
- **Turning the knowledge graph on or off right after an update no longer fails with "unreachable".** For a few seconds after stack-control is recreated it is running but not yet listening; the request now retries for up to 15 seconds before it reports a failure.
- **`make backup` before `make update` no longer blocks the update, and `make rollback` keeps the overlays set in `.env`.** On a git checkout `make backup` rebuilt the backup image, which on Docker's containerd image store removed the image the running backup container came from, so `make update` refused to continue. It now runs inside the running backup container. `make rollback` ignored a `COMPOSE_FILE` set in `.env` and started the previous images without those overlays (ADR 274).
- **Lumogis Web works when opened over plain HTTP from another device.** Browsers offer `crypto.randomUUID` only on HTTPS or `localhost`, so at an address like `http://<machine>:8080` Chat crashed straight after sign-in ("Something went wrong"), and saving a capture failed with an invalid client id. One helper now falls back to `crypto.getRandomValues`, and every caller uses it.
- **Settings and Admin work on a phone.** The two-column layout never stacked on narrow screens, so every Settings and Admin page was squeezed into a column about 100 pixels wide. The layout's container query measured the element it was restyling, which CSS does not allow; the container is now a wrapper around it.
- **Unknown Settings and Admin addresses open the first page instead of a blank one.** Their catch-all redirect used a relative target, which looped (`/me/secure-address/profile/profile/…`).
- **Email signals lists only email.** It also listed system health alerts, and the system monitor stored a new copy of each alert every 30 minutes while its condition held (504 copies of one alert in 11 days on a test install). The page now asks for email only, and the monitor raises the same alert at most once a day (`SYSTEM_MONITOR_REPEAT_HOURS`, default 24); a changed value, such as a longer queue, still alerts at once.
- **Diagnostics no longer shows an internal backlog note.**
- **A credential save with no key configured now answers 503, not 500,** with the existing "credentials unavailable" message.
- **stack-control no longer passes Compose the `.env` values it started with.** Compose gives the process environment precedence over `.env`, and stack-control is created with `env_file:.env`, so a restart from Settings could recreate a container with a setting from the day the sidecar started (for example a changed `LUMOGIS_PUBLIC_ORIGIN`). Every Compose call now reads `.env` from the file itself.
- **Voice capture works again through the Lumogis front door.** Caddy sent `Permissions-Policy: microphone=()` on every response, which made browsers refuse the microphone, so voice memos and dictation failed whenever Lumogis was opened through Caddy (the normal install), even on `localhost`. It now sends `microphone=(self)`; the camera and the other features stay off. The content security policy also allows `https://*.homes.lumogis.app` for the upcoming secure home address.
- **`make rollback` restores the previous version again after `make update` on a git checkout.** The update rebuilt the Lumogis images under the same tag, so rollback either restarted the new version or failed with "No such image". The update now keeps the running images under local `lumogis-rollback/…` tags, never fetched from a registry, until the next successful update. Running `make update` again after one that failed its health check no longer replaces the rollback state.
- **A merged entry's search record carries its place and type again.** After two entries were merged, the winner's record in the search index lacked its scope and type, so the duplicate check never found it. It is now written the same way extraction writes it.
- **Mentioning an entry by another name no longer renames it elsewhere.** When Lumogis recognised a mention such as "Meier" as the entry "Dr. Meier", it announced the new entry under "Meier", which could rename the map node and the search record to the alias. It now uses the entry's own name and type.
- **A name Lumogis already knows as another name is no longer forgotten.** When an entry's other name was all lower case (for example "alex" after renaming the entry to "Alexander"), the first mention of that name removed it from the entry's other names, so the next mention created a second entry. It is now kept.
- In Settings → Diagnostics, the graph row no longer says "GRAPH_BACKEND is not falkordb" when the graph runs as a separate service; it says Core keeps no graph store in that mode.
- **Entity search and entity pages work when the knowledge graph is on.** With the graph switched on, searching for a person or place and opening its page failed with "unavailable". They now work the same with the graph on or off, and an entry's related entries come only from your own documents and chats.

### Security

- **Sign-out now needs a request from Lumogis's own page.** `POST /api/v1/auth/logout` checks the request's origin like other changes do, so another site can no longer sign a member out. Clients that send a Bearer token (Lumogis Search, scripts) are not affected.
- **Requests to a secure address must come from that address.** On any `*.homes.lumogis.app` name, a browser request that changes something is accepted only when its `Origin` is that same address, whatever `LUMOGIS_PUBLIC_ORIGIN` says. Other households' addresses share the parent name, so this stops one household's page from acting on another's Lumogis. The sign-in cookie is always `Secure` there, and sign-in refresh and sign-out ignore a planted cookie of the same name that Lumogis did not issue.
- Knowledge Graph: the graph service's entity deduplication again limits its vector-search candidates to the member's own personal entities, as Core's copy does. Defence in depth: no behaviour change in any shipped configuration.
- **The knowledge graph's contradiction check no longer uses a cloud model listed for it in `config/models.yaml`.** If a cloud model, or a model on a host outside your network, was listed first for this check, it was used and received your notes, voice memo transcripts or chat summaries, although the check is meant to stay on your server. It now uses the local default model instead, and the admin diagnostics page shows which model it uses.
- Knowledge Graph (when sign-in is on): the graph service's own MCP endpoint requires sign-in and uses the signed-in member, not a name sent by the client; graph settings, stop-entities, backfill, clean-up and code reindex calls need an admin; a member who is demoted or signed out everywhere loses graph access at once.

---

---

## [0.13.1] — 2026-11-15

### Fixed

- **The admin Software updates card no longer always says an update is available.** Lumogis reported its version as 0.3.0rc1 in 0.12.0 and 0.13.0, so the card compared that with the latest release and always offered one. It now reports 0.13.1, and a release can no longer be published while the code reports a different version.

### Security

- **Dependency updates for the desktop search app** (`clients/lumogis-search`, not yet released): updates within the existing version ranges for its test tooling, and newer `quinn-proto`, `rustls` and `serde_with` in the app itself. The images a household runs are not affected. The remaining alerts need major upgrades of the test tooling and are tracked separately.

### Changed

- **The agent guides for this repository** (`AGENTS.md`, `docs/LUMOGIS_AGENT_ORIENTATION.md`) describe the current release, the five published images and the two-file install.

---

## [0.13.0] — 2026-11-15

**Install Lumogis from two files.** Download two files from this release into an empty folder and run `docker compose up -d`: every image is pulled ready-made, nothing is built, and you need no copy of the source. Update later with `docker compose pull && docker compose up -d`. This release's `docker-compose.yml` and `example.env` are new (the first release to carry them).

### Added

- **Install Lumogis from two downloaded files.** Each release now carries a `docker-compose.yml` and an `example.env`. In an empty folder, save them (the second as `.env`) and run `docker compose up -d`: every image is pulled from `ghcr.io/lumogis/`, nothing is built, and no source checkout is needed. Update with `docker compose pull && docker compose up -d`. The quickstart leads with this, and the new [Upgrading](docs/deployment/upgrading.md) guide covers backups without `make`, pinning a version and going back.
- **Five published images instead of two.** `lumogis-stack-control`, `lumogis-backup` and `lumogis-caddy` are published and attested like `lumogis-orchestrator` and `lumogis-web`. Each image records the version it was built as (`LUMOGIS_IMAGE_VERSION`), and backups record it too.
- **A warning when your `docker-compose.yml` is older than your images.** If a release changes the compose file and you only pulled the images, **Admin → Diagnostics** shows `compose_file_stale` and says to download the new file (keep your `.env`).

### Changed

- **In a git checkout, `docker compose up -d` builds the Lumogis images every time.** The repo's `docker-compose.yml` now names them `ghcr.io/lumogis/<image>:dev-<compose project>` with `pull_policy: build`, so a checkout always runs its own code instead of a stale local image. The layer cache makes this quick when nothing changed. `make update` pulls the images it does not build, then rebuilds all Lumogis images (it used to rebuild only the backup image), and stops before touching the stack if the build fails.
- **`LUMOGIS_VERSION` is now the image tag.** In a release install it chooses the version (`release` follows the latest release; `0.13.0` pins one). In a checkout leave it unset: a set value tags your local builds with that release name. With `docker-compose.ghcr.yml`, it replaces `IMAGE_TAG`, which is read for one more release; the overlay's `latest` is the head of public `main`, so a backup there records the short commit as its version.
- **The orchestrator image creates the database schema itself on a new, empty database**, from the `postgres/init.sql` and migrations baked into it, so an install needs no files from the source. Existing databases are untouched. When the schema cannot be created, the orchestrator stops with `FATAL: no database schema` instead of starting half-working.
- **Qdrant runs the stock `qdrant/qdrant:v1.14.1` image.** Lumogis built its own only to add `curl` for the health check, which now uses bash.
- **`docker-compose.ghcr.yml` pulls all five Lumogis images**, and `docker compose pull` with it really fetches a new `latest` (it used to skip images already present).
- **`make verify-public-rc` also runs the knowledge graph service's tests inside its own image** (`make compose-test-kg`). CI runs them only from the checkout, so a test that failed only in the image went unnoticed.

### Fixed

- **The capabilities and changelog pages on lumogis.ai link decision records and guides on GitHub.** They used repo-relative links, which do not exist on the site.
- **The pre-built images on GHCR are published again.** The workflow that builds `ghcr.io/lumogis/lumogis-orchestrator` and `lumogis-web` (with SLSA provenance) was dropped from the public repo in 0.5.0, so `latest` stayed on a build from 2026-06-02 and no version tags were made. It is back, and each release is tagged (for example `IMAGE_TAG=0.12.0`). Installs that build locally, the default, were not affected.
- **Chat History titles no longer show the model's raw JSON.** When the model wrapped its session summary in a Markdown code fence, Lumogis stored the whole answer, so a History title read like `` ```json { "summary": … ``. The fence is now removed before the summary is read, and an answer that is not a JSON object is stored as plain text.

---

## [0.12.0] — 2026-11-14

The first release with the **knowledge graph** back in the public source (AGPL-3.0-only), and the first with **email**: Lumogis reads, sorts, searches and sends mail from your own mailboxes, with every send approved by you.

### Added

- **Email: connect your mailboxes.** Add IMAP mailboxes from **Settings → Sources**: a guided setup per provider (Gmail, iCloud, Fastmail or any other server), a live connection test before anything is saved, a choice of how much history to import, and labels taken from your mailbox folders. Gmail and Outlook can sign in with OAuth instead of an app password. Each household member adds their own mailboxes ([ADR 174](docs/decisions/174-email-imap-signal-source.md), [ADR 182](docs/decisions/182-email-setup-v2.md), [ADR 190](docs/decisions/190-oauth2-provider-connect.md)).
- **Email: one inbox across mailboxes.** `/email` shows all your mailboxes together, sorted by an on-box model into what needs you, what can wait and what is noise. You can edit the categories and correct the sorting, and Lumogis learns from your 👍 / 👎. A morning brief, snooze and "needs you" push notifications bring mail back when it matters ([ADR 175](docs/decisions/175-email-user-editable-taxonomy.md), [ADR 180](docs/decisions/180-proactive-email-surfacing.md), [ADR 181](docs/decisions/181-feedback-relevance.md), [ADR 213](docs/decisions/213-lum-821-unified-mailboxes.md)).
- **Email: read safely.** Opening a message shows its extracted text, never the sender's HTML, so tracking pixels and scripts never run. Very large messages are fetched text-only. Attachments can be opened, filed into your document library, or left alone, with dangerous file types refused ([ADR 193](docs/decisions/193-lum-742-email-surface-routing.md), [ADR 207](docs/decisions/207-lum-810-email-body-read-view.md), [ADR 208](docs/decisions/208-lum-814-text-only-imap-fetch.md)).
- **Email: search.** Search sender, subject and message text across all your mailboxes, on the box, with match counts per field; quoted earlier messages in a thread are not counted twice ([ADR 211](docs/decisions/211-lum-819-email-search.md)).
- **Email: write and send.** Reply, reply-all, forward and compose new mail from a chosen sending address, with signatures, recipient suggestions, attachments and voice dictation. An on-box model can shorten or change the tone of a draft. Sending asks first by default (**What Lumogis may do**): you see the message, then send it or save it to your mailbox's Drafts. A message that fails to send stays listed under **Approvals → Messages that didn't send**, with what is known about whether it went out, and can be saved to Drafts from there ([ADR 185](docs/decisions/185-smtp-send.md), [ADR 186](docs/decisions/186-reply-threading.md), [ADR 187](docs/decisions/187-send-ux.md), [ADR 189](docs/decisions/189-email-compose-refine.md), [ADR 202](docs/decisions/202-lum-793-failed-send-recovery.md), [ADR 214](docs/decisions/214-lum-831-sending-address.md)).
- **Email: tidy the mailbox, if you allow it.** Optionally move or label mail in your real mailbox to match Lumogis's sorting, handle spam using your provider's own verdict, and unsubscribe from lists. Mailbox changes can be undone, and unsubscribing never makes Lumogis fetch a link the sender chose ([ADR 176](docs/decisions/176-email-imap-writeback.md), [ADR 183](docs/decisions/183-email-label-write-back.md), [ADR 184](docs/decisions/184-spam-handling.md), [ADR 191](docs/decisions/191-email-unsubscribe.md)).
- **What Lumogis may do** (`/me/permissions`): one page, in plain language, for whether Lumogis asks first or just acts, per kind of action ([ADR 220](docs/decisions/220-lum-849-autonomy-surface.md)).
- **Sources you can pause.** Paperless, RSS and web-page sources can be added by any household member, edited, synced on demand, paused and resumed from **Settings → Sources**, and each shows why its last check failed. A paused source stops polling and picks up where it left off when resumed ([ADR 215](docs/decisions/215-lum-837-source-edit-and-health.md), [ADR 217](docs/decisions/217-lum-839-per-user-source-creation.md), [ADR 226](docs/decisions/226-lum-844-source-pause.md)).
- **Connectors you can switch off and test.** Each stored credential has an on/off switch, a **Test connection** button, and shows which of its fields are set, without revealing them ([ADR 218](docs/decisions/218-lum-834-connector-test-connection.md), [ADR 219](docs/decisions/219-lum-834-connector-enabled-and-field-presence.md)).
- **Forgot password.** The sign-in page always says how to recover access. An admin can also turn on reset by email under **Admin → System mail**, after a successful test send ([ADR 209](docs/decisions/209-lum-65-forgot-password-reset.md)).
- **One document library.** `/documents` lists uploads, watched folders, paperless documents and filed email attachments together, each marked with where it came from.
- **Background model tasks.** Each background job (signal enrichment, the email tasks, entity and relation extraction, session summaries, the weekly review, and temporal extraction and judging) can list its own models in order in a `tasks:` block of `config/models.yaml`. Tasks that can see email content accept only on-box models. Admin diagnostics show which model each task uses ([ADR 241](docs/decisions/241-lum-877-task-model-routing.md)).
- **Lumogis Web explains blocked cloud models.** When privacy mode keeps cloud models off, the chat and document-chat model pickers and the API-key card say so and link admins to **Admin → Privacy mode**. `GET /api/v1/models` adds an optional `disabled_reason` ([ADR 227](docs/decisions/227-lum-881-privacy-question-where-blocked.md)).
- **`make doctor` checks the client-IP setting behind Tailscale Serve and tunnels**: it reports a `LUMOGIS_CADDY_TRUSTED_PROXIES` value Caddy would refuse, or one that no longer includes the compose gateway ([ADR 253](docs/decisions/253-lum-901-doctor-gateway-check.md)).
- **For contributors:** `scripts/test-env.sh` runs a command in a test-runner image, offline, optionally with throwaway Postgres, Qdrant and FalkorDB; `make bench-recall` measures memory recall latency on synthetic data; `make e2e-ingest-live` proves ingest ownership on a live stack and fails on any skip.

### Changed

- **The knowledge graph is AGPL-3.0-only and ships in the public source again.** The graph service, the in-process plugin, the FalkorDB adapter, the graph webhook dispatcher, the graph compose overlays, their tests and ADRs 007, 008, 009, 011 and 035 are back in this repository. It is no longer a commercial add-in.
  - **This republishes existing work.** Most of this surface was first published under AGPL-3.0-only in this repository from 2026-05-02 until the 0.3.1 snapshot removed it on 2026-05-15, and its source files carried AGPL-3.0-only headers throughout ([ADR 229](docs/decisions/229-lum-855-kg-agpl-relicence.md), [ADR 239](docs/decisions/239-lum-855-kg-publication-as-built.md)).
  - **Nothing changes at runtime.** `GRAPH_MODE` still defaults to `disabled`. `service` is the mode that works on the standard Core image.
  - The graph capability reports `license_mode: community` and the name "Lumogis Graph".
- **A default install opens straight into Lumogis Web.** With sign-in off (the default), Lumogis Web opens as the single admin, with a banner saying sign-in is off and who can reach it; an empty chat offers to connect a source or upload a file. The quickstart explains how to turn sign-in on ([ADR 247](docs/decisions/247-lum-853-default-install-first-run.md)).
- **The orchestrator image is about 7 GB smaller.** PyTorch now comes from its CPU-only index, so the first `docker compose up -d` downloads and builds much less. The optional reranker runs on the CPU as before.
- **A new install no longer starts LibreChat.** `.env.example` leaves `COMPOSE_PROFILES` empty, so a fresh checkout runs Lumogis Web only. An existing install keeps its own `.env`; set `COMPOSE_PROFILES=librechat` to keep LibreChat.
- **Backups include your files.** `make backup` now also copies uploaded documents, inbox files, capture photos and voice notes, and the knowledge graph's stop-entities list, hard-linked against the previous snapshot so a daily backup adds only what changed. `make restore` puts them back without deleting anything. Snapshots now hold your original files unencrypted, so treat the backup folder like the documents themselves. **Admin → System status** shows what the backup covers ([ADR 248](docs/decisions/248-lum-892-backup-uploads-capture-media.md)).
  - **Upgrade note.** `make update` and `make build` handle the move automatically. A source install that does not use `make update` runs `docker compose build backup` once; one that runs `docker compose up -d --build` directly runs `make rescue-captures` first.
- **Faster database access under load.** Core keeps a small pool of Postgres connections instead of sharing one (`LUMOGIS_PG_POOL_MIN` / `LUMOGIS_PG_POOL_MAX`, default 2 / 10) ([ADR 210](docs/decisions/210-lum-816-db-connection-pooling.md)).
- **Mailbox health names the right fix** for OAuth and app-password mailboxes, and OAuth token refresh waits as long as the provider asks instead of retrying at once ([ADR 225](docs/decisions/225-lum-777-mailbox-auth-method.md), [ADR 228](docs/decisions/228-lum-761-oauth-refresh-transient-retry.md)).
- **Lumogis Web** moves to ESLint 10 and TypeScript 5.9, and needs Node 20.19 or later to build.

### Removed

- **`SIGNAL_LLM_MODEL`, `EMAIL_CLASSIFY_MODEL`, `LUMOGIS_TEMPORAL_EXTRACT_MODEL` and `LUMOGIS_TEMPORAL_JUDGE_MODEL` are no longer read.** Core logs a warning at start-up if one is set; move the value to the matching task in `config/models.yaml`.
- **`POST /ingest` no longer takes a folder** (see Security).

### Fixed

- **`docker compose up -d` failed on Apple Silicon and other arm64 machines**: the backup image assumed x86-64. It now builds and runs on both.
- **A new chat could start on a model that cannot answer** when Lumogis Web opened before Core was ready; it now starts on the first model that is turned on.
- **A new install printed "pull access denied for lumogis-qdrant"**, and **`make update` stopped at its first step** on a default install, for the same reason (the Qdrant image is built locally). Both fixed; `make update` and `make rollback` also respect `ORCHESTRATOR_HOST_PORT`.
- **Capture photos, voice notes and the stop-entities list were lost on every image update**; they now live in the workspace.
- **Lumogis Web no longer signs you out at random** when an idle connection closed under a token refresh (Caddy now closes idle Core connections first) ([ADR 245](docs/decisions/245-lum-875-caddy-upstream-keepalive.md)).
- **A fast double-click on an autonomy setting** wrote the change twice; it now writes it once.
- **Knowledge graph:** a member's own documents, entities, sessions, notes and audio memos now appear in the graph's views; shared notes and audio memos come back in other members' recall; merging two entities moves their typed relations to the survivor; the temporal contradiction judge runs on a default install; a fresh FalkorDB no longer reports as down ([ADR 234](docs/decisions/234-lum-598-entity-merge-edge-repoint.md), [ADR 255](docs/decisions/255-lum-954-shared-note-recall-payload.md), [ADR 259](docs/decisions/259-lum-952-graph-reconcile-correctness.md), [ADR 260](docs/decisions/260-lum-961-kg-node-scope.md)).
- **Approvals:** switching a capability connector to Do works, and rows that cannot be switched say so instead of offering a button that fails ([ADR 258](docs/decisions/258-lum-962-approvals-do-mode-flip.md)).
- **With sign-in off, a cloud key saved in Lumogis Web turns its model on** at once; a connector you switched off no longer shows as active anywhere ([ADR 238](docs/decisions/238-lum-850-connector-disabled-read-models.md), [ADR 244](docs/decisions/244-lum-889-auth-off-web-llm-key.md)).
- **Mailbox hosts written with international characters and their punycode form now match** ([ADR 237](docs/decisions/237-lum-782-imap-host-idna-normalization.md)).
- Shell scripts keep their executable bit on a fresh checkout; several Lumogis Web layout and copy fixes.

### Security

- **Household members could reach other members' files through folder ingest, chat file tools and folder browsing.** `POST /ingest` is now an admin-only **Rescan** of the configured library folders; chat's file tools follow the same visibility as search; `GET /browse` is admin-only ([ADR 230](docs/decisions/230-lum-884-ingest-and-chat-tool-ownership.md)).
- **Account changes end every credential the person holds.** Deleting a user, demoting an admin, changing a password and "sign out everywhere" now also revoke their MCP tokens and cancel invites they sent. Access and refresh tokens carry a type and are each accepted only where they belong, and a refresh secret distinct from `AUTH_SECRET` is required when sign-in is on ([ADR 243](docs/decisions/243-lum-885-account-state-token-cascade.md), [ADR 252](docs/decisions/252-lum-896-refresh-rotation-revoke-race.md)).
  - **Operator impact:** MCP clients need a new token after a password change, "sign out everywhere", a demotion or a deletion. A hand-written `.env` with sign-in on and no separate refresh secret stops booting until one is set (`make doctor` reports it).
- **Access tokens need their revocation claim and live at most an hour.** `LUMOGIS_REQUIRE_TV_CLAIM` is on by default, and `ACCESS_TOKEN_TTL_SECONDS` is capped at 3600.
- **Deployment hardening.** Port 8000 answers only on the host itself (use the Caddy URL from other devices); the client IP behind Caddy can no longer be chosen by a LAN host (`LUMOGIS_TRUSTED_PROXIES` defaults to `caddy` — remove an old private-range list from `.env`); the orchestrator no longer runs as root, drops every Linux capability it does not need, and the web container runs unprivileged; LibreChat's port 3080 and the optional LiteLLM proxy's port 4000 are published on `127.0.0.1` only; sign-in off on a public hostname is flagged at start-up ([ADR 240](docs/decisions/240-lum-880-litellm-overlay-loopback.md), [ADR 246](docs/decisions/246-lum-887-deployment-hardening.md)).
- **Lumogis Web clears one member's chats, drafts, cached pages and push subscription before the next person signs in on the same device**; unsynced offline captures stay with the member who saved them ([ADR 242](docs/decisions/242-lum-886-web-signout-client-state.md), [ADR 251](docs/decisions/251-lum-897-query-cache-session-epoch.md)).
- **Email egress is contained.** Outbound IMAP is limited to each mailbox's declared host, in-process on every install and, on Docker, optionally through an egress proxy that refuses undeclared hosts at the network layer; a process-wide outbound ceiling backs this up. RSS and page fetches validate every redirect ([ADR 178](docs/decisions/178-lum-625-email-imap-egress-containment.md), [ADR 179](docs/decisions/179-lum-571-process-wide-egress-ceiling.md), [ADR 216](docs/decisions/216-lum-838-rss-page-outbound-policy.md)).
- **Chat session summaries stay on the box** ([ADR 250](docs/decisions/250-lum-899-session-summary-local-only.md)).
- **The graph management page loads no third-party scripts**; its libraries are served from the box with integrity hashes ([ADR 256](docs/decisions/256-lum-941-graph-page-vendored-scripts.md)).

---

## [0.11.0] — 2026-07-17

### Added

- **Capability invoke contract v1** — capability manifests declare per-tool invoke paths, a versioned request/response wire envelope, structured errors, and optional auth modes; Core negotiates contract version and validates tool output when schemas are present.
- **Capability permission scopes** — manifests can declare required permission scopes; Core enforces least-privilege grants at invoke time; members and admins can grant or revoke optional scope arrays on connector permissions.
- **Community capability sandbox** — out-of-process capabilities outside the operator-maintained first-party allowlist are treated as community (untrusted), hidden from the LLM tool catalog by default, and blocked from in-process plugin loading.
- **Container egress containment (optional Compose overlay)** — community capability containers can run on an isolated Docker network with outbound traffic routed through a Squid forward proxy that allowlists only declared `external_endpoints`; see **`docs/guides/capability-egress-containment.md`**.
- **Admin safety playground** — household admins can run a curated injection test suite and ad-hoc probes against live sanitiser and scanner rules without persistence, hooks, or audit side effects.
- **Secrets scanner for user config** — optional pattern-based scanning blocks loading user-authored configuration that embeds credential-like material before it enters LLM context.
- **Tool-result injection scanner** — optional scanning redacts tool outputs that match injection patterns before they enter the session context.

### Changed

- **Connector permissions API** — grant and revoke endpoints accept optional scope arrays for fine-grained capability grants alongside Ask/Do modes.
- **Knowledge graph capability service** — conforms to capability invoke contract v1 with manifest-declared invoke paths and standard envelopes.
- **Mock capability service** — updated as the contract v1 reference implementation for CI smoke tests and operator overlays.

---

## [0.10.1] — 2026-07-12

### Fixed

- **Public repository CI** — ruff lint on the captures status-filter pytest module (fixture reuse via `pytest_plugins` instead of shadowing imports).

---

## [0.10.0] — 2026-07-12

### Added

- **Capture inbox and archive** — Quick Capture adds **Inbox** (edit, delete, retry, and commit pending or failed notes to memory) and **Archive** (read-only provenance for indexed captures); the captures list API supports status filtering and surfaces `last_error` on failed rows.
- **Admin search and retrieval settings** — household admins can enable or disable the BGE cross-encoder reranker from Lumogis Web, with honest pending-restart state and a health indicator on System status.
- **Admin feature flags panel** — read-only view of experimental orchestrator feature flags and their descriptions (env/compose remains the mutation path).
- **Web shell refresh** — collapsible primary sidebar, shared button styles, profile menu in the header, and **Settings → Appearance** for light/dark theme (theme toggle removed from the global header).
- **README screenshots** — curated main-screen captures for onboarding and documentation.

### Changed

- **Lumogis Web UI** — updated layouts and components across chat, documents, capture, approvals, admin users, audit, and upload surfaces for clearer hierarchy and mobile-friendly navigation.
- **My activity** — the member audit log is labeled **My activity** (formerly “Audit log”) with expanded date and event-type filters.
- **Document upload** — styled dropzone, selected-file chips, and clearer per-file progress during ingest batches.

### Fixed

- **Export and CI hygiene** — feature-ID catalog row count stays aligned with coverage matrices; OpenAPI snapshot matches the captures list API description.

---

## [0.9.5] — 2026-07-09

### Fixed

- **CI (private and public)** — lumogis-graph stats privacy tests mock `fetch_one` for LUM-577 `allows_shared` DB lookups; purge sweeper graph projection test skips when the premium `plugins.graph.writer` module is absent on the AGPL export.

---

## [0.9.4] — 2026-07-09

### Fixed

- **Public repository CI** — doctor integration reaches Postgres inside the compose network (overlay pins `POSTGRES_HOST=postgres`); Lumogis MCP stdio smoke tests use shared fast-start lifespan stubs; document-chat injection tests skip premium graph webhook patching when the AGPL export omits that module.

---

## [0.9.3] — 2026-07-09

### Fixed

- **Public repository CI** — doctor integration skips Ollama model pulls on GitHub Actions; Lumogis MCP stdio tests no longer block on Postgres remap during in-process startup; export-tree pytest skips when run from the published AGPL snapshot (no private export templates in-tree).

---

## [0.9.2] — 2026-07-09

### Fixed

- **Public repository CI** — pytest collection on the AGPL export tree no longer imports premium-only graph integration tests; doctor integration startup is more reliable on slow CI hosts (graph disabled in the doctor overlay, extended compose wait); Lumogis MCP stdio integration tests tolerate slower uvicorn bind on GitHub Actions.

---

## [0.9.1] — 2026-07-09

### Fixed

- **Public repository CI** — GitHub Actions on the AGPL export tree passes orchestrator lint, backup round-trip (Postgres + Qdrant when the premium FalkorDB overlay is absent), and doctor integration startup checks again after the 0.9.0 publish.

---

## [0.9.0] — 2026-07-09

### Added

- **Household document sharing** — owners can share library documents with the household; shared chunks become searchable and usable in document-chat for other members; large shares run as background jobs with honest partial-success reporting.
- **Household entity sharing** — owners can publish extracted entities from Search; when graph mode is enabled, shared documents also cascade entities into shared Postgres, Qdrant, and (in service graph mode) FalkorDB with refcounted retraction on unshare or purge.
- **Household conversation sharing** — share a conversation summary with the household from chat; members can discover shared threads through the existing sharing surfaces.
- **Household invite flow** — admins mint single-use invite links; new members redeem, set credentials, and complete an optional welcome onboarding step; admins can gate whether invitees may access shared scope.
- **Household admin panel** — member and admin counts, last-active column, promote/demote with confirmation, and self-guard rails on disable and delete.
- **Member audit log** — **`/audit`** for all authenticated users with date presets, event-type filters, pagination, and privacy or cloud markers on external-call rows.
- **Cloud LLM privacy mode** — hard local-only enforcement at the provider chokepoint; fresh installs default to local-only; per-user further restriction and instance-level admin lock; blocked remote attempts are audit-logged; plan or complex flows fall back to local Ollama with a warning.
- **MCP stdio bridge for Cursor** — new AGPL client package **`clients/lumogis-mcp/`** (`lumogis-mcp`) forwards MCP tool calls to Core's Streamable HTTP endpoint; **`make lumogis-cursor-install`** merges server config into **`~/.cursor/mcp.json`**.
- **MCP memory write surface** — **`add_memory`**, **`add_entity`**, and **`add_relation`** on **`/mcp/`** persist into Postgres and Qdrant with optional graph projection; **`forget`**, **`update_observation`**, and **`checkpoint`** support reversible archive and supersede flows.
- **MCP `recall` fusion tool** — read tool combining semantic, BM25 keyword, one-hop graph, and temporal validity filters with reciprocal rank fusion and optional cross-encoder rerank.
- **MCP token scopes** — mint-time **`mcp:read`** / **`mcp:write`** selection; the Web UI defaults to read-only unless write is explicitly granted; omitted scopes now mint read-only tokens instead of unrestricted ones.
- **Multi-bank memory isolation** — separate **`coding`**, **`personal`**, and **`default`** banks for MCP memories and graph projections.
- **Document chat mode** — scoped **`POST /api/v1/chat/completions`** with optional **`document_id`**, citation metadata on responses, and Lumogis Web route **`/documents/:documentId/chat`** with a context strip.
- **Ingest job progress** — per-stage progress (extract → chunk → embed → graph) via poll endpoints and SSE on the events stream; uploads return **`job_id`**; the upload panel shows multi-file batch progress.
- **Software update visibility** — admin System status card comparing the running version to the latest GitHub release; operator **`scripts/update/`** pull and rollback helpers with migration preview (**`make migrate-dry-run`**).
- **Feature-flag registry** — experimental subsystems gated by **`LUMOGIS_FF_*`** env vars (all default off) with a read-only admin visibility endpoint.
- **Household biography conflict review** — divergent shared-scope facts surface for admin review with represent-both, confirm-one, keep-both, and dismiss outcomes; losers are archived, not deleted.
- **LLM circuit breaker** — consecutive-failure breaker on the central LLM provider path to limit runaway spend when a model or upstream is down.
- **Lumogis Search Wayland recovery** — CLI **`--toggle`** / **`--show`** / **`--hide`**, cold-start show-once, and compositor-keybinding guidance when in-app global hotkeys fail on Wayland.
- **`EVALUATION.md`** — public operator self-evaluation guide at repo root on AGPL export; linked from **`README.md`**.

### Changed

- **Household RBAC hardening** — scope publish and unpublish routes require authenticated users; **`last_seen_at`** is tracked for admin visibility.
- **Privacy mode defaults** — new installs start local-only; existing cloud-using installs are migrated to explicit opt-in via settings migration.
- **MCP tool annotations** — every Core and KG MCP tool advertises MCP **`ToolAnnotations`** hints so clients can auto-approve safe read-only tools.
- **MCP `forget` / `update_observation`** — archived memories also purge projected FalkorDB relationships on the correct bank graph when graph mode is enabled.
- **User data export/import** — FalkorDB export covers all configured banks; legacy combined export format is retained for backward compatibility.
- **`make doctor`** — versioned core-service allowlist manifest, **`--fix`** Makefile shortcuts, and restart-loop guard on automated compose restarts.
- **Unscoped v1 chat context** — **`POST /api/v1/chat/completions`** without **`document_id`** now receives session memory, optional auto-RAG, and graph snippets consistent with legacy chat posture.
- **Web UX polish** — shared loading skeletons, plain-language error states with retry, and a non-admin service-health banner on chat when Ollama, Qdrant, or graph paths degrade.

### Fixed

- **Document re-ingest orphans** — sparse Qdrant chunks left after partial ingest blocks are cleared via payload-scoped delete before re-indexing.
- **Share / unshare races** — rapid toggle no longer drops an unshare intent when a share job is still in flight.
- **Shared-scope visibility for personal-only members** — knowledge-graph Qdrant and Cypher filters honour **`allows_shared=false`** in parity with orchestrator paths.
- **Conversation purge leftovers** — background sweeper retries failed Qdrant or graph arms for partially purged conversations.
- **Audit log routing** — browser navigations to **`/audit`** serve the Lumogis Web SPA instead of the legacy JSON API route.
- **Shared entity badges on Search** — the entity list refreshes after publish or unpublish so household badges stay in sync with the card.
- **LLM circuit breaker streaming** — successful streamed replies no longer leave a false consecutive-failure streak that could open the breaker.

### Removed

- **Dead admin Mint MCP-token control** — the Lumogis Web admin MCP-tokens view no longer exposes a Mint button for a route that was never implemented; minting remains self-service at **`/me/mcp-tokens`**.

### Security

- **Deterministic local-only LLM routing** — remote models are blocked, hidden from **`/v1/models`**, and audit-logged when privacy mode is on.
- **MCP Origin-header DNS-rebinding guard** — Core **`/mcp/*`** rejects non-localhost **`Origin`** headers with **`403`** before token validation.
- **Qdrant loopback-only host publish** — default Compose binds the Qdrant host port to **`127.0.0.1`** so the unauthenticated vector HTTP API is not reachable on the LAN.
- **Legacy `users.refresh_token_jti` column** — dropped; refresh state lives only in **`auth_sessions`** and **`users.token_version`**.

---

## [0.8.0] — 2026-06-15

### Added — scheduled Postgres, Qdrant, and optional FalkorDB snapshots via a Compose backup sidecar; operator commands **`make backup`**, **`make backup-verify`**, **`make backup-prune`**, and **`make restore SNAPSHOT=…`**; integrity manifests and 7-daily / 4-weekly retention; operator guide **[`docs/guides/backup-restore.md`](docs/guides/backup-restore.md)**.
- **Admin backup status** — read-only **Disaster recovery backup** panel on **System status** (last verified snapshot, age, size, store coverage, stale warning).
- **Notification routing (v1)** — in-process dispatcher with per-user preference storage, ntfy / Web Push / in-app SSE channel adapters, and producer migration off ad-hoc notification hooks.
- **Notification preferences in Lumogis Web** — editable per-notification-type × per-channel matrix under **Me → Notifications** with optimistic saves.
- **Admin Ollama management (typed v1 API)** — discovery, async model pull with job polling, and model delete from the **System status** panel via **`/api/v1/admin/ollama/*`** (legacy HTML dashboard routes remain as thin delegates).
- **Lumogis Search overlay** — system tray with **Show Lumogis** and **Quit** for discoverable summon/recovery alongside the global hotkey; refreshed settings panel layout and design tokens; admin ingest-path editing and restart-required banner from the overlay; Playwright and WebdriverIO e2e coverage for overlay flows.
- **Cold-start embedding readiness** — orchestrator waits for embedding model availability and can bootstrap Qdrant collections after restarts when ingest paths need re-indexing.
- **Backup integration CI** — compose-backed smoke for backup scripts and retention policy.

### Changed

- **Admin System status** — combines stack health, Ollama pull/delete controls, and DR backup visibility in one panel.
- **Legacy `POST /backup`** — documented and positioned as a lightweight logical JSON export; **`make backup`** is the canonical disaster-recovery path.
- **Public export hygiene** — Lumogis Search remains the sole shipped desktop client tree; proprietary server-only sources stay out of the AGPL export.

### Fixed

- **Conversation transcript sync** — further hardening for client-minted thread IDs and purge invariants (carried from prior release line).
- **Public RC verification** — integration gate tolerates co-located dev stacks when the test Compose project owns host ports; full gate includes Playwright prove mode after Caddy auth readiness.

### Security

- **Backup artefacts** — instance-scoped snapshots live under operator-controlled host paths; restore requires explicit confirmation and quiesces Core before store writes.

---

## [0.7.1] — 2026-06-15

### Added

- **`CONTRIBUTING-BEGINNERS.md`** in the public AGPL tree — step-by-step onboarding for first-time contributors, including a copy-paste agent prompt; root copy is produced by the export pipeline from `docs/public-export/`.
- **Persona A distribution documentation** — Persona A/B/C matrix in the reference manual, Lumogis Search install how-to for Docker-track self-hosters, and cross-links from the root README and capabilities overview.

### Changed

- **`CONTRIBUTING.md` extractor how-to** — aligned with live `@extractor(".ext")` registration in Core (canonical example: `orchestrator/adapters/pdf_extractor.py`).

### Fixed

- **Conversation transcript sync** — `PUT /api/v1/conversations/{id}` upserts the `web_conversations` header for client-minted thread IDs so debounced message sync no longer fails silently; purged conversations stay blocked after hard delete.

---

## [0.7.0] — 2026-06-06

### Added

- **Lumogis Search desktop overlay** at **`clients/lumogis-search/`** — AGPL household memory search client (global hotkey, **`GET /api/v1/memory/search`**, OS keychain session, role-gated admin ingest paths), in-webview first-run onboarding (server URL → **`/healthz`** → auth → search), and **`overlay.json` schema v2** with **`onboardingComplete`**. Build with **`make search-dev`** / **`make search-build`**.
- **Conversation history in Lumogis Web** — browse, continue, and delete past conversations with server-backed session lists and multi-store purge APIs.
- **Admin stack health** — read-only **System status** panel combining curated admin diagnostics with stack-control service rows.
- **First-run onboarding** in Lumogis Web — skippable orientation modal (per-user completion timestamp) plus a shared empty state for the chat zero-state.
- **First wow moment** — guided first-query and entity-discovery cards on chat after onboarding and when entities are ready; server wow-state APIs and live readiness hints over the events stream.
- **Inbox folder-watch hardening** — configurable inbox path and mode (`event` | `poll` | `off`), write-stability before ingest, poll fallback, quarantine for terminal failures, and inbox liveness on **`/healthz`** without exposing absolute paths.
- **`make doctor`** — read-only operator health CLI (Compose status/config, `.env` grammar checks, optional **`/healthz`** probes, optional JSON output). See **`scripts/doctor/README.md`**.
- **First-run quickstart** — **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published GHCR image path; **`README.md`** cross-links for discoverability.
- **paperless-ngx ingest** (Docker / self-hosted) — read-only REST polling into the normal chunk → embed → Qdrant path with per-user encrypted credentials and deduplicated external document tracking.
- **Hybrid context building** — word-boundary entity matches plus optional Qdrant semantic top-up, configurable entity budget env vars, and a dedicated entities token slice in chat context allocation.
- **GHCR image provenance** — SLSA Level 2 build attestations for **`ghcr.io/lumogis/lumogis-orchestrator`** and **`ghcr.io/lumogis/lumogis-web`**; verify with **`gh attestation verify`** (see **`docs/capabilities.md`**).
- **CHANGELOG CI gate** on pull requests that touch product paths unless explicitly skipped in the PR body.
- **Chat auto-RAG (opt-in)** — optional per-turn injection of top document chunks into **`POST /v1/chat/completions`** context via **`LUMOGIS_AUTO_RAG_*`** env knobs (default **off**).
- **Notification architecture decision record** — documents the planned unified dispatcher, preference schema, and channel adapters; **no runtime behaviour change** in this release.

### Security

- **Pre-launch security audit artefacts** — structured findings under **`docs/security-audit/`** plus a committed OWASP ZAP baseline report; path-gated CI **`security-audit`** job runs local audit checks and advisory static analysis on Core (and graph service when present).
- **Qdrant household-union filters** — vector search filters now honour OR-shaped visibility constraints so semantic search cannot return unscoped points across household members.
- **Graph stats privacy tests** — canonical regression coverage for **`GET /graph/stats`** visibility binding in the graph service test suite.

### Changed

- **Admin settings ingest paths** — breaking **`GET /settings`** shape: `filesystem_root` fields removed; `ingest_paths`, `pending_ingest_paths`, `restart_required`, and `paperless_configured` added with multi-path env round-trip on PUT/restart.
- **Push ingest upload** — **`POST /api/v1/ingest/upload`** accepts multipart uploads, returns **`202`** with `file_id`, and queues batch ingest from workspace uploads.
- **Public webhook `DOCUMENT_INGESTED` payload** — additive **`ingestion_source_kind`** (`filesystem` | `external`); external sources may use stable logical URIs instead of filesystem paths.
- **CI OpenAPI contract check** — path-gated **`openapi-check`** job and **`make openapi-check`** alias for offline snapshot drift detection.
- **`GRAPH_MODE` default is `disabled`** — fresh installs omit graph wiring until operators opt into in-process or service modes; missing premium modules degrade with a single structured warning.
- **`LUMOGIS_TOOL_CATALOG_ENABLED` defaults on** when unset so capability tools merge when services are healthy; set **`false`** explicitly to restore the previous opt-in behaviour.

### Fixed

- **Release verification** — `make verify-public-rc-full` brings the RC stack back up for Playwright, rebuilds the web image, and waits for auth readiness before e2e; Playwright prove mode runs serially with stabler login and session-harness refresh.

---

## [0.6.0] — 2026-05-30

### Added

- **Inbox folder-watch hardening (LUM-330):** configurable `LUMOGIS_INBOX_PATH` / `LUMOGIS_INBOX_MODE` (`event` | `poll` | `off`), write-stability before ingest, `enqueue_inbox_file` seam, poll-mode mtime fast-path, quarantine on terminal failures under `ai-workspace/quarantine/`, `/healthz` inbox liveness fields (no absolute paths), and auth-gated inbox block on `GET /api/v1/admin/diagnostics`.
- **Desktop memory overlay (LUM-329):** Tauri 2 app under **`clients/lumogis-desktop/`** — global hotkey (default **Ctrl+Shift+L** / **⌃⇧L**), frameless overlay, **`GET /api/v1/memory/search`** (5 hits), OS keychain for bearer JWT, library-root sandbox for native open/reveal; **`make desktop-dev`** / **`make desktop-build`**; CI workflow **`.github/workflows/desktop-build.yml`**. Proprietary tree — excluded from public export via **`scripts/public-export-strip-list.txt`**.
- **Client-only overlay distribution (LUM-398):** Persona B household-member profile — CI artefacts **`lumogis-overlay-*`**, **`tauri.client-only.conf.json`**, in-webview first-run onboarding (server URL → **`/healthz`** → auth → search with optional empty library roots), **`overlay.json` schema v2** with **`onboardingComplete`**; **`make desktop-build-client-only`** for local parity.
- **`make doctor` (LUM-199):** read-only operator health CLI — Compose **`ps`/`config`**, config grammar checks on **`.env`** (no **`source`**), optional **`/healthz`** probes, optional **`--json`** v1 contract (**`scripts/doctor/schema.v1.json`**), security category opt-in (**`--security`** / **`LUMOGIS_DOCTOR_RUN_SECURITY=1`**). See **`scripts/doctor/README.md`**.
- **First-run onboarding (LUM-165):** skippable Lumogis Web orientation modal (per-user `users.onboarding_completed_at`, `GET`/`PATCH /api/v1/me/onboarding`) plus a shared `EmptyState` for the chat zero-state.
- **First-run quickstart (LUM-184):** operator guide **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published **GHCR** image path (`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`, first-boot **Ollama** / **Postgres** behaviour, **`curl`** health smoke vs **`make health`**, and common errors); **`README.md`** and **`docs/README.md`** cross-link for discoverability.
- **paperless-ngx ingest (LUM-281, Docker / self-hosted):** read-only REST polling into the normal chunk → embed → Qdrant path; per-user encrypted credentials (`paperless` connector); `POST /api/v1/sources` with `source_type: "paperless"`; `sources.poll_cursor` + `external_documents` dedup; operator env `PAPERLESS_*`, `PAPERLESS_POLL_PAGE_SIZE`, and outbound URL policy knobs `LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` / `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` (see reference manual).
- **CONTEXT_BUILDING (LUM-210):** hybrid entity selection — word-boundary explicit matches plus optional Qdrant semantic top-up (`LUMOGIS_CONTEXT_BUILDER_SEMANTIC`), configurable entity budget and ranking env vars (mirrored in Core + `lumogis-graph` config), `user_id` threaded through the hook and KG `POST /context`, dedicated **`entities`** token slice in chat `allocate()`, and `visible_qdrant_filter` mirrored into **`services/lumogis-graph/visibility.py`** for the semantic path.
- **GHCR images** (`ghcr.io/lumogis/lumogis-orchestrator`, `ghcr.io/lumogis/lumogis-web`): GitHub-hosted SLSA Level 2 build-provenance attestations from the public **`lumogis/lumogis`** publish workflow; verify with **`gh attestation verify`** (see **`docs/capabilities.md`** — **Verifying image provenance**).
- **CHANGELOG CI gate** on pull requests that touch product paths: GitHub Actions enforces a `CHANGELOG.md` update unless `Skip-Changelog` or `[skip changelog]` in the PR body; see `CONTRIBUTING.md`, `.github/workflows/changelog.yml`, and local `make changelog-check`.
- **Chat auto-RAG (LUM-308):** optional per-turn injection of top **`documents`** chunks into **`POST /v1/chat/completions`** context (`LUMOGIS_AUTO_RAG_*` env knobs; default **off**); hybrid/RRF vs dense gating, BGE reranker floor when configured, and **`search_files`** dedupe against injected point ids.

### Security

- **Pre-launch hybrid audit (LUM-190):** structured findings under **`docs/security-audit/pre-launch-audit-2026.md`** (path differs from plan text `docs/security/` when a root-owned `docs/security` mount exists — see doc header) + committed **`docs/security-audit/zap-baseline-2026.json`** (OWASP ZAP baseline against `https://example.com`, pinned `ghcr.io/zaproxy/zaproxy` digest, auth **`none`**); path-gated CI job **`security-audit`** runs **`make audit-local`** (blocking) and advisory **Bandit** on `orchestrator/` (+ `services/lumogis-graph/` when present); **`scripts/requirements-security-audit.txt`** pins Bandit; **`make bandit-check`** for local parity; **`.github/scripts/security-audit-paths.sh`** + contract tests in **`.github/scripts/test-security-audit-paths.sh`**. Also: **`stack-control/requirements.txt`** bumps **FastAPI** to **0.136.1** (Starlette **1.0.1** for pip-audit); **`clients/lumogis-web`** lockfile via **`npm audit fix`** for a clean **`npm audit`**.
- **Qdrant household-union filters:** `adapters/qdrant_store.py::_build_filter` now implements top-level `should` (OR of branches) and `match.any` clauses, matching `visibility.visible_qdrant_filter`’s default shape. Previously only flat `must` lists were translated; the default filter had no top-level `must`, so Qdrant received an empty `must` constraint and could return **unscoped** points — a cross-tenant isolation failure for semantic search on `documents` / `entities` and for CONTEXT_BUILDING Phase B when semantic top-up is enabled.
- **LUM-23 / FP-042 — graph stats privacy evidence:** removed the long-skipped Core `orchestrator/tests/premium/test_graph_viz_routes.py` module; canonical regression coverage for `GET /graph/stats` visibility binding lives in **`services/lumogis-graph/tests/test_graph_stats_privacy.py`** (records Cypher/SQL + params; see **`docs/decisions/DEBT.md`** Resolved row).

### Changed

- **Admin settings ingest paths (LUM-397, C1):** breaking `GET /settings` rename — `filesystem_root` and `pending_filesystem_root` removed; `ingest_paths`, `pending_ingest_paths`, `restart_required`, and `paperless_configured` added. Multi-path env round-trip via `INGEST_PATHS_HOST` and `INGEST_PATHS` on PUT/restart.
- **Push ingest upload (LUM-397, C4):** `POST /api/v1/ingest/upload` — multipart `file`, `require_user`, `202` with `file_id`; persistent store under workspace `uploads/`; batch `ingest_upload` handler.
- **LUM-322:** ADR-061 revisit conditions now document deferral of a parallel **`orchestrator.doctor`** CLI in favour of **`GET /admin/health`** until explicit gates fire; **`scripts/doctor/README.md`** cross-links when to use shell **`make doctor`** vs authenticated admin health.
- **Public webhook `DOCUMENT_INGESTED` payload** (Core → `lumogis-graph` when `GRAPH_MODE=service`): additive field **`ingestion_source_kind`** (`"filesystem"` \| `"external"`; default **`"filesystem"`**). When **`"external"`**, **`file_path`** may be a stable logical URI such as **`paperless://{source-uuid}/documents/{id}`** instead of a filesystem path.
- **LUM-94:** CI adds a path-gated **`openapi-check`** job (alongside existing **`lint-and-test`**) plus **`make openapi-check`** as an alias of **`make web-codegen-check`**; contributor docs now describe offline OpenAPI snapshot / codegen drift checks (`dump_openapi`, not a live orchestrator). **`openapi.snapshot.json`** refreshed to match current **`dump_openapi`** output (schema emission deltas).
- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent (AGPL export / partial trees).
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset: operators running capability services no longer need to set this flag for the LLM to merge eligible tools. Operators who want the previous behaviour (**no** merged capability tools / OOP dispatch) must set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.

---

## [0.5.2] — 2026-05-24

### Fixed

- **Public CI lint:** orchestrator **`ruff format`** pass so the published **`lint-and-test`** job’s format check exits cleanly on GitHub Actions (no runtime behaviour change).

---

---

## [0.5.1] — 2026-05-24

### Fixed

- **Public CI lint:** orchestrator import ordering and line-length violations that blocked the **`lint-and-test`** job on published **`main`**.
- **Public CI doctor integration:** **`doctor-integration`** job compose chain no longer merges **`docker-compose.test.yml`**’s **`include:`** overlay with the base file (avoids **“services.orchestrator conflicts with imported resource”** on GitHub Actions); uses **`docker-compose.test-doctor.yml`** instead.

---

---

## [0.5.0] — 2026-05-24

### Added

- **`make doctor`:** read-only operator health CLI — Compose **`ps`/`config`**, config grammar checks on **`.env`** (no **`source`**), optional **`/healthz`** probes, optional **`--json`** v1 contract (**`scripts/doctor/schema.v1.json`**), security category opt-in (**`--security`** / **`LUMOGIS_DOCTOR_RUN_SECURITY=1`**). See **`scripts/doctor/README.md`**.
- **First-run onboarding:** skippable Lumogis Web orientation modal (per-user `users.onboarding_completed_at`, `GET`/`PATCH /api/v1/me/onboarding`) plus a shared empty-state component for the chat zero-state.
- **First-run quickstart:** operator guide **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published **GHCR** image path (`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`, first-boot **Ollama** / **Postgres** behaviour, **`curl`** health smoke vs **`make health`**, and common errors); **`README.md`** and **`docs/README.md`** cross-link for discoverability.
- **Remote access guide:** **[`docs/deployment/remote-access.md`](docs/deployment/remote-access.md)** documents off-LAN household access patterns (Tailscale-first).
- **paperless-ngx ingest (Docker / self-hosted):** read-only REST polling into the normal chunk → embed → Qdrant path; per-user encrypted credentials (`paperless` connector); `POST /api/v1/sources` with `source_type: "paperless"`; `sources.poll_cursor` + `external_documents` dedup; operator env `PAPERLESS_*`, `PAPERLESS_POLL_PAGE_SIZE`, and outbound URL policy knobs `LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` / `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` (see reference manual).
- **Hybrid context building:** word-boundary explicit entity matches plus optional Qdrant semantic top-up (`LUMOGIS_CONTEXT_BUILDER_SEMANTIC`), configurable entity budget and ranking env vars, dedicated **`entities`** token slice in chat context allocation, and visibility filters mirrored into graph service queries when premium graph modules are present.
- **GHCR image attestations:** published **`ghcr.io/lumogis/lumogis-orchestrator`** and **`ghcr.io/lumogis/lumogis-web`** images carry GitHub-hosted SLSA Level 2 build-provenance attestations verifiable with **`gh attestation verify`** (see **`docs/capabilities.md`** — **Verifying image provenance**).
- **Chat auto-RAG:** optional per-turn injection of top **`documents`** chunks into **`POST /v1/chat/completions`** context (`LUMOGIS_AUTO_RAG_*` env knobs; default **off**); hybrid/RRF vs dense gating, BGE reranker floor when configured, and **`search_files`** dedupe against injected point ids.
- **Public CI:** path-gated **`openapi-check`**, **`doctor-integration`**, and **`security-audit`** jobs in **`.github/workflows/ci.yml`**; offline OpenAPI snapshot/codegen drift checks via **`make openapi-check`**; semantic breaking-change gate via **`make openapi-breaking-check`** (requires **oasdiff**).

### Security

- **Pre-launch security audit:** structured findings under **`docs/security-audit/pre-launch-audit-2026.md`** plus committed **`docs/security-audit/zap-baseline-2026.json`** (OWASP ZAP baseline, auth **`none`**); path-gated CI job **`security-audit`** runs **`make audit-local`** (blocking) and advisory **Bandit** on `orchestrator/` (+ `services/lumogis-graph/` when present). **`stack-control/requirements.txt`** bumps **FastAPI** to **0.136.1**; **`clients/lumogis-web`** lockfile refreshed for clean **`npm audit`**.
- **Qdrant household-union filters:** vector search now honours top-level **`should`** (OR) and **`match.any`** clauses in visibility filters, fixing a cross-tenant isolation failure for semantic search on **`documents`** / **`entities`** and for semantic context-building top-up when enabled.
- **Graph statistics privacy:** canonical regression coverage for operator graph statistics visibility lives in the knowledge-graph service test suite; redundant Core-side test scaffolding removed from the default layout.

### Changed

- **Public webhook `DOCUMENT_INGESTED` payload** (Core → graph service when `GRAPH_MODE=service`): additive field **`ingestion_source_kind`** (`"filesystem"` \| `"external"`; default **`"filesystem"`**). When **`"external"`**, **`file_path`** may be a stable logical URI such as **`paperless://{source-uuid}/documents/{id}`** instead of a filesystem path.
- **OpenAPI contract checks** use offline snapshot/codegen drift checks (`dump_openapi`, not a live orchestrator). **`openapi.snapshot.json`** refreshed to match current schema emission.
- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent (AGPL export / partial trees).
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset: operators running capability services no longer need to set this flag for the LLM to merge eligible tools. Operators who want the previous behaviour (**no** merged capability tools / out-of-process dispatch) must set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.

---

---

## [0.4.0] — 2026-05-15

### Added

- **Ingest and context hardening** for retrieved material: pattern checks at ingest, structured origin metadata stored with vectors, explicit framing of retrieved fragments in chat context (with per-request scaffolding), a configurable cap on chained tool calls in a single model turn, and audit-friendly signals when high-severity ingest patterns fire. Operators can tune or disable the sanitiser via environment variables documented in the reference manual.
- **Action proposal execution** uses a database-backed queue with atomic claims so the same approved proposal cannot be executed twice by different clients; long-running or crashed workers time out to a terminal error state rather than returning work to an executable queue.
- **Multi-device sessions** for household accounts: refresh tokens are tracked per device session instead of a single rotating slot, and access tokens carry a monotonic per-user version so password changes and global sign-out invalidate outstanding access JWTs immediately instead of waiting for access-token expiry alone.
- **Maintainer and CI gates** for public-boundary work: Makefile targets run orchestrator tests in Compose, web lint/test/build, and optional integration slices; graph **RELATES_TO** merge policy is checked in CI to keep projection tests aligned with documented edge direction.

### Changed

- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent in this tree.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset so capability tools merge when services are healthy and bearer trust is valid. Operators who want the previous behaviour (**no** merged capability tools) set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.
- **Default Compose Qdrant host publish** uses **`${QDRANT_HOST_PORT:-6334}:6333`** so the developer stack avoids collision with another vector store on host port 6333; disposable integration env files can set a different host port for parallel stacks.
- **Public export profile** continues to ship AGPL Core, web client, and tests without bundling premium-only graph service source trees; premium graph remains a separate packaging concern.

### Fixed

- **Web OpenAPI contract check** normalises `info.version` the same way as the committed snapshot generator so semver drift on the live JSON endpoint does not false-fail release verification when the route surface matches.

---

## [0.3.0] — 2026-05-13

### BREAKING — Knowledge graph proxy when `GRAPH_MODE=service`

- **`query_graph` does not reach the out-of-process graph bridge** until **`GRAPH_WEBHOOK_SECRET`** is set on Core **or** **`LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET`** is enabled (`true` / `1` / `yes`). Otherwise Core stays fail-closed (no outbound HTTP for that tool path; user-facing unavailable message).
- Deployments that relied on graph calls **without** **`GRAPH_WEBHOOK_SECRET`** on Core while the graph service accepted unsigned requests **must** align symmetric secrets or enable the Core opt-in deliberately. **`GRAPH_MODE=inprocess`** is unchanged.

### BREAKING — Operator diagnostics (credential fingerprint)

- **`GET /api/v1/admin/diagnostics/credential-key-fingerprint`** returns a **per-tier** shape (**user**, **household**, **system**) plus aggregate row counts. External integrations that assumed the older flat layout need updating. The Lumogis Web rotation badge expects the new shape.

### Security

- **stack-control (FastAPI / Starlette).** Pinned FastAPI to **0.123.10** so dependencies resolve to Starlette **0.50.x** and satisfy CVE-2025-54121 and CVE-2025-62727. Earlier FastAPI lines capped Starlette below **0.47**, which could not satisfy those patched ranges.

### Added

- **Household accounts and sessions.** **Admin** and **member** roles with JWT access tokens and rotating refresh cookies; Lumogis Web as the primary interactive shell when **`AUTH_ENABLED`**.

- **Password management and household portability.** Self-service password change, admin-initiated resets, documented CLI recovery for operators with shell access, and admin-mediated user export/import flows.

- **Connector-backed integrations.** Encrypted connector credential storage, CalDAV and **ntfy** connectors as surfaced in Lumogis Web, LLM provider API keys scoped per user where configured.

- **Opaque MCP bearer tokens** for **`/mcp/*`** clients in authenticated deployments—distinct from interactive JWTs—plus mint/revoke surfaces in Me and Admin.

- **Structured audit logging** with durable append-only trails and request correlation for operator review.

- **Household and instance-system connector credentials.** Admins store shared connector secrets at household or whole-instance scope; resolution walks **user → household → system → environment** and fails closed on decrypt errors without silent fallback across tiers. Rotation tooling and audit summaries understand all tiers; household/system secrets stay out of per-user export bundles.

- **Per-user connector Ask/Do permissions.** Permission modes are stored **per user**, drive catalog labels and execution checks, and routine Ask→Do elevation tracks approvals **per user**. Authenticated APIs let members manage their own connector modes and admins manage any account.

- **Out-of-process knowledge-graph service.** Optional **`lumogis-graph`** runs projection, FalkorDB writes, reconciliation jobs, **`query_graph`**, and **`/context`** for chat when **`GRAPH_MODE=service`**; **`GRAPH_MODE`** selects **`inprocess`**, **`service`**, or **`disabled`**. Capability manifests may advertise **`management_url`** for operator UIs.

- **Lumogis Web responsive and installable client.** Mobile-oriented UX for chat, approvals, and navigation shells; **PWA** manifest and service worker (**precache** and notification hooks—not full offline intelligence); **browser Web Push** opt-in with subscription storage and minimal payloads.

- **Capture ledger APIs.** Authenticated **`/api/v1/captures`** routes create and manage capture rows (including attachments and transcription hooks), list with pagination, and submit captures for indexing; complements the **`/capture`** QuickCapture UI.

- **Push-to-talk transcription.** **`POST /api/v1/voice/transcribe`** returns transcript text from uploaded audio when speech-to-text is enabled (`STT_BACKEND`); optional Whisper-class **sidecar** deployment via Compose overlay.

- **Admin diagnostics "foundation signals".** Read-only diagnostics expose extra orchestrator readiness hints used by the operator dashboard.

- **Published Docker images.** `ghcr.io/lumogis/lumogis-orchestrator` and `ghcr.io/lumogis/lumogis-web` are now published automatically to GitHub Container Registry on every release — supports `linux/amd64` and `linux/arm64` (Apple Silicon, Raspberry Pi, ARM servers). Self-hosters can pull pre-built images without building from source.

- **Pull-based deployment overlay.** `docker-compose.ghcr.yml` lets operators use published images via `COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml docker compose up -d --pull always` without modifying the default developer compose file.

### Changed

- Chat may inject optional graph **`/context`** fragments when **`GRAPH_MODE=service`** (short timeout; misses do not block replies).

- In-process graph plugin disables routers, hooks, and scheduled graph jobs when **`GRAPH_MODE` ≠ `inprocess`** so service mode does not double-write.

### Deprecated

- Legacy **`GET /permissions`** and **`PUT /permissions/{connector}`** still respond but require admin, advertise deprecation metadata, and steer callers to **`/api/v1/me/permissions`**; slated for **`410 Gone`** in an upcoming minor release. Legacy **`PUT`** affects only the caller's own per-user row.

### Fixed

- **stack-control tests:** the **`RESTART_SECRET`** restart-flow test mocks Docker Compose instead of shelling out, so CI passes without a compose project working directory.

- Capture attachment storage rejects unsafe absolute path segments in normalized keys.

- Speech-to-text sidecar URL allowlisting rejects IPv4-mapped IPv6 literals used to bypass host checks.

### Notes

- Makefile-driven Compose test targets pin **`COMPOSE_FILE`** so local **`docker compose`** overrides cannot pull in missing fragments during **`compose-test`** runs.

- CI enforces a Compose manifest policy check (allowed **`env_file`** usage and forbidden sensitive keys in tracked stacks).

- Connector permission mode caching is **single-worker**; multi-worker orchestrator replicas could briefly serve stale labels until cache refresh—single-worker remains the supported default.

- Disabling a user revokes MCP bearer tokens promptly; outstanding JWT access tokens may remain valid until expiry.

- Release verification gates (`make verify-public-rc`, `make verify-public-rc-full`) are now documented Makefile targets; maintainers can validate the full release pipeline locally before publishing.

---

## [0.3.0rc1] — 2026-03-19

Initial public release candidate.
