# 首次生产迁移收据 — 2026-10-11

本轮按用户批准的“发布 → 准备 → 双端验证 → 人工激活 → 有限真实请求 → Web 切换”执行。NAS 备用 SmartDNS、VM104 主 SmartDNS/Mihomo 已读回同一规则版本；新管理器已接管 `rules.lclwf.date`。旧 review/controller 已停止且 restart policy 为 `no`，原数据、配置和恢复材料保留。自动部署关闭，每日北京时间 04:17 构建及成功后的下载保留。

## 发布和运行身份

| 项目 | 已核对的值 |
| --- | --- |
| 正式 Release | [`rules-215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea`](https://github.com/987N/dns-rule-builder/releases/tag/rules-215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea) |
| 发布代码 | `c20c91e1a7ae937297fdd21424992dea62923a8c` |
| 数据提交 | `a08559c430fe43723f6f187046f0cecc27b0a5cd` |
| v2fly 快照 | `c6adacf29cc4fb05e04651fcf03e00989ef9f797` |
| NAS 固定镜像 | `ghcr.io/987n/dns-rule-builder@sha256:66f3b197be352fb1f2a379e21a4512ef60fb5b4ac6d0ff53bfd4a1a5d0196721` |
| 镜像 config ID | `sha256:35848757c3d9c3bc045910f355ff628813a4725f4dda53316998dd394625e55b`，Linux/amd64 |
| 规则规模 | 60 个启用类别、59 个非空类别；2,416 个 SmartDNS 域名，其中 SUFFIX 2,070、EXACT 346；63 个 Mihomo Provider 片段 |

[测试 CI](https://github.com/987N/dns-rule-builder/actions/runs/38150459512)、[镜像 CI](https://github.com/987N/dns-rule-builder/actions/runs/38150460793)成功。[正式规则工作流](https://github.com/987N/dns-rule-builder/actions/runs/38150573722)两次执行成功，并复用相同 Release 和资产。NAS 专用 PAT 实际触发的[构建](https://github.com/987N/dns-rule-builder/actions/runs/38150807991)也成功，随后管理器下载并校验该版本。旧六字段 rules-data 未改写。

下载校验包括 8 个外置资产、83 个包内产物及许可材料、SHA256SUMS、包内外 manifest 字节一致性、Provider 成员/顺序/策略映射及代码/数据/上游身份。镜像匿名拉取和普通 UID、只读根文件系统运行验证通过。公开 manifest 保持 `review_required=false`、`publish_allowed=true`、`deployment_allowed=false`；仅有目标语义未确认和部署总开关关闭两项阻塞。首次人工部署使用绑定本 Release、设备程序/配置及双端结果的本地验证记录，没有改写公开 manifest 或绕过其他审核。

## 设备准备和可恢复材料

NAS root 准备材料目录为 `/vol1/1000/DockerData/dns-rule-builder-install-materials/442e2a4`，清单锁定哈希 `5aa7d04df78e7d4da8c4c1e1e92c597d05e6a97d5be37997eee2284ab83f46d0`。root 收据 `/etc/dns-rule-builder-preparation-receipt.json`确认受保护备份完成：130 个文件、38 个目录、1 个 symlink、17,979,754 字节；原始备份位于 root-only `/vol1/1000/DockerData/dns-rule-builder-install-backups/`。收据不包含具体子目录名，本记录不猜测该名称。

旧 controller 停止后另取最终状态快照：`/vol1/1000/DockerData/dns-rule-builder/backups/pre-switch-20261011T154059Z`，7 个文件，清单 SHA256 `c4b729dbe43220754a8c3b8ef87517c8190e3f4b04f65a6f13a9130c2ca63096`。SQLite 使用在线备份并检查完整性；私有状态只留在 NAS。

VM104 完整备份为 `/var/backups/dns-rule-builder/pre-install-20261011T151922Z-23810`，清单 SHA256 `ddcf53baab5adb2f276ba76e67aa99534d6b775ad7c57f80f01fd6bf549576da`。保留完整原规则树、配置、unit/drop-in、授权及 helper 元数据和 21 个 selector 选择。原 schema-3 版本 `a4825b3e7243e8a14116f04553b6e1e2160111508a1f12a5c7ec59755459d5be`及旧链接仍保留，不能把旧链接存在误解为当前仍运行旧规则。

部署受限 SSH helper、独立 keys 和 host-pinned known_hosts，未修改系统 Python 依赖。NAS 六个 secret 分别单文件只读挂载，属 `10001:10001`、权限 `0600`；新 Web 密码独立于旧密码。本机 gh 凭据未复制。管理容器 UID/GID `10001:10001`，只读根文件系统、capabilities 全部移除、无 Docker socket，数据目录为 `/vol1/1000/DockerData/dns-rule-builder`。

实际程序验证使用 VM104 Mihomo `v1.19.32` 和两端 SmartDNS `1.2026.v48.4.2`。VM104 服务身份和 HomeDir/SAFE_PATHS 已核对；隔离验证另外允许实际 UI 路径，没有扩大生产服务的 SAFE_PATHS。准备中遇到父目录组权限和旧 DNS RULE-SET 引用问题，均先修复并重新 stage/validate，失败候选保留隔离，之后才激活。

部署 helper 与管理镜像分别记录：NAS helper 仍为最初 `442e2a4` 安装材料；VM104 已安装 `target_runtime.py` SHA256 `f336e50dfe8ea38417ab4b6037155ee2807b319874a575a8c66b96f5fd9be1fa`。后续源码增加 SmartDNS 重复 overlay 去重及下一 schema-4 更新兼容测试，该最新源码尚未装入两台设备，不代表已验证第二次生产迁移。后续人工升级应先更新和验证相应 helper；自动部署保持关闭。

## 激活和配置验收

准备 UUID `b101f493-6fee-446d-ba7c-322965cd5c53`，任务 `abb69a55-7979-4125-9172-5470b0d35cce`成功；人工部署任务 `18a8bb9f-e422-46cc-9d28-5e1aa9bd2fe9`成功。双端全部 stage/validate 后，按 NAS 备用 → VM104 主路径串行激活并读回。NAS 持久化收据位于 `/data/receipts/prepare-b101f493-6fee-446d-ba7c-322965cd5c53.json`和对应 `manual-...json`（容器内路径）。

最终再次受限 SSH 只读读回时间 `2026-10-11T15:52:30Z`，两端均返回 `215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea`，部署开关为 `false`。容器重建后的 HTTPS 登录仍可读到成功部署任务；当日定时任务日期和 GitHub 同步提交保留。

VM104 9 个区域组定义保持一致，定义摘要 SHA256 `38b28aa30dff4218f66b3b903a411b0b659a0e37b449a1d362d081f933d71dbb`。7 个受保护 Provider、7 个直连 RULE-SET 和 13 条静态规则保留。服务策略组手动选择 `ProxySelect` 或已有区域组；兼容选择 AI、Google、Netflix、Telegram 沿用。上游、节点/provider 定义、tun/TProxy 和控制 API 凭据保持原值。

14 个经审核 Direct 例外作为本地配置 overlay，不删除公开 Google 父域规则。两端 SmartDNS 各有 14 对 EXACT/SUFFIX nameserver/address 例外。Mihomo DNS rule filter 先直连例外，再按完整 manifest 顺序的 63 个 Provider 进入 fake-ip，最后 MATCH real-ip；旧失效 provider 引用被替换，未受管理 DNS 规则保留。

VM104 活跃配置 SHA256：

- Mihomo：`69d78e53a1854442fedbc289b38fc05a4b53fa876c0a0e385b14f39a9d00edcc`
- SmartDNS：`44ba3195d8bfc5a9cdc150268ff1529761b9cfe094e71ecb4a9827dbd16da1ae`

## 有限真实请求与 Web 切换

| 检查 | 实际结果与边界 |
| --- | --- |
| 主 SmartDNS `10.255.50.2:53053` | 7 个代表域名 × A/AAAA/CNAME × UDP/TCP，共 42 次 NOERROR |
| 网关 DNS `10.77.0.1:53` | 同一矩阵 42 次 NOERROR；总计主路径/网关 84 次 |
| NAS 备用 `10.77.0.4:53053` | 24 次 NOERROR，12 次国外 A/AAAA NXDOMAIN（既有拒绝语义）；5 次 SERVFAIL 和 1 次无状态的国外 CNAME 检查未通过，部署前也存在；未声称备用全矩阵通过 |
| 保护直连 | `mtalk.google.com` 在备用的 6 次查询恢复正常解析；网关仍为真实 IP，子域按 Google 后缀进入 Fake-IP |
| EXACT/SUFFIX | 22 个 UDP/TCP A 探针通过：`cdn.workos.com` 精确 Fake-IP、其子域无 Fake-IP；默认 `example.com` Real-IP；TMDB、Fanart、Pornhub、Netflix 代表域名 Fake-IP。无答案子域只证明未返回 Fake-IP，不证明其业务存在或可达 |
| 国内 HTTPS | NAS 客户端访问百度 200，目标真实 IP `110.242.70.57` |
| 国外 HTTPS | Google generate_204 返回 204，经 `198.18.0.20`；OpenAI API 返回 401，经 `198.18.1.199`，仅证明有限传输及未认证响应，不证明 ChatGPT 登录或功能 |

Caddy 仅把既有 `rules.lclwf.date` 上游 `172.27.0.1:18080`改为 `172.27.0.1:18081`。因现网是单文件 bind，保留原 inode 写入，验证后 `SIGUSR1` reload。先前用 loopback 443 的检查因 Caddy 只绑定 NAS LAN IP 而失败，已恢复旧内容，再用实际 `10.77.0.4:443`核对成功，没有调整监听或放宽 TLS。

切换前 SHA256 `0893d496ac7eba6693138b4a9c693de73920cd11d684a88bd183f3b0a4b96fee`；切换后 `f5fc6f111f4e7cbb5e008f68d4b643f84933d9e31628c175dfa40041e3137731`。原文件备份为 `/vol1/1000/DockerData/_compose/config/network/caddy/Caddyfile.before-dns-rule-builder-20261011T155056Z`。本地 NAS HTTPS 入口证书验证、healthz、独立新密码登录、Secure cookie 和部署历史读取通过；匿名部署 API 返回 401。这不等于外网回家入口或所有客户端验证。

新管理器和 NAS SmartDNS healthy；VM104 两个服务 active。旧 `homegateway-rules-review`、`homegateway-rules-controller` exited，restart policy 均为 `no`。未删除旧目录、镜像、卷或 Compose 恢复材料；旧 Compose 栈不应再作为正常管理服务启动。新管理器是本轮规则唯一写入者。

## 后续边界和恢复

保持当前覆盖，缺失服务以后经审核补充。未收录默认 Real-IP，TikTok 关闭，广泛 Apple 清单关闭。每日构建/下载与人工部署分开，首次成功不授予未来无人值守激活权限。

若仅 Web 失败，恢复上述 Caddy 备份、验证/reload，再按旧固定 Compose 单服务启动 review。若需恢复首次切换前规则，使用设备完整受保护备份恢复配置、完整 schema-3 规则树、链接和原属主权限，分别仅重启 NAS SmartDNS 和 VM104 SmartDNS/Mihomo，再读回并验证；不能仅恢复一个 symlink。新 target 的 rollback 仅在它记录的 previous 版本完整可恢复时使用。无法确认回退时冻结所有规则写入者，转人工处理。本轮没有实际执行生产回退。

没有改动 OPNsense、DNSDist、DNS 上游或 Fake-IP 路由；没有故障注入、卷删除或全栈重建。未覆盖所有家庭客户端、全部记录类型（HTTPS/SVCB/TXT 等）、完整故障矩阵、流媒体播放、成人网站页面或长时间稳定性。有限请求不扩大为上述结论。
