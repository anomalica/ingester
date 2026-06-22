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

# Read a flat top-level key from the Safe (empty/missing -> non-zero exit).
safe_get() {
	local v
	v="$("$SOPS" -d --extract "[\"$1\"]" "$SAFE" 2>/dev/null)"
	[[ -n "$v" ]] || return 1
	printf '%s' "$v"
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

# HF_TOKEN (pyannote diarisation), flat top-level.
hf="$(safe_get HF_TOKEN)" || {
	echo "gen-env: top-level HF_TOKEN missing in the Safe" >&2
	exit 1
}
upsert HF_TOKEN "$hf"
echo "gen-env: HF_TOKEN <- Safe top-level" >&2

echo "gen-env: done - .env secrets refreshed from the Safe." >&2
