# Remote access

## Overview

Lumogis runs on a home server or workstation. By default it is reachable only on the local network (for example **http://localhost/** or **http://192.168.x.x/**). This guide covers how to give household members secure access from any device, including mobile, when they are off the LAN.

**Lumogis Search** (Persona A/B) uses the same server URL you configure in first-run onboarding — see [`clients/lumogis-search/README.md`](../../clients/lumogis-search/README.md).

## Why HTTPS is required

Lumogis Web is a progressive web app (PWA). The service worker (`clients/lumogis-web/src/pwa/sw.ts`) and Web Push notifications require a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)—HTTPS in practice. Plain HTTP access prevents PWA installation and disables push notifications. Both **Tailscale Serve** and **Cloudflare Tunnel** terminate HTTPS for you automatically, so household devices get a working install and push path without exposing raw port forwarding on your router.

## Option A: Tailscale Serve (recommended)

**Why recommended:** Traffic stays on your private Tailscale mesh—nothing is published to the open internet. Tailscale Serve is free for personal use, provisions HTTPS certificates automatically, and needs no router port forwarding.

**Prerequisites:** [Tailscale](https://tailscale.com/download) installed on the Lumogis host and on each household member's phone, tablet, or laptop. A free Tailscale account for the operator (household members join the same tailnet via invite).

**Setup:**

1. Install Tailscale on the Lumogis host and authenticate (see [Tailscale download](https://tailscale.com/download)). On Linux, the install script plus `sudo tailscale up` is typical.
2. With the Lumogis stack running and Caddy listening on port 80, expose it to your tailnet:

   ```bash
   tailscale serve --bg http://127.0.0.1:80
   ```

   (`--bg` keeps Serve running after you close the shell. Tailscale proxies only `127.0.0.1` backends.)

3. Each household member installs Tailscale on their device and joins the same tailnet. They open Lumogis at **`https://<hostname>.<tailnet>.ts.net`** (for example `https://homelab.tailabc123.ts.net`).

The exact hostname appears in the [Tailscale admin console](https://login.tailscale.com/admin/machines) or via `tailscale status` on the host. When you enable auth (`AUTH_ENABLED=true`), set **`LUMOGIS_PUBLIC_ORIGIN`** in `.env` to that HTTPS URL so cookies and CSRF checks match the browser origin.

**Reference:** [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve)

## Option B: Cloudflare Tunnel (alternative)

**When to use:** You need a quick one-off preview, or Tailscale is not viable for your household.

**Trade-off:** Traffic transits Cloudflare's network. The tunnel is encrypted, but it is not the same end-to-end private mesh model as Tailscale.

**Quick tunnel (no Cloudflare account):** With Lumogis reachable at **http://localhost/** (Caddy on port 80):

```bash
cloudflared tunnel --url http://localhost:80
```

`cloudflared` prints a temporary **`https://<random>.trycloudflare.com`** URL. Share that link only with people you trust; the URL stops working when the `cloudflared` process exits. Quick tunnels are for testing—not production uptime. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

**Named tunnel (free Cloudflare account):** For a stable hostname on a domain you control, follow Cloudflare's [Create a tunnel (dashboard)](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/) guide and point the tunnel service URL at **`http://localhost:80`**.

### Rate limits behind Tailscale Serve or Cloudflare Tunnel

Lumogis keys its per-IP login, password-reset and invite limits on the client address. Caddy decides that address and trusts no upstream proxy by default, so behind Tailscale Serve or a tunnel **every device shares one per-IP bucket** (one household member's failed logins can lock the others out for a minute). To give each device its own bucket, tell Caddy which upstream to believe with `LUMOGIS_CADDY_TRUSTED_PROXIES` in `.env`:

- **Proxy on the host** (Tailscale Serve, `cloudflared` installed on the host, a host nginx): find the compose gateway with `docker compose exec caddy ip route show default` (the address after `via` on the `default` line; the second line it prints is the network, not the gateway) and set `LUMOGIS_CADDY_TRUSTED_PROXIES=<gateway>/32`. This also trusts every process on the host. The gateway belongs to the compose network, so `docker compose down` followed by `up` can recreate it with a different address: re-check it then, or the value silently stops matching.
- **`cloudflared` as a container on the Lumogis network:** it reaches Caddy from its own, changing container IP, not the gateway. Either run it on the host instead, or trust the network's subnet (`docker network inspect <project>_default --format '{{(index .IPAM.Config 0).Subnet}}'`). The subnet trusts **every** container on that network to set the client IP, including capability containers attached to it.
- Values are **space-separated** CIDRs, quoted, for example `LUMOGIS_CADDY_TRUSTED_PROXIES="172.18.0.1/32 100.64.0.0/10"`. **Commas break it.**
- Check a new value **before** restarting Caddy: `docker compose run --no-deps --rm caddy caddy validate --config /etc/caddy/Caddyfile && echo ok`. A malformed value (a comma, a typo) stops Caddy, and with it the web UI.
- Then restart Caddy: `docker compose up -d caddy`.
- **Docker Desktop:** do not trust the gateway. Every LAN client reaches Caddy through it there, so any device on your network could choose the address its login limit keys on.
- **`make doctor`** prints the current gateway (row `caddy-gateway`) and checks the value (row `caddy-trusted-proxies`): an error when Caddy would refuse it, a warning when it no longer includes the gateway (for example after `down` and `up`) or trusts the gateway on Docker Desktop.

Core itself trusts only the Caddy container (`LUMOGIS_TRUSTED_PROXIES=caddy`, the compose default); do not widen that list for a proxy in front of Caddy.

## Option C: LAN only (mDNS)

On many home networks, **Avahi** or **mDNS** advertises a friendly hostname such as **`lumogis.local`**, so you can open the UI without remembering an IP address while on Wi‑Fi. That convenience is LAN-only: it does not reach phones on cellular or laptops on other networks, and it does not provide HTTPS. Use this path for at-home browsing only; pair it with Option A or B when household members need off-LAN or mobile access.

## Security guidance

- **Never forward port 8000.** It is published on `127.0.0.1` only; reach Core through Caddy (port 80 or your tunnel).
- **Do not** expose Lumogis directly to the public internet (no raw router port forwarding to port 80 or 8000). Unauthenticated or weakly authenticated instances on the open internet are an easy target.
- **Tailscale Serve** is the preferred path because it keeps the service off the public internet while still delivering HTTPS to trusted devices on your tailnet. **Tailscale Funnel** publishes the same `*.ts.net` name to the whole internet: turn sign-in on before you enable it.
- **Sign-in off on a public hostname is flagged.** When `AUTH_ENABLED=false` and `CADDY_DOMAIN` names a public hostname, the Caddy container prints a WARNING block each time it starts (`docker compose logs caddy`). It does not refuse to start. Names under `.local`, `.lan`, `.home.arpa`, `.internal`, `.test`, `.localhost` and `.ts.net`, single-label names and private IP addresses count as private. Caddy cannot see a tunnel or a router port forward, so a public install with `CADDY_DOMAIN=:80` gets no warning.
- **LibreChat** (the optional `librechat` profile) is published on `127.0.0.1:3080` only and is not behind Caddy. Use Lumogis Web from other devices.
- If you run a **named Cloudflare Tunnel** on a public hostname, treat the instance as internet-adjacent: enable Lumogis auth, use strong credentials, and consider [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) to restrict who can reach the URL when the stack holds sensitive personal data.
