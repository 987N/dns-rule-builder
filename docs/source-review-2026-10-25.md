# Source review: 2026-10-25

This review addresses the source-scope blockers in build `99e243d49793bfd7d1f652a67d186ac6b063bba119dca8484eb5078e112d29b8`, using its locked upstream snapshot (`v2fly/domain-list-community` revision `664598567e92d797bea11bb83cf939d1f37e9512`).

## Microsoft and Xbox selectors

The prior approved profiles were tied to revision `c6adacf29cc4fb05e04651fcf03e00989ef9f797`. Comparing the two official raw files at those revisions shows `data/microsoft` is byte-for-byte unchanged. `data/xbox` removes only the active line `include:asobo`; the remaining active include selectors are unchanged. The apparent `#include:activision-blizzard` line is a comment at both revisions. The new selector scopes are therefore narrower: the Microsoft profile no longer reaches `data/asobo` through its Microsoft → Xbox closure, and the Xbox profile no longer includes it either. No new selector or domain scope was introduced.

The source's MIT license is unchanged: both snapshot LICENSE files have SHA-256 `b9d84a22870d3f21c91a4c6e410c9cc51d00902f5233ad0c84011479244bf7d2`. The profiles remain finite and partial; their service-identity references remain Microsoft's official [Microsoft 365 endpoint documentation](https://learn.microsoft.com/en-us/microsoft-365/enterprise/managing-office-365-endpoints?view=o365-worldwide) and [Xbox Support](https://support.xbox.com/). The exact new file revision is recorded in each profile.

## Exact shared-host exclusions

Two shared-host rules are excluded by exact category, source file, and raw-line SHA-256. OpenAI's [official GovCloud configuration documentation](https://learn.chatgpt.com/docs/enterprise/govcloud-configuration) lists `o33249.ingest.us.sentry.io` as an OpenAI/ChatGPT endpoint. Sentry's [official ingestion announcement](https://sentry.io/changelog/ingestion-ip-addresses-are-changing/) identifies `*.ingest.us.sentry.io` as an ingestion hostname, and its [API authentication documentation](https://docs.sentry.io/api/auth/) describes DSNs as receiving event data. Given those first-party facts, the upstream `@ads` marker, and the existing exact exclusion for the older `o33249.ingest.sentry.io` host, this exact endpoint is treated as telemetry and excluded. The purpose classification is an inference from those sources and the upstream marker, not a direct claim made by the OpenAI page.

The Twitch rule `d3stzm2eumvgb4.cloudfront.net` is excluded as one exact host because first-party ownership evidence was not established during this review. The existing Twitch exact CDN host remains unchanged. No shared suffix allowance was added.

These exclusions do not alter source selection or classify all Sentry/CloudFront hosts as excluded. Future raw-line changes require a new exact review.

## Validation

An isolated rebuild using rules-data commit `390d777b0cb504aca260e395b56e7efbd142451a` and the same locked upstream revision produced local candidate `e8c4d2f7a5e10d1596c272edbf2226e257f0cf742b8fd3050846c94096f03b45`: publication allowed, no publication blockers, 3,965 category rules, and no unresolved conflicts. Both exact exclusions were reported as applied, and neither hostname appears in the compiled SmartDNS or Mihomo files. This local candidate is validation evidence, not a production Release.

The full local test suite passed: 281 tests, with one existing Starlette/httpx deprecation warning. Production publication and activation must still use the normal manager workflow and target validation gates.
