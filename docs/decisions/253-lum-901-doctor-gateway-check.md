# ADR-253: `make doctor` checks Caddy's trusted proxy against the compose gateway; no zero-config gateway trust (LUM-901)
**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10
**Decided by:** as-shipped implementation (retrospective), `exec:claude` session for LUM-901
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/doctor_caddy_gateway_check_retro.md`
**Draft mirror:** `.cursor/adrs/doctor_caddy_gateway_check.md`
**Linear:** LUM-901 (follow-up of LUM-887; P2 gap from its /verify-plan 2026-11-09)

## Context

Since [ADR 246](246-lum-887-deployment-hardening.md), Caddy trusts no upstream proxy by default (`LUMOGIS_CADDY_TRUSTED_PROXIES=127.0.0.1/32`). Behind Tailscale Serve, Cloudflare Tunnel or another proxy on the host, every device therefore shares one per-IP bucket for login, password reset and invites. Each device gets its own bucket again once the operator sets the knob to the compose gateway `/32`. Two things go wrong with that setting:

- The gateway can change when the compose network is recreated (`down`, then `up`). A stale value silently brings the shared bucket back.
- A malformed value, for example one with a comma, stops Caddy and with it the web UI.

ADR 246 also left a revisit condition: adopt zero-config trust of the compose gateway if households report shared lockouts, excluding Docker Desktop, where every LAN client arrives through the gateway. LUM-901 asked for a doctor check and a decision on that condition.

## Decision

1. **Doctor check.** `scripts/doctor/checks/client-ip.sh` (run by `scripts/doctor/run.sh` after `network.sh`) adds two rows in the `network` category:
   - **`caddy-gateway`**: the address after `via` on the `default` line of `docker compose exec -T caddy ip route show default`. BusyBox prints the subnet line too, and that line is ignored. The row is `skipped` when Caddy is not running or the route cannot be read.
   - **`caddy-trusted-proxies`**: the configured value, read from `docker compose config`, so it is the value Caddy gets on its next start. Missing means `127.0.0.1/32`.
     - **`error`** when Caddy would refuse the value. A host-side parser mirrors Caddy's static IP source (`netip.ParsePrefix` / `ParseAddr`, whitespace-separated, `private_ranges`), so the check still works while a bad value keeps Caddy down. When Caddy runs, the row also runs `caddy validate` inside it, with the value passed as `-e`. A refusal (exit 1) is an error, and the row shows Caddy's `msg`.
     - **`warn`** when the value does not include the current gateway. Behind a proxy on the host, that means the value is stale. The remediation names the `/32` to set and says to ignore the warning when the value names a proxy on another machine.
     - **`warn`** when the value includes the gateway on **Docker Desktop** (`docker info --format '{{.OperatingSystem}}'` contains "Docker Desktop"). There any LAN device could choose its rate-limit IP.
     - **`ok`** otherwise. With the default value the message names the `/32` to set behind a host proxy, except on Docker Desktop, where it says not to.
2. **Read-only.** The check only uses `docker compose exec` into a running Caddy. It never uses `docker compose run`, which would create a container, and it never restarts anything. That keeps doctor's read-only default (ADR 061).
3. **No zero-config gateway trust.** Caddy keeps trusting no upstream by default. The revisit condition in ADR 246 stays open, unchanged, and is restated below. Reasons:
   - No household has reported a shared lockout yet, so the trigger has not fired. Per-email limits are unaffected.
   - Caddy's `trusted_proxies static` cannot name "my gateway". Adopting it would need a start-time wrapper that computes the gateway, which is the extra moving part in the front door that ADR 246 rejected for commas, or a custom Caddy IP-source module and image.
   - The Docker Desktop exclusion cannot be decided reliably inside the Caddy container. A wrong guess hands every LAN device the choice of its rate-limit IP, which is finding ID-007 again. Doctor runs on the host and can ask `docker info`, so the diagnosis lives there.
   - Trusting the gateway also trusts every process on the host. Doing that should stay a choice the operator makes and can see in `.env`.

## Alternatives Considered

Not chosen at ship time:
- **Zero-config gateway trust now** (wrapper entrypoint or custom module): see Decision 3.
- **Mismatch as `error`:** a proxy on another machine is a legitimate value that need not include the gateway. An error would make `make doctor` exit 2 permanently on such installs.
- **Validate with `docker compose run --no-deps --rm caddy caddy validate`** (the documented manual pre-check): it creates a container, which a read-only doctor run should not do, and the host-side parser already covers a stopped Caddy.
- **Host-side parser only:** it would miss a refusal for a reason the parser does not model. `caddy validate` stays authoritative whenever Caddy runs.
- **Comparing the running Caddy's environment with `.env`** (edited without `up -d caddy`): useful, but outside LUM-901's "Done when". It is recorded as an in-code note in `client-ip.sh` and in `scripts/doctor/README.md`.

## Consequences

- **Easier:** after a shared-lockout report, an operator runs `make doctor` and sees the gateway, whether the knob matches it, and whether Caddy accepts the value, without reading the LUM-887 plan. A comma value is reported as an error even while Caddy is down.
- **Harder:**
  - Installs whose knob names a proxy on another machine get a permanent `warn` (exit 1). The remediation text says to ignore it.
  - Docker Desktop installs that trust the gateway get a `warn`.
- **Future chunks must know:**
  - The parser in `client-ip.sh` must keep matching Caddy's static IP source. A Caddy upgrade that changes what `trusted_proxies static` accepts needs a matching change there. `caddy validate` covers the running case.
  - The doctor JSON contract stays v1. The new rows only add `checks[]` entries in the existing `network` category.
  - Rootless Docker's port driver may also present every client from one address. That is not detected (in-code note).

## Revisit conditions

- If Tailscale Serve or tunnel users report shared rate-limit lockouts, adopt zero-config trust of the compose gateway in Caddy, with Docker Desktop excluded. This carries ADR 246's condition forward. Doctor's Docker Desktop detection (`docker info`) is the host-side half; the container side needs its own reliable signal.
- If Caddy changes what `trusted_proxies static` accepts, update the parser in `client-ip.sh`.
- If operators report the "proxy on another machine" warning as noise, add an explicit opt-out rather than turning the row into `ok`.

## Linear linkage (Product OS)

- **Existing issue:** LUM-901 (`type:improvement`, `area:release`, `area:docs`, `exec:claude`), follow-up of LUM-887.
- **New issue needed:** no. The zero-config trust revisit stays a condition here and in ADR 246.
- **Historical evidence only:** no; LUM-901 is active and closes with this work.

## Testing retrospective

- **Added:** 22 tests in `orchestrator/tests/test_doctor_cli.py`. The `docker` stub gains `docker info`, `exec caddy ip route` and `exec caddy caddy validate` cases. The tests cover the gateway parse, the default value, matching values, a wide range, a stale gateway, malformed values (comma, `/33`, a name, `/032`) with Caddy up and down, a `caddy validate` refusal, Docker Desktop with and without the gateway, Caddy stopped, an unreadable route, and the wiring and exec bit. All 22 fail with the check unwired.
- **Run:** `AUTH_ENABLED=false pytest tests/test_doctor_cli.py` → 102 passed, 3 skipped (pre-existing jq/docker PATH skips). The related static suites (`test_deployment_hardening_config.py` and four more) → 200 passed, 3 skipped. `make lint` → 0. `make exec-bit-check` → 0. `make changelog-check` → 0. `shellcheck` on `client-ip.sh` → 0.
- **Live:** a throwaway one-service compose project (`lum901probe`, `caddy:2-alpine`, the repo Caddyfile), removed afterwards. BusyBox printed `default via 172.19.0.1 dev eth0` plus the subnet line, and the row reported `172.19.0.1`. Default, stale, matching and comma values gave ok, warn, ok and error. With Caddy stopped, the gateway row was skipped and the value row was ok. `caddy validate` via `exec -e` returned 1 for a comma value and for `/032`, and 0 for `private_ranges`, a bare IP and host bits set (`172.19.0.5/16`). These match the parser.
- **Not run:**
  - A Docker Desktop host (none available; stubbed).
  - `make compose-test-doctor`, which would start a `lumogis-test` stack and was excluded tonight. CI's path-gated `doctor-integration` job runs it, because the change touches `scripts/doctor/**`.
  - The full orchestrator suite (no orchestrator Python changed).

## Status history
- 2026-11-10: Finalised by /record-retro (retrospective).
