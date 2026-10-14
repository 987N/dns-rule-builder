# 常规规则自动部署上线收据 — 2026-10-14

用户批准验证、推送和部署，并要求使用 Luna。Luna 独立只读复核，主 Agent 负责提交、发布、生产变更与最终验收。以下记录为本次有限验证，不代表持续网络健康。

## 实现与发布

- 实现提交：`eb8e7e5e82f7f786f6462dd7b1fdcb856cc37510`，已推送 main。
- 常规更新自动部署，大改动等待人工批准。总体新增加删除超过 10% 且超过 50 条，或单类别超过 30% 且超过 20 条，需要批准；类别、策略、来源、程序和审核处置变化也需批准。现有硬门禁不被人工确认绕过。
- 定时入口保持北京时间 04:17；网页入口为“生成并应用”。基准必须实时双端一致且本机可验证，授权绑定版本、准备请求、策略版本、manifest 和短期验证证据。激活前再次读取基准与开关，使用现有双端激活及回退状态机。
- 主 Agent 与 Luna 的完整测试均为 `258 passed`，保留现有 Starlette/httpx 弃用提示；JavaScript 语法和 diff 检查通过。主 Agent 独立检查四种前端状态逻辑。
- [tests CI](https://github.com/987N/dns-rule-builder/actions/runs/38503086436) 与 [image CI](https://github.com/987N/dns-rule-builder/actions/runs/38503086411) 均成功。
- 镜像为 `ghcr.io/987n/dns-rule-builder@sha256:deb89d000aac03c6d444090ea995fd86ac9256a23c76e8bb258c4cbefb057051`，NAS 拉取后确认 amd64、OCI revision 等于实现提交。

## 基线、备份与管理器更新

- 更新前管理器为 `sha256:558555297bf49521459673ae63e6a02f87acb83410f930ed724979a54a9c65d3`，OCI revision `cb26de4826d99336d1f7053bd2d35cd390107c36`；容器 healthy、重启次数 0。
- 无活动任务，草稿干净，规则数据为 `967f2b63a0b898b5c14fe4a9ff37c416833eec59`。更新前候选为 `45017337ea301515c9e2bffdaae863c25d6537cf773181016f0f060779cba785`；实际运行版本与候选分开记录。
- 更新前受限 SSH 双端实时读回均为 `58baae5be4d2fc47582d743b9542d7c868879e04ff9e4958d9c464ba27c3b4ec`。原 `deployment_enabled: false`、`maintenance_disabled: false`，无自动部署字段。
- 部署文件与配置备份：NAS `/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261014-automatic-rules/`，目录 0700，包含 Compose、安装脚本、旧镜像引用、target 配置及 checksum 记录。
- SQLite 在线备份与目标基线：容器 `/data/backups/pre-update-20261014-automatic-rules/`，宿主机对应 `/vol1/1000/DockerData/dns-rule-builder/backups/pre-update-20261014-automatic-rules/`。数据库 integrity_check 为 `ok`，备份 SHA256 为 `a600cbc2f622a5a198cd0487e1ff49914db832de069d07604241a192b1b66168`。凭据未复制或扩大挂载。
- 原位增加 `automatic_deployment_enabled: false`，保留配置文件 inode；Compose dry-run 与镜像身份检查通过后，通过既有安装脚本只重建 `dns-rule-builder`，未更换 helper。
- 管理器更新后 healthy、重启次数 0，HTTPS health/login 通过，匿名 API 为 401，登录 cookie 为 Secure。实际 HTTPS `static/app.js` SHA256 与源码一致：`ee62eb70961eb59d708fad1e25fbf917472ff803749998b46ce470a9ffe5d44d`。
- 更新后、首次规则迁移前，候选与已保存数据保持不变，自动开关仍为 false。

## 首次规则迁移与自动开关

- 生产管理器构建任务：`54e1a54f-4729-4907-9773-585dafe83b7b`，请求编号 `05330584-8917-4036-985f-9f576a5ce713`。
- 初次触发没有对应 GitHub run。经多次 GitHub 和 NAS 同编号查询确认缺失后，使用工作站按原编号补发；管理器继续跟踪原任务，没有创建第二条管理器任务。[规则 CI](https://github.com/987N/dns-rule-builder/actions/runs/38503635971)。
- 规则 CI 成功，正式 [Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-2ecc7b4c9b202fad72967254b61f98f8d5855739e886bf1bbd798de073f3e576) 为 `2ecc7b4c9b202fad72967254b61f98f8d5855739e886bf1bbd798de073f3e576`，管理器下载校验成功且非 stale。对照实际旧运行版本：新增 0、删除 0、策略变化 0，类别配置一致；85 个启用类别、3,945 条分类规则、3,547 个 SmartDNS 独立语义。
- 旧规则不含新自动比较元数据，因此此次通过明确人工路径迁移：准备任务 `3f800e46-f5cc-4eca-a6af-a36f7b996de1`，准备请求 `a7d18386-9c1e-4b8b-bfe2-9749e72a6d7a`，双端 validated。人工激活任务 `65ce5de3-dd9b-524e-a7ad-3d58d8271a3a` 成功，状态记录 NAS 备用端、VM104 主端依次完成。
- `2026-10-14T03:02:00Z`（北京时间 11:02）部署读回双端为新 Release；随后再次受限 SSH 读回一致，目标状态检查通过。23 个策略组的成员、保存选择和实际选择摘要与迁移前一致。
- 迁移后检查网关 `10.77.0.1:53`、主端 `10.255.50.2:53053`、NAS 备用端 `10.77.0.4:53053`，三个域名各做 UDP/TCP A 查询，18/18 符合预期。百度返回真实地址；网关与主端 Steam/YouTube 返回 Fake-IP，NAS 备用端二者为既有 NXDOMAIN 拒绝语义。
- NAS 主机经正常网络路径发出的 HTTPS 请求：百度 200、Google `generate_204` 为 204、OpenAI `/v1/models` 未认证为 401；401 不证明认证 API 可用。
- 验收后原位设置 `automatic_deployment_enabled: true`，保持 `maintenance_disabled: false` 和旧 `deployment_enabled: false`。配置 inode 保留，最终 SHA256 为 `da6bc91cda3821739ee1157713dc1d3d69494889a598bd40302ccff773732c69`。API 回读自动开关为 true，草稿仍干净、已保存规则 SHA 不变，无活动任务。
- `2026-10-14T03:03:21Z` 的生产只读判定再次实时读取双端，返回 `auto_allowed`、`unchanged: true`，确认当前版本无需重复激活。管理器仍 healthy、重启次数 0，NAS SmartDNS healthy，HTTPS health 为 ok。
- 操作收据保存在 NAS `/vol1/1000/DockerData/dns-rule-builder/receipts/automatic-rules-20261014/`。诊断期间多次短连接登录曾收到一次正常 429，未降低登录限制；后续在限流窗口自然释放后完成操作。

## 恢复方法与验证边界

- 管理器恢复：确认没有活动部署，关闭新自动开关，保留当前数据副本；恢复备份中的 target 配置和旧不可变镜像引用，以同一安装脚本只重建管理器。数据库只在确认兼容问题后停管理器恢复在线备份，不删除数据、Volume 或凭据。
- 规则恢复使用本次成功部署记录的上一版本和现有回退接口，随后读回双端并检查有限 DNS/HTTPS 请求。管理器回退不能替代规则回退。
- 没有更改 OPNsense、DNSDist、DNS 上游、Caddy、Fake-IP 路由或策略组成员/选择；不覆盖全家客户端、完整故障矩阵、WAN DNS 泄漏或持续可用性。
- 本次证明生产首次人工迁移、自动开关回读及同版本无需更新判定；没有为了测试人为制造域名增删，尚未观察下一次真实常规变更的无人值守激活。自动小改动、大改动停批、失败回退和中断恢复由本地测试覆盖，没有在生产故障注入。
