# 新增类别发布与管理器更新收据 — 2026-10-12

用户授权提交、推送与部署。本轮代码与正式规则已经发布，NAS 管理器已更新，85 类新规则已完成双端准备并按 NAS 备用 → VM104 主路径激活，最终受限 SSH 读回一致。用户在自己的终端完成 NAS helper 的 sudo 更新后，主 Agent 核对哈希并继续部署；人工开关仅在激活窗口开启，完成后已关闭。定时构建和下载保留，不执行自动激活。

## 已完成

- 代码提交并推送：`d22d97443403dd7e7938b6b797e3e20be12135d2`，本地完整测试 191 项通过。
- [测试 CI](https://github.com/987N/dns-rule-builder/actions/runs/38202527344)、[镜像 CI](https://github.com/987N/dns-rule-builder/actions/runs/38202527416)、[正式规则发布](https://github.com/987N/dns-rule-builder/actions/runs/38202558576)成功。
- 正式规则：[rules-bf54299122de955e3e048add2621ee61f39c7975637c93ca80dc0c541fe4edc2](https://github.com/987N/dns-rule-builder/releases/tag/rules-bf54299122de955e3e048add2621ee61f39c7975637c93ca80dc0c541fe4edc2)，数据提交 `0fae7627a84fd2f4d097ba472995ce21f29bfc77`。外置 SHA256SUMS、包内产物、完整 manifest、代码与数据身份通过校验；85 个启用类别、3,944 条分类规则、3,546 个独立语义、100 个 Provider、0 个未解决冲突。
- NAS 运行镜像：`ghcr.io/987n/dns-rule-builder@sha256:c98f3c689bb2866323b388cd123d4f3cb7f36efd6664d2f5202caa14167ca030`。Linux/amd64 和 OCI revision 对应上述代码提交；仅重建 `dns-rule-builder`，容器 healthy，HTTPS `rules.lclwf.date/healthz` 返回 `ok`。
- 管理器专用凭据实际触发的[构建](https://github.com/987N/dns-rule-builder/actions/runs/38202937953)成功，复用同一正式 Release；管理器任务 `63ff4b3a-ef41-481a-bd38-444acaef42e0` 成功，候选已提升至 `bf542991…`，`stale=false`，已保存的数据版本一致。任务收据在 NAS `/data/receipts/build-20261012-d22d974.json`（容器内路径）。
- VM104 与 NAS 只更新 `target_runtime.py` 和安装清单，属主 root、模式 `0644`，新 SHA256 `4fbb36365db9626e98dc1b644b5b31a4da82183448bbad51477634495c29bb07`。helper 更新本身没有重启 DNS 服务；后续规则激活按正常流程加载新配置。没有变更 target 配置、入口或访问控制。

## 恢复材料

- 管理器在线 SQLite 备份 `/vol1/1000/DockerData/dns-rule-builder/backups/pre-update-20261012-d22d974/controller.sqlite3`，完整性检查 `ok`，同目录保留 candidate/previous 链接目标。
- NAS 栈和 target 配置备份 `/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261012-d22d974/`，目录 `0700`，含原 image-digest、Compose、install 脚本与 target 配置。原管理器镜像 digest 为 `66f3b197be352fb1f2a379e21a4512ef60fb5b4ac6d0ff53bfd4a1a5d0196721`；需要恢复管理器时使用旧 digest 和同一 Compose 单服务重建，保留持久化数据。
- VM104 与 NAS helper 的旧模块、清单与入口各保留在设备上的 root-only `/var/backups/dns-rule-builder/helper-update-20261012-d22d974/`。部署状态保留双端 previous 规则版本 `215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea`，旧规则和集成配置仍保留，可按该部署请求使用受限 helper 的规则回退流程。本轮没有实际执行回退，不声称回退已实测。

## 已完成的规则部署

准备请求 `e73e02a7-f5a1-409a-9e15-1c22aeb6d6c0`、准备任务 `011bdaa5-ce28-4211-898e-2e02cba3f8a5` 与人工部署任务 `29e2946d-4597-4c3b-8e56-d48b03d9a3ca` 均成功。双端准备与目标程序验证通过后才开启人工开关，激活顺序为 `nas-backup` → `vm104-main`，未触发回退。NAS 本地收据为 `/data/receipts/prepare-20261012-d22d974.json` 与 `/data/receipts/manual-20261012-d22d974.json`（容器内路径）。

最终受限 SSH status 读回时间为 `2026-10-12T04:27:21Z`（北京时间 12:27）：两端均为 `bf54299122de955e3e048add2621ee61f39c7975637c93ca80dc0c541fe4edc2`，部署开关 `false`；NAS 管理器与备用 SmartDNS healthy，VM104 SmartDNS/Mihomo active。VM104 新旧集成元数据的 9 个区域组定义摘要、保护直连例外摘要均一致。激活流程校验配置与服务读回并恢复所选策略组；没有更换节点、DNS 上游、OPNsense、DNSDist 或 Fake-IP 路由。

新 Release 的公开 manifest 仍保留恰好两个标准门禁：目标语义验证与人工部署总开关；人工激活使用绑定该 Release、目标二进制和配置的有效本地验证记录，不修改公开 manifest。来源审核与发布门禁已通过；开关重新关闭不表示已经激活的版本被撤销。

## 有限真实请求与边界

更新前留存网关、VM104 主 SmartDNS 与 NAS 备用的 `www.baidu.com`、`store.steampowered.com`、`music.youtube.com`、`www.linux.org` UDP/TCP A 共 24 次基线：22 次 NOERROR，NAS 对 YouTube Music 的两次 NXDOMAIN 为既有拒绝行为。更新后相同 24 次检查中，17 次 NOERROR、NAS 三个国外域名共 6 次预期 NXDOMAIN；百度保持真实地址，主路径三个国外域名返回 Fake-IP。首次主 SmartDNS Steam UDP 查询在 2 秒内超时，其 TCP 检查成功；随后在主 SmartDNS 和网关分别做 UDP/TCP 各 3 次 Steam 查询，12 次全部 NOERROR/Fake-IP。没有足够证据确定首次超时原因，不把复测成功解释为持续稳定性证明。

NAS 客户端使用正常系统解析且不设置 HTTP 代理进行有限 HTTPS 请求：百度 `200`/真实 IP `110.242.70.57`，Google generate_204 `204`/Fake-IP `198.18.0.20`，OpenAI 未认证 models `401`/Fake-IP `198.18.1.199`，Linux.org `403`/Fake-IP `198.18.1.207`。403 只证明收到站点拒绝响应，不证明 Linux.org 页面可正常使用；401 不证明 OpenAI 登录或产品功能。管理网页 HTTPS healthz 为 `ok`，实际返回的 `static/app.js` 哈希与已发布源码一致。

没有验证全家客户端缓存、AAAA/其他记录类型完整矩阵、故障切换、流媒体播放或长时间稳定性。规则部署与有限验收已完成，这些边界保留。
