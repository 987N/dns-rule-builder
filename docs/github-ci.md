# GitHub CI 与规则快照接口

公开仓库 [987N/dns-rule-builder](https://github.com/987N/dns-rule-builder) 已上传实现代码，并完成真实 GitHub Actions 验收。`main` 保存工作流和程序，`rules-data` 只保存 `rules-data/state.yaml` 一份原子 YAML 状态。该文件包含类别开关、逻辑策略映射、优先级、自定义规则、直连声明、已审核排除项和共享域名审核记录。来源排除项按类别、规范域名和 EXACT/SUFFIX 类型逐条匹配，只删除相应上游来源规则；不会删除自定义规则，也不会推断父子域。无效、重复或未命中的记录保留在 `reports/excluded-rules.json` 并阻止审核通过/部署。排除项及其 SHA、结果报告均进入 manifest 身份。

NAS 保存规则时应使用 Contents API 读取文件 SHA，并在更新请求中带回 SHA。409/422 冲突要求重新读取、合并和确认，不能覆盖远端更新。工作流 dispatch 固定使用 `main`，输入为完整 `rules-data` commit SHA、request UUID 和可选自定义-only smoke 标志。Actions 从 `rules-data` 分支验证输入提交可达性，再用 `git show` 读取该提交的 YAML；数据分支只解析为受限数据结构，不 checkout 或执行其内容。

规则工作流不使用 GitHub 定时触发。构建按仓库 concurrency group 串行进行：代码固定在 dispatch 事件 SHA，规则状态固定在输入数据 SHA，上游 `master` 在构建开始时解析为完整 commit SHA，再下载完整归档、检查大小、SHA-256、目录结构和安全解压路径。Runner 使用 `GITHUB_TOKEN` 认证上游 commit API 查询以减轻共享出口的匿名限流；凭据只发送到 `api.github.com`，下载归档的 `codeload.github.com` 请求不带凭据。manifest 记录代码、数据、上游 commit 和归档哈希。`input_snapshot_time_utc` 是这些输入 commit 的确定性 UTC 时间上界，用于保证相同不可变输入得到相同 manifest 和发布资产；它不是 CI 实际完成时间。管理界面的任务新鲜度应使用 workflow `completed_at` 或本地任务记录。

Release tag 固定为 `rules-<64位release_id>`。完整压缩包、manifest、SHA256SUMS 及适用的第三方许可证材料都先上传 draft；仅在所有资产成功上传、数据分支 head 仍等于构建输入且代码分支 head 仍等于触发 SHA 时，才把 draft 发布为 latest。CI 串行锁和不可变 tag 提供幂等基础；同名已上传资产必须匹配 GitHub 返回的 SHA-256 digest，否则停止，不覆盖。仓库的 immutable releases 已启用并通过 API 和实际发布结果核实。临时 Actions artifact 保留 7 天，只用于故障诊断。

正常第三方规则构建在许可证审批未通过时只保留短期诊断 artifact，不发布公开 Release。独立 `smoke_test=true` 使用固定的 `example.net` 自定义精确规则并关闭第三方分类，用于验收真实 Release API 流程；不得把 smoke 产物当成家庭规则部署版本。

应用镜像 workflow 只从 `main` 代码 SHA 构建，先通过同一代码 SHA 的 pytest job，GHCR 名称转小写；标签是 commit SHA 与 `latest`，不监听 `rules-*` 规则 Release，避免构建和 Release 相互触发。发布摘要以 GHCR digest 输出。应用工作流没有 NAS 凭据，也没有任何家庭设备连接步骤。

## 远端验收结果与边界

- 测试和应用镜像工作流真实通过，最终本地回归为 56 项。GHCR 镜像可匿名拉取；Compose 应使用验收收据内的固定 digest。
- [修正后的 smoke workflow](https://github.com/987N/dns-rule-builder/actions/runs/38063028341) 首次及第二次运行均成功，Release ID、数量和资产摘要未增加或改变。对应 [不可变测试 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-cef8fb0db2ee2a5b7a83bc0aad07d71a72e1b009e45f7f948df81fddfba9e61c) 的 ZIP、SHA256SUMS、manifest 身份及全部产物哈希已下载验证。
- [无效数据 commit 测试](https://github.com/987N/dns-rule-builder/actions/runs/38062605244) 按预期失败，构建和发布步骤跳过，没有产生公开 Release。
- [正常第三方规则构建](https://github.com/987N/dns-rule-builder/actions/runs/38062723479) 成功；许可证门禁阻止公开规则发布，仅留下保留 7 天的诊断 artifact，下载后 manifest 校验通过。
- 实际 Contents API 的 SHA 条件更新成功，旧 SHA 返回 409；验收后数据文件逐字节恢复为初始状态。数据分支 tree 只包含 `rules-data/state.yaml`。
- SDK 的 workflow UUID 对应关系、公开下载和现有 Release/资产复用已与真实 GitHub API 验证。

首次重复发布测试暴露了 GitHub 允许同 tag 新建 draft 的行为。实现已改为先查已发布 tag，再分页查 draft；已有资产仅按 digest 复用，不覆盖。旧实现的一个失败测试草稿保留为未发布记录，不是可用版本。首次和修正后的公开 Release 都只有自定义测试域名，并带 `build_purpose=smoke`；管理器会拒绝把它们作为正常候选。

这些结果不等于 NAS 上的凭据、持久目录、生产 target adapter 或设备部署已验收。应用运行仍需用户准备仅限本仓库的 fine-grained PAT 与独立 secret 文件；本轮未把开发用 `gh` 凭据复制到 NAS 或容器。
