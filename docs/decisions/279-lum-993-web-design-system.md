# ADR-279: One design system for Lumogis Web (shadcn/ui on Base UI with Tailwind v4)
**Status:** Finalised (canonical copy: `docs/decisions/279-lum-993-web-design-system.md`)
**Created:** 2026-11-17
**Last updated:** 2026-11-18
**Decided by:** /explore, plan A review and arbitration, and Thomas (2026-11-17/18)
**Finalised by:** /verify-plan 2026-11-18 (all four passes merged)
**Plan:** `LUM-993-design-system-shell-settings.plan.md` (archived)

## Context

Lumogis Web has no shared component layer and no shared page structure:
- one `Button` against 246 raw buttons;
- 571 inline styles;
- a 4,100-line global stylesheet;
- monospace body text;
- four dialog implementations;
- no popover or help component;
- page headings from `<h1>` to `<h3>`.

The household product reads as a developer tool (LUM-993). Thomas asked for one design system, a home as clean as Claude's or ChatGPT's, one grouped Settings, and an (i) explanation on every page and non-obvious action.

## Decision

Adopt shadcn/ui components, on Base UI primitives, styled with Tailwind v4 and lucide icons, copied into `clients/lumogis-web/src/components/ui/`. Theme them from the existing `--lumogis-*` tokens through Tailwind's `@theme`, so brand colours and the WCAG contrast test carry over.

Build every page on one template: PageHeader with title, one-line purpose, an (i) InfoTip and one primary action, plus PageSection, EmptyState, LoadingState and ErrorState.

UI text uses the system font; monospace is kept for code, ids and paths. During migration Tailwind is loaded without its Preflight reset, `tokens.css` sits in a `legacy` cascade layer below utilities, and Preflight's reset is scoped to shadcn components (`[data-slot]`); Preflight returns once the last legacy page has moved. Pages migrate in passes, and each page's own CSS and inline styles are deleted as it moves.

ADR 222's class-name guard is amended: an unprefixed class passes only if Tailwind, compiled from the app's `ui.css`, generates a rule for it, so misspelt or foreign classes still fail.

### Related decisions recorded with plan A (2026-11-17)
- **One Settings at `/settings/*`** replaces `/me/*`, `/admin/*` and `/audit`, with client-side redirects kept for at least two releases. Because `/settings*` is also the legacy orchestrator settings API, Caddy and the Vite proxy route only browser navigations (`Accept: text/html`) to Lumogis Web, the pattern already used for `/search`, `/audit` and `/entities` and guarded by `orchestrator/tests/test_spa_routes_shared_with_legacy_api.py`.
- **Navigation:** Email appears only once a mailbox is connected (LUM-642/655/658 kept; Thomas reversed the R1 proposal); the phone has five tabs (Chat, Search, Library, Capture, More); no top header.
- **Settings pages:**
  - Tools and capabilities is a developer page.
  - Admins no longer edit a member's personal keys in the UI (the API is unchanged).
- **Colour and type:** amber is a fill colour, never text. Navigation text uses `--lumogis-accent-text` (light `#9a5b00`, dark `#ffbe54`), checked by a contrast test that reads the real stylesheets.

## As implemented (2026-11-18)

Shipped in four passes, each approved by Thomas from a before/after page: Pass 1 (foundation), Pass 2a (shell), Pass 2b (Chat home), Pass 3 (one Settings). Where the implementation differs from the decision above:

- **Type:** body and UI text use the system font; **headings and the wordmark keep Syne** (Thomas, 18 Nov 2026). Monospace stays for code, ids and paths.
- **Colour:** `--lumogis-accent-text` is light `#8f5400` (the planned `#9a5b00` measured 4.2:1 on the active-navigation tint), dark `#ffbe54`. Field borders use a new `--lumogis-field-border` (3:1 or more on every surface); the solid danger button takes `--lumogis-danger-fg`. `tests/design/tokenContrast.test.ts` reads the real stylesheets in all four theme states.
- **Navigation follows the core-and-plugins rule** (LUM-462 record): Chat, Search, Library, Capture always; Email once a mailbox is connected; Approvals only while something that can act is connected, with a waiting count; the microphone only while a speech engine runs. One read-only endpoint, `GET /api/v1/me/shell`, answers what to show; its waiting count is pending proposals plus failed sends to retry (the denied-action history is not counted, it never clears). There is no Admin item: household pages live inside Settings.
- **One Settings:** `/settings/:slug`, built from one list (`src/features/settings/settingsNav.ts`) of 26 pages in seven groups, with audiences (everyone, admin, developer behind a per-browser "Show developer tools" switch) and Mine/Household views (`?view=household`, admins only). **The Settings frame renders the one PageHeader** (title, purpose line, (i), the Mine/Household switch) for every page; the views no longer render titles. Guarded by `tests/design/pageHeader.test.ts` and `tests/features/settings/pageHeaders.render.test.tsx`. 34 old addresses redirect (query and router state kept). Caddy's `@legacy_settings_api` sends `/settings*` to Core only when the request does not accept HTML.
- **Chat:** the model choice is a native `<select>` styled as a chip (phones get their own picker), not a Base UI Select.
- **Class-name guard (ADR 222, amended):** Tailwind-generated classes pass, matched at the class boundary; `cn()` and `cva()` arguments are scanned.
- **Bundle:** entry JS about 326 KB gzip after Pass 3. The plan's 320 KB stop line was raised to 400 KB by Thomas (18 Nov 2026); `scripts/check-bundle-budget.mjs` reports and fails past it.
- **Not done here (done in plan B, below):** page bodies inside Settings keep their legacy markup and CSS; single-user mode needed no handling (removed from the web by LUM-981); the real-iPhone Safari check is a manual gate.

## As implemented — plan B (2026-11-19)

- **Every page body on the components** (Passes 4–7): Settings (Email accounts and labels; You, Email and documents, Connections; Privacy and control, Household, Advanced, developer pages), then Capture, Library, entities, Search, Email, Approvals, Chat leftovers, sign-in, setup and onboarding. Row actions beyond the primary one sit in `components/page/RowActions` ("…"); destructive actions confirm in `ui/alert-dialog`; developer detail goes under a collapsed "Details".
- **Ratchets, now empty:** `tests/design/pageBodyRatchet.test.ts` (inline `style={{…}}`, raw `<button>`, `window.confirm` under `src/features`) and `dialogRatchet.test.ts` (hand-made dialogs, native `<dialog>`, `window.confirm`, `lumogis-modal`). Any new occurrence fails.
- **Preflight is back** (Pass 8): layers are `theme, base, components, utilities`; `tokens.css` follows Preflight in `base` and holds only the tokens, the html/body/heading/link base and the app shell layout (3,197 → 365 lines). The `legacy` layer and the `[data-slot]`-scoped reset are gone.
- **Touch targets in px:** `--lumogis-size-body` (15 px) is set on `html`, so `rem` is 15 px; the 44 px floor is `pointer-coarse:*-[44px]`, not `*-11` (which gave 41.25 px). Moving the body size off `html` was declined: it would resize every approved page.
- **Mail list:** the sorted-mail list left Settings → Email accounts; the Email page already shows it.
- **Checked on a real phone** (Thomas, 2026-11-19, Chrome on iOS / WebKit) on lumogis-dev; the one problem found (Health's model table too wide) was fixed the same day.

## Alternatives Considered

- **Headless primitives (Base UI or React Aria Components) with our own CSS:** keeps Tailwind out, but leaves the look of every component to us, which is how the drift happened.
- **Mantine v8:** complete and fast, but a look we restyle against and components we cannot edit in place.
- **Continue hand-rolled:** re-implements focus traps, positioning and keyboard handling the primitives solve.
- **MUI, Ant Design, Chakra and Radix Themes:** ruled out for look, weight or no advantage.

Full detail: `.cursor/explorations/LUM-993-web_ux_polish.md`.

## Consequences

- **Easier:**
  - Accessible dialogs, popovers, menus, tabs, selects and toasts from one source.
  - A consistent look by default.
  - Sidebar, settings and chat blocks close to the target.
  - Agents implement new surfaces in the house style.
  - Claude Design can load the real React components.
- **Harder:**
  - Two styling systems coexist until migration ends.
  - Contributors need Tailwind.
  - ADR 222 changes.
- **Future chunks:** every new Web surface (Notes, Family library, Install, Sign out everywhere) uses the new components and the new Settings structure. New inline styles should be refused once a ratchet guard lands.

## Revisit conditions

- The spike shows Vitest or Playwright breaking at scale, or more than about 150 KB gzip added to the main bundle.
- shadcn/ui stops supporting Base UI, or Base UI loses maintenance.
- Thomas decides Tailwind must stay out of the codebase (switch to the headless-plus-own-CSS option).

## Status history
- 2026-11-17: Draft created by /explore
- 2026-11-17: Revised after the proof of concept (Preflight scoping, guard rule, bundle +60 KB gzip)
- 2026-11-17: Revised during /review-plan --arbitrate R1 (plan A): recorded the /settings routing, navigation, Settings-page and accent-token decisions
- 2026-11-18: **Finalised by `/verify-plan`** after Pass 3 — as-implemented section added (Syne headings, accent token values, core-and-plugins menu with /api/v1/me/shell, frame-level PageHeader, native model chip, bundle cap 400 KB).
- 2026-11-19: Plan B as-implemented section added by `/verify-plan` (all page bodies on the components, ratchets empty, Preflight back, touch floor in px).
