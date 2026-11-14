#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Caddy container preflight (LUM-916): warn loudly when sign-in is off and
# CADDY_DOMAIN names a public hostname, then exec the real command.
#
# Caddy is the one service that knows the domain, so the check lives here and
# not in Core. It warns and never refuses: an auth-off install on a private
# name keeps working, and Caddy cannot see every way an install becomes public
# (a Cloudflare Tunnel or router port forward with CADDY_DOMAIN=:80 looks the
# same as a LAN install). Run with no arguments to print the classification
# only: `CADDY_DOMAIN=x AUTH_ENABLED=false sh docker/caddy/preflight.sh`.
#
# Private (no warning): an address with no host (`:80`), localhost and
# *.localhost, *.local, *.lan, *.home.arpa, *.internal, *.test, a single-label
# name, a Tailscale *.ts.net name, and loopback, private, link-local and CGNAT
# (100.64.0.0/10, Tailscale) IP literals. A *.ts.net name resolves and connects
# only inside the tailnet; Tailscale Funnel can publish it, but Funnel is
# switched on in Tailscale, where this check cannot see it, and the docs say
# to turn sign-in on before using it. Everything else is public.

# Print the public hosts in "$1" (Caddy site addresses, comma or space
# separated), one per line. Prints nothing when every address is private.
lumogis_public_hosts() {
	set -f # a wildcard site address must not glob against files
	for addr in $(printf '%s' "$1" | tr ',' ' '); do
		host=$addr
		case $host in
			*://*) host=${host#*://} ;;
		esac
		host=${host%%/*}
		case $host in
			\[*\]*)
				host=${host#\[}
				host=${host%%\]*}
				;;
			*:*:*) ;; # bare IPv6 literal
			*:*) host=${host%:*} ;;
		esac
		host=$(printf '%s' "$host" | tr 'A-Z' 'a-z')
		host=${host%.}
		if ! lumogis_host_is_private "$host"; then
			printf '%s\n' "$host"
		fi
	done
	set +f
}

# Return 0 when "$1" (a lowercased host, no port) is private.
lumogis_host_is_private() {
	h=$1
	case $h in
		'' | localhost | *.localhost | *.local | *.lan | *.home.arpa | *.internal | *.test | *.ts.net)
			return 0
			;;
		*:*) # IPv6 literal
			case $h in
				::1 | fc* | fd* | fe8* | fe9* | fea* | feb*) return 0 ;;
			esac
			return 1
			;;
	esac
	if lumogis_is_ipv4 "$h"; then
		lumogis_ipv4_is_private "$h"
		return $?
	fi
	case $h in
		*.*) return 1 ;; # a dotted name with a public suffix
	esac
	return 0 # single label: never in public DNS
}

lumogis_is_ipv4() {
	printf '%s' "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
}

lumogis_ipv4_is_private() {
	old_ifs=$IFS
	IFS=.
	# shellcheck disable=SC2086 # split on dots on purpose
	set -- $1
	IFS=$old_ifs
	a=$1
	b=$2
	[ "$a" -eq 10 ] && return 0
	[ "$a" -eq 127 ] && return 0
	[ "$a" -eq 192 ] && [ "$b" -eq 168 ] && return 0
	[ "$a" -eq 172 ] && [ "$b" -ge 16 ] && [ "$b" -le 31 ] && return 0
	[ "$a" -eq 169 ] && [ "$b" -eq 254 ] && return 0
	[ "$a" -eq 100 ] && [ "$b" -ge 64 ] && [ "$b" -le 127 ] && return 0
	[ "$a" -eq 0 ] && return 0
	return 1
}

lumogis_preflight() {
	auth=$(printf '%s' "${AUTH_ENABLED:-false}" | tr 'A-Z' 'a-z' | tr -d ' ')
	if [ "$auth" = "true" ]; then
		return 0
	fi
	public=$(lumogis_public_hosts "${CADDY_DOMAIN:-:80}" | tr '\n' ' ')
	public=${public% }
	if [ -z "$public" ]; then
		return 0
	fi
	cat >&2 <<EOF
[lumogis-preflight] WARNING ==========================================================
[lumogis-preflight] WARNING: sign-in is OFF (AUTH_ENABLED=false) but CADDY_DOMAIN
[lumogis-preflight] WARNING: names a public hostname: $public
[lumogis-preflight] WARNING: Anyone who can reach that address uses Lumogis as its
[lumogis-preflight] WARNING: admin and can read everything it stores.
[lumogis-preflight] WARNING: Turn sign-in on before publishing this address:
[lumogis-preflight] WARNING: docs/deployment/quickstart.md#turn-on-sign-in
[lumogis-preflight] WARNING ==========================================================
EOF
	return 1
}

if [ "${LUMOGIS_PREFLIGHT_LIB:-0}" = "1" ]; then
	return 0 2>/dev/null || exit 0 # sourced by tests for the functions only
fi

lumogis_preflight || true
if [ "$#" -gt 0 ]; then
	exec "$@"
fi
