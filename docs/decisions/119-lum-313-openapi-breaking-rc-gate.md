# ADR-119: OpenAPI breaking check in verify-public-rc (LUM-313)

**Status:** Finalised

**Created:** 2026-06-22

**Last updated:** 2026-06-22

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-22

**Issue:** [LUM-313](https://linear.app/lumogis/issue/LUM-313)

**Related:** [ADR-061](061-lum-303-public-ci-parity-openapi-check-via-export.md), [ADR-060](060-lum-302-openapi-breaking-change-classifier.md)

## Context

OpenAPI breaking classification ran in PR CI but not in the RC umbrella. LUM-313 wires parity into **`make verify-public-rc`**.

## Decision

**Makefile** — `verify-public-rc` invokes openapi-breaking-check with the same contract as CI (documented in **`docs/testing/automated-test-strategy.md`**).

## Consequences

- **Easier:** RC promotion catches breaking OpenAPI drift before private `main`.
- **Harder:** RC runs slightly longer.

## Status history

- 2026-06-22: Finalised by `/record-retro` — on `dev` @ `c1d30c1d4`.
