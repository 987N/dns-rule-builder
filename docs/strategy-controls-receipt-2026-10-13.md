# 策略组控制实施收据（2026-10-13）

## 当前阶段

策略组控制已发布并上线。2026-10-13 11:35 UTC 回读确认 VM104 helper 和 NAS 管理器均已更新；正式接口的 23 组读取及全部成员预览通过。用户关闭旧测试页后，剩余页面内确认、取消、失效成员修复和窄屏操作也已在隔离浏览器补齐验收。

初次上线按既有授权只做目录读取和成员预览，没有改变生产策略组成员、当前选择或规则版本。安装前已分别备份 VM104 组件和 NAS SQLite/部署文件；DNS 与 Mihomo 未重启。

## 已实现行为

- Web 新增“策略组”工作区，仅编辑当前活动规则构建器管理的 `select` 组。
- 当前选择通过受限 SSH 目标操作切换、回读并持久化。候选成员修改先预览，再确认应用；目标离线校验完整配置、重载 Mihomo 并恢复其他选择。
- VM104 是策略偏好的权威保存位置；NAS 保留任务和审计内容。节点偏好、订阅 URL 和认证信息不写入公共规则仓库。
- 引用包含明确的类型、名称和订阅来源。目录只使用配置明确声明的真实订阅，排除 Mihomo 返回的 Compatible/default 虚拟条目。重名和失效引用保留提示，受影响的后续应用或部署要求先修复。
- 策略操作与规则部署共用目标锁，采用偏好版本和预览摘要防止旧请求应用；已验证配置的策略覆盖不得掩盖无关配置漂移。
- 规则准备、部署及受支持的回滚保留有效偏好。维护禁用时仍可读取和预览，修改任务在受理及执行时均检查维护开关。

## 发布与验收证据

- 源码提交：[`2ed9ab71a7d65737098e5140484e6629b44dc2ad`](https://github.com/987N/dns-rule-builder/commit/2ed9ab71a7d65737098e5140484e6629b44dc2ad)。
- 主 Agent 独立全套测试：`218 passed`；另有一项现有 Starlette TestClient 弃用提示。`node --check static/app.js` 和 `git diff --check` 通过。后续 UI 改动重新通过 JavaScript 语法检查及 CI。
- [GitHub tests](https://github.com/987N/dns-rule-builder/actions/runs/38350903997) 和 [image 工作流](https://github.com/987N/dns-rule-builder/actions/runs/38350904034) 均成功。
- 最终 UI 提交：[`cb26de4826d99336d1f7053bd2d35cd390107c36`](https://github.com/987N/dns-rule-builder/commit/cb26de4826d99336d1f7053bd2d35cd390107c36)，改用页面内成员应用确认，移除原始引用 JSON 并明确偏好保存位置。[最终 tests](https://github.com/987N/dns-rule-builder/actions/runs/38390056672) 和 [最终 image](https://github.com/987N/dns-rule-builder/actions/runs/38390056632) 均成功。
- 已安装最终镜像：`ghcr.io/987n/dns-rule-builder@sha256:558555297bf49521459673ae63e6a02f87acb83410f930ed724979a54a9c65d3`。检查为 `amd64`、OCI revision 等于 `cb26de4` 完整提交；容器 healthy、restart count 0，实际 HTTPS `/healthz` 返回 `{"status":"ok"}`。
- 与现网相同的 Mihomo `v1.19.32` 官方二进制，在本机隔离实例中验证了订阅节点精确过滤、成员应用、节点选择及回读、其他组选择保留、偏好保存、旧版本并发请求拒绝、marker 写后中断恢复和无关漂移拒绝。
- 主 Agent 独立隔离 API 夹具验收通过：登录、23 组、可选/歧义订阅节点、维护关闭时读取和预览可用且拒绝修改、失效成员修复、任务轮询、冲突后的恢复。该夹具不连接家庭网络，不证明浏览器点击或真实服务访问。

## 生产基线、安装与验收

两目标活动规则版本仍为 `58baae5be4d2fc47582d743b9542d7c868879e04ff9e4958d9c464ba27c3b4ec`，安装后实时双目标回读一致。此前管理器镜像为 `sha256:bfe70e2ea7f1cb66c748e84a8d1d23bdaaf74f6f8cf656e2e52b9146547b3669`，仅管理器容器被重建。

VM104 上受管组为 23 个，每组当前 11 个成员；实际配置仅声明一个订阅来源，共 52 个订阅节点。原接口的 449 项统计包含虚拟组条目，不能作为真实订阅节点数量。

安装前用暂存代码读取真实目录，确认 23 组、52 个订阅节点、完整 11 项成员及实际选择可回读；脱敏目录序列化大小为 41,969 bytes。实际 VM104 Mihomo `v1.19.32` 对临时 AI 候选配置的离线验证通过，临时文件已清理，实际配置未改变。

已安装的 helper 哈希（对应 `2ed9ab7`，最终 UI 提交没有改变模块）：

| 模块 | SHA-256 |
| --- | --- |
| `target_agent.py` | `953d0b7870d9473412f3055072f4e04b58c16bd9900b54627108a435ab5103f7` |
| `target_runtime.py` | `a7ad9819a03b91e8b97368ff0d0c01d3869b8eff94d6360d5724fd03cd651c17` |

NAS SmartDNS-only helper 保持原接口。目标配置与备份逐字节一致，开关仍为 `deployment_enabled: false`、`maintenance_disabled: false`；Web 人工维护可用状态为 true，不代表启用自动规则部署。

备份位置：VM104 `/var/backups/dns-rule-builder/strategy-controls-20261013-2ed9ab7`；NAS 部署文件 `/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261013-cb26de4-strategies`；NAS 数据库 `/data/backups/pre-update-20261013-cb26de4-strategies/controller.sqlite3`（容器内路径，SQLite integrity_check 为 ok）。VM104 安装脚本支持失败时恢复旧模块与安装清单；NAS 可恢复旧不可变镜像及部署文件，数据库需要在管理器停止时恢复。

正式登录/API 验收：偏好 revision 0，23 个组均为 11 个成员，选中项与实际选择一致；52 个真实订阅节点均来自配置声明的订阅。全部 23 组成员预览通过，预览前后目录完全相同。前后比对活动规则、全部组成员与选择、运行配置及保护配置哈希均相同。VM104 Mihomo 与 SmartDNS 均为 active。候选仍为 `942a6fa5f39f4b72f3a2bca919ee43c1bdf33bd7e506fae5e9cf53d64e274e7b`，草稿 dirty 为 false；没有执行生产 select/apply 或规则激活。

## 验收边界

本地隔离浏览器已实操登录、工作区导航、立即切换并保存及成功回读、候选成员编辑和预览。用户关闭阻挡输入的旧测试页后，新版页面内确认的取消保持原成员和选择；确认后任务成功，新增成员和保存选择均正确回读。失效成员取消勾选、预览、确认、任务完成后，成员数减少一项、失效提示消失且有效选择保留。390×844 窄屏下筛选、预览及确认框取消均可用；页面 scrollWidth 等于 390，确认框左右边界为 14/376，无横向溢出。截图保存于本地测试产物，未包含生产节点偏好。

上述 GUI 使用隔离 fixture；实际配置应用由真实 Mihomo 隔离实例验证，生产接口仅做读取和预览，没有将测试成员或选择写入家庭网络。这些证据分别证明对应层级，不等同于生产成员应用或外部目标连通性。

旧规则准备凭证已经过期；下一次规则激活前应重新准备以绑定策略偏好。此次上线没有验证全家客户端、WAN DNS 泄漏、故障切换或外部目标端到端可用性。测试 fixture 已停止。
