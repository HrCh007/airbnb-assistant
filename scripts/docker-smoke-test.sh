#!/usr/bin/env bash
# Docker smoke test: build + start the stack, check health, connectivity, the
# acceptance conversation through the frontend proxy, and persistence across
# a full `docker compose down` / `up`. Uses no API keys.
#   ./scripts/docker-smoke-test.sh            # build and test
#   NO_BUILD=1 ./scripts/docker-smoke-test.sh # reuse existing images
set -euo pipefail
cd "$(dirname "$0")/.."
FRONT="http://localhost:${FRONTEND_PORT:-3000}"
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; docker compose ps; docker compose logs --tail 40 backend || true; exit 1; }

wait_healthy() {
  echo "Waiting for services to become healthy..."
  for _ in $(seq 1 90); do
    states=$(docker compose ps --format '{{.Service}}={{.Health}}' | grep -E '^(ollama|chromadb|backend|frontend)=' || true)
    if [ "$(echo "$states" | grep -c '=healthy')" -eq 4 ]; then echo "$states" | sed 's/^/  /'; return 0; fi
    sleep 4
  done
  echo "$states"; return 1
}

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1"; }

ask() { # session token message -> concatenated SSE tokens/replace + place count
  curl -sN -X POST "$FRONT/api/chat/message" -H "X-Session-Token: $2" -H 'content-type: application/json' \
    -d "{\"session_id\":\"$1\",\"message\":\"$3\"}" | python3 -c '
import json, sys
text, places, ev = "", 0, None
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.startswith("event: "): ev = line[7:]
    elif line.startswith("data: "):
        d = json.loads(line[6:])
        if ev == "token": text += d
        elif ev == "replace": text = d
        elif ev == "places": places = len(d["items"])
print(json.dumps({"text": text, "places": places}))'
}

echo "== 1. Configuration has no paid API keys"
if grep -rqiE '^(ANTHROPIC|OPENAI|GOOGLE_MAPS|GEMINI)[A-Z_]*=.+' .env 2>/dev/null; then fail ".env contains paid API keys"; fi
docker compose config -q && pass "docker compose config is valid"

echo "== 2. Build and start"
if [ "${NO_BUILD:-0}" = "1" ]; then docker compose up -d --no-build; else docker compose up -d --build; fi
wait_healthy || fail "services did not become healthy"
pass "ollama, chromadb, backend, frontend healthy"

echo "== 3. Connectivity (browser -> frontend -> backend -> chromadb/ollama)"
status=$(curl -sf "$FRONT/api/status") || fail "frontend /api proxy unreachable"
[ "$(echo "$status" | json 'd["paid_apis"]')" = "none" ] && pass "no paid APIs configured"
[ "$(echo "$status" | json 'd["vector_store"]["reachable"]')" = "True" ] && pass "backend -> chromadb" || fail "chromadb unreachable"
llm=$(echo "$status" | json 'd["llm"]["state"]'); echo "  INFO  backend -> ollama: $llm"
[ "$llm" != "unreachable" ] && pass "backend -> ollama reachable" || fail "ollama unreachable"
curl -sf "$FRONT/" | grep -q "Hideout Studios" && pass "frontend renders"

echo "== 4. Acceptance conversation through the frontend proxy"
s=$(curl -sf -X POST "$FRONT/api/chat/sessions" -H 'content-type: application/json' -d '{}')
SID=$(echo "$s" | json 'd["session_id"]'); TOK=$(echo "$s" | json 'd["session_token"]')
curl -sf -X PUT "$FRONT/api/chat/sessions/$SID/selection" -H "X-Session-Token: $TOK" -H 'content-type: application/json' -d '{"location_slug":"saket"}' >/dev/null
r=$(ask "$SID" "$TOK" "What's the Wi-Fi password?"); echo "$r" | grep -q "saket-demo-2468" && pass "Wi-Fi password from Saket RAG" || fail "wifi: $r"
r=$(ask "$SID" "$TOK" "Suggest some nice places for dinner nearby."); [ "$(echo "$r" | json 'd["places"]')" -gt 0 ] && pass "dinner recommendations ($(echo "$r" | json 'd["places"]') places)" || fail "dinner: $r"
r=$(ask "$SID" "$TOK" "Anything more budget-friendly?"); [ "$(echo "$r" | json 'd["places"]')" -gt 0 ] && pass "cheaper follow-up" || fail "cheaper: $r"
r=$(ask "$SID" "$TOK" "What about nightlife? Somewhere for drinks"); [ "$(echo "$r" | json 'd["places"]')" -gt 0 ] && pass "nightlife" || fail "nightlife: $r"
r=$(ask "$SID" "$TOK" "What's the checkout time tomorrow?"); echo "$r" | grep -q "11:00 AM" && pass "checkout time" || fail "checkout: $r"

echo "== 5. Persistence across docker compose down / up"
before=$(curl -sf "$FRONT/api/chat/sessions/$SID/messages" -H "X-Session-Token: $TOK" | json 'len(d)')
docker compose down
docker compose up -d --no-build
wait_healthy || fail "services did not come back"
after=$(curl -sf "$FRONT/api/chat/sessions/$SID/messages" -H "X-Session-Token: $TOK" | json 'len(d)')
[ "$before" = "$after" ] && pass "conversation survived restart ($after messages)" || fail "messages $before -> $after"
r=$(ask "$SID" "$TOK" "What's the Wi-Fi password?"); echo "$r" | grep -q "saket-demo-2468" && pass "vectors survived restart" || fail "after restart: $r"

echo "All smoke tests passed."
