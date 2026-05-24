# AGENTS.md — Lumogis Agent Operating Guide

## Purpose

This file is a **repo-level operating guide** for coding agents working in Lumogis. It tells agents how work is **routed** (which skill or path to use) and what **guardrails** apply.

It does **not** replace `.cursor/skills` or `.cursor/rules`. If you need a detailed workflow, use the relevant skill. When uncertain, start with `/navigator`.

## Lumogis context pack

For Lumogis architecture, roadmap, release, Linear/Product OS, MCP, tools, permissions, KG/memory, Agentic Core, or implementation work, read:

- `docs/LUMOGIS_CONTEXT_PACK.md`

The context pack is the canonical repo-evidence onboarding summary for Cursor, ChatGPT, Claude, and other assistants. Do not duplicate it into guidance files. If it is stale, run or request **`/update-context-pack`**.

**This file (**`AGENTS.md`**) stays routing and guardrails;** the context pack carries **current orientation** distilled from repo/devtools evidence.

## Repo model

- **lumogis-app** is the product repo (application code, tests, Docker, shipping paths).
- **lumogis-devtools** holds Cursor skills, Product OS tooling, Linear-related scripts, registries, and reports.
- **lumogis-app/.cursor** is symlinked to **lumogis-devtools/cursor** (skills, rules, plans, and devtools-owned artefacts resolve through that link).
- **lumogis-public** or **upstream/main** (when present) is the **public AGPL export line**, not the same as the private product default remote by role.
- Do **not** treat devtools state as product runtime state.
- Do **not** commit local Cursor settings, generated caches, or ephemeral machine-specific blobs.

## Branch model

- **lumogis-app `dev`** — active integration branch for day-to-day product work.
- **lumogis-app `main` / `origin/main`** — clean **private** release line.
- **Public `upstream/main` or public `main`** — exported AGPL snapshot; **not** a normal byte-for-byte mirror of private `main`.
- **lumogis-devtools `main`** — active internal tooling branch.
- **`dev` and private `main` may intentionally diverge.**
- **Public and private `main` may have different commit hashes** even when content largely overlaps.
- Do **not** judge public/private alignment by Git ancestry alone; use release logs, export records, and file-level comparison when that matters.

## Workflow router

| Situation | Use |
| --------- | --- |
| Unsure what to do next | `/navigator` |
| Need branch topology, push plan, or cleanup | `/cleanup-and-audit-branches` |
| Need to review Cursor-created branches | `/review-cursor-branches` |
| Need to explore unclear scope | `/explore` |
| Assess an option, tool, or integration against Lumogis constraints (go/no-go before full `/explore`) | `/evaluate` |
| Planned implementation | `/create-plan` → `/review-plan` → implement → `/verify-plan` |
| Need drift check before closure | `/navigator drift` |
| Need Linear comment/status update | `/linear-update` |
| Useful work shipped without plan | `/record-retro` |
| Merge verified agent branch or worktree into `dev` after `run-workflow` or isolated implement | `/merge-workflow` |
| Promote scoped work from `dev` to private `main` | `/prepare-private-release-from-dev` |
| Publish private `main` to public AGPL | `/publish-private-main-to-public` |
| Refresh AI / bootstrap context summary (`docs/LUMOGIS_CONTEXT_PACK.md`) | `/update-context-pack` |

Do not invent a new workflow when an existing skill owns the task.

## Hard rules

- Do **not** merge all of `dev` into `main` unless Thomas explicitly asks for full `dev` promotion.
- Do **not** push private `main` directly to public.
- Do **not** mutate Linear except through `/linear-update` or an explicitly approved script.
- Do **not** commit secrets, tokens, private keys, API keys, or local credentials.
- Do **not** commit `cursor/settings.json`, `__pycache__/`, `node_modules/`, `.pytest_cache/`, build caches, or other generated local artefacts (unless explicitly intended and reviewed).
- Do **not** delete docs, branches, or remote branches without an explicit audit and approval.
- Actionable follow-ups, P2/P3 items, deferred work, optional hardening, and suggested future work need a **Linear outcome** (not orphan markdown TODOs as the system of record).
- Prefer **small, scoped** changes over broad cleanup.
- If public/private boundaries, auth, credentials, release/export, or destructive operations are involved, **stop** and route through the relevant skill.
- Do **not** claim verification success without command output or an explicit reason why a check could not run.

## Verification expectations

- Run **meaningful** tests based on touched paths.
- Backend/orchestrator changes: appropriate **pytest** / **compose** tests where the repo defines them.
- Web/client changes: appropriate **npm** test/build/lint checks where applicable.
- Release/export changes: the **release/public** verification checks defined by skills and scripts.
- Docs-only changes: at least **diff hygiene** and **source-of-truth** review (no fabricated “green” without basis).
- Before Linear **Done**, run `/verify-plan` and `/navigator drift` where applicable.
- If checks cannot run, record **why** and **classify the gap** (do not silently skip).

## Product OS / Linear rules

- **Linear** is the active backlog and status surface.
- Repo and devtools artefacts are **durable evidence** for what was decided and shipped.
- `/verify-plan` produces **closure evidence** tied to planned work.
- `/linear-update` applies **explicit** Linear comments/status changes (no ad-hoc API guessing).
- **P0** gaps block **Done**.
- **P1** gaps need **explicit acceptance**.
- **P2/P3** actionable follow-ups may be deferred only if **each** has a **Linear outcome**.
- Markdown-only future work is **not** a final backlog state.

## Common local noise

- `lumogis-devtools/cursor/settings.json` (or the same path via the app symlink) is usually **local Cursor config** — not product source.
- `orchestrator/plugins/graph/__pycache__/` is **generated Python cache**.
- These should **not** be committed unless explicitly intended.

## verify-public-rc environment requirements

- **Docker and UFW:** Docker publishes ports via iptables. On hosts using **ufw**, the default `FORWARD` policy can block container-to-container or published-port traffic until Docker chains are allowed. For a quick non-persistent check: `sudo iptables -I DOCKER-USER -j ACCEPT`. For a durable host fix, add an equivalent allow rule block to **`/etc/ufw/after.rules`** (see UFW + Docker documentation) so it survives `ufw reload`.
- **`make verify-public-rc`:** Safe on a busy developer machine when you set **`VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1`** (skips `integration-public-rc.sh` with a warning). Use only when a long-lived production-style stack would otherwise conflict; do not use this mode as the final gate before publish.
- **`make verify-public-rc-full`:** Expects a clean environment — stop conflicting stacks first. Treat this as the full pre-publish gate; integration always runs even if **`VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1`** is set in the environment.
- **`QDRANT_HOST_PORT`:** Host publish port for Qdrant defaults to **6334** in the main compose file. **`config/test.env.example`** sets **`QDRANT_HOST_PORT=6335`** for the **`lumogis-test`** compose project so the RC stack can run beside a default dev stack.

## Relationship to skills

- **AGENTS.md** — routing and guardrails only.
- **`.cursor/skills/`** — detailed workflows (implementation, release, review, retro).
- **`.cursor/rules/`** — Cursor-specific persistent rules loaded with the workspace.
- **`scripts/`**, **`scripts/linear`**, and similar — executable checks and tooling; follow skill/repo docs when invoking them.

Keep detailed procedures in **skills**, not in this file.
