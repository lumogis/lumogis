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

Everything else: stays on your machine.

## Comparison

| Product     | Analytics | Opt-out? |
| ----------- | --------- | -------- |
| Lumogis     | None      | N/A      |
| Thunderbolt | PostHog   | Yes      |
| Jan         | Opted-out | Yes      |
| Khoj Cloud  | Standard  | Yes      |
| ChatGPT     | Extensive | Limited  |
