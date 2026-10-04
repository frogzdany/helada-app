#!/usr/bin/env bash
# Checks a deployed stage from the outside. Read-only: it sends no message and changes no data.
#
#   smoke.sh <dev|prod>
#
# The first request after an image change takes 7 to 25 seconds, and the function serves one
# request at a time, so every check is retried before it counts as failed.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

STAGE="${1:-}"
need_stage "$STAGE"

output() {
  aws cloudformation describe-stacks --stack-name "Helada-$STAGE-Edge" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
DASHBOARD="$(output DashboardUrl)"
PHONE="$(output PhoneUrl)"
LANDING="$(output LandingUrl)"

FAILED=0
BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

# expect <name> <url> [tries]: 200, retried every 5 seconds. The body stays in $BODY.
expect() {
  local name="$1" url="$2" tries="${3:-6}" code=000 i
  for ((i = 1; i <= tries; i++)); do
    code="$(curl -s -o "$BODY" -w '%{http_code}' --max-time 35 "$url" || true)"
    if [ "$code" = 200 ]; then
      echo "ok    $name"
      return 0
    fi
    sleep 5
  done
  echo "FAIL  $name ($url answered $code)"
  FAILED=1
  return 1
}

# The app first: /api/storage answers once the new instance has loaded its data back.
if expect "app is up and its data is loaded" "$DASHBOARD/api/storage" 30; then
  python3 - "$BODY" <<'PY' || FAILED=1
import json, sys
s = json.load(open(sys.argv[1]))
print(f"      restored {s.get('restored_rows')} rows and {s.get('restored_files')} files; last_error: {s.get('last_error')}")
sys.exit(1 if s.get("last_error") else 0)
PY
fi
expect "parcels" "$DASHBOARD/api/parcels" || true
if expect "config" "$DASHBOARD/api/config"; then
  python3 - "$BODY" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
print(f"      speech to text: {c.get('asr')}; voice: {c.get('tts')}; model: {c.get('model_source')}")
PY
fi

expect "dashboard page" "$DASHBOARD/" || true
expect "phone page" "$PHONE/" || true
# The service worker is never cached at the edge, so it shows whether this commit's files arrived.
if expect "phone service worker" "$PHONE/sw.js"; then
  WANT="$(grep -m1 -o 'const VERSION = "[^"]*"' "$REPO_ROOT/app/static/movil/sw.js" || true)"
  if [ -n "$WANT" ] && ! grep -q -F "$WANT" "$BODY"; then
    echo "FAIL  phone service worker is not this commit's ($WANT)"
    FAILED=1
  fi
fi
if [ -n "$LANDING" ] && [ "$LANDING" != None ]; then
  expect "landing page" "$LANDING/" || true
fi

if [ $FAILED = 1 ]; then
  echo "smoke: $STAGE FAILED"
  exit 1
fi
echo "smoke: $STAGE ok"
