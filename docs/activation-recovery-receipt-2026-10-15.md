# 自动部署中断修复与恢复收据 — 2026-10-15

用户报告 `vm104-main: SSH activate failed (exit 1)`、部署状态 `manual_intervention`，并要求分析、使用 Luna 解决；此前已授权验证、推送与部署。Luna 实现主端运行时修复并复核管理器恢复边界，主 Agent 独立验收、提交和执行生产操作。以下为本次有限验证。

## 事故事实与原因

- 北京时间 04:17 的每日任务 `b5674e95-b143-5ca5-9632-f3b425b8379b`，规则 [CI](https://github.com/987N/dns-rule-builder/actions/runs/38630745404) 成功；准备/部署请求 `0c96e064-5adb-4d81-9181-9fab0b6a27d3`。候选 `7705b63ed00eff24a6af518d45f09fd505d02a8e1af2a1ffc3fafe1abb03728b` 相对实际基准 `2ecc7b4c9b202fad72967254b61f98f8d5855739e886bf1bbd798de073f3e576` 新增 0、删除 0、策略变化 0，通过常规更新判定。
- NAS 备用端激活完成；VM104 服务配置已写入候选、SmartDNS/Mihomo 已重启，但 `current` 和活动标记仍指向旧版，留下 `activation-intent.json`。服务均 active，检查时 DNS 仍能响应；不能把部署中断等同于完整网络中断。
- 确定的恢复缺陷：运行时发现激活意图就拒绝返回稳定版本，而管理器回退前要求双端普通 status 均成功，因此没有进入已有的目标回退方法，最终卡在人工干预。
- 可能的初始触发：Mihomo systemd 启动完成后控制 API 仍有短暂就绪窗口，旧代码立即恢复策略选择且没有等待/重试。日志时间、已写入配置和未完成标记符合此路径；旧 helper 隐去具体异常细节，因此不能断言精确异常已被证明。

## 修复与验证

- 实现提交 `6534e5c5fd292216bcb3965855a1b9c79341d6e2`，重启后显式恢复授权补充提交 `8dba27a9608f300ec416e9709c8883466c7bd6a5`，均已推送 main。
- 主端等待控制 API、策略组与成员就绪，暂态网络/HTTP 故障在有界时间内重试；授权和结构错误继续失败。激活意图增加无敏感信息的阶段标记。
- 新增受限 `recovery-status`，严格核对本次候选、上一版本、集成记录、配置 hash、guard、指针与标记；管理器收齐双端证据后才按逆序回退。未知第三方状态仍停止，普通 status 不伪造稳定版本。显式恢复通过现有认证 API 发起，授权随状态持久化，重启可续跑。
- 规则、配置、审核及托管产物内容完全相同的版本更新记为无需更新，避免无意义重启；程序或政策变化仍需批准，既有检查失败不会被批准绕过。
- 主 Agent 最终完整测试 `271 passed`；JavaScript 语法与 diff 检查通过。保留既有 Starlette/httpx 弃用提示。Luna 独立复核显式恢复和自动恢复的边界，未发现阻断问题。
- 最终 [tests CI](https://github.com/987N/dns-rule-builder/actions/runs/38659706681) 与 [image CI](https://github.com/987N/dns-rule-builder/actions/runs/38659706544) 成功。镜像 `ghcr.io/987n/dns-rule-builder@sha256:0fd8396a1438a396e8aa0dea986ecbc62b4840f78fc489cdefd4580b160184cc`；NAS 拉取后核对 amd64、OCI revision 等于 `8dba27a...`。

## 备份、更新与恢复

- 先原位暂停 `automatic_deployment_enabled`，保留配置 inode，维护禁用仍为 false。操作前无活动任务。
- NAS 配置与原镜像引用：`/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-recovery-20261015/`。SQLite 在线备份、原任务及部署记录：`/vol1/1000/DockerData/dns-rule-builder/backups/pre-recovery-20261015/`，数据库 integrity_check 为 ok。
- VM104 受保护备份 `/var/backups/dns-rule-builder/recovery-20261015/before-recovery.tar`，目录 0700、文件 0600，SHA256 `70f59323e9597a6546c28a6a57235ad3a75613d9d1d1517201f86afcce210591`。含原 helper、配置、意图和版本指针，敏感配置只保留在设备上。
- 在目标操作锁内核对原 helper 备份后，原子更新 VM104 的 `target_agent.py` 和 `target_runtime.py`，刷新安装 hash 清单；未更改目标配置。NAS helper 沿用现有版本，本次 NAS 已完成激活、可用普通 status/rollback，不需要中断恢复扩展。
- Compose dry-run 后仅更新 NAS 管理器；回退镜像为 `sha256:deb89d000aac03c6d444090ea995fd86ac9256a23c76e8bb258c4cbefb057051`。更新后 healthy、重启次数 0，HTTPS health/login 通过，安全 cookie 保留。线上 JavaScript SHA256 与源码一致：`55ec75707f3a6bfedc14551e126f7d0bfb3f5a20e2169def7a35b92c919af287`。
- 新管理器通过正式受限 SSH 对 VM104 返回 `recovery_safe: true`，核对 NAS 为候选版本后，经 `/api/deployment/rollback` 创建恢复任务 `47940121-f6d1-46b6-bb3a-bca89c13cbd4`。
- `2026-10-15T00:26:24Z` 恢复成功，原部署状态为 `failed_rolled_back`、回退错误为空，双端读回 `2ecc7b4c...`。VM104 激活意图已清除、SmartDNS/Mihomo active，40 个 Selector 的成员和当前选择摘要与操作前一致。原每日任务保留历史报错，没有改写为成功，也没有手改数据库或删除中断证据来强行激活。

## 修复版本构建

- 管理器构建任务 `44e361f2-77dc-482d-8a5e-69a13684de8a`，请求 `642d3bf5-3aff-42d7-a8f4-81b866eef0e7`，对应 [规则 CI](https://github.com/987N/dns-rule-builder/actions/runs/38660050127) 成功。已保存规则数据保持 `967f2b63a0b898b5c14fe4a9ff37c416833eec59`。
- 下载并校验的正式候选 [Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-37db243d9037194b8f8c813f82997ac07ebe2d450ac9c0a097c24f3c70b99b97) 为 `37db243d9037194b8f8c813f82997ac07ebe2d450ac9c0a097c24f3c70b99b97`，非 stale。实时双端基准为恢复后的 `2ecc7b4c...`，新增、删除、策略及类别策略变化均为 0；审批原因为构建程序变化。85 个启用类别、3,945 条分类规则，去重比较基数 3,943，SmartDNS 独立语义 3,547。
- 双端重新准备任务 `55fe226d-7894-4230-897b-10fcb07aa3a6`，准备请求 `1fe9ac1c-f442-4ae6-9d31-43d4d6125a89`。生产自动开关仍保持 false，使用用户已授权的人工路径部署修复。
- 准备状态 validated 后，人工激活任务 `93b73dca-44ee-5052-9297-82b7723f8ac7` 于 `2026-10-15T00:32:13Z` 成功；记录明确完成 NAS 备用端、VM104 主端依次激活。随后再次受限 SSH 实时读回双端为 `37db243d...`，判定 `auto_allowed`、`unchanged: true`。
- 激活后 VM104 SmartDNS/Mihomo 均 active，40 个 Selector 的成员与选择摘要保持一致，无激活意图遗留；NAS 管理器和 SmartDNS 备用容器 healthy、重启次数均为 0。
- 激活后对网关 `10.77.0.1:53`、主端 `10.255.50.2:53053`、备用端 `10.77.0.4:53053` 查询百度、Steam 和 YouTube，各做 UDP/TCP A 请求，18/18 符合预期：国内真实地址，网关/主端国外 Fake-IP，NAS 国外 NXDOMAIN。NAS 主机正常网络路径 HTTPS：百度 200、Google `generate_204` 为 204、OpenAI `/v1/models` 未认证为 401，401 不证明认证 API 成功。回退完成后、再次激活前也做过同样检查，结果一致。
- 验收后原位恢复 `automatic_deployment_enabled: true`，配置 inode 保留，文件与暂停前备份逐字节一致，SHA256 `da6bc91cda3821739ee1157713dc1d3d69494889a598bd40302ccff773732c69`；维护禁用仍为 false，旧 `deployment_enabled: false` 保留。正式 HTTPS API 回读自动开关为 true、最新部署 succeeded、旧中断部署 failed_rolled_back；候选与双端实际版本一致。草稿干净、已保存数据 SHA 不变，无活动任务。
- 运行收据保存于 NAS `/vol1/1000/DockerData/dns-rule-builder/receipts/recovery-20261015/`，包含激活前判定和最终双端/恢复状态。本轮证明真实中断恢复和修复版本人工激活；没有制造新的域名变化或生产故障注入来验证下一次无人值守激活。

## 恢复边界

管理器可使用上述旧不可变镜像、同一安装脚本恢复；规则恢复必须通过已验证的部署记录和双端读回，不能只改 symlink。VM helper 原版本和安装清单保留在受保护备份中。未更改 OPNsense、DNSDist、DNS 上游或 Fake-IP 路由。本轮不代表所有客户端、全部记录类型、完整故障矩阵或长期稳定性。
