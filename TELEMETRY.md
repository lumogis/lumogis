# Telemetry

## Lumogis collects nothing.

Not because we disabled analytics.
Not because we turned off a setting.
Because there is no mechanism.

Lumogis runs on your hardware. Your documents, your voice captures,
your sessions — they live on your machine.
There is no Lumogis server to send data to.

## How to verify

1. The orchestrator source code is published under AGPL v3:
   https://github.com/lumogis/lumogis
2. Search the codebase for any analytics libraries:
```bash
   grep -r "posthog\|mixpanel\|amplitude\|segment\|analytics" orchestrator/
```
   Expected result: no matches.
3. Run Lumogis with a network monitor (Wireshark, Little Snitch, mitmproxy)
   and observe: no unexpected outbound connections.

## What does leave your machine (with your knowledge)

When you configure a cloud LLM provider (Anthropic, OpenAI, etc.)
and use cloud routing:
- Document excerpts are sent to that provider's API for processing
- This is disclosed in the UI before each cloud-routed query
- You can disable cloud routing entirely in Admin → Privacy Mode (`/admin/privacy-mode`)

When you use Tailscale for remote access:
- Your device registers with Tailscale's coordination server
- Traffic itself is peer-to-peer encrypted
- This is Tailscale's behaviour, not Lumogis's

When speech-to-text is on (it is in an install from the Release files):
- The speech engine (Speaches) downloads the speech model from `huggingface.co`
  the first time Lumogis installs it, and again if you reset it, and checks the
  model for updates when it starts. These requests name the model; they carry no
  audio, transcript or Lumogis data. The engine's own usage reporting is switched
  off (`HF_HUB_DISABLE_TELEMETRY=1`). Measured with the engine's network traffic
  captured over a restart and two transcriptions: `huggingface.co` was the only
  destination.
- Turn it off in `.env` (`STT_BACKEND=none`, remove `stt` from
  `COMPOSE_PROFILES`) and nothing is contacted; see
  [Speech-to-text](docs/deployment/quickstart.md#speech-to-text).
- `make verify-no-telemetry` checks Lumogis's own code in `orchestrator/`, not
  this engine.

When you install or update Lumogis:
- Docker downloads the Lumogis images from `ghcr.io` and the other images
  (PostgreSQL, Qdrant, Ollama and the speech engine) from Docker Hub and `ghcr.io`.
- On first start Lumogis asks Ollama to download two local models from Ollama's
  model registry: the search model (`nomic-embed-text`) and a small chat model
  (`llama3.2:3b`). After that they run offline. Leave out the chat model with
  `OLLAMA_EXTRA_MODELS=` (empty) in `.env`.

When an admin opens the update notice in Admin → System status:
- Lumogis asks GitHub (`api.github.com`) for the newest Lumogis release, at most
  once an hour. The request names only the Lumogis repository; it carries no
  data about you or your install. Switch it off with
  `LUMOGIS_UPDATE_CHECK_ENABLED=0`.

When you turn on the secure address (Admin → Secure address; off by default):
- Lumogis registers with Lumogis's name service (`dns.homes.lumogis.app`), sending
  this computer's public device key and its home-network address (for example
  `192.168.1.20`). The name and that address become visible in public DNS.
- Caddy gets the certificate from Let's Encrypt, so the name appears in public
  certificate logs. See [Secure address at home](docs/deployment/remote-access.md#secure-address-at-home).
- Turning it off removes the name; nothing is contacted while it stays off.

When you connect something yourself (all off until you set them up):
- A mailbox (IMAP and SMTP), Paperless, a news feed or web page, a cloud AI
  provider, ntfy or browser notifications: Lumogis talks to the server you named,
  and only to that server. Browser notifications go through your browser
  maker's push service.

Everything else stays on your machine. Lumogis has no analytics and sends nothing
about how you use it to anyone.

## Comparison

| Product     | Analytics | Opt-out? |
| ----------- | --------- | -------- |
| Lumogis     | None      | N/A      |
| Thunderbolt | PostHog   | Yes      |
| Jan         | Opted-out | Yes      |
| Khoj Cloud  | Standard  | Yes      |
| ChatGPT     | Extensive | Limited  |
