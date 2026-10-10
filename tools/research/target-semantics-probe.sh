#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURES="$ROOT/research/fixtures/target-probe"
SMARTDNS_IMAGE="pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff"
MIHOMO_IMAGE="metacubex/mihomo@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e"
SMARTDNS_NAME="dns-rule-builder-smartdns-probe-$$"
MIHOMO_NAME="dns-rule-builder-mihomo-probe-$$"
TMP_DIR="$(mktemp -d)"
cleanup() {
  docker rm -f "$SMARTDNS_NAME" "$MIHOMO_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

docker image inspect "$SMARTDNS_IMAGE" >/dev/null
docker image inspect "$MIHOMO_IMAGE" >/dev/null

docker run -d --network none --name "$SMARTDNS_NAME" \
  -v "$FIXTURES:/probe:ro" --entrypoint /usr/sbin/smartdns "$SMARTDNS_IMAGE" \
  -f -c /probe/smartdns.conf -p - >/dev/null
for _ in {1..30}; do
  if docker exec "$SMARTDNS_NAME" nslookup api.example.test 127.0.0.1 >/dev/null 2>&1; then break; fi
  sleep 0.2
done
assert_answer() {
  local domain="$1" expected="$2" output
  output="$(docker exec "$SMARTDNS_NAME" nslookup "$domain" 127.0.0.1)"
  grep -F "Address: $expected" <<<"$output" >/dev/null || {
    echo "unexpected DNS answer for $domain: expected $expected" >&2
    exit 1
  }
}
assert_answer api.example.test 192.0.2.10
assert_answer child.api.example.test 192.0.2.20
assert_answer suffix.example.test 192.0.2.10
assert_answer child.suffix.example.test 192.0.2.10
docker rm -f "$SMARTDNS_NAME" >/dev/null

docker run --rm --network none -v "$FIXTURES/mihomo-semantics:/probe:ro" \
  --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /probe -f /probe/config.yaml
if [[ -n "${FULL_MIHOMO_CANDIDATE:-}" ]]; then
  candidate="$(cd "$FULL_MIHOMO_CANDIDATE" && pwd)"
  test -f "$candidate/config.yaml"
  test -d "$candidate/providers"
  docker run --rm --network none -v "$candidate:/candidate:ro" \
    --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /candidate -f /candidate/config.yaml
else
  echo "skip full candidate validation (set FULL_MIHOMO_CANDIDATE to an already-built config.yaml/providers directory)"
fi
if docker run --rm --network none -v "$FIXTURES/mihomo-semantics:/probe:ro" \
  --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /probe -f /probe/outside-config.yaml \
  >"$TMP_DIR/safe-path-denied.log" 2>&1; then
  echo "Mihomo unexpectedly accepted a provider outside HomeDir/SAFE_PATHS" >&2
  exit 1
fi
grep -F "SAFE_PATHS" "$TMP_DIR/safe-path-denied.log" >/dev/null
docker run --rm --network none -e SAFE_PATHS=/outside \
  -v "$FIXTURES/mihomo-semantics:/probe:ro" \
  -v "$FIXTURES/mihomo-semantics/providers:/outside/providers:ro" \
  --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /probe -f /probe/outside-config.yaml

docker run -d --network none --name "$MIHOMO_NAME" \
  -v "$FIXTURES/mihomo-semantics:/probe:ro" --entrypoint /mihomo "$MIHOMO_IMAGE" \
  -d /probe -f /probe/config.yaml >/dev/null
sleep 1
for domain in api.example.test child.api.example.test example.test gemini.google.com google.com; do
  docker exec "$MIHOMO_NAME" sh -c \
    "http_proxy=http://127.0.0.1:17890 wget -T 2 -O /dev/null http://$domain/" \
    >/dev/null 2>&1 || true
done
docker logs "$MIHOMO_NAME" >"$TMP_DIR/mihomo-routing.log" 2>&1
for expected in \
  "api.example.test:80 match RuleSet(Exact) using ExactPolicy[REJECT]" \
  "child.api.example.test:80 match RuleSet(Parent) using ParentPolicy[REJECT]" \
  "example.test:80 match RuleSet(Parent) using ParentPolicy[REJECT]" \
  "gemini.google.com:80 match RuleSet(Gemini) using GeminiPolicy[REJECT]" \
  "google.com:80 match RuleSet(Google) using GooglePolicy[REJECT]"; do
  grep -F "$expected" "$TMP_DIR/mihomo-routing.log" >/dev/null || {
    echo "missing Mihomo routing evidence: $expected" >&2
    exit 1
  }
done
echo "isolated SmartDNS/Mihomo target semantics checks passed"
