# DNS Rule Builder

日常操作与最新管理器升级结果见[2026-10-13 维护收据](docs/deployment-receipt-2026-10-13.md)。日常规则更新在网页完成；镜像/helper 升级单独安排。

本项目从锁定的上游规则快照和已保存配置生成 SmartDNS 与 Mihomo 候选规则。管理 Web、GitHub Actions 工作流和 schema-v4 部署状态机已实现。2026-10-11 已完成 17 类基线的正式 GitHub CI、不可变 Release、重复构建复用与公开 GHCR 镜像验收，见[发布收据](docs/publication-receipt-2026-10-11.md)。每日构建只生成并下载候选；日常人工流程由“生成并检查”固定草稿、构建 Release 并验证两个目标，再由管理员单独确认激活。当前生产版本、目标状态和部署开关以[生产迁移收据](docs/production-migration-receipt-2026-10-11.md)及后续更新收据为准；[迁移预检](docs/production-preflight-2026-10-11.md)是历史阶段记录。

扩充后的本地候选默认启用 60 个类别，包含 2,416 个独立域名语义项与 63 个 Provider 片段；与已发布基线比较未减少原有规则。来源审批及隔离匹配检查已通过，部分 CDN、Paramount+ 播放和字幕下载依赖仍有明确覆盖限制，见[扩充验收](docs/coverage-acceptance-2026-10-11.md)。此候选尚不能替代正式发布和目标实机验收。

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

`inventory` 和 `build` 读取 `config/sources.yaml` 及类别选中的来源。默认发布集合使用 v2fly 独立服务 key 和受控人工规则；blackmatrix7 保留为研究及关闭类别的候选来源。多来源构建先运行 `uv run python scripts/fetch_upstream_snapshot.py --cache-root .cache/upstream > .cache/sources-snapshot.json`，再使用 `--sources-snapshot .cache/sources-snapshot.json`。来源目录必须有对应 `.source-revision` 标记；本地提供的 revision 只用于身份校验，不自行证明远端提交。

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

EXACT 和 SUFFIX 的含义会保留。SmartDNS 精确规则输出为 -.host.example；已用隔离 Docker 中的 SmartDNS 1.2026.v48.4.2 验证根域/子域差异。早期隔离目标语义实验使用 Mihomo v1.19.32 的 16-provider 产物验证配置、规则匹配、SAFE_PATHS 路径边界和 Provider 顺序；最终 20-provider 候选的测试见下文，实验记录见[隔离目标语义实验](docs/target-semantics.md)。这不等于已确认 VM104/NAS 实际二进制版本或生产集成。Mihomo 文件位于 mihomo/providers/；自定义精确规则进入全局置顶 CustomExact-* Provider。逻辑策略组名称不代表已经匹配现网 Mihomo 配置。

## 候选、审核与部署门禁

每个成功构建都带有 `manifest.json` 和报告。`input_snapshot_time_utc` 是输入 commit 的确定性时间上界，不代表 Actions 实际完成时间；管理端应以 workflow `completed_at` 或本地任务记录判断新鲜度。`validate` 会重算版本身份并核对全部产物哈希、文件集合和符号链接。候选构建、获准部署和实际运行版本是不同状态。

G3 阶段候选的审核、目标语义和部署开关结果是历史验收快照，不表示当前运行状态。来源许可、provenance ZIP 与外部 checksum 流程已通过主 Agent 复验，因此该本地候选获准发布；其后正式发布结果见发布收据，远端版本 ID 与本地候选 ID 不同。G3 本地候选 `0b8f836df17bcd1551eee1c189a538281e1720779a01a6017135bd17e2899787` 的自动化测试（102 项）和 2,623 项顺序 oracle 均通过；Mihomo v1.19.32 的 20 个 Provider syntax 检查及 21 条路由请求日志、SmartDNS 1.2026.v48.4.2 的 4 项 EXACT/SUFFIX 请求也通过。管理镜像以 UID 10001、network none、只读根文件系统和 drop ALL capabilities 运行；登录、未认证 401、旧 6 字段 SQLite draft GET 补空及 PUT roundtrip 均通过。未验证真实服务访问或家庭设备生产集成。

`config/categories.yaml` 列出首批候选服务以及保守禁用的宽泛类别。未知规则保存在 `reports/unsupported.json`。新出现的不支持语法、启用分类无有效规则、分类数量较上个成功版本减少超过 30%、未解决的跨策略冲突、Provider 顺序循环或自定义直连范围冲突都会要求人工审核，并阻止部署。Provider、策略组和类别标识会在输出前校验。

已知共享域名由 `config/shared-domains.yaml` 驱动；只有与本次实际规则相符的条目才会写入证据报告。未审核且命中的共享域名会阻止部署。审批记录属于 `rules-data/state.yaml`，必须带审核人、日期和理由，并进入候选身份。Schema 1 可选字段 `unsupported_dispositions` 保存对具体 unsupported 规则签名的处置；字段缺省等价于 `[]`。数据同步会保留这类精确处置，code-managed baseline 仍独立生效，不会被空的数据分支字段清除。

## GitHub 工作流

`.github/workflows/rules.yml` 只接受手动 dispatch，以不可变 data commit 构建；正式构建先下载并完整校验上一正式 Release，作为分类异常减少检查的基线。`.github/workflows/tests.yml` 运行测试；`.github/workflows/image.yml` 仅在 `main` 测试通过后发布 GHCR commit 标签与 `latest`。三者的 Actions 都固定到完整 commit SHA。规则 Release 先上传 draft，输入代码或数据快照不再是当前分支 head 时不会标成 latest。`rules-data/state.yaml` 是独立数据分支的唯一状态文件。详细接口、真实验收结果和剩余边界见 [GitHub CI 说明](docs/github-ci.md)。

## 管理容器（P3）

管理界面、单后台 worker 和 SQLite 任务索引运行在一个容器中。它可以编辑公开规则状态、服务端预览和校验批量导入、按 SHA 条件写回 GitHub、触发并轮询 Actions、下载并校验候选 Release、查看报告和保留历史。空闲时每 15 分钟只读检查远端提交与 Release 元数据，不会覆写草稿或候选。

### 日常规则更新

打开“人工部署”工作区，点击“生成并检查”。管理器会固定当前草稿快照，按 GitHub SHA 条件保存，触发并轮询 Actions，校验正式 Release，然后按 NAS 备用、VM104 主目标的顺序暂存并验证；该任务不会激活目标。页面随后展示固定 Release、目标最近读回和规则差异。若任一目标版本不一致或没有可验证的本机基线，页面会清楚显示差异不可用，不会把上一个构建版本当作正在运行的版本。

“部署此版本”会再次确认页面当前选中的 Release 和准备请求，并创建绑定该 Release、manifest 与短期 attestation 的一次性人工授权。验证过期时，在“版本选择与任务详情”中选中该 Release 并点“重新检查所选版本”；这只会重做准备，不会激活。若草稿已比候选更新，需在确认框明确选择继续部署旧版本。页面的“回退最近一次”默认选择最近成功的部署，也可在详情中选择其他成功记录。任务状态和技术 ID 收在详情及操作记录中。

每日北京时间 04:17 的任务只构建并下载候选，不准备、不激活目标。规则更新与管理器镜像/helper 升级是不同维护工作，镜像升级不会由本页规则操作触发。

受限 SSH target 操作要求管理员会话、CSRF、本机 Release 校验和短期双目标验证。`config/targets-v4.yaml` 中 `maintenance_disabled` 缺省按 `true` 处理；只有显式设置为 `false` 才接受新的网页人工部署确认。旧 `deployment_enabled` 字段不会开启新的部署，仅供继续恢复升级前持久化的旧任务。每日构建及下载不会自动部署。设备安装和首次切换仍需完成独立实机验收。

部署前将 `.env.example` 复制为 `.env`，先把 `DNS_RULE_BUILDER_IMAGE` 改成验收过的 GHCR digest；准备 `secrets/github_token`、`secrets/admin_password` 和 `secrets/session_secret` 三个**独立文件**。GitHub token 建议只授予该仓库 `Contents: read/write`、`Actions: write`；文件必须可由容器运行 UID 读取并保持只读挂载。管理员密码和 session secret 至少 16 个字符；没有默认密码。按 NAS 的数据目录设置 `DATA_DIR`，并让目录可由 UID/GID 10001 写入。`docker compose config` 检查配置后用 `docker compose up -d` 启动。默认 loopback HTTP 示例关闭 Secure Cookie，适合本机反代前测试；反向代理使用 HTTPS 时设置 `SECURE_COOKIES=true`。

手动构建镜像：`docker build -t dns-rule-builder:local .`，随后在本机 `.env` 将 `DNS_RULE_BUILDER_IMAGE=dns-rule-builder:local`。Compose 默认只绑定 `127.0.0.1:8080`，生产访问应通过受控 HTTPS 反向代理。每日北京时间 04:17 由容器 worker 触发一次 GitHub 构建；容器启动时至多补当天一次。SQLite 与版本目录持久化在 `/data`。

## 规则和来源说明

- Surge `DOMAIN` 转为 `EXACT`，`DOMAIN-SUFFIX` 转为 `SUFFIX`。
- Surge Resolve 文件会影响 IP 解析语义；目前不将其作为额外来源合并。
- 裸 Apple Domain Set 仅在来源显式声明 `surge-domain-set` 格式时解析。当前锁定快照中，点前缀表示后缀规则，裸域名表示精确规则；该映射与 Apple 完整类型化列表逐条比对一致。
- `Foreign-General` 只接收自定义规则和经过逐项审核的独立来源；不导入 Global、Proxy 或 `geolocation-!cn` 等汇总规则集。
- 来源快照、实际分类目录、许可证注意事项、规则格式证据与未完成验证见 `docs/research.md` 和 `docs/source-categories.md`。

## Schema 4 目标适配（P4）

config/targets-v4.yaml 将 NAS 备用目标定义为 SmartDNS-only，将 VM104 主目标定义为 SmartDNS + Mihomo。src/dns_rule_builder/target_v4.py 校验产物哈希、动态 Provider 列表、EXACT/SUFFIX 语义和目标角色，并提供持久化 stage-both → validate-both → backup-activate → main-activate 状态机及模拟器。受限 SSH transport 与独立 target 入口通过实际配置哈希、服务状态和版本标记读回验证运行版本，并保留旧 schema-3 完整配置恢复路径。是否可接受新人工部署由 `maintenance_disabled` 明确控制；缺省按禁用处理，旧 `deployment_enabled` 仅兼容恢复旧任务。设备安装与实机验收状态见当前生产收据及[生产安装说明](docs/production-installation.md)。

管理员可在 Web 的“策略组”工作区读取主目标当前选择，并编辑受规则构建器管理的策略组候选。全局节点目录仅返回经过清理的策略组、静态节点、配置中明确声明的 proxy-provider 节点和 `REJECT`；订阅 URL、认证信息、配置路径与 provider 虚拟 Compatible/default 条目不进入网页。点击“选择”会将当前选择立即写入目标并读回、持久化；修改候选需先预览，确认后由目标离线校验完整 Mihomo 配置，再 reload、恢复其他组选择并读回。该操作通过登录会话、CSRF 与串行任务队列执行；`maintenance_disabled: true` 时只允许查看和预览，所有已排队任务仍会在执行前再次检查维护开关。候选偏好保存在 VM104 的 root 管理状态中，后续规则准备和受支持的回滚会沿用有效偏好；实际节点消失或名称歧义时会显示提示并拒绝猜测。

## 策略组与迁移方案（P5）

策略组映射仍为可配置逻辑组，不预定生产组成员或节点；Google、YouTube、Gemini 使用各自独立的默认逻辑组，各用途和服务策略组手动选择香港节点、美国节点等区域组；区域内部节点由迁移验收确认。R4 默认候选的 19 个 deployment blockers、最终候选的门禁结果及其部署开关状态都是历史比较基线。最终候选有 1,564 条保留来源的类别规则、1,337 个语义项、20 个 Provider 片段和 18 条精确签名审核的 unsupported；272 项 precedence 已解决、0 项未解决，2,623-witness Provider oracle 通过。Schema-1 optional `unsupported_dispositions` 已接入 CLI/controller；字段缺省按空列表兼容，精确处置保留在数据状态中，code-managed baseline 不会被清除。最终二进制和管理容器验证结果见上文。没有验证真实服务访问。证据见[门禁收敛验收](docs/gate-acceptance.md)。生产基线、备份、写入者切换、分阶段验收与回退步骤见[生产迁移方案](docs/production-migration-plan.md)；实际执行结果以单独收据为准。

## R1–R5 历史扩展基线

17 类发布基线的目录含 141 项，其中 124 项为配置类别（包括为读取旧配置保留、禁止启用的 Apple 聚合别名），当时 17 项默认开启（原有 15 项及 TMDB、Fanart）；TikTok 已从当前目录与运行映射移除。BM `83f37120c155f67c8eef43221dabe15cc3c95fec`、v2fly `c6adacf29cc4fb05e04651fcf03e00989ef9f797` 均固定。Surge 与 v2fly adapter 已实现，包含 typed/full/domain、include 属性筛选、来源 affiliation 及正负属性处理。较早的 R5 默认构建基线有 1,102 条分类规则、1,073 个语义项、16 个非空类别和 65 条 unsupported；这些是历史统计。17 类发布基线有 1,564 条分类规则、1,337 个语义项、20 个 Provider 片段和 18 条已按精确签名审核的 unsupported。首次部署前的新增类别和最终统计以扩充候选的审核报告及新发布收据为准。

Mihomo 多用途组和 Google、YouTube、Gemini、Telegram、Netflix、DisneyPlus、PrimeVideo、HBO、Hulu、Abema、Bahamut 独立选择组模板支持手动选择香港节点、美国节点等区域组，并保留 Proxy/REJECT 入口。区域内部节点仍是占位符，模板不可部署。R5 本地目录语义审计已完成：124 个配置类别中 123 个有源，parser 共解析 7,319 条规则并生成 123 个 Providers；全量 syntax-only `-t` 通过，含 Apple 兼容宽泛类别的影子 parser 审计，但它们仍禁止启用。审计保留 1,422 条 unsupported 记录，不替代早期默认构建基线的 65 条统计，也不替代最终候选的 18 条 unsupported。Perplexity、xAI、Pornhub、TMDB、Fanart 五类及 CustomExact 覆盖的 14 条 Mihomo REJECT 请求日志通过；SmartDNS `network none` 检查通过 Fanart 精确主机子域回退和 xAI 后缀子域匹配。测试请求只验证本地规则选择，不代表访问真实服务；syntax-only 通过也不代表所有类别可启用或 Provider 顺序已批准。

可复现目录语义审计：

```sh
bash tools/research/expanded-semantics-probe.sh "$PWD/.cache/expanded-semantics-new-run"
```

此命令复现的是本轮固定缓存环境。运行前需要已有 `.cache/root-expansion-sources.json`，其中锁定 BM、v2fly 与 manual source 的 `source_root`、revision 和 hash；还需要 BM 归档 `.cache/ios_rule_script-83f37120.tar.gz`、v2fly 归档 `.cache/v2fly-c6adacf.tar.gz` 及对应 cache markers，并已缓存本轮固定的 Mihomo、SmartDNS 镜像。脚本拒绝覆盖已经存在的输出目录，因此示例使用新目录。Clean clone 不能直接运行此命令，需先准备相同锁定来源、校验信息、cache markers 和固定镜像。

浏览器目录筛选、批量预览与应用已通过真实复验，single-candidate enable 仍受 build gate 约束。TMDB/Fanart 只覆盖静态已记录依赖；MoviePilot 实际插件路径、TVDB 与动态集成尚无可审计来源，不据此推断全量外部兜底。完整构建、门禁和 probe 证据见[扩展验收记录](docs/expansion-acceptance.md)。

## 开发检查

```sh
uv run pytest -q
```

旧版 `uv run --frozen pytest -q` 回归为 **56 passed**，并完成真实 GitHub 测试、规则构建、不可变 Release、重复发布幂等和 GHCR 发布。扩展后的本地回归与构建结果以当前主验收记录为准；正常第三方规则仍受许可证门禁限制，公开 smoke Release 不可用于家庭部署。完整旧版证据见[开发验收收据](docs/acceptance.md)。没有生产 target 安装或设备变更。
