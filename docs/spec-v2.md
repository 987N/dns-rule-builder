# DNS Rule Builder V1 → V2 规格差异

此文件把 `DNS_Rule_Builder_Agent_Research_Spec_V1.md` 与已选的新实施方向对齐。它记录架构决策，不代表相应组件已实现或生产已验收。

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
| 类别和映射 | 例子包含 `AI`、`Google`、`Proxy` 等目标组 | 组映射是可配置逻辑策略组；生产 Mihomo 组名/节点成员留待策略组重整时用户决定 |
| 上游语法 | Surge 主/拆分都待实现 | 首版主 Surge `.list` 为主输入，只接受可等价转换的 DOMAIN/SUFFIX；Resolve 文件视作行为变体，裸域名文件必须专用识别；v2fly/Sukka 暂不纳入 |
| 成功状态 | 本地构建 current | 分离 CI 构建成功、下载校验成功、允许部署、target 实际运行版本；失败不替换上一个成功版本 |
| 一致性与失效 | 双端来自同构建并留本地旧版本 | manifest 锁 code/data/source hashes；下载校验失败、CI失败、API失败均保留上一运行版本并记录具体错误 |

## 对 V1 的明确保留

未命中代理规则的默认 Real-IP、`Foreign-General` 只接收人工选择的独立长尾规则/用户规则、不得使用 Global/Proxy/geolocation-!cn 综合列表、不能改 OPNsense/DNSDist 或 Fake-IP 路由、NAS 不新增 Mihomo、策略组细则留待用户确认。

## R0 门禁

本目录已形成 R0–P4 本地实现与文档，GitHub 仓库 `987N/dns-rule-builder` 已创建，但本地代码尚未上传且未执行真实 Actions/Release。候选 SmartDNS/Mihomo 二进制隔离测试已通过；家庭现网实际 target 版本和生产集成未核实。P4 schema-v4 仅为本地模拟协议，部署总开关关闭。此状态不能声称全链路可用或生产就绪。

