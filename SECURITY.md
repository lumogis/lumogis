# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.3.x (current) | Yes |
| < 0.3.0 | No |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security bugs.** Public disclosure before a fix is available puts all users at risk.

Instead, email **lumogis@pm.me** with:

- A description of the vulnerability
- Reproduction steps (as minimal as possible)
- Your OS and Docker version
- Whether you believe it is already being exploited

We aim to acknowledge all reports within **48 hours** and to provide a fix or mitigation plan within **14 days** for confirmed vulnerabilities.

## What to Expect

1. We confirm receipt of your report.
2. We investigate and confirm the vulnerability.
3. We develop and test a fix in a private branch.
4. We coordinate a disclosure date with you.
5. We publish the fix and credit you in the release notes (unless you prefer to remain anonymous).

## Credit Policy

Reporters who responsibly disclose valid vulnerabilities will be credited in the `CHANGELOG.md` entry for the fixing release, unless they request otherwise.

## Scope

In scope:
- `orchestrator/` — all Python services, routes, and adapters
- `mcp-servers/` — MCP server implementations
- `postgres/` — SQL schema and init scripts
- Docker Compose stack configuration
- Authentication and permission boundaries

Out of scope:
- Vulnerabilities in third-party dependencies (report upstream)
- Denial of service via resource exhaustion on self-hosted instances
- Issues requiring physical access to the host machine

## Security Design Notes

For a detailed record of the initial security audit (SQL injection, path traversal, MCP boundary, Ask/Do boundary), see [`docs/SECURITY-AUDIT-001.md`](docs/SECURITY-AUDIT-001.md).
