# ADR-240: The LiteLLM overlay publishes its port on loopback only (LUM-880)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/litellm_overlay_loopback_retro.md`
**Draft mirror:** `.cursor/adrs/litellm_overlay_loopback.md`
**Linear:** LUM-880

## Context

The optional LiteLLM overlay (`docker-compose.litellm.yml`) published port 4000 on every interface. It set no master key, and it passed `ANTHROPIC_API_KEY` into the container. The setup guide told operators to enable it exactly like that. Anyone who could reach the host on port 4000 could send requests on the operator's paid key without logging in. On a host with the firewall off, that was the whole local network. The exposure was found while checking LiteLLM as a routing option for task model routing (LUM-230).

## Decision

The overlay publishes port 4000 on `127.0.0.1` only, and LiteLLM keeps running without a master key.

- Core never used the published port. It reaches the proxy as `http://litellm:4000` on the compose network, so the change does not affect Core.
- The dashboard stays available on the Lumogis host. From another machine, operators use an SSH tunnel, not a wider binding.
- No master key is set. With `proxy_url` set, `get_llm_provider` sends the provider key that `effective_api_key` resolves (the member's saved key, or the environment value when auth is off) as the request key. LiteLLM with a master key would reject that key, so every proxied call would fail. Supporting a master key needs a change in Core.
- `docs/extending/extending-the-stack.md` states the exposure, what not to do, why there is no master key, and that other containers on the compose network can still reach the proxy.
- `orchestrator/tests/test_litellm_overlay_loopback.py` fails if any `ports` entry of the `litellm` service binds beyond `127.0.0.1`.

## Alternatives considered

- **Require a master key.** Not chosen: Core cannot send one without a design change, as above. A keyed background model would also fail the ADR 026 startup check under `AUTH_ENABLED=true`.
- **Remove the port publish entirely.** Not chosen: the dashboard on the host is the main reason to run the overlay.
- **A host-port variable such as `LITELLM_HOST_PORT`.** Not chosen: nothing needs a different host port today, and a variable invites widening the binding.
- **A general rule that every optional overlay binds loopback.** Not chosen in this change: it needs an audit of each overlay, and some (Caddy) must publish on every interface.

## Consequences

- A LAN client can no longer spend the operator's Anthropic key through port 4000.
- Any container on the compose network can still call the proxy without authentication. This is documented, not enforced.
- The models in `config/litellm.yaml` call Anthropic with the `ANTHROPIC_API_KEY` from `.env`, so requests routed through them are billed to that key, whoever sent them.

## Revisit conditions

- LiteLLM must be reachable from another host: design master-key support in Core first, then widen the binding.
- Task model routing (LUM-877) adds LiteLLM as a task target, or a capability overlay ships next to it: reconsider the unauthenticated compose-network exposure.
- The LiteLLM image pin is lifted: re-check whether the new release changes auth defaults.

## Linear linkage (Product OS)

- **Existing issue:** LUM-880
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

The new test fails on the old `"4000:4000"` binding and passes on the fix. `docker compose config` renders the port with `host_ip: 127.0.0.1`, and the compose policy check passes on the base file plus the overlay. No live LiteLLM run was done.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
