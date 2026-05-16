# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.3.x (current) | Yes |
| < 0.3.0 | No |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security bugs.** Public disclosure before a fix is available puts all users at risk.

### GitHub Private Vulnerability Reporting

If you have a GitHub account, you may also use **GitHub Private vulnerability reporting** on **`lumogis/lumogis`** when it is enabled. This keeps the thread on GitHub’s platform and is subject to **GitHub’s terms and data handling**.

### Email reporting

Email **lumogis@pm.me** (ProtonMail). This path is intended to stay available even when you cannot or prefer not to use GitHub.

Include in your message:

- A description of the vulnerability
- Reproduction steps (as minimal as possible)
- Your OS and Docker version
- Whether you believe it is already being exploited

**Choose whichever channel fits your threat model** — either is acceptable for valid reports.

**Do not open a public GitHub issue** for undisclosed security bugs.

We aim to acknowledge all reports within **48 hours**. After acknowledgement, we provide **status updates at least every 14 days** while remediation work is ongoing.

For **confirmed** vulnerabilities we target issuing a coordinated fix or advisory within **90 days** by default. Some fixes take longer when they span multiple components — we explain extensions and revised timelines rather than silently delaying.

If we judge there is **material risk of active exploitation** (maintainer judgement informed by credible signals such as in-the-wild exploitation or authoritative third-party confirmation), we **accelerate** coordination toward earlier disclosure.

These timelines describe **maintainer intent**, not an undertaking that binds Lumogis in every jurisdiction — volunteer bandwidth may occasionally delay responses.

If you have not heard back within **14 days** of your initial mail, please **send one polite follow-up** (spam filters happen).

Concurrent reports may receive **bundled status updates** when effort is constrained.

## What to Expect

1. We confirm receipt of your report.
2. We investigate and confirm the vulnerability.
3. We develop and test a fix in a private branch.
4. We coordinate a disclosure date with you.
5. We publish the fix and, **if you requested credit**, credit you as above; otherwise we keep public credit **anonymous by default**.

## Credit Policy

If you **ask to be credited**, we will include your chosen name/handle (and optional link) in `CHANGELOG.md` and in the GitHub Security Advisory body when one is published.

If you **do not** ask for credit, we default to **anonymous** acknowledgement in those public surfaces (subject to mandatory legal disclosure).

## Coordinated disclosure

Please **do not publicly disclose** details of a vulnerability until we agree a coordinated release date that protects users.

We work towards **coordinated publication** alongside the fix or advisory. Default target is **90 days** after acknowledgement for confirmed issues, with faster timelines when exploitation risk warrants it, and documented extensions when fixes are unusually complex.

If we cannot meet a timeline, we explain why and propose next steps rather than going silent.

## Safe harbour for security research

This section is **not legal advice**. It summarises **community norms** we follow for **good-faith** security research coordinated through this policy.

If you comply with this policy’s scope and reporting instructions, **we intend not to pursue legal action against you** solely for testing and reporting activities that we reasonably believe were **necessary** to discover and responsibly disclose a security issue.

Good-faith research **does not** include (for example): harming people or accessing their data without authorisation; destructive testing against systems you neither own nor operate with explicit permission; sustained denial-of-service attacks; violating applicable law; harvesting unrelated personal data.

We may still collaborate with authorities for **bad-faith** or **out-of-scope** activity.

## Reporter information

Your report may include contact details and reproduction materials. Access is limited to **maintainers triaging and fixing the issue**. We retain report content **only as long as reasonably necessary** to remediate and publish a coordinated disclosure, except where law requires otherwise.

We currently use **`lumogis@pm.me`** for **both** vulnerability reports and **Code of Conduct** enforcement mail. They are handled under **different processes** — please put “**security:**” in the subject for vulnerability mail.

## Scope

In scope:

- `orchestrator/` — all Python services, routes, and adapters
- `mcp-servers/` — MCP server implementations
- `postgres/` — SQL schema and init scripts
- Docker Compose stack configuration
- Authentication and permission boundaries
- `clients/lumogis-web/` — Lumogis Web SPA surface
- `docker/caddy/Caddyfile` — edge / reverse-proxy configuration we ship
- **GitHub Actions workflows, release automation, or registry-integrity concerns** affecting this repository are **accepted** through the **same private channels** above; **hard supply-chain guarantees** mature on **LUM-227** / **LUM-228** timelines.

Out of scope:

- Vulnerabilities in third-party dependencies (report upstream)
- Denial of service via resource exhaustion on self-hosted instances
- Issues requiring physical access to the host machine

Telemetry disclosure is tracked for release under **LUM-217**; a dedicated TELEMETRY.md may ship in the same programme.

## Security Design Notes

For themes covered in the initial security review (SQL injection, path traversal, MCP boundary, Ask/Do boundary), see **`docs/LUMOGIS_REFERENCE_MANUAL.md`** and the cited **ADRs** under **`docs/decisions/`**.
