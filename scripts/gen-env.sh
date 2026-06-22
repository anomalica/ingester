#!/usr/bin/env bash
# Refresh the ingester's .env API secrets from the Safe, so a hand-edited .env
# can't silently drift to a wrong account and break billing isolation. The
# secrets are the source of truth in the Safe's flat top-level; this writes them
# into .env. INGESTER_VERSION and INGEST_USE_API are computed per-run by the
# ./ingest host script and are deliberately left untouched here.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO}/.env"
SAFE="${ANOMALICA_SAFE:-${HOME}/repos/secrets}/store/anomalica.yaml"
export SOPS_AGE_KEY_FILE="${SOPS_AGE_KEY_FILE:-${HOME}/.config/sops/age/keys.txt}"
SOPS="$(command -v sops || echo "${HOME}/.nix-profile/bin/sops")"

[[ -x "$SOPS" ]] || {
	echo "gen-env: sops not found (PATH or ${HOME}/.nix-profile/bin/sops)" >&2
	exit 1
}
[[ -f "$SAFE" ]] || {
	echo "gen-env: Safe not found at ${SAFE}" >&2
	exit 1
}

# Read a key from the Safe: try the flat top-level first, then an optional
# fallback extract path (used while a legacy section key is being migrated).
safe_get() {
	local v
	v="$("$SOPS" -d --extract "[\"$1\"]" "$SAFE" 2>/dev/null)"
	if [[ -n "$v" ]]; then
		printf '%s' "$v"
		return 0
	fi
	if [[ $# -ge 2 ]]; then
		v="$("$SOPS" -d --extract "$2" "$SAFE" 2>/dev/null)"
		[[ -n "$v" ]] && {
			printf '%s' "$v"
			return 0
		}
	fi
	return 1
}

# Replace (or add) KEY=value in .env without disturbing other lines.
upsert() {
	touch "$ENV_FILE"
	local tmp
	tmp="$(mktemp)"
	grep -v "^$1=" "$ENV_FILE" >"$tmp" || true
	printf '%s=%s\n' "$1" "$2" >>"$tmp"
	mv "$tmp" "$ENV_FILE"
}

anthropic="$(safe_get ANTHROPIC_API_KEY)" || {
	echo "gen-env: top-level ANTHROPIC_API_KEY missing in the Safe" >&2
	exit 1
}
upsert ANTHROPIC_API_KEY "$anthropic"
echo "gen-env: ANTHROPIC_API_KEY <- Safe top-level (isolated org)" >&2

# HF_TOKEN (pyannote diarisation): prefer top-level; fall back to the legacy
# [ingester] section during the migration to flat top-level.
if hf="$(safe_get HF_TOKEN '["ingester"]["HF_TOKEN"]')"; then
	upsert HF_TOKEN "$hf"
	echo "gen-env: HF_TOKEN <- Safe" >&2
else
	echo "gen-env: WARNING - HF_TOKEN absent from the Safe; left .env unchanged" >&2
fi

echo "gen-env: done - .env secrets refreshed from the Safe." >&2
