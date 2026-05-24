# Remote access

## Overview

Lumogis runs on a home server or workstation. By default it is reachable only on the local network (for example **http://localhost/** or **http://192.168.x.x/**). This guide covers how to give household members secure access from any device, including mobile, when they are off the LAN.

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

## Option C: LAN only (mDNS)

On many home networks, **Avahi** or **mDNS** advertises a friendly hostname such as **`lumogis.local`**, so you can open the UI without remembering an IP address while on Wi‑Fi. That convenience is LAN-only: it does not reach phones on cellular or laptops on other networks, and it does not provide HTTPS. Use this path for at-home browsing only; pair it with Option A or B when household members need off-LAN or mobile access.

## Security guidance

- **Do not** expose Lumogis directly to the public internet (no raw router port forwarding to port 80 or 8000). Unauthenticated or weakly authenticated instances on the open internet are an easy target.
- **Tailscale Serve** is the preferred path because it keeps the service off the public internet while still delivering HTTPS to trusted devices on your tailnet.
- If you run a **named Cloudflare Tunnel** on a public hostname, treat the instance as internet-adjacent: enable Lumogis auth, use strong credentials, and consider [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) to restrict who can reach the URL when the stack holds sensitive personal data.
