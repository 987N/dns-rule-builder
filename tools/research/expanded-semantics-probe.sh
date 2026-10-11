#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:-$ROOT/.cache/expanded-semantics-r5}"
[[ "$OUT" == /* ]] || OUT="$ROOT/$OUT"
mkdir -p "$(dirname "$OUT")"
OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
cd "$ROOT"
MIHOMO_IMAGE="metacubex/mihomo@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e"
SMARTDNS_IMAGE="pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff"
EVIDENCE="$OUT/binary-evidence"
MIHOMO_NAME="dns-rule-builder-r5-mihomo-$$"
SMARTDNS_NAME="dns-rule-builder-r5-smartdns-$$"
cleanup() {
  docker rm -f "$MIHOMO_NAME" "$SMARTDNS_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image inspect "$MIHOMO_IMAGE" >/dev/null
docker image inspect "$SMARTDNS_IMAGE" >/dev/null
[[ ! -e "$OUT" ]] || { echo "output directory already exists: $OUT" >&2; exit 1; }
prepare="$(uv run --frozen python "$ROOT/tools/research/expanded-semantics-probe.py" --output-root "$OUT")"
mkdir -p "$EVIDENCE"
printf '%s\n' "$prepare" >"$EVIDENCE/prepare.json"

docker run --rm --network none -v "$OUT/all-category-syntax:/probe:ro" \
  --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /probe -f /probe/config.yaml \
  >"$EVIDENCE/mihomo-all-categories-test.log" 2>&1
docker run --rm --network none -v "$OUT/mihomo-representative:/probe:ro" \
  --entrypoint /mihomo "$MIHOMO_IMAGE" -t -d /probe -f /probe/config.yaml \
  >"$EVIDENCE/mihomo-representative-test.log" 2>&1

docker run -d --network none --name "$MIHOMO_NAME" \
  -v "$OUT/mihomo-representative:/probe:ro" --entrypoint /mihomo "$MIHOMO_IMAGE" \
  -d /probe -f /probe/config.yaml >"$EVIDENCE/mihomo-container-id.txt"
sleep 1
python3 - "$OUT/probe-plan.json" >"$EVIDENCE/probes.tsv" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
for probe in plan["mihomo_probes"]:
    provider = probe["expected_provider"]
    action = f"RuleSet({provider})" if provider else "Match"
    policy = probe["expected_policy"]
    if policy == "MATCH":
        policy = "REJECT"
    print("\t".join((probe["domain"], action, policy)))
PY
while IFS=$'\t' read -r domain action policy; do
  docker exec "$MIHOMO_NAME" sh -c \
    "http_proxy=http://127.0.0.1:17890 wget -T 2 -O /dev/null http://$domain/" \
    >"$EVIDENCE/request-$(printf '%s' "$domain" | tr '.:' '__').log" 2>&1 || true
done <"$EVIDENCE/probes.tsv"
docker logs "$MIHOMO_NAME" >"$EVIDENCE/mihomo-routing.log" 2>&1
while IFS=$'\t' read -r domain action policy; do
  expected="$domain:80 match $action using $policy[REJECT]"
  if [[ "$policy" == "REJECT" ]]; then
    expected="$domain:80 match $action using REJECT"
  fi
  grep -F "$expected" "$EVIDENCE/mihomo-routing.log" >/dev/null || {
    echo "missing Mihomo routing evidence: $expected" >&2
    exit 1
  }
done <"$EVIDENCE/probes.tsv"
docker rm -f "$MIHOMO_NAME" >/dev/null

docker run -d --network none --name "$SMARTDNS_NAME" \
  -v "$OUT/smartdns-probe:/probe:ro" --entrypoint /usr/sbin/smartdns "$SMARTDNS_IMAGE" \
  -f -c /probe/smartdns.conf -p - >"$EVIDENCE/smartdns-container-id.txt"
for _ in {1..30}; do
  if docker exec "$SMARTDNS_NAME" nslookup grok.com 127.0.0.1 >/dev/null 2>&1; then break; fi
  sleep 0.2
done
assert_dns() {
  local domain="$1" expected="$2" actual
  actual="$(docker exec "$SMARTDNS_NAME" nslookup "$domain" 127.0.0.1)"
  {
    printf 'domain=%s expected=%s\n' "$domain" "$expected"
    printf '%s\n\n' "$actual"
  } >>"$EVIDENCE/smartdns-query-answers.txt"
  grep -F "Address: $expected" <<<"$actual" >/dev/null || {
    echo "unexpected SmartDNS answer for $domain (expected $expected)" >&2
    echo "$actual" >&2
    exit 1
  }
}
assert_dns "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["smartdns_exact_host"])' "$OUT/probe-plan.json")" 192.0.2.10
assert_dns "child.$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["smartdns_exact_host"])' "$OUT/probe-plan.json")" 192.0.2.20
assert_dns "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["smartdns_suffix_root"])' "$OUT/probe-plan.json")" 192.0.2.10
assert_dns "child.$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["smartdns_suffix_root"])' "$OUT/probe-plan.json")" 192.0.2.10
docker logs "$SMARTDNS_NAME" >"$EVIDENCE/smartdns.log" 2>&1
docker rm -f "$SMARTDNS_NAME" >/dev/null
echo "R5 local shadow source parse, Mihomo syntax/routing and SmartDNS semantics passed; evidence: $EVIDENCE"
