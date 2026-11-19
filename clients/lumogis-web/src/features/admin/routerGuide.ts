// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// LUM-331 — how to let a router answer for homes.lumogis.app. Routers with DNS
// rebind protection drop public names that point into the home network, which
// is exactly what a secure address at home is. Each entry allows only the
// Lumogis zone, never turns the protection off. Menu paths follow each
// vendor's current firmware as documented; community reports refine them
// over time (the router matrix beyond these is a LUM-331 follow-up).

export const SECURE_ADDRESS_ZONE = "homes.lumogis.app";

export interface RouterGuide {
  id: string;
  name: string;
  steps: string[];
}

export const ROUTER_GUIDES: RouterGuide[] = [
  {
    id: "fritzbox",
    name: "FRITZ!Box",
    steps: [
      "Open http://fritz.box and sign in.",
      "Go to Home Network → Network → Network Settings (Heimnetz → Netzwerk → Netzwerkeinstellungen).",
      "Under DNS Rebind Protection (DNS-Rebind-Schutz), add homes.lumogis.app on its own line.",
      "Press Apply (Übernehmen).",
    ],
  },
  {
    id: "openwrt",
    name: "OpenWrt (dnsmasq)",
    steps: [
      "In LuCI, go to Network → DHCP and DNS → Filter.",
      "Under Domain whitelist (rebind_domain), add homes.lumogis.app.",
      "Press Save & Apply.",
      "Or on the command line: uci add_list dhcp.@dnsmasq[0].rebind_domain='homes.lumogis.app' && uci commit dhcp && /etc/init.d/dnsmasq restart",
    ],
  },
  {
    id: "pihole",
    name: "Pi-hole",
    steps: [
      "Pi-hole blocks these names only if its rebind protection was turned on. If your devices open other sites but not the secure address, add this dnsmasq line: rebind-domain-ok=/homes.lumogis.app/",
      "Pi-hole v6: Settings → All settings → Miscellaneous → misc.dnsmasq_lines. Pi-hole v5: a new file /etc/dnsmasq.d/99-lumogis.conf.",
      "Restart Pi-hole's DNS: pihole restartdns",
    ],
  },
  {
    id: "opnsense",
    name: "OPNsense / pfSense (Unbound)",
    steps: [
      "Go to Services → Unbound DNS → Advanced (OPNsense) or Services → DNS Resolver → Advanced Settings (pfSense).",
      "Under Private Domains, add homes.lumogis.app.",
      "Save and apply. On pfSense you may instead add to Custom options: private-domain: \"homes.lumogis.app\"",
    ],
  },
  {
    id: "unifi",
    name: "UniFi",
    steps: [
      "UniFi gateways do not block these names by default.",
      "If your devices use a DNS filter (for example Pi-hole or AdGuard Home), follow that product's steps instead.",
      "If you turned on DNS Shield or content filtering, allow homes.lumogis.app there.",
    ],
  },
  {
    id: "generic",
    name: "Other routers",
    steps: [
      "Look in your router's DNS settings for DNS rebind protection (sometimes called rebind attack protection or private domain filtering).",
      "Add homes.lumogis.app as an allowed domain. Do not turn the protection off.",
      "If your router cannot do that, set your devices' DNS to a public resolver such as 1.1.1.1 or 9.9.9.9.",
    ],
  },
];

export const ROUTER_REBOOT_NOTE =
  "After a router restart your computer may get a new home network address. Lumogis notices within about ten minutes and updates the secure address.";
