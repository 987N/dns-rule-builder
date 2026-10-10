# 开发验收收据

日期：2026-10-10。实现由 Luna 执行，主 Agent 独立检查、运行测试与隔离实例。本文件区分开发结果、外部验收和生产状态。

## 已获得的证据

- 最终本地回归 **56 passed**；覆盖精确/后缀语义、重叠与顺序循环、直连保护、共享域名/排除审核、数据状态、认证/CSRF、导入预览、任务恢复、旧快照保护和部署失败矩阵。
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

## 真实 GitHub 验收

仓库 [987N/dns-rule-builder](https://github.com/987N/dns-rule-builder) 已公开源码。`main` 保存程序和工作流，`rules-data` 的当前 tree 只含 `rules-data/state.yaml`。最初连接器写入返回 403；用户随后在本机安装并登录 `gh`，远端验收由该认证完成。开发凭据没有复制到 NAS、容器或仓库。

| 检查 | 真实结果与证据 |
|---|---|
| 最新代码测试 | [tests run 38063197025](https://github.com/987N/dns-rule-builder/actions/runs/38063197025) 成功；56 项回归 |
| GHCR 镜像 | [image run 38063197111](https://github.com/987N/dns-rule-builder/actions/runs/38063197111) 成功；linux/amd64；公开拉取 |
| 修正后的规则发布 | [rules run 38063028341](https://github.com/987N/dns-rule-builder/actions/runs/38063028341) 首次及第二次运行均成功 |
| 同输入幂等 | 第二次运行复用 Release `409026697`；公开 Release 数量及全部资产摘要未增加或改变 |
| 失败不发布 | [无效 data commit run 38062605244](https://github.com/987N/dns-rule-builder/actions/runs/38062605244) 按预期失败，构建/发布均跳过 |
| 正常上游构建门禁 | [run 38062723479](https://github.com/987N/dns-rule-builder/actions/runs/38062723479) 构建成功；第三方许可证门禁阻止发布；仅保留 7 天诊断 artifact，下载后 manifest 校验通过 |
| 真实状态并发 | Contents API 条件更新成功；旧 SHA 返回 409；测试后原始 YAML 字节全部恢复，当前数据 commit 为 `a08559c430fe43723f6f187046f0cecc27b0a5cd` |
| SDK 接口 | UUID 关联到实际 workflow run、无凭据公开下载、既有 Release 与资产按 digest 复用均通过 |
| 不可变设置 | 仓库 API `enabled=true`，公开 smoke Release `immutable=true` |

最终镜像固定为：

```text
ghcr.io/987n/dns-rule-builder@sha256:2b212db7dc5975209468dfef383149d6688188541a3a29a157d9cfa10d805705
```

对应代码 commit `a02c16329638c1ce4085120ce9bd8ece94a32629`。本机原生构建为 arm64；公开镜像构建为 amd64。上述最终固定镜像已匿名拉取，并以普通 UID 10001、只读根文件系统、禁用网络和三个独立假 secret 再次启动；等待服务就绪后健康检查及认证登录均返回 200，持久化验收标记仍存在。测试容器已清理。这些隔离结果不能当成 NAS 当前设备或生产运行的证明。

[修正后的不可变测试 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-cef8fb0db2ee2a5b7a83bc0aad07d71a72e1b009e45f7f948df81fddfba9e61c) 的 ZIP、SHA256SUMS、manifest 与全部产物哈希已下载验证。身份为 `cef8fb0db2ee2a5b7a83bc0aad07d71a72e1b009e45f7f948df81fddfba9e61c`，数据 commit 为上述 `a08559c...`，代码 commit 为 `82349bb60ded2416c2af2fc8a575c500b12374af`。它只含自定义 `example.net` 精确规则，带 `build_purpose=smoke`，不进入正常候选，不允许部署，也不更新默认可用规则。

首次重复发布测试暴露了 GitHub 允许同 tag 创建新 draft 的行为。旧实现先 POST 再等待 422，因而生成重复 draft；发布被 immutable 保护拒绝，原公开资产没有改变。Luna 已改为先查询发布版、再分页查 draft，新增五项回归；真实复验成功。保留一个明确标记的未发布失败测试草稿作为记录，未删除已有 Release 或资产。两个公开 Release 均为 smoke 测试。

## 生产与后续边界

R0/P1 的研究和构建核心、P2 的工作流及实际 GitHub 验收、P3 的单容器管理、P4 的 schema-v4 校验与部署模拟已完成开发验收。P5 交付为[策略组映射草案](policy-group-mapping-draft.md)和[生产迁移评审计划](production-migration-plan.md)，需要用户确定真实策略组、节点和例外后再形成设备配置 diff。

P4 尚无 SSH/HTTP 生产传输实现；模拟器不证明设备配置 reload、真实 DNS 或故障恢复。没有安装 target adapter、修改生产 DNS/Mihomo/OPNsense/DNSDist、停用旧 controller/review 或启用自动部署。生产配置、普通 UID 的 NAS 持久目录、仅限本仓库的运行 PAT 与三个独立 secret 文件仍须后续准备和验收。

旧项目的 AGENTS、网络基线、controller 脚本、NAS target 脚本和两份旧 Compose 文件的前后 SHA-256 一致。`HomeGatewayConfig/` 未读取为现网事实或修改。
