# Rule Format and Precedence

## Matching model

- DOMAIN,x.example is EXACT and matches only x.example.
- DOMAIN-SUFFIX,example is SUFFIX and matches both example and any subdomain boundary such as a.example; it does not match notexample.
- Inputs are lowercased and IDNA-encoded, and one trailing root dot is removed. URLs, IP literals, single-label names and local suffixes (.local, .lan, home.arpa) are rejected.
- IP, keyword, regex, process and user-agent rules are not converted into DNS domain entries. They remain traceable in unsupported.json.
- Bare domain lists require a configured format. For this snapshot's Apple Domain Set, leading dot means SUFFIX and no leading dot means EXACT; a full-set comparison against Apple_All confirmed the exact mapping for that snapshot only.
- `categories.yaml` may keep legacy `source_file` or declare `source_refs: [{source_id, file, format}]`. Two or more refs are rejected unless the category explicitly says `source_merge_review: approved`; sources are never unioned implicitly.
- v2fly `domain-list-community` maps bare entries and `domain:` to SUFFIX and `full:` to EXACT. `include:key @must @-ban` is recursively expanded with all required attributes and no banned attributes; filters also apply through nested includes. `&KEY` on an entry adds that entry to an additional virtual list. Keyword and regexp entries are never approximated. G2 exact-signature dispositions allow the current reviewed 18 unsupported records to remain in reports with `review_required: false`; a new or changed signature still requires review.
- v2fly affiliation resolution scans the full `data/` tree. Its files are hashed into source identity, together with every file used by an include expansion. Cycles, missing includes, unsafe paths, symlinks, oversized files and malformed attributes fail the build.
- Schema-4 manifests record `source_capability_version: 1`, named source commits/archive hashes and source-file hashes. Compiler capability 2 adds a provenance-preserving `provider_map` to the candidate identity and verifier checks; earlier schema-4 manifests without newer capability markers remain readable through their compatible verification paths. G2 independently verified two pre-existing real-CI schema-4 candidates.
- `rules-data/state.yaml` schema 1 accepts optional `unsupported_dispositions`. Older schema-1 snapshots without the field are read as an empty list. Exact user dispositions are persisted and synchronized with the data state, then included in candidate identity and the unsupported-disposition report; they are combined with the code-managed approval baseline and cannot erase it. The CLI/controller path accepts and round-trips the optional field.

## Output

SmartDNS EXACT uses -.host.example; SUFFIX uses example. Mihomo Providers use classical YAML payload rules DOMAIN,host.example and DOMAIN-SUFFIX,example. The generated local provider path is ./providers/<name>.yaml, relative to the Mihomo HomeDir.

EXACT custom rules each go to a CustomExact-<category provider> file. All such Rule-Set entries precede every ordinary Provider entry. Among ordinary semantic rules, a more specific match precedes a broader overlapping match; for the same match scope across different policy groups, the lower configured integer priority wins. Unresolved same-scope cross-policy order is a blocker. The compiler emits contiguous category/policy runs as provider fragments, so one category may produce multiple fragments; it does not sort whole categories by a single priority. Cross-category providers retain their rules and provenance.

force_direct entries are treated as suffix scopes for collision analysis. Any parent/child/equal overlap with a proxy rule is blocking until its SmartDNS exclusion semantics are reviewed. The builder does not silently remove a direct exception or proxy child rule.

The G3 acceptance candidate passed Mihomo v1.19.32 offline syntax checks for all 20 providers and 21 representative route-log requests. Its generated SmartDNS set passed four offline EXACT/SUFFIX requests on SmartDNS 1.2026.v48.4.2. The independent 2,623-witness provider-order oracle also passed. These results apply to the G3 acceptance candidate; the optional schema-1 disposition field has since been connected to CLI/controller; the corrected candidate has been rebuilt; the main agent confirms the automated tests and 2,623-witness ordering oracle pass, while final binary and management-container checks remain in progress. The earlier R5 binary checks are historical baseline evidence. Household target versions and production integration remain unverified; deployment remains disabled.

