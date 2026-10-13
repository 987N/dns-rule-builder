# 日常部署流程简化与管理器升级收据 — 2026-10-13

用户批准由 Luna 实施，主 Agent 独立验收、提交推送、通过 CI 发布，并升级 NAS 管理器。生产验收只生成和准备验证，不激活规则。以下是本次带时间的结果，不代表持续网络健康。

## 实现与验证

- 实现提交：`ef85cbf660e0c26d5f3599222dd3fee470f613bf`，已推送 main。
- 日常入口收敛为生成并检查、确认固定版本部署、回退最近一次。生成任务冻结草稿、条件保存到 GitHub、校验绑定版本的 Release，再通过受限 helper 双端准备；不会激活。维护禁用与一次人工授权分开，授权绑定 Release、准备请求、manifest 和验证证据。
- 保留激活顺序与读回检查；激活中断后证据失效或维护禁用时尝试回退，未知或第三方目标版本停止并要求人工处理。旧配置不能直接授权新的网页激活。
- 主 Agent 完整测试 `203 passed`；最终差异边界修正后相关测试 `50 passed`。JavaScript 语法和 staged diff 检查通过。完整测试需允许本机模拟 HTTP 服务监听；沙箱下的 10 项运行时测试失败在正常权限下通过。保留现有 FastAPI 测试依赖弃用警告。
- 本地浏览器确认生成准备成功且不自动激活、一次模拟部署成功、差异随运行基线刷新、验证过期时禁止部署。浏览器连接随后超时，回退点击与详情展开未完成浏览器验收；同一本地模拟器的认证 API 已验证回退成功及固定版本重新准备且不激活。夹具已正常停止。
- [测试 CI](https://github.com/987N/dns-rule-builder/actions/runs/38335935404)、[镜像 CI](https://github.com/987N/dns-rule-builder/actions/runs/38335935337)均成功。

## NAS 管理器升级

- 旧镜像：`ghcr.io/987n/dns-rule-builder@sha256:818069a73641e1e4f818488ebed9d81a88a200c13559df8834d678b940127798`，OCI revision `a0ea181d14148cbf96acf4b01582ed12256529c5`。
- 新镜像：`ghcr.io/987n/dns-rule-builder@sha256:bfe70e2ea7f1cb66c748e84a8d1d23bdaaf74f6f8cf656e2e52b9146547b3669`，架构 amd64，OCI revision 与实现提交一致。
- 升级前确认无活动任务、草稿干净，并读取两端真实运行版本。Compose dry-run、SQLite 在线备份与完整性检查通过后，仅用现有 `install.sh` 重建 `dns-rule-builder`，没有本地镜像覆盖。
- 生产 target 配置显式设置 `maintenance_disabled: false`，允许管理员对具体任务人工授权；旧 `deployment_enabled` 设置为 false。定时任务仍只构建和下载，不准备、不激活。
- 管理器 healthy，restart count 0；HTTPS `rules.lclwf.date/healthz` 返回 `ok`。认证 API 与新页面控件检查通过；HTTPS 实际提供的 `static/app.js` 与实现源码 SHA256 同为 `c256550a4f2dc6864245bc7e0737d680b286c296c7287566f9d4794c7e06b980`。

## 生产生成与双端准备

- 使用已保存规则数据 `967f2b63a0b898b5c14fe4a9ff37c416833eec59`，未产生新的规则数据提交。
- 任务 `eec0bd19-76c1-4245-890f-3347b4b65b7f` 成功；管理器触发的[正式规则构建](https://github.com/987N/dns-rule-builder/actions/runs/38336283960)成功。
- 已下载校验的[候选 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-942a6fa5f39f4b72f3a2bca919ee43c1bdf33bd7e506fae5e9cf53d64e274e7b)：`942a6fa5f39f4b72f3a2bca919ee43c1bdf33bd7e506fae5e9cf53d64e274e7b`。
- 双端准备请求 `6ea0e90d-e6ea-47e2-a422-07554767346d` 为 `validated`；当次验证有效期截至 `2026-10-13T03:21:51.693780Z`，过期需重新检查。
- 任务记录读回时间 `2026-10-13T02:21:52.743370Z`；之后再次受限 SSH 实时读回，两端仍为 `58baae5be4d2fc47582d743b9542d7c868879e04ff9e4958d9c464ba27c3b4ec`，与升级前相同。
- 实际版本差异 API 可比较：新增 0、删除 0、域名策略变化 0、分类策略变化 0。候选身份随代码版本变化，不表示运行规则已切换。

## 恢复材料与边界

- SQLite 在线备份和 candidate/previous 链接、双端版本记录：NAS `/vol1/1000/DockerData/dns-rule-builder/backups/pre-update-20261013-ef85cbf/`，完整性检查 `ok`。
- 旧 Compose、安装脚本、镜像引用和 target 配置：`/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261013-ef85cbf/`，目录 `0700`。没有复制或扩大挂载凭据。
- 如需恢复管理器，先确认没有活动部署，保留当前状态副本，恢复上述备份中的 target 配置和镜像引用，再以旧 digest 运行同一 `install.sh` 的单服务重建。数据保持原位；只有确认数据兼容失败才恢复在线备份，不删除数据或 Volume。
- 本次未更换目标 helper、未激活规则、未重启 SmartDNS/Mihomo，未修改 Caddy、OPNsense、DNSDist、DNS 上游或 Fake-IP 路由。
- 没有生产规则激活/回退演练、DNS/HTTPS 自动网络验收、故障切换或全家客户端验收。生产结果只证明管理器升级、发布下载与双端准备路径通过；浏览器回退点击验收缺口保留。
