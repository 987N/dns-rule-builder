# 单容器管理端

P3 实现范围是管理与候选版本管理，不是生产 target 部署。FastAPI、Jinja 页面和一个串行后台线程运行在同一进程；SQLite 只存任务、下载索引和本地审计。`rules-data/state.yaml` 仍是远端规则事实来源。容器内运行单 Uvicorn worker，避免重复调度和任务竞争。

## 初始化

从 `.env.example` 建立未跟踪的 `.env`，并创建三个独立 secret 文件：

- `secrets/github_token`：fine-grained PAT，仅 `987N/dns-rule-builder`；授予 `Contents: read/write` 与 `Actions: write`，无需 `Workflows: write`。
- `secrets/admin_password`：本地管理员密码，至少 16 个字符。
- `secrets/session_secret`：至少 16 个字符的随机 session 签名密钥。

容器默认 UID/GID 10001，secret 文件只读挂载且必须能由该 UID 读取，`data/` 目录必须可写。密钥不放 `.env`、GitHub 规则数据、数据库或日志。`.env.example` 只包含路径和非秘密设置。

先将 `DNS_RULE_BUILDER_IMAGE` 指向测试通过的不可变 digest；本地验收可显式使用 `dns-rule-builder:local`。Compose 启动前运行 `docker compose config`；单机 `docker compose up -d`。默认只监听 `127.0.0.1:8080`，且 loopback HTTP 示例默认关闭 Secure Cookie，便于反代前本机验收。生产应由 HTTPS 反向代理提供入口并设置 `SECURE_COOKIES=true`。容器根文件系统只读、非 root、丢弃 Linux capabilities、无 Docker socket；仅 `/data` 可写，并有 32 MiB `/tmp`。

## 交互与状态

规则编辑先写本地草稿，页面明确标出未同步；保存任务将规则 YAML 以 Contents API SHA 做 compare-and-swap。遇到并发修改会保留草稿并提示从 GitHub 重新载入。生成操作只接受已成功同步的 commit SHA，生成 UUID 关联 workflow dispatch，轮询已有 run，成功后核对 Release tag、manifest 身份、代码与数据 commit 及所有产物哈希，再原子放入候选版本目录。

进程在触发请求前先持久化 UUID。重启后按 UUID 查询已有 workflow，不会自动再次 dispatch；已记录 run id 的任务续查该 run。若无法确认触发是否到达 GitHub，任务显示受阻，需要人工核对原 workflow 后再操作。远端保存若响应丢失，会先比较远端文件内容哈希；完全一致时视作幂等完成，否则按旧 SHA 条件写入并清楚呈现冲突。

版本目录不解压路径穿越、符号链接或特殊文件，压缩包最多 128 MiB、解压最多 512 MiB。当前候选和上一候选通过原子 symlink 保留，清理时最多额外保留最近 30 个版本。可下载完整 bundle、SmartDNS 规则、Mihomo Providers 和报告，可查看版本统计、explain、diff 及任务审计记录。

调度使用 `Asia/Shanghai` 的每日 04:17；同一日期只创建一次定时任务。容器重启可恢复已有 dispatch/poll/download 阶段，不重复触发；容器未运行时最多补一次当日遗漏。API 错误和临时网络问题有速率限制、重试退避及任务状态记录。自动部署保持关闭，target 语义验证和生产迁移属于后续独立阶段。

没有运行中的任务时，后台会每 15 分钟只读查看数据分支 head 与最新正常 Release 元数据，并在页面显示检查时间、远端是否出现新提交和认证/限流错误。此检查不更新草稿或候选。共享域名的批准记录可随 `rules-data` YAML 保存，但当前 UI 只提供逐条上游来源排除；共享域名审阅仍须由规则状态维护者按配置项记录审核人、理由和时间，尚无独立的图形审核表单。

## 本地检查

`uv run --frozen pytest -q` 覆盖 fake GitHub save → dispatch → poll → Release 下载 → SHA 校验 → candidate 原子切换流程，以及登录、CSRF、状态校验和安全解包。`docker compose config` 需要本地 secret 文件存在；本文不包含或提供任何实际凭据。
