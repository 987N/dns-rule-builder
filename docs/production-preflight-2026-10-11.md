# Production migration preflight — 2026-10-11

This is a read-only service baseline and a candidate integration contract, not a deployment receipt. The only NAS write so far was tightening the user-prepared GitHub PAT file mode to `0600`. No rules, DNS upstreams, running service configuration, Caddy route, or controller state have been switched.

The original 17-category release and initial image are recorded in [publication-receipt-2026-10-11.md](publication-receipt-2026-10-11.md). The user subsequently selected expansion of common independent foreign services before the first production switch. The expanded release and final deployment-capable image must receive fresh acceptance; the initial image is not the production deployment image.

## Observed service baseline

Both targets currently use legacy rule version `a4825b3e7243e8a14116f04553b6e1e2160111508a1f12a5c7ec59755459d5be`.

| Item | Observed value |
|---|---|
| VM104 | `10.255.50.2`, active SmartDNS and the only Mihomo |
| VM104 SmartDNS | `1.2026.v48.4.2`, `/usr/sbin/smartdns`, service user `smartdns`, group `router-services` |
| VM104 SmartDNS configuration | `/etc/router-services/smartdns.conf`, SHA-256 `a9e8f20f2f858e599b1ae3204f7dff65a00dc4a272b4982e12fc355e3290f6c0` |
| VM104 SmartDNS binary | SHA-256 `79af6bc2dda96def69c219e8bf3685dcbc6cf2fa5daa3868dd99c2b5c9a8d8e6`; requires existing `LD_LIBRARY_PATH=/usr/local/lib/smartdns` |
| VM104 Mihomo | `v1.19.32`, `/usr/local/bin/mihomo`, service user `mihomo`, group `router-services` |
| VM104 Mihomo configuration | `/etc/router-services/mihomo.yaml`, SHA-256 `8db4584c0e731bc4c931ae75d2e780ae37b72ad09e3a796c90e6509e6415e106` |
| VM104 Mihomo binary | SHA-256 `6de241191ca39f9ac1cd6e3194fc9303c19055d3549c8bdb4ff1349c89173819` |
| Mihomo directories | HomeDir `/var/lib/router-services/mihomo`; existing `SAFE_PATHS=/etc/router-services/rules` |
| NAS backup | `10.77.0.4:53053`, container `smartdns-backup`, observed healthy; no second Mihomo |
| NAS SmartDNS image | `pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff` |
| NAS SmartDNS binary | SHA-256 `6a976bcfa562121afa51f4a08106ea740251aba7d86b52490bd4852a5bbf537d` |
| NAS SmartDNS configuration | `/vol1/1000/DockerData/_compose/stacks/smartdns-backup/config/smartdns.conf`, SHA-256 `987af01b3fcf27ca700ef8abab290416d103f138f78e7d87480f536d242bef09` |
| Existing Web entrance | `rules.lclwf.date` → `172.27.0.1:18080`; Caddy configuration SHA-256 `0893d496ac7eba6693138b4a9c693de73920cd11d684a88bd183f3b0a4b96fee` |

These observations were made before preparation. Recheck the live hashes, pointers and service state before taking the final backup or activation; a later drift must invalidate the local validation record.

## Preserved policy and DNS boundaries

The nine existing region groups are `香港自动策略`, `台湾自动策略`, `日本自动策略`, `韩国自动策略`, `新国自动策略`, `美国自动策略`, `英国自动策略`, `法国自动策略`, and `德国自动策略`. They are existing `url-test` groups. Their full definitions, provider filtering, member nodes and internal settings remain intact. New and affected service groups become manual `select` groups referencing these groups, with `ProxySelect` as the default for new groups. Existing selections must be read and restored where compatible; a specific-node selection requires a verified mapping or the generic selector fallback. `DNS-PROXY` and subscription/provider configuration are outside the migration scope.

Retain the seven existing DIRECT providers: `LAN`, `Direct`, `ChinaDownloadCDN`, `iCloudChina`, `ApplePushNotificationService`, `AppleSoftwareUpdates`, and `ESET_China`. Pin their protected payloads in the staged local target snapshot. Preserve the existing non-Provider rules and final MATCH behavior. Replace the other legacy managed RULE-SET routes with the selected release's ordered dynamic Providers.

Fourteen legacy Direct scopes overlap the published Google rules:

- EXACT: `alt1-mtalk.google.com` through `alt8-mtalk.google.com`.
- EXACT: `clientservices.googleapis.com`, `dl.google.com`, `dl.l.google.com`, `mtalk.google.com`, `update.googleapis.com`.
- SUFFIX: `googletraveladservices.com`.

These remain local, target-attested Direct exceptions. The public release's valid Google parent domains are retained and its manifest is not rewritten. Both target candidates must have matching nameserver/address ignore exceptions with exact versus suffix semantics. NAS also retains its existing `address /domain-set:proxy-domains/#` binding.

## Independent isolated evidence

Using the pinned NAS SmartDNS image, a network-isolated namespace and two local fixture upstreams, the main Agent checked:

- `nameserver /-.mtalk.google.com/-` sends the exact name to the default fixture upstream; its child still follows the proxy suffix.
- `nameserver /googletraveladservices.com/-` sends root and child to the default upstream.
- Google parent rules still select the proxy fixture upstream; an uncollected name selects the default upstream.
- With NAS's SOA address-set binding present, corresponding `address /.../-` exceptions restore default upstream answers for the same Direct scopes.

This proves the tested rule semantics for the pinned binary, not successful access to Google or other real services. The final target configurations still need the actual target-version isolated checks, actual service-identity readability, and limited real requests after activation.

## Credential and installation readiness

The user prepared the NAS-specific PAT at `/vol1/1000/DockerData/_compose/secrets/local/dns-rule-builder/github_token`. Read-only repository/workflow requests returned HTTP 200. This does not by itself prove write/dispatch permissions. Its file mode is now `0600`, owner `1000:1001`; installation must change ownership to `10001:10001` before the ordinary UID container can read it. The credential value was not copied to the workstation or printed.

Reuse the existing Web password only by mounting its explicitly allowed single file. New session and deployment-key files remain to be provisioned on NAS. The current NAS account cannot run noninteractive sudo. Finish and review the minimal installer before requesting the user's one interactive sudo step; never use a privileged Docker mount to bypass that boundary.

The NAS single-service Compose dry-run passed independently. Its temporary backend is limited to `127.0.0.1:18081` and `172.27.0.1:18081`. Caddy stays on the old backend until both target activations/readbacks and limited real requests pass. Full device-local backups and restore checks remain prerequisites; none has yet been claimed as completed.

Automatic deployment remains disabled. This preflight does not establish production activation, end-to-end client coverage, or a rollback exercise.
## 补充：切换前有限 HTTPS 基线

从 NAS 以普通用户执行无认证、无 Cookie 的两次 HTTPS 请求：`https://www.baidu.com` 返回 HTTP 200，连接真实地址 `110.242.70.57`；`https://www.google.com` 返回 HTTP 302，连接 Fake-IP `198.18.0.20`。两次 curl 均正常退出。这证明当时 NAS 的这两个有限请求可完成；Google 重定向未继续跟随，不代表流媒体、成人网站、字幕下载、所有客户端或故障场景已验证。执行期间未修改 DNS、代理配置或服务。
