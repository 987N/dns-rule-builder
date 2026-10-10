# Rule Format and Precedence

## Matching model

- DOMAIN,x.example is EXACT and matches only x.example.
- DOMAIN-SUFFIX,example is SUFFIX and matches both example and any subdomain boundary such as a.example; it does not match notexample.
- Inputs are lowercased and IDNA-encoded, and one trailing root dot is removed. URLs, IP literals, single-label names and local suffixes (.local, .lan, home.arpa) are rejected.
- IP, keyword, regex, process and user-agent rules are not converted into DNS domain entries. They remain traceable in unsupported.json.
- Bare domain lists require a configured format. For this snapshot's Apple Domain Set, leading dot means SUFFIX and no leading dot means EXACT; a full-set comparison against Apple_All confirmed the exact mapping for that snapshot only.

## Output

SmartDNS EXACT uses -.host.example; SUFFIX uses example. Mihomo Providers use classical YAML payload rules DOMAIN,host.example and DOMAIN-SUFFIX,example. The generated local provider path is ./providers/<name>.yaml, relative to the Mihomo HomeDir.

EXACT custom rules each go to a CustomExact-<category provider> file. All such Rule-Set entries precede every ordinary Provider entry. Other category order uses configured integer priority, lower first. Different policy groups with equal priority and overlapping match space are unresolved and block deployment; cross-category providers retain their rules and provenance.

force_direct entries are treated as suffix scopes for collision analysis. Any parent/child/equal overlap with a proxy rule is blocking until SmartDNS exclusion semantics are target-tested. The builder does not silently remove a direct exception or proxy child rule.

The current output is a static candidate. Rule-provider path security, provider contents, exact SmartDNS semantics and observed Mihomo order still require validation against isolated target binaries before enabling deployment.

