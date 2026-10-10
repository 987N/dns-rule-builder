"""Small isolated rule-matching model; it is not a SmartDNS/Mihomo test."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    domain: str
    match_type: str
    category: str
    policy: str


def matches(rule: Rule, query: str) -> bool:
    query = query.rstrip(".").lower()
    domain = rule.domain.rstrip(".").lower()
    if rule.match_type == "EXACT":
        return query == domain
    if rule.match_type == "SUFFIX":
        return query == domain or query.endswith("." + domain)
    raise ValueError(f"unsupported match type: {rule.match_type}")


def first_policy(rules: list[Rule], query: str) -> str | None:
    for rule in rules:
        if matches(rule, query):
            return rule.policy
    return None


def main() -> None:
    exact = Rule("api.example.com", "EXACT", "custom", "AI")
    suffix = Rule("example.com", "SUFFIX", "general", "Proxy")
    assert matches(exact, "API.Example.com.")
    assert not matches(exact, "v2.api.example.com")
    assert matches(suffix, "example.com")
    assert matches(suffix, "www.example.com")
    assert not matches(suffix, "notexample.com")
    assert first_policy([exact, suffix], "api.example.com") == "AI"
    assert first_policy([suffix, exact], "api.example.com") == "Proxy"
    print("PASS: modeled EXACT/SUFFIX and first-match policy ordering")
    print("LIMIT: model only; target SmartDNS/Mihomo binaries were not exercised")


if __name__ == "__main__":
    main()
