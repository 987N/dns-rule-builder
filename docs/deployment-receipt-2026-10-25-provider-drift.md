# Proxy provider drift 修复发布记录 — 2026-10-25

## 已完成

- 源码提交 `da11e2faed6e418f01318d6af94a9799b6710bff` 已推送 `main`。GitHub tests run `40168193857` 与 image run `40168193871` 均成功。
- 发布镜像：`ghcr.io/987n/dns-rule-builder@sha256:8e458323a7df481295668e7b5229649809f39dad0a22f920814445610cf3271c`。NAS 拉取后确认 `amd64`，OCI revision 与源码提交一致。
- 更新前 NAS manager 为 healthy，image digest `sha256:0fd8396a1438a396e8aa0dea986ecbc62b4840f78fc489cdefd4580b160184cc`、OCI revision `8dba27a9608f300ec416e9709c8883466c7bd6a5`，restart count `0`；更新前无 queued/running/polling/downloading 任务。
- 更新前备份位于 NAS `/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261025-da11e2f/`，包括 Compose、targets 配置、image-digest 和 container inspect。SQLite 在线备份位于 `/vol1/1000/DockerData/dns-rule-builder/backups/pre-update-20261025-da11e2f/controller.sqlite3`，`PRAGMA integrity_check` 为 `ok`，SHA256 `fd3cfd7ca81521d3dcbf3ecabf1f0f8f8d7825c71aa71d5d98c7ebfa18e80e87`。
- VM104 helper 备份位于 root-only `/var/backups/dns-rule-builder/helper-update-20261025-da11e2f/`。只更新 `target_runtime.py` 与安装清单；新 runtime SHA256 `4dd003a7608df9e51805252d628870bc69413e8db8e5b903545fbcc8eea0a6a8`。`/etc/dns-rule-target-v4.yaml` SHA256 保持 `052b95b8e944c8edd94058802fcb3000d5bf32bbfd5cc1c42b60b1ededed0457`；guard 仅绑定 SmartDNS/Mihomo systemd unit。未更改目标配置或重启 DNS 服务。
- NAS helper 保持原状：该目标仅管理 SmartDNS，其规则哈希/接口不受 `proxy-providers` 漂移修复影响。新 manager 与旧 NAS helper 的受限 `status` 接口兼容。
- NAS manager 单服务已使用不可变 digest 重建，未触碰依赖服务。当前容器 healthy、restart count `0`，实际镜像为上述 digest、OCI revision 为 `da11e2faed6e418f01318d6af94a9799b6710bff`；HTTPS `/healthz` 返回 `{"status":"ok"}`。
- 更新 helper 和 manager 后，新 manager 通过受限 helper 读回 NAS `nas-backup` 与 VM104 `vm104-main` 均为原 release `db3579959a275078edad73beab92bbe398139b123d806db290adce636b934487`。task 结束后再次受限读回仍相同；persisted `target_actual_status` 于 `2026-10-25T06:28:11Z` 记录双端同版本，`active_tasks=0`。`deployment_enabled` 保持 `false`，`automatic_deployment_enabled` 保持 `true`，`maintenance_disabled` 保持 `false`。
- VM104 唯一 `proxy-providers` 项的规范化 SHA256 为 `e132e50e43a261f46ad25d15ce3136add3ea3c6f6d1ecb07c2b88a521297a2b1`，仅保存摘要；审核后再次计算摘要相同。VM104 完整 Mihomo 配置 SHA256 与更新前独立读回完全相同（`e1ce825923156bec852a41061e549f851dce545f130663d0758fbe479ba68401`），证明 helper 更新期间订阅配置未被写入。
- 主 Agent 独立完成有限 DNS 检查：VM104 SmartDNS `10.255.50.2:53053` 与客户端 DNS CT106 `10.255.50.3:53`，百度和 Google 的 UDP/TCP 共 8 个请求均为 `NOERROR`；百度返回 `110.242.70.57`，Google 返回 Fake-IP `198.18.0.20`。

## 正式生成流程结果与限制

- manager 正式 `POST /api/deployment/generate` 返回 `202`，任务 `7f10d8bf-d426-4fa2-b953-14ddf87783c9` 触发 rules workflow `40168916893`。workflow CI 成功，但 manager 依据该 run 的审核报告将任务标记为 `blocked`，错误为“构建完成，但来源/许可证发布审核门禁阻止发布”。没有形成可用候选 Release，也没有进入目标 prepare、deployment decision 或 activation；双端仍保持原版本 `db3579959a275078edad73beab92bbe398139b123d806db290adce636b934487`。
- 审核报告所列未发布产物标识为 `99e243d49793bfd7d1f652a67d186ac6b063bba119dca8484eb5078e112d29b8`，对应 data commit `390d777b0cb504aca260e395b56e7efbd142451a`、code commit `da11e2faed6e418f01318d6af94a9799b6710bff`。发布门禁有 4 项：`microsoft` 与 `xbox` 的 V2Fly source include selectors 均需复核；新增 shared infrastructure 的 `openai` exact rule `o33249.ingest.us.sentry.io` 和 `twitch` exact rule `d3stzm2eumvgb4.cloudfront.net` 扩展了规则范围、需复核；规则或来源需先完成人工审核。未绕过审核门禁、未改变规则数据、部署开关或目标状态。该流程没有产生可用于计算规则增删量或 `approval_required` 的已发布候选，因此不能报告规则 diff 数量。
- 本次操作前未单独记录 `proxy-providers` 规范化哈希。VM104 完整 Mihomo 配置 SHA256 在 helper 更新前后独立核对相同（`e1ce825923156bec852a41061e549f851dce545f130663d0758fbe479ba68401`），更新期间没有写入订阅配置；当前 provider 规范化 SHA256 为 `e132e50e43a261f46ad25d15ce3136add3ea3c6f6d1ecb07c2b88a521297a2b1`。审核门禁阻止了 candidate 发布与应用，因此不存在规则更新后的 provider 对照值。
- manager healthy、双端 helper status、有限 DNS 检查证明本次代码和管理器更新已运行；它们不代表规则 Release 已发布或激活。解除来源/规则审核 blocker 后，需通过正式 manager 流程重新生成、比较并按现有批准门禁决定是否应用。
