# Remote access

## Overview

Lumogis runs on a home server or workstation. By default it is reachable only on the local network (for example **http://localhost/** or **http://192.168.x.x/**). This guide covers how to give household members secure access from any device, including mobile, when they are off the LAN.

**Lumogis Search** (Persona A/B) uses the same server URL you configure in first-run onboarding — see [`clients/lumogis-search/README.md`](../../clients/lumogis-search/README.md).

## Why HTTPS is required

Lumogis Web is a progressive web app (PWA). The service worker (`clients/lumogis-web/src/pwa/sw.ts`) and Web Push notifications require a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)—HTTPS in practice. Plain HTTP access prevents PWA installation and disables push notifications. At home, the free **secure address** below provides HTTPS. Away from home, **Tailscale Serve** and **Cloudflare Tunnel** terminate HTTPS for you automatically, so household devices get a working install and push path without exposing raw port forwarding on your router.

## Secure address at home

**Settings → Home address** gives this Lumogis a free `https://` name for use **on your home network**, for example `https://kq7f3r2a….homes.lumogis.app`. Phones and laptops on your home Wi-Fi open it with a real certificate, so installing the app, voice input and notifications work without installing anything on them. It does not work away from home: for that, use Option A or B below.

**Turn it on:**

1. Open **Settings → Home address**.
2. Lumogis looks for this computer's home network address when the page opens and suggests it (the address your phone uses to open Lumogis, for example `192.168.1.20`). Check it, tick the confirmation and press **Turn on secure address**. On Docker Desktop, Lumogis cannot see the address, and the page shows how to find it (`ipconfig getifaddr en0` in a Mac's Terminal, `ipconfig` on Windows); or open Lumogis at `http://<that address>` instead of `localhost` and it fills the address in.
3. Wait a minute or two while Lumogis gets a name and a certificate. The page shows the address when it is ready, with **Copy address** and **Check on this device**.
4. Open the address on each device and sign in there once. It is a new address, so the browser keeps a separate sign-in, offline copy and notification setting for it. The old address keeps working.

**What becomes public.** Lumogis's name service learns this computer's **home network address** (a private address such as `192.168.1.20`) and stores it under a random name. That name and the private address are visible in **public DNS**, and the name appears in **public certificate logs**, as every certificate does. Nobody outside your home network can open the address: a private address only leads somewhere on your own network. The name service does not see your data, your traffic or your sign-ins; the certificate's private key stays on this computer.

**Outbound connections.** With the secure address on, Lumogis contacts its name service (`dns.homes.lumogis.app`) to register, update the address and renew the certificate, and Caddy contacts Let's Encrypt. While it is off, nothing is contacted, apart from one call right after you turn it off to remove the name.

**If a device cannot open it.** Many routers block public names that point into the home network ("DNS rebind protection"). The page then says your router hides the address and shows steps for common routers (FRITZ!Box, OpenWrt, Pi-hole, OPNsense/pfSense, UniFi). Allow `homes.lumogis.app` in the router; do not turn the protection off. **Check on this device** only runs when the page was opened by a home network address; from anywhere else, check on a phone or laptop on your home Wi-Fi.

**Right after you turn it on,** a device that looked the name up too early can take up to 30 minutes to find it. The page says "the name is new" during that time instead of pointing at your router.

**After a router restart** your computer may get a new home network address. Lumogis notices within about ten minutes and updates the secure address; until then the address may not open. To avoid this, give the computer a fixed address in your router (a DHCP reservation).

**Things to know:**

- One secure address belongs to **one computer**. Moving Lumogis with a backup keeps the name; running the same backup on two computers at once does not work, and the page warns about it.
- If the workspace is wiped, Lumogis gets a new name. The page says so; the old address stops working.
- If Caddy publishes HTTPS on another host port (`CADDY_HTTPS_HOST_PORT`), the address includes that port.
- **Sign-in off:** anyone on your home network who opens the address uses Lumogis as its admin, as with the plain address. Turn sign-in on first.
- **Testing with staging.** Setting `LUMOGIS_HOME_DNS_ACME_STAGING=true` in `.env` switches to Let's Encrypt's test certificates. Every `.app` name is HSTS-preloaded, so a browser refuses a staging certificate with no way past it: check with `curl -k` or `openssl s_client`, and use the real certificates (`false`) for phone tests. To switch, edit `.env` and run `docker compose up -d` (`docker compose restart` keeps the old value). Lumogis re-applies the site on its own within a few minutes, with no Turn off and on, and the page shows "issuing" until the new certificate is served.
- **Turn off** on the same page removes the name from the name service and the site from Caddy. Devices go back to the plain address.

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

The exact hostname appears in the [Tailscale admin console](https://login.tailscale.com/admin/machines) or via `tailscale status` on the host. Sign-in is on by default. **`LUMOGIS_PUBLIC_ORIGIN`** is optional: set it in `.env` to that HTTPS URL so cookies and CSRF checks match the browser origin exactly, and so email password reset and other emailed links work (Lumogis never builds an email link from the request's `Host`). To create the first admin account through this address, set it first, or create the admin at the computer's network address.

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

On many home networks, **Avahi** or **mDNS** advertises a friendly hostname such as **`lumogis.local`**, so you can open the UI without remembering an IP address while on Wi‑Fi. That convenience is LAN-only: it does not reach phones on cellular or laptops on other networks, and it does not provide HTTPS (use the secure address at home for that). Use this path for at-home browsing only; pair it with Option A or B when household members need off-LAN or mobile access.

## Security guidance

- **Never forward port 8000.** It is published on `127.0.0.1` only; reach Core through Caddy (port 80 or your tunnel).
- **Do not** expose Lumogis directly to the public internet (no raw router port forwarding to port 80 or 8000). Unauthenticated or weakly authenticated instances on the open internet are an easy target.
- **Tailscale Serve** is the preferred path because it keeps the service off the public internet while still delivering HTTPS to trusted devices on your tailnet. **Tailscale Funnel** publishes the same `*.ts.net` name to the whole internet: turn sign-in on before you enable it.
- **Sign-in off on a public hostname is flagged.** When `AUTH_ENABLED=false` and `CADDY_DOMAIN` names a public hostname, the Caddy container prints a WARNING block each time it starts (`docker compose logs caddy`). It does not refuse to start. Names under `.local`, `.lan`, `.home.arpa`, `.internal`, `.test`, `.localhost` and `.ts.net`, single-label names and private IP addresses count as private. Caddy cannot see a tunnel or a router port forward, so a public install with `CADDY_DOMAIN=:80` gets no warning.
- **LibreChat** (the optional `librechat` profile) is published on `127.0.0.1:3080` only and is not behind Caddy. Use Lumogis Web from other devices.
- If you run a **named Cloudflare Tunnel** on a public hostname, treat the instance as internet-adjacent: enable Lumogis auth, use strong credentials, and consider [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/) to restrict who can reach the URL when the stack holds sensitive personal data.
