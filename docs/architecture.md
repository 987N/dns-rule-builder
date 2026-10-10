# DNS Rule Builder R0 架构规格

## 服务边界和数据流

```text
管理 Web（单 NAS 容器）
  ├─ GitHub Contents API：读写 rules-data 分支上的单一规则状态文件
  ├─ GitHub Actions API：手动或每日 workflow_dispatch
  ├─ GitHub Release API：发现并下载不可变构建产物
  └─ NAS 本地持久卷：SQLite 草稿/任务/审核/部署记录、产物版本和日志
                         │
                         ▼
GitHub Actions（托管 runner，无家庭网络访问）
  锁定 main 代码 + rules-data commit + 上游 commit
  → 解析 → 冲突/质量门禁 → SmartDNS 与 Mihomo 双端产物 → manifest/hash → Draft Release → 发布
                         │
                         ▼
NAS 候选版本 → 哈希验证 → 审核/允许部署 → 两个 target 的 stage/validate/activate → 记录结果
```

规则真值由 GitHub `rules-data` 分支的单一 YAML 状态文件提供。程序和工作流在 `main`；Actions 只接受数据 commit SHA 并从数据分支读取该精确快照。配置包括用户自定义域名、启用类别、逻辑策略组映射、优先级及审核排除项。映射值是可配置的逻辑策略组；生产 Mihomo 真实组名和组成员待策略组重整阶段决定。

NAS 页面保存和生成是两个操作。写入 API 使用当前 blob SHA 做条件更新，冲突不覆盖远端。GitHub 不可用时只可保存本地标记为未同步的草稿。工作流使用 concurrency group 串行，同一数据快照可重试且 release identity 由规范化输入和产物哈希决定；较旧数据快照完成不得覆盖较新快照的“最新可用”选择。

## 单容器 NAS 运行方式

管理 Web、单后台执行器、每日调度器共用一个 Python/FastAPI 进程和单容器。SQLite 仅记录 NAS 本地草稿、队列、审核、部署/下载索引和审计，不另存一份成功发布的规则事实。进程内单 worker 串行任务；容器启动时恢复未完成任务，重复的定时遗漏最多补一次。默认每日 04:17 Asia/Shanghai 触发 GitHub 构建。

空闲时管理进程每 15 分钟在独立只读后台线程检查 `rules-data` head 和最新正常规则 Release 元数据；请求超时/限流会退避到最多一小时。此轮询只更新“远端有变化”状态，不改写草稿、候选或部署状态。

Fine-grained PAT 仅授权目标仓库 `Contents: write` 和 `Actions: write`；通过独立只读 secret 文件注入，不提供 `Workflows: write`。请求处理仅接受结构化动作，不拼 shell。无默认密码；登录、CSRF、限速、输入校验、非 root、只读根文件系统和无 Docker socket 是上线要求。GitHub CI 不获得 SSH 私钥、不连接或访问家庭设备。

## 版本与安全状态

manifest 至少锁定 schema/builder 版本、代码 commit、数据 commit、上游 commit、来源文件 SHA-256、输出 SHA-256、类别审核状态和构建结果。Release 含规则 bundle、manifest、校验文件、来源/许可证说明；同输入不重复发布。发布机制确认可用且许可审查完成前，产物公开发布门禁保持关闭。

状态分别记录 CI build、NAS 下载校验、允许部署、target 实际运行版本。失败不能替换上一个成功版本。默认保留最近 30 个成功版本，并一直保留当前与回退版本。CI、下载或 GitHub API 失败时退避并显示原因，继续保留现有运行规则。

## 生产 target 适配边界

新项目后续 P4 在自身目录内实现 schema 4 的双 target 适配协议与模拟器；不修改旧 target/生产脚本。部署器必须先对 VM104 主 SmartDNS 和 NAS 备用 SmartDNS 完成 staging 与 validation，然后串行激活备用、再主路径。两设备间不是原子事务；要持久化各设备版本和回退状态。任何路径/hash校验、验证、激活或回退不确定时停止后续自动动作并报告。部署默认关闭；人工验收一个完整部署与回退后才另议自动化。

自动部署不是 R0/P1/P2/P3 的默认能力。schema-v4 本地状态机、双 target handler 接口和故障模拟器见[部署适配规格](schema4-deployment.md)，只验证本地 candidate 和可注入的模拟 target。OPNsense、Dnsmasq、DNSDist、DNS 上游、Fake-IP 和 Mihomo 节点不属于 CI 范围；GitHub runner 无生产网络访问。

