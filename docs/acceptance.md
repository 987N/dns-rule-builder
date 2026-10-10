# 开发验收收据

日期：2026-10-10。实现由 Luna 执行，主 Agent 独立检查、运行测试与隔离实例。本文件区分开发结果、外部验收和生产状态。

## 已获得的证据

- 锁定 blackmatrix7 快照 `83f37120c155f67c8eef43221dabe15cc3c95fec`，本地实际构建得到 1098 条分类规则、1069 个全局语义项、65 条不支持规则、24 个跨分类重叠。manifest 与全部产物哈希校验通过。
- 实际报告识别 YouTube/Google Provider 顺序循环、一个新增不支持类型和五条共享域名待审核项；候选继续阻止部署，第三方规则公开发布门禁也保持关闭。
- Actions 工作流经 `actionlint` 检查通过。此检查不代表 GitHub Runner 已执行。
- Docker 镜像构建成功。隔离测试容器使用 UID/GID 10001、只读根文件系统、capabilities 全部丢弃、`no-new-privileges`、三个独立只读假 secret 和禁用网络。健康检查、登录页面和认证登录均返回 200，登录生成 CSRF token。
- SQLite 验收标记在容器重启后保持，重启后的健康检查返回 200。测试容器已清理。没有挂载真实凭据或运行中的 NAS 数据。
- Compose 配置检查通过。浏览器测试使用本机 fake GitHub 服务，不能作为真实 GitHub CI 的证明。

## 真实二进制隔离语义

SmartDNS 镜像来自当前 NAS Compose 的 image 字段，固定摘要为 `pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff`，二进制报告 `1.2026.v48.4.2`。实例使用 `--network none`，仅查询容器内 `127.0.0.1:53`：

| Domain Set 项 | 查询 | 结果 |
|---|---|---|
| `-.api.example.test` | `api.example.test` | 精确规则地址 `192.0.2.10` |
| 同上 | `child.api.example.test` | 父域规则地址 `192.0.2.20` |
| `suffix.example.test` | 主域及子域 | 后缀规则地址 `192.0.2.10` |

上述格式对应 [SmartDNS 官方域名规则说明](https://pymumu.github.io/smartdns/config/domain-rule/)。未把此结果扩大为当前 VM104 或 NAS 实例均已确认相同版本。

Mihomo 测试使用官方镜像 `metacubex/mihomo@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e`（`v1.19.32`）：

- 完整本地产物的 14 个 Providers 配合测试用逻辑策略组，通过 `-t`。
- 禁用网络的实例将全部测试策略指向 REJECT。真实 HTTP 代理请求的匹配日志显示：精确域名命中 Exact；其子域命中 Parent；`gemini.google.com` 先命中 Gemini；`google.com` 命中 Google。HTTP 502 是测试 REJECT 的预期响应，不表示目标网站访问成功。
- HomeDir 为 `/isolated-home` 时，`/outside/providers/` 被拒绝；显式设置 `SAFE_PATHS=/outside` 后通过。路径约束对应 [Mihomo 官方 Providers 文档](https://wiki.metacubex.one/en/config/rule-providers/)。

生产目标版本、完整合并配置和真实业务请求仍待后续迁移阶段验证；`target_semantics_verified` 与部署开关保持关闭。

## 远端与生产状态

公开仓库 [987N/dns-rule-builder](https://github.com/987N/dns-rule-builder) 已创建，目前仅有初始化 README。GitHub 连接器创建 blob 返回 `403 Resource not accessible by integration`；本机 SSH 与 HTTPS Git 也没有可用认证。没有上传实现代码、建立数据分支、执行真实 dispatch、发布规则 Release 或发布 GHCR 镜像。

继续外部验收需要将该仓库加入 Codex GitHub 连接器的可写范围，或由用户准备可用的 GitHub Git 认证。应用运行所需的 fine-grained PAT 是另一个后续配置步骤，不应为源码上传扩大其权限。

未安装 target adapter、未修改生产 DNS/Mihomo/OPNsense/DNSDist、未停用旧 controller/review、未启用自动部署。策略组成员与节点选择由用户在具体迁移阶段确定。
