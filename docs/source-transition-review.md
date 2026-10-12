# 默认来源迁移评审（G1）

本记录说明本阶段批准的来源选择、许可证证据口径和已完成的固定快照差异审计。G1 来源审核已由主 Agent 独立复核；它只允许所列数据源进入 Builder 审核范围，不表示部署门禁已解除、规则已发布或已部署。

## 来源选择与许可证证据

默认仍为 17 个类别：14 个现有发布服务加 TMDB，共 15 个类别使用固定 v2fly key；两个已批准的人工/通用类别保持原有语义。OpenAI 与 Docker 各有基于官方业务文档的精确人工依赖。BM 固定快照用于迁移基线对比，不作为这 15 个默认类别的目标发布来源。逐项 key/include 闭包、转换差异和不支持语法已由 [source-transition-audit.py](../tools/research/source-transition-audit.py) 审计，当前本地收据位于忽略目录 `.cache/root-g1-source-audit/summary.json`；主 Agent 独立重跑得到相同 totals。该收据是本机审计产物，不是跟踪版本的发布文件。

| 默认类别 | 目标固定 v2fly key |
|---|---|
| `openai` | `openai` |
| `claude` | `anthropic` |
| `gemini` | `google-gemini` |
| `copilot` | `github-copilot` |
| `google` | `google` |
| `telegram` | `telegram` |
| `github` | `github` |
| `docker` | `docker` |
| `gitlab` | `gitlab` |
| `jetbrains` | `jetbrains` |
| `vercel` | `vercel` |
| `discord` | `discord` |
| `reddit` | `reddit` |
| `youtube` | `youtube` |
| `tmdb` | `tmdb` |

v2fly 固定 revision 为 [`c6adacf29cc4fb05e04651fcf03e00989ef9f797`](https://github.com/v2fly/domain-list-community/tree/c6adacf29cc4fb05e04651fcf03e00989ef9f797)，根 MIT 条款副本为 [`../licenses/v2fly-MIT.txt`](../licenses/v2fly-MIT.txt)。本项目对普通数据采用比例化审查：核对所选输入 key/文件、固定快照中的 include 展开、实际内容哈希，并检查未发现所选文件级别的冲突 license/禁止再发布声明。该数据范围后续再发布须保留 MIT 声明；资产打包状态见下文。不要求逐条域名做律师审查，也不据此断言每个原始贡献者身份均已确认。含第三方支付、身份、分析、错误报告或其他跨服务共享基础设施的条目另加用途风险检查。

BM 固定 revision [`83f37120c155f67c8eef43221dabe15cc3c95fec`](https://github.com/blackmatrix7/ios_rule_script/tree/83f37120c155f67c8eef43221dabe15cc3c95fec) 仍保留在研究/关闭候选快照，但其 README 的转载及研究用途文字使公开再发布适用范围不明确，故本轮不作为默认来源。项目源码许可证不因数据来源切换而变更。MIT 仅适用于 [`config/curated/`](../config/curated/) 下明确圈定的数据文件，不适用于程序代码。G1 阶段已复核 v2fly 根 MIT 副本、人工数据 MIT/notice 以及所选 key/include 闭包哈希；当时尚未完成规则 ZIP 内许可材料和发布身份哈希收敛。该后续工作已由 G3 主验收完成：候选包内含许可证、notice、来源审核摘要及哈希，相关来源许可 provenance 纳入候选身份；独立许可副本由已验证候选包导出，`SHA256SUMS` 用于完整性校验但不是数字签名。详见[门禁收敛验收](gate-acceptance.md)。

## 业务依赖和高风险共享域名

下列厂商资料在 2026-10-11 核对，用于识别优先保留的专属业务 host，以及应从默认代理规则排除的统计/错误报告端点。它们描述厂商建议的网络依赖，不证明本机真实请求或完整服务清单。

| 产品 | 一手证据与本轮处理 |
|---|---|
| OpenAI / ChatGPT | [OpenAI 网络建议](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps)（2026-10-11 获取；页面显示 3 天前更新）列出 `*.auth.openai.com`、`auth0.openai.com`、`cdn.workos.com`、`forwarder.workos.com`、`frontend-apps-multi-region.workos.com`、`cdn.openaimerge.com`、`chatgpt.com`、`*.openai.com`、`*.oaiusercontent.com` 等登录、产品与文件服务域，也列出 Stripe 的 `js.stripe.com`。当前只选择经评审的专属/EXACT 业务候选，例如 WorkOS 登录/前端 host、OpenAI merge CDN 与 Stripe.js；具体规则仍以最终配置和 source audit 为准。该网页是公司网络指导而非完整 DNS/流量契约：challenge/送信/Intercom/Apple App Attest、WebSocket 路径和 Voice UDP 动态 IP 等尚未承诺覆盖。该文档同时列出 `rum.browser-intake-datadoghq.com` 与 `o33249.ingest.sentry.io`；它们属于遥测/错误报告风险项，本轮不纳入默认代理来源。`openai.qualtrics.com` 来自源数据的调查/反馈候选，也排除在默认代理输入之外。 |
| Docker Desktop / Hub | [Docker 官方 Desktop allowlist](https://docs.docker.com/desktop/enterprise/allow-list/) 指定认证 host（`auth.docker.io`、`login.docker.com`、`auth.docker.com`）、拉取/推送 host（`registry-1.docker.io`、`production.cloudfront.docker.com`）以及其他产品依赖。优先使用这些专属 host。Docker 文档将 Bugsnag 主机用途标为错误报告；旧 BM CloudFront shared hosts 不作为当前依赖证据或默认输入。 |
| Claude Code | [Anthropic corporate proxy 文档](https://docs.anthropic.com/en/docs/claude-code/corporate-proxy) 将 `api.anthropic.com` 用于 API、`statsig.anthropic.com` 用于 telemetry/metrics、`sentry.io` 用于 error reporting；[数据使用说明](https://docs.anthropic.com/en/docs/claude-code/data-usage) 说明 Statsig/Sentry 默认开启且可分别关闭。登录/API 专属产品 host 可按官方范围保留；遥测与错误报告按共享服务风险排除。该证据只适用于 Claude Code，不能直接替代 Claude.ai 的运行清单。 |

OpenAI v2 key 中的 `chatgpt.com`、`chatgpt.site`、`chat.com` 与 `crixet.com` 不能仅凭同处一个 key 同等处理。[OpenAI ChatGPT Sites 条款](https://openai.com/policies/chatgpt-sites-terms/)（2026-07-23 更新）说明 OpenAI 可提供其拥有、以 `chatgpt.site` 结尾的子域；OpenAI 官方 [ChatGPT 网络建议](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps) 也明确列有 ChatGPT/OpenAI host。OpenAI Academy 将 `chat.com` 直接列作 ChatGPT 入口（[官方资源页](https://academy.openai.com/en/public/clubs/higher-education-05x4z/resources/resources-chatgpt-for-faculty-session-2026-05-12)），按本轮业务归属标准足以保留；不声称该页面证明了域名收购或法律所有权。`crixet.com` 未找到足够的一手 OpenAI 产品归属或依赖证据，故不得进入默认发布集合。

## 固定快照差异与部分覆盖

主 Agent 独立重跑后的 G1 汇总为：15 个迁移类别；BM 基线 1,100 条可比较规则语义；所选 v2fly 输入解析出 1,559 条支持的 EXACT/SUFFIX 语义，另有 18 条不支持语法；移除 6 条经授权的精确排除项，加入 9 条人工精确依赖后，目标集合为 1,562 条。相对 BM 基线，语义集合新增 634 条、移除 172 条。新增/移除是两个固定快照间的语义差，不是流量覆盖率、服务完整度或质量分数。

| 类别 | v2fly key | 有效规则 | 新增 / 移除 | 覆盖审核 |
|---|---|---:|---:|---|
| `openai` | `openai` | 26 | +16 / −21 | partial：1 条 regex 不支持；4 条精确排除（含 `crixet.com`、统计/错误报告/问卷端点）；8 条官方依赖人工精确补充。保留 `chat.com`、`chatgpt.site`。 |
| `claude` | `anthropic` | 9 | +7 / −1 | 支持语法完整；与其他服务共享语义另按来源收据审视。 |
| `gemini` | `google-gemini` | 46 | +38 / −2 | 支持语法完整；与 Google 有 46 条交叠。 |
| `copilot` | `github-copilot` | 4 | +4 / −46 | 支持语法完整；排除 2 条遥测 host。 |
| `google` | `google` | 1,106 | +492 / −71 | partial：14 条本项目不接受的单标签/TLD 后缀输入（parser reason: `invalid DNS name or top-level suffix`）、2 条 regex 不支持；include 闭包含 YouTube 与 Google DeepMind，非外国域名兜底。 |
| `telegram` | `telegram` | 21 | +3 / −6 | 支持语法完整。 |
| `github` | `github` | 65 | +40 / −5 | 支持语法完整；与 Copilot 有 4 条交叠。 |
| `docker` | `docker` | 7 | +4 / −4 | 支持语法完整；含 1 条官方 allowlist 精确人工依赖。 |
| `gitlab` | `gitlab` | 5 | +2 / −3 | 支持语法完整。 |
| `jetbrains` | `jetbrains` | 25 | +12 / −1 | 支持语法完整。 |
| `vercel` | `vercel` | 27 | +3 / −3 | 支持语法完整。 |
| `discord` | `discord` | 28 | +2 / −3 | 支持语法完整。 |
| `reddit` | `reddit` | 12 | +7 / −3 | 支持语法完整。 |
| `youtube` | `youtube` | 177 | +2 / −3 | partial：1 条本项目不接受的单标签/TLD 后缀输入（parser reason: `invalid DNS name or top-level suffix`）；与 Google 有 177 条交叠。 |
| `tmdb` | `tmdb` | 4 | +2 / −0 | 支持语法完整。 |

“partial”仅指源中存在无法转为有效域名 EXACT/SUFFIX 的语法；18 条原始 unsupported 记录保留在审计收据中，没有静默丢弃。6 条人工批准的排除另列为意图，不计为语法 partial；9 条人工精确补充包括 OpenAI 8 条、Docker 1 条。类别语法可完整转换不代表该服务覆盖完整，也不代表已批准路由顺序或允许启用。source 属性 `@cn`、`@ads`、`@telemetry` 保留为出处元数据，不自动决定路由；Google/YouTube、Gemini/Google 和 Copilot/GitHub 的重叠仍须按路由策略处理。

审计边界：G1 结果确认固定输入的解析、include 展开、所选文件哈希、支持/不支持语法与快照差异；不证明真实服务访问、完整 DNS/流量清单、所有类别已准许启用、部署门禁全通过、Release 发布或生产部署。许可资产随候选打包与 provenance 验证见 G3 状态。

## G2 编译与审核状态

主 Agent 独立验收 compiler capability 2 与 manifest `provider_map`：20 个 Provider 片段覆盖 1,564 条保留来源规则、1,337 个域名语义；18 条 unsupported 精确签名均保留在报告，G2 候选 `review_required: false`。旧 source capability 1 的 schema-4 verifier 兼容仍通过。另以不含 BM 缓存的冷项目、空 schema-1 用户规则状态构建 v2fly/人工来源候选成功；2,623 个独立规则主域、子域与未收录域 witness 对照实际 Provider 顺序全部通过。G2 当时只剩生产 target semantics 未确认与 deployment master switch 关闭两个部署阻塞；G3 后续验收另行确认发布许可。真实服务访问、家庭网络生产适配仍未证明。详见[门禁收敛验收](gate-acceptance.md)。

## G3 发布 provenance 与本地目标验证

主 Agent 已复验默认验收候选 `review_required: false`、`publish_allowed: true`、`deployment_allowed: false`，仅生产 target semantics 未确认和 deployment master switch 关闭两项部署阻塞。候选 provenance ZIP、来源许可/notice、source review summary 与外置 checksum 通过自包含与一致性检查；checksum 清单不是数字签名，也不表示已创建 Release。

该验收候选的 Mihomo v1.19.32 20-provider 配置通过断网语法检查，21 条代表性请求的规则路由日志通过；SmartDNS v1.2026.v48.4.2 从实际生成的 set 处理 4 个断网 EXACT/SUFFIX 请求通过。此候选仍不证明真实服务访问或家庭设备集成。主验收随后发现 schema-1 optional unsupported disposition 的 CLI/controller 接口尚未接通，实施端正在补齐；后续修改会产生新的候选身份，最终 build ID 与复验状态待更新。详见[门禁收敛验收](gate-acceptance.md)。

## 2026-10-12 新增类别来源复核

最新 GitHub 状态 `0fae7627a84fd2f4d097ba472995ce21f29bfc77` 保持 85 个用户启用类别。本轮对其中原先没有可发布来源档案的 25 类逐类审核，并继续把每类最终来源限定为一个明确选择：21 类使用固定 v2fly MIT key；没有独立 key 的 Linux、VikACG、AnonV 与 YouTube Music 使用项目自有 MIT 精确域名列表。选择状态不自动扩展成完整服务覆盖。

| 类别 | 选定来源 | 类别 | 选定来源 |
|---|---|---|---|
| `steam` | v2fly `steam` | `microsoft` | v2fly `microsoft` |
| `anthropic` | v2fly `anthropic` | `perplexity` | v2fly `perplexity` |
| `xai` | v2fly `xai` | `cursor` | v2fly `cursor` |
| `huggingface` | v2fly `huggingface` | `netlify` | v2fly `netlify` |
| `linux` | curated EXACT：`linux.org`、`www.linux.org`、`kernel.org` | `ubuntu` | v2fly `ubuntu` |
| `electron` | v2fly `electron` | `xbox` | v2fly `xbox` |
| `playstation` | v2fly `playstation` | `blizzard` | v2fly `blizzard` |
| `riot` | v2fly `riot` | `ubisoft` | v2fly `ubisoft` |
| `ea` | v2fly `ea` | `gog` | v2fly `gog` |
| `imdb` | v2fly `imdb` | `japonx` | v2fly `japonx` |
| `picacg` | v2fly `picacg` | `vikacg` | curated EXACT：`vikacg.com` |
| `anonv` | curated EXACT：`anon-v.com` | `apple_dev` | v2fly `apple-dev` |
| `youtube_music` | curated EXACT：`music.youtube.com` | | |

这些新来源均标记为 partial。v2fly 是有限社区列表，绝不代表厂商完整 endpoint 清单；`microsoft_services` 特别宽，微软文档也说明硬编码/间接 FQDN 清单不适合作为稳定端点契约。项目人工列表只纳入有一手首页证据的精确入口。Linux 仍保留用户选择的 `linux` 类别 ID；此前 BM 的 `linux.org` suffix 被缩窄为 Linux.org 站点两个 EXACT 首页入口，另保留 `kernel.org`。这不覆盖发行版、软件仓库、内核文档或 Linux 生态其他流量。Anthropic 和 Claude 是独立策略类别但使用相同 v2fly `anthropic` key；构建按语义去重。

来源共享范围另做两条精确排除：Netlify key 中的 `d33wubrfki0l68.cloudfront.net` 没有 Netlify 一手证据可证明它由 Netlify 管理，因此仅从 Netlify 类排除此 host，并继续禁止 `cloudfront.net` suffix；Ubisoft 官方 cookie 声明将 Qualtrics 列为第三方，因此只从 Ubisoft 类排除 `ubisoft.siteintercept.qualtrics.com` 这条 survey host。未因此放行任何共享基础设施 suffix。

本轮逐条确认 11 个解析为 invalid DNS/TLD 的单标签输入（Microsoft key 8 条、Xbox 1 条、PlayStation 1 条、IMDb 1 条）并添加对应 raw hash 与源行精确的 `keep_rejected_in_report` disposition。它们继续被拒绝且保留在 unsupported 报告，未按类别整体放行。其余 27 条已有精确签名处置不变。所有来源候选的选定 key/file、MIT/NOTICE、固定 include 闭包及源文件哈希记录在 `config/source-approvals.yaml`；服务一手身份/范围证据与实际选定源路径列在 `config/service-catalog.yaml`。

在最新 85 类状态上使用隔离临时项目重新应用 `state.yaml` 并构建，用于验证 source/profile 门禁、解析、provider 顺序与冲突；这不是发布或部署授权。发布与生产门禁仍独立于来源审核，本次没有调整标准 target 或 deployment switch。

主 Agent 与 Luna 在两个独立项目副本中复现同一候选 `731e111ba21bbf766efd39b7c79bc8bedb2ca3f7cc62bfeba906942f87a48fad`：85 个启用类别、3,944 条分类规则、3,546 个独立语义与 SmartDNS 条目、100 个 Mihomo Provider，38 条 unsupported 保留并已有精确处置，未解决冲突为 0。完整 manifest 与产物哈希校验通过，`review_required=false`、`publish_allowed=true`；部署阻塞恰好为目标准备验证与人工部署总开关两项。固定 Mihomo v1.19.32 镜像在本机 `--network none` 下校验全部 Provider 成功；这是语法检查，没有验证真实服务访问、生产双端适配或家庭客户端。本次没有提交、发布、更新管理容器或激活新规则。
