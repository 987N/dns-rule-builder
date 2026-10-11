# DNS Rule Builder V1 → V2 规格差异

此文件把 `DNS_Rule_Builder_Agent_Research_Spec_V1.md` 与实施方向对齐。表格记录最初架构选择；当前实现与验收阶段以本文末尾的状态说明及收据为准，不能据设计表推断生产已验收。

| 主题 | V1 原文 | V2 方向 |
|---|---|---|
| Git 与构建位置 | NAS 本地构建；V1 仅输出 | 新建公开 `dns-rule-builder` 仓库；GitHub Actions 在托管 runner 执行抓取、合并、验证、发布 |
| 规则数据事实源 | 推荐 `custom-rules.yaml`，也允许 SQLite | GitHub `rules-data` 分支单一原子 YAML 文件；SQLite 留 NAS 保存草稿、任务、审核、运行记录 |
| 规则保存和构建 | 修改数据后本地构建 | Web 保存只同步数据；手动或每日 `workflow_dispatch` 单独构建 |
| 容器数 | Builder + Admin 两容器，共享任务目录 | 一个 NAS 管理容器内 Web、定时调度和后台任务串行执行；GitHub Runner 是外部 CI，不是 NAS 第二容器 |
| 定时 | 本地 worker 每日检查上游 | NAS 每日 04:17 Asia/Shanghai 调用 GitHub `workflow_dispatch`；不依赖 GitHub `schedule` |
| 发布接口 | 本地 `output/<build_id>` 和 `current` 指针 | GitHub Release 是不可变规则分发接口；公开产物启用前需完成上游许可证审查 |
| 构建输入 | 隐含本地配置和源数据 | `main` 锁代码，数据分支按 SHA 锁规则快照，上游源锁定单次构建 commit；旧快照不能覆盖新快照的最新状态 |
| 部署 | 明确不下发生产；仅供手工集成 | R0-P4 实现部署适配/模拟并保持部署开关关闭；P5 单独形成生产迁移计划，经过人工验收后再决定是否启用 |
| SmartDNS/Mihomo | 规则语义主要写为设计约束 | 保留 EXACT/SUFFIX、分类语义、custom exact 置顶 provider、逻辑策略组映射与跨类顺序阻断；目标二进制实测完成前不可生产就绪 |
| 类别和映射 | 例子包含 `AI`、`Google`、`Proxy` 等目标组 | 组映射是可配置逻辑策略组；Google、YouTube、Gemini 使用独立默认组，生产 Mihomo 节点成员仍未选择 |
| 上游语法 | Surge 主/拆分都待实现 | 当前锁定 blackmatrix7 Surge 与 v2fly 来源；typed/domain/full 语义及 v2fly include 属性、affiliation 已由实际 adapter 处理。Resolve 行为变体不作为默认合并输入；不支持语法保留为 blocker |
| 成功状态 | 本地构建 current | 分离 CI 构建成功、下载校验成功、允许部署、target 实际运行版本；失败不替换上一个成功版本 |
| 一致性与失效 | 双端来自同构建并留本地旧版本 | manifest 锁 code/data/source hashes；下载校验失败、CI失败、API失败均保留上一运行版本并记录具体错误 |

## 对 V1 的明确保留

未命中代理规则的默认 Real-IP、`Foreign-General` 只接收人工选择的独立长尾规则/用户规则、不得使用 Global/Proxy/geolocation-!cn 综合列表、不能改 OPNsense/DNSDist 或 Fake-IP 路由、NAS 不新增 Mihomo；生产策略组成员和节点选择留待用户确认。

## 已验收旧版与当前扩展

2026-10-11 用户确定策略组手动选择既有区域代理组，保留香港节点、美国节点等区域分组；用途与独立服务组可直接选择区域组，并保留统一 Proxy 入口。具体完整区域清单和区域内部节点在生产迁移前核对保留，本地模板不绑定节点。用户授权本地提交源码与构建验证结果，暂不切换和部署。区域模板后的 108 项测试及固定二进制断网检查通过，最新工作树候选和产物哈希见 [构建验证摘要](build-validation-2026-10-11.json)及[验收收据](gate-acceptance.md)。此前 G4 的版本和测试数作为历史验收记录保留。

旧版 GitHub 仓库、真实 Actions、不可变 Release 与 GHCR 镜像已通过验收，历史证据见 [`docs/acceptance.md`](acceptance.md)。当前 R1–R5 扩展是在此之后的本地工作树变更，尚无对应远端 CI 验收。

当前来源目录为 141 项，其中 124 个配置类别（含为读取旧配置保留、禁止启用的 Apple 聚合别名）、17 个默认启用类别；manifest 保持 schema 4、source capability 1、compiler capability 2 与 `provider_map` 身份/校验，并增加 license capability 1 和来源许可 provenance；旧 schema-4 manifest 兼容路径仍保留。固定来源为 blackmatrix7 `83f37120c155f67c8eef43221dabe15cc3c95fec` 与 v2fly `c6adacf29cc4fb05e04651fcf03e00989ef9f797`。R4 默认候选的 19 个 deployment blockers 是历史比较基线。

G2 完成编译与审核接口后，G3 主验收确认默认候选 `review_required=false`、`publish_allowed=true`；仅生产 target semantics 未确认与 deployment 总开关关闭两项部署阻塞。候选有 1,564 条保留出处的类别规则、1,337 个域名语义和 20 个 Provider 片段；6 条 G1 精确排除生效，18 条精确审核签名的 unsupported 仍保留报告。2,623 个规则主域、子域和未收录域 witness 对照实际 Provider 顺序全部通过。来源许可 provenance、包内通知/许可和外部 checksum 由主 Agent 复验通过。该候选还通过 Mihomo 20-provider 断网语法检查、21 条代表性路由日志及 SmartDNS generated set 的 4 个 EXACT/SUFFIX 断网请求；没有验证真实服务访问。Schema-1 可选 `unsupported_dispositions` 已接通 CLI/controller，字段缺省按空列表处理且不会清除 code-managed baseline；精确处置写入候选身份与报告，并随数据状态同步。最终候选 `0b8f836df17bcd1551eee1c189a538281e1720779a01a6017135bd17e2899787` 的 102 项自动化测试与 2,623 项顺序 oracle 通过；Mihomo v1.19.32 的 20 个 Provider syntax 和 21 条代表性路由日志、SmartDNS 1.2026.v48.4.2 的 4 项 EXACT/SUFFIX 请求通过。管理容器以 UID 10001、network none、只读根文件系统和 drop ALL capabilities 运行；登录、401、旧 6 字段 SQLite draft GET 补空及 PUT roundtrip 通过。候选 manifest 为 `review_required=false`、`publish_allowed=true`、`deployment_allowed=false`；配置中的 `deployment_enabled` 总开关仍关闭。没有验证真实服务访问或生产设备集成。

历史 R5 基线的 Mihomo/SmartDNS checks 不代表当前候选。G3 主验收候选通过 Mihomo v1.19.32 的 20-provider 断网语法检查和 21 条代表性路由日志，SmartDNS 1.2026.v48.4.2 generated set 的 4 个断网 EXACT/SUFFIX 请求亦通过；这些检查不等于真实服务访问或家庭设备运行。另有 R5 全目录语义审计覆盖 124 个配置类别中的 123 个有源类别，解析 7,319 条规则并生成 123 个 Providers；全量 syntax-only `-t` 通过，保留 1,422 条 unsupported 记录。该 audit 不取代较早默认构建基线的 65 条 unsupported，也不批准全部类别启用或 Provider 排序；最终候选保留 18 条精确签名审核的 unsupported。复现命令见 [README](../README.md)，详细收据见[扩展验收记录](expansion-acceptance.md)与[门禁收敛验收](gate-acceptance.md)。Schema-1 optional `unsupported_dispositions` 已接通 CLI/controller；字段缺省按空列表处理，code-managed baseline 不会被清除。最终候选 `0b8f836df17bcd1551eee1c189a538281e1720779a01a6017135bd17e2899787` 的 102 项自动化测试、2,623 项顺序 oracle、Mihomo 20-provider syntax/21 条路由请求及 SmartDNS 4 项 EXACT/SUFFIX 请求均已通过；管理容器安全运行边界、登录和旧 6 字段 SQLite draft 兼容读写也已通过。家庭现网 target 版本、配置整合与生产集成未核实；deployment 总开关关闭。此状态不能声称全链路可用或生产就绪。

