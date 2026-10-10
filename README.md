# DNS Rule Builder

本项目从锁定的上游规则快照和已保存配置生成 SmartDNS 与 Mihomo 候选规则。管理 Web、GitHub Actions 工作流和 schema-v4 部署模拟器均在本地实现；当前没有真实远端 Actions 验收，也没有生产设备连接或配置写入。GitHub 仓库 987N/dns-rule-builder 已创建，但当前本地项目代码尚未上传。

## 快速开始

需要 `uv` 和 Python 3.11 或更新版本。R0 使用的上游归档缓存于被 Git 忽略的 `.cache/` 目录。

```sh
uv sync --locked
uv run dns-rule-builder inventory
uv run dns-rule-builder build --output-root .cache/build-results
uv run dns-rule-builder validate .cache/build-results/current
uv run dns-rule-builder report .cache/build-results/current
uv run dns-rule-builder explain .cache/build-results/current gemini.google.com
uv run dns-rule-builder diff <旧版本ID> <新版本ID>
```

`inventory` 和 `build` 默认读取 `config/sources.yaml` 指定的来源及 `.cache/ios_rule_script-83f37120` 解压目录。来源快照放在其他位置时使用 `--source-root PATH`。`--source-revision SHA` 可指定完整 commit SHA；来源目录必须有内容相同的 `.source-revision` 标记。该选项只记录并校验已提供的快照身份，不会自行下载或证明远端提交。

`build --output-root .cache/build-results` 成功后会创建不可变候选目录 `.cache/build-results/releases/<64位SHA-256>`，并将 `.cache/build-results/current` 更新为最新成功候选的链接。失败构建不会替换该链接。上面的 `validate`、`report` 和 `explain` 命令均对这个实际构建目录工作。

## 自定义规则

创建本地文件 `data/custom-rules.yaml`（该目录被 Git 忽略，不会纳入代码提交）：

```yaml
rules:
  - domain: api.example.net
    match_type: EXACT
    category: foreign_general
    enabled: true
force_direct: []
```

EXACT 和 SUFFIX 的含义会保留。SmartDNS 精确规则输出为 -.host.example；已用隔离 Docker 中的 SmartDNS 1.2026.v48.4.2 验证根域/子域差异。Mihomo v1.19.32 已通过 14-provider 配置校验及隔离规则匹配测试，含 SAFE_PATHS 路径边界。详见 [隔离目标语义实验](docs/target-semantics.md)。这不等于已确认 VM104/NAS 实际二进制版本或生产集成。Mihomo 文件位于 mihomo/providers/；自定义精确规则进入全局置顶 CustomExact-* Provider。逻辑策略组名称不代表已经匹配现网 Mihomo 配置。

## 候选、审核与部署门禁

每个成功构建都带有 `manifest.json` 和报告。`input_snapshot_time_utc` 是输入 commit 的确定性时间上界，不代表 Actions 实际完成时间；管理端应以 workflow `completed_at` 或本地任务记录判断新鲜度。`validate` 会重算版本身份并核对全部产物哈希、文件集合和符号链接。候选构建、获准部署和实际运行版本是不同状态。

默认 target_semantics_verified: false 且 deployment_enabled: false，因此部署关闭。隔离测试使用的版本不代表已确认现网目标。license_release_approved: false 会阻止第三方规则公开发布；来源许可证、署名和再发布条件仍需审核。即使门禁关闭，本地候选仍可构建和分析。

`config/categories.yaml` 列出首批候选服务以及保守禁用的宽泛类别。未知规则保存在 `reports/unsupported.json`。新出现的不支持语法、启用分类无有效规则、分类数量较上个成功版本减少超过 30%、未解决的跨策略冲突、Provider 顺序循环或自定义直连范围冲突都会要求人工审核，并阻止部署。Provider、策略组和类别标识会在输出前校验。

已知共享域名由 `config/shared-domains.yaml` 驱动；只有与本次实际规则相符的条目才会写入证据报告。未审核且命中的共享域名会阻止部署。批准记录属于 `rules-data/state.yaml`，必须带审核人、日期和理由，并进入候选身份。

## GitHub 工作流

`.github/workflows/rules.yml` 只接受手动 dispatch，以不可变 data commit 构建；`.github/workflows/tests.yml` 运行测试；`.github/workflows/image.yml` 仅在 `main` 测试通过后发布 GHCR commit 标签与 `latest`。三者的 Actions 都固定到完整 commit SHA。规则 Release 先上传 draft，输入代码或数据快照不再是当前分支 head 时不会标成 latest。`rules-data/state.yaml` 是独立数据分支的唯一状态文件。详细接口、真实验收结果和剩余边界见 [GitHub CI 说明](docs/github-ci.md)。

## 管理容器（P3）

管理界面、单后台 worker 和 SQLite 任务索引运行在一个容器中。它可以编辑公开规则状态、服务端预览和校验批量导入、按 SHA 条件写回 GitHub、触发并轮询 Actions、下载并校验候选 Release、查看报告和保留历史。空闲时每 15 分钟只读检查远端提交与 Release 元数据，不会覆写草稿或候选。当前不连接家庭 DNS 或代理设备，也不具备生产部署能力。

部署前将 `.env.example` 复制为 `.env`，先把 `DNS_RULE_BUILDER_IMAGE` 改成验收过的 GHCR digest；准备 `secrets/github_token`、`secrets/admin_password` 和 `secrets/session_secret` 三个**独立文件**。GitHub token 建议只授予该仓库 `Contents: read/write`、`Actions: write`；文件必须可由容器运行 UID 读取并保持只读挂载。管理员密码和 session secret 至少 16 个字符；没有默认密码。按 NAS 的数据目录设置 `DATA_DIR`，并让目录可由 UID/GID 10001 写入。`docker compose config` 检查配置后用 `docker compose up -d` 启动。默认 loopback HTTP 示例关闭 Secure Cookie，适合本机反代前测试；反向代理使用 HTTPS 时设置 `SECURE_COOKIES=true`。

手动构建镜像：`docker build -t dns-rule-builder:local .`，随后在本机 `.env` 将 `DNS_RULE_BUILDER_IMAGE=dns-rule-builder:local`。Compose 默认只绑定 `127.0.0.1:8080`，生产访问应通过受控 HTTPS 反向代理。每日北京时间 04:17 由容器 worker 触发一次 GitHub 构建；容器启动时至多补当天一次。SQLite 与版本目录持久化在 `/data`。

## 规则和来源说明

- Surge `DOMAIN` 转为 `EXACT`，`DOMAIN-SUFFIX` 转为 `SUFFIX`。
- Surge Resolve 文件会影响 IP 解析语义；目前不将其作为额外来源合并。
- 裸 Apple Domain Set 仅在来源显式声明 `surge-domain-set` 格式时解析。当前锁定快照中，点前缀表示后缀规则，裸域名表示精确规则；该映射与 Apple 完整类型化列表逐条比对一致。
- `Foreign-General` 只接收自定义规则和经过逐项审核的独立来源；不导入 Global、Proxy 或 `geolocation-!cn` 等汇总规则集。
- 来源快照、实际分类目录、许可证注意事项、规则格式证据与未完成验证见 `docs/research.md` 和 `docs/source-categories.md`。

## Schema 4 目标适配模拟（P4）

config/targets-v4.yaml 将 NAS 备用目标定义为 SmartDNS-only，将 VM104 主目标定义为 SmartDNS + Mihomo。src/dns_rule_builder/target_v4.py 校验产物哈希、动态 Provider 列表、EXACT/SUFFIX 语义和目标角色，并提供持久化 stage-both → validate-both → backup-activate → main-activate 状态机及故障注入模拟器。它没有 SSH 或生产传输实现，默认开关保持关闭；与现行 schema 3 的兼容差异和限制见 [P4 设计](docs/schema4-deployment.md)。

## 策略组与迁移方案（P5）

策略组映射仍为可配置逻辑组，不预定生产组成员或节点。YouTube/Google 的跨 Provider 环、1 个未审核规则类型、5 个共享域名和来源许可是当前需要裁决的构建门禁。生产基线、备份、写入者切换、分阶段验收与回退步骤见 [生产迁移计划](docs/production-migration-plan.md)；该计划不是执行收据。

## 开发检查

```sh
uv run pytest -q
```

`uv run --frozen pytest -q` 的最终回归为 **56 passed**。真实 GitHub 测试、规则构建、不可变 Release、重复发布幂等和 GHCR 发布均已执行。正常第三方规则因许可证门禁关闭而只产生短期诊断 artifact；两个公开 Release 均为自定义 `example.net` smoke 测试，不可用于家庭部署。完整证据、首次重复发布暴露并修正的问题及剩余限制见 [开发验收收据](docs/acceptance.md)。没有生产 target 安装或设备变更。阶段证据和本地验收收据见 [验收记录](docs/acceptance.md)。
