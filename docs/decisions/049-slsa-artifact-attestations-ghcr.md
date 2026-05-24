# ADR 049 — SLSA artifact attestations for GHCR published images

**Status:** Finalised  
**Date:** 2026-05-17  
**Issue:** [LUM-228](https://linear.app/lumogis/issue/LUM-228/slsa-artifact-attestations-for-ghcr-published-images)  
**Related:** ADR 036 (GHCR multi-arch + compose overlay, LUM-192); ADR 037 (GHCR publish from public repo only, LUM-225); LUM-227 (CODEOWNERS on workflows — adjacent governance control)

## Context

ADR 036 established multi-arch GHCR publishing and the `docker-compose.ghcr.yml` overlay. ADR 037 narrowed the trust boundary so those images are only built from the verified public `lumogis/lumogis` tree and pinned every third-party Action in `publish-image.yml` to an immutable commit SHA. Neither ADR adds a cryptographic chain that a consumer of `ghcr.io/lumogis/lumogis-orchestrator:<tag>` or `ghcr.io/lumogis/lumogis-web:<tag>` can verify after `docker pull`. `SECURITY.md` defers "hard supply-chain guarantees" to "LUM-227 / LUM-228 timelines." LUM-228 is the attestation half of that pair.

The relevant constraint is project-shape, not technology: Lumogis is a single-maintainer AGPL household-AI project publishing two images from one public repo. The threat model is "this image really came from `lumogis/lumogis` at the SHA the release log claims," not "an arbitrary builder in a multi-tenant environment cannot forge inputs." That keeps SLSA Level 2 as the honest target and lets the heavier SLSA Level 3 container generator stay a future option.

## Decision

1. **Emit a SLSA build provenance attestation per published image** in `lumogis/lumogis/.github/workflows/publish-image.yml` using **`actions/attest-build-provenance@<immutable-sha>`** (pinned release; v4.1.0 at implementation), keyed by the digest output of the existing `docker/build-push-action` step. One attestation step per published image (`lumogis-orchestrator`, `lumogis-web`). **Transitive actions:** pinning **`actions/attest-build-provenance@<sha>`** is the **atomic pin unit** under ADR 037 (GitHub releases the composite as one unit); add explicit inner-action pins only if a **GHSA** requires it.
2. **Keep `docker/build-push-action`'s in-manifest provenance enabled at `mode=max`** explicitly (it is the default for public repos but should be set deliberately so a future default change does not silently regress us). This is complementary — `gh attestation verify` consumes the attest-action output; `cosign` / `docker buildx imagetools inspect` consumers can still read the in-manifest predicate.
3. **Grant the workflow the minimum permissions required:** `id-token: write`, `attestations: write`, `packages: write`, `contents: read` — scoped **per publish job** in YAML (never workflow-wide for OIDC/attestations). No org-level changes; ADR 037's "guard: `github.repository == 'lumogis/lumogis'`" remains the fork guard.
4. **SLSA Level 2 is the deliberate starting posture.** SLSA Level 3 via `slsa-framework/slsa-github-generator` is explicitly **not** adopted now; see Revisit conditions.
5. **Image signing via `cosign sign` is out of scope** for LUM-228 (it solves a different problem). May be layered later as a separate decision if warranted.
6. **Document the verifier recipe in `docs/capabilities.md`** (one short "Verifying image provenance" subsection showing `gh attestation verify oci://ghcr.io/lumogis/lumogis-orchestrator:<tag> -R lumogis/lumogis`) and **update `SECURITY.md` + `.github/SECURITY.md`** to retire the "LUM-228 deferral" wording once the workflow ships.

## Alternatives Considered

See `.cursor/explorations/LUM-228-slsa_attestations_ghcr.md` for the full comparison table.

- **`docker/build-push-action` provenance alone (Option 2)** — does not match LUM-228's explicit `gh attestation verify` acceptance criterion; kept on as a complement, not a replacement.
- **`slsa-framework/slsa-github-generator` container workflow (Option 3)** — SLSA L3 but materially heavier (isolated builder job, second reusable workflow to pin per ADR 037, reverification of ADR 037's RC gate matrix). Marginal trust gain over Option 1 is modest for a single-maintainer public AGPL release pipeline; recorded as a Revisit condition.
- **`cosign sign` keyless (Option 4)** — adds image authenticity but not a SLSA provenance predicate. Wrong artefact for this ticket.

## Consequences

**Easier**

- Self-hosters and downstream packagers can verify provenance with **one** GitHub-CLI line, no extra tooling install beyond `gh`.
- ADR 037's "trusted source repo" boundary acquires a verifiable cryptographic counterpart — the trust claim is no longer "you have to trust our release log."
- LUM-261 (future `lumogis-graph` private GHCR publish) has a known pattern to copy when it ships.

**Harder**

- Future regression of the attestation step is now a visible production concern; verifying the recipe in `docs/capabilities.md` becomes part of the implicit RC checklist.
- `publish-image.yml` carries one additional immutable-SHA pin to rotate as the action releases.
- **Public transparency:** each successful attestation writes an **immutable public Rekor** entry tying the **image digest** to **workflow identity** (repository, ref, workflow path, builder metadata). No image layers are published; this is the standard **public-good Sigstore** posture for a public AGPL pipeline. Downstream **LUM-261** (private-repo GHCR) must **explicitly accept** the same public-Rekor visibility for digests before copying this pattern.

**Not changed**

- No runtime application code, no Docker Compose surface, no Python dependency, no `.env` variable.
- ADR 037's trust boundary, fork-guard, SHA-pin discipline, and Makefile `verify-public-rc*` gates remain authoritative.
- Public-repo / private-repo split: the workflow lives in `lumogis/lumogis`; the docs that reference it live in `lumogis-app` and `lumogis-devtools`.

## Revisit conditions

Revisit this decision in the direction of SLSA Level 3 (Option 3) **if any** of the following becomes true:

- A downstream consumer (distribution, enterprise self-hoster, security review) requests SLSA L3 attestations explicitly and L2 is documented as insufficient for their evaluation.
- We adopt a multi-builder publish topology where the build job and the provenance job can no longer be trivially trusted as one unit (e.g. reusable workflows owned by different teams, contributed self-hosted runners).
- A material vulnerability in `actions/attest-build-provenance` itself or the GitHub attestations API changes the cost-benefit balance.
- We start publishing more than three images from `lumogis/lumogis` and the per-image attest steps would benefit from being consolidated into a single reusable SLSA-L3 generator call.

Revisit the docs-only choices (which CLI to lead with in `docs/capabilities.md`) **if** the `gh` CLI's `attestation` subcommand changes shape materially, or if `docker buildx imagetools inspect --format` becomes the more ergonomic path for a typical Lumogis operator.

Do **not** revisit purely because a newer major version of `actions/attest-build-provenance` ships — bump the immutable SHA pin under the existing ADR 037 discipline; no ADR change needed unless the action's contract changes.

## Status history

- 2026-05-16: Draft created by `/explore --headless LUM-228` (Claude Opus 4.7).
- 2026-05-16: Revised during `/review-plan --arbitrate` R1 — Composer: job-scoped permissions language; **composite pin stance** (wrapper SHA as atomic unit unless GHSA); **public Rekor / Sigstore** consequence for releases; **LUM-261** disclosure note.
- 2026-05-17: **Finalised** in `docs/decisions/` as ADR 049 — public `publish-image.yml` attestation wiring merged on `lumogis/lumogis`; product documentation and verification evidence aligned with plan LUM-228.
