# Independent-service coverage expansion — 2026-10-11

## Outcome

This review enables 43 independent service categories: 41 from pinned service-specific keys plus two narrowly scoped manual categories. Against the prior published 17-category manifest `66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961`, it adds 1,130 category-assigned rules and 1,079 distinct `EXACT`/`SUFFIX` domain semantics with no prior semantic removals. The candidate contains 2,416 unique semantic rules and 60 enabled categories, including the existing empty `Foreign-General` fallback. These finite domain-rule counts do not claim that every endpoint, regional hostname, application API, media host, or CDN is covered.

The final local candidate build used release `4d8b5786a9ba968d28f8d2d99db1328c89840883b2fd992432de46c11c9640c0`: `review_required=false`, `publish_allowed=true`, `deployment_allowed=false`, 2,694 rules, 2,416 SmartDNS entries/unique semantic rules, 27 unsupported signatures retained in review output, and zero unresolved policy conflicts. The only deployment blockers are unverified target semantics and the disabled deployment master switch. This is a reproducible local candidate, not a publication or production action. The previous published manifest above is the comparison baseline; intermediate review builds are not treated as production baselines.

## Selected source and evidence

All newly enabled categories use one finite service-specific key from the v2fly `domain-list-community` snapshot at revision `c6adacf29cc4fb05e04651fcf03e00989ef9f797`, under the repository MIT license. The approval ledger records each selected key, exact recursive include closure, closure aggregate and per-file SHA-256 values, selector hash, selected-file legal declarations, license/notice scope, reviewer, and reviewed date. `source_file` inventory in the source manifest covers the repository data tree; the approved scope is the profile's selected key and recorded include closure only.

The pinned upstream repository explains its per-file domain-list model in the [v2fly project](https://github.com/v2fly/domain-list-community). Service identity evidence is recorded per entry in `config/service-catalog.yaml` using the service's own site and the corresponding pinned upstream key path. For the one permitted Twitch CloudFront host, the official [Twitch API reference](https://dev.twitch.tv/docs/api/reference) publishes `https://d3aqoihi2n8ty8.cloudfront.net/actions/cheer/...` asset URLs. That evidence supports this exact hostname, not the `cloudfront.net` suffix or other distributions.

## Enabled additions

Counts below are selected category rule counts after source-approved exact exclusions and before semantic de-duplication across categories.

| Purpose group | Enabled categories and rule counts | Group rule assignments |
| --- | --- | ---: |
| Streaming | Netflix 24; Disney family 227; Prime Video 18; HBO/Max (`hbo` key) 64; Hulu 48; Twitch 9; Dailymotion 3; Vimeo 16; Paramount+ web entry points 2 | 411 |
| Music | Spotify 28; Deezer 2; Tidal 3; SoundCloud 3; Last.fm 4 | 40 |
| Downloads | Nyaa 2; RuTracker 9; The Pirate Bay 4; 1337x 10; OpenSubtitles web/API hosts 5 | 30 |
| Adult independent sites | Pornhub 9; XVideos 5; XNXX 5; xHamster 19; SpankBang 3; JavDB 4; JavBus 3; MissAV 7; Jable 3; E-Hentai 8; DMM adult 2 | 68 |
| Social | LinkedIn 12; Instagram 74; Facebook 397; X/Twitter 24 | 507 |
| Development | npm 3; Python 6; Anaconda 4; SourceForge 4; Atlassian 11; Figma 1 | 29 |
| News/reference | BBC 27; CNN 9; New York Times 9 | 45 |
| **Total** | **43 categories** | **1,130** |

Service groups remain distinct where already defined (including Netflix, DisneyPlus, PrimeVideo, HBO, and Hulu); Twitch, music, social, development, download, knowledge, and adult services use their configured purpose/service groups. No region node or node-member selection was hard-coded. Hulu's numeric priority is 49 and Disney is 50; the compiler orders the more-specific policy first and uses lower numeric priority for same-scope rules. This resolves the 48 same-domain Disney-family/Hulu overlaps without deleting either source: 47 are `SUFFIX` rules and `hulu.playback.edge.bamgrid.com` is an `EXACT` rule. Both retain their separate policy groups, with the overlap routed through Hulu first. The Disney key includes Hulu as a family member, so the 227-rule category must not be read as a Disney+-only endpoint inventory. Hulu-branded aliases and lookalikes inherited from the source key were not individually verified as official Hulu properties.

## Exact shared-host decisions

No shared infrastructure suffix was added to any service. Of 32 new exact rules under reviewed shared roots, one is retained and 31 are excluded with signatures scoped to category, key, file, domain, match type, and raw-line hash:

- Retained: `twitch / data/twitch / d3aqoihi2n8ty8.cloudfront.net` (`EXACT`), because Twitch's official API reference uses it for cheer asset URLs.
- Excluded: 25 other Twitch CloudFront hostnames, pending service-specific official evidence for each distribution.
- Excluded: five Prime Video CloudFront hostnames, pending Prime Video-specific evidence.
- Excluded: `disney.my.sentry.io` (`EXACT`), a monitoring host rather than a demonstrated playback dependency.

The source approvals preserve the six pre-existing exclusions and add 31 precise exclusions. There is no wildcard, `SUFFIX`, or global CDN approval. The resulting Twitch and Prime Video lists have partial CDN coverage; playback/download components that require the excluded hosts may not work until exact ownership and service need are established.

## Explicit exclusions and known gaps

- TikTok is absent. `Foreign-General` remains the existing empty manual fallback; no aggregate list was imported or attached.
- The broad v2fly `category-porn` key remains disabled; adult coverage consists only of the 11 independent site keys listed above.
- Apple Music and Apple service aggregates remain disabled/direct by default. No Apple global list was enabled.
- Amazon coverage uses only the `primevideo` key, not an Amazon-family key.
- No independent OpenSubtitles or YTS key exists in the pinned snapshot. OpenSubtitles therefore uses a separate, manually maintained five-host `EXACT` list (`opensubtitles.com`, `www.opensubtitles.com`, `api.opensubtitles.com`, conditional `vip-api.opensubtitles.com`, and `ai.opensubtitles.com`). The [official REST API docs](https://ai.opensubtitles.com/docs) identify `api.opensubtitles.com` as the API host and say login may return `vip-api.opensubtitles.com`; the [official OpenSubtitles forum](https://forum.opensubtitles.com/t/download-rate-limit/6415) documents a `www.opensubtitles.com` download URL. This list does not include `.org` legacy API hosts, Cloudflare/CDN parents, or inferred subtitle file hosts; downloads can still need unlisted dependencies. A `subscene` key exists but was not selected because an upstream label alone did not establish current service ownership/coverage. YTS remains off because no independent pinned key or currently validated official host was established.
- There is no standalone `paramountplus` key in the pinned snapshot. The `cbs` key contains CBS services as well as an annotated Paramount Plus subsection, so its flat family scope was not enabled as a Paramount+-only substitute. Paramount+ instead receives only exact `paramountplus.com` and `www.paramountplus.com` desktop entry points, based on [Paramount+ support instructions](https://support.paramountplus.com/s/article/PI-How-can-I-watch-Paramount-on-my-Desktop-Web-Browser?language=en_US). This does not include API, authentication, player, media, regional, or CDN dependencies, so it is front-door-only partial coverage. The `hbo` key includes Max-branded domains and remains reviewed as the HBO/Max family list.
- YouTube Music has no standalone key or policy category, but an `explain` query against the final candidate confirms `music.youtube.com` matches the existing `youtube.com` suffix rule. That rule wins in the YouTube policy group; the shared Google `youtube.com` suffix is also present. Shared `googlevideo.com` ownership and routing were left as-is. This is not an uncovered-domain claim or a dedicated Music route.
- This pass does not claim comprehensive PT coverage, private trackers, or every service named in the request. Only independent keys present at the reviewed snapshot were considered; no private tracker names or sources were invented.
- Unsupported rule syntax remains exact and narrow: seven selected-key `REGEXP` signatures are report-only and not converted; the invalid single-label `hbo` and `bbc` `DOMAIN` signatures remain rejected in reports. Across all active categories, 27 unsupported signatures are retained in the build review output.

The net additions are intentionally partial: service lists can lag current vendor endpoints, source membership can include aliases or third-party domains, and absent keys/domains remain direct unless separately reviewed and added.
