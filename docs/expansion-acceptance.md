# 国外服务目录与策略组扩展验收

本轮为本地源码、服务目录、策略组模板与隔离验证扩展，不提交、推送、发布、安装 NAS 容器或修改家庭设备。旧版 `docs/acceptance.md` 的 GitHub CI 结果属于上一版，不代表本轮扩展已在远端执行。

## 初始边界

- 开始时已有 TikTok 移除及映射草案修改，四个文件的 diff 保留在忽略的 `.cache/expansion-baseline.diff`，不回退。
- 生产范围文件哈希保存在忽略的 `.cache/expansion-protected-hashes.json`，交付前复核。
- 旧真实 CI smoke `cef8fb0db2ee2a5b7a83bc0aad07d71a72e1b009e45f7f948df81fddfba9e61c` 和 normal `9e8c8494b7b6f0fce742ad852da710c2f0a2403261517c6350d8df061e089f31` 在改造前通过 schema 4 校验，均不允许部署；改造后再验证兼容。

## R1 目录与来源

主 Agent 独立执行 `uv run --frozen python tools/research/catalog_inventory.py --offline --check` 成功：blackmatrix7 全归档有 669 个 Surge 目录（665 个同名主文件），v2fly 有 1,545 个 data key，精选目录有 132 项服务和候选。固定 commit 和归档 SHA 见 `docs/service-catalog-research.md`。

检查过程修正了两类问题：有限 API 目录造成的不存在误判，以及用途组遗漏/Apple 海外组语义不准确。Perplexity、xAI、TMDB 和常见成人服务的独立 v2fly key 均已核对真实归档。来源文件头、README 日期与仓库 commit 日期分列；未收集每个文件 Git 历史的项目不宣称近期更新。

研究目录的发现与待审核状态不表示代理意图或生产可用，实际构建选择、审核门禁和后续阶段验收另列。

## R2 多来源与兼容

主 Agent 独立运行全套测试：66 项通过。额外使用与实现分开的临时契约夹具，验证嵌套 include 的全部正属性/负属性过滤、被 include 引用的虚拟 affiliation、EXACT/SUFFIX 和路径穿越拒绝。

真实固定 v2fly 输入核查：Perplexity 5 条、xAI 4 条、Cursor 4 条、Hugging Face 3 条、Netlify 10 条、TMDB 4 条、Pornhub 9 条可转换域名规则，JavDB 4 条域名及 1 条不支持的正则规则。完整 1,545 个文件进入 affiliation 来源扫描哈希；这证明解析和记录行为，不证明站点可访问。

新 manifest 保持 schema 4，来源能力版本为 1，分别记录来源 commit、归档哈希、适配器版本、选择引用和真实文件哈希。虚拟 key 作为 list key 记录，不伪造物理文件哈希。两份旧真实 CI 产物由新版 verifier 再次通过原身份校验，未改写文件。

CI YAML 已接入多来源锁定流程，当前仅做本地检查；本轮没有运行真实远端 CI、发布规则或更新 GHCR。

## R3 分类与媒体依赖

主 Agent 全套测试 72 项通过，并独立核对 124 个运行类别的每个引用：blackmatrix7 文件、v2fly key 和受控人工文件均真实存在，无重复 Provider 名称。目录扩展为 141 项，17 个类别默认启用（原有 15 项加 TMDB/Fanart）；其余新增独立服务默认关闭。

独立固定快照构建成功，统计为 1,102 条分类规则、1,073 条全局语义条目、16 个非空类别、65 条不支持规则。来源身份与全部产物校验通过；该候选仍不可发布或部署。TMDB 的 api.themoviedb.org、api.tmdb.org、image.tmdb.org 匹配 Media-Metadata；Fanart 的 webservice.fanart.tv、assets.fanart.tv 为 EXACT，child.assets.fanart.tv 不匹配。apple.com 无代理匹配。

初稿中的 AppleID/硬件/常用媒体代理候选和 Google 来源状态错误已修正：前者仅为直连研究项；Google 保留既有运行分类并标明待审核。旧宽泛 Apple 类别保留读取兼容，但开启后受不可运行门禁约束。TikTok 不在运行配置和服务目录中。Fanart 已有本项目维护的可构建来源，其“缺独立上游 key”字段与整体来源可用状态分别记录。

新增审核门禁也覆盖既有尚未完成目录审核的启用服务，不伪造审核通过；真实候选显示 14 项目录审核阻断及已有不支持类型、Google/YouTube 循环、共享域名、目标语义和部署开关阻断。Google/YouTube 循环的具体原因保留在映射文档，不通过简单排序或删域名掩盖。

正式项目配置的 custom-only fixture 回归证明 smoke 不含上游规则，仍与正常规则版本区分。静态媒体覆盖不证明实际安装的 MoviePilot 插件或应用内代理/DoH 路径已验收。

## R4 策略模板与管理界面

主 Agent 独立运行 77 项测试通过，随后用本地假 GitHub 服务进行真实浏览器验证：新增精确规则后批量停用 Fanart，规则仍保留；预览后继续新增规则会使应用按钮失效；刷新保留未保存的浏览器编辑。没有使用真实 PAT，也没有提交规则到 GitHub。补修后再次复验：Pornhub 可选择，批量启用返回待审核阻塞且应用按钮禁用；Apple 与 AppleDomainSet 均不可启用。主 Agent 对最终 R4 全套 77 项测试复验通过。

主 Agent 用锁定 Mihomo v1.19.32 镜像检查了由默认候选渲染的 16 Provider / 17 规则完整模板，检查成功。SmartDNS v1.2026.v48.4.2 精确/后缀、Mihomo Provider 顺序与 SAFE_PATHS 的既有隔离探针再次通过。测试配置将代理组固定为 REJECT，未使用生产节点。

本地应用镜像成功构建；在 UID 10001、只读根文件系统、断网和临时数据目录条件下启动，healthz 返回 ok，目录读取为 141 项。仅创建并删除本次测试容器，未发布镜像或安装到 NAS。

窄窗口截图检查后修正布局：586px 视口的页面宽度与滚动宽度一致，表格单独横向滚动；禁用按钮有明确的禁用样式，服务目录锚点不会被固定导航遮挡。

最终 R4 源码状态用同一固定输入重建两次，得到相同版本 `5223a1c68156a0bc48f55cfa9f26600d68cb0f38b1182b57ff4f5f32c51e27f3`，27 个产物哈希全部一致并通过新版 verifier；该版本有 19 项部署阻塞，publish_allowed/deployment_allowed 均为 false。两个旧真实 CI 版本再次校验通过。该完整候选模板通过固定 Mihomo 二进制检查。

## R5 全目录语法审计与真实来源语义

Luna 负责探针与文档收尾，主 Agent 独立运行 `bash tools/research/expanded-semantics-probe.sh "$PWD/.cache/root-expanded-semantics-acceptance"` 成功。124 个配置类别中 123 个有来源，另一个为纯人工 Foreign-General；影子解析有 7,319 条域名规则、123 个非空 Providers 和 1,422 条 unsupported。全量 Provider 配置与代表性候选均通过锁定 Mihomo v1.19.32 的 `-t`。全量配置仅用于来源解析及语法审计，包含禁止生产启用的 Apple 兼容项，不进行全部类别的部署顺序批准。

在 `--network none` 下执行 14 条代表请求，实际 Mihomo 日志验证 Perplexity、xAI、Pornhub、TMDB、Fanart 及 CustomExact 跨类别优先级。主 Agent 额外独立断言 Fanart 的精确主机子域落到 Match/REJECT、`perplexity.ai` 的 CustomExact-Foreign-General 优先且其子域仍选 Perplexity。SmartDNS 从真实候选复制生成的 Domain Set，验证 Fanart EXACT 子域回到隔离父域地址、xAI SUFFIX 主域及子域均命中。没有访问实际服务或家庭设备；REJECT 日志只证明规则选择。

补充审计输出后，主 Agent 单独运行 prepare 到 `.cache/root-expanded-semantics-audit-final` 并验证：`all-category-syntax/unsupported.json` 有 1,422 条完整明细，每条含 source、revision、文件、行号、原文及原因；`categories.json` 有 124 条类别摘要。此补充只写审计 JSON，未重复二进制测试。所有证据为本地忽略目录，未发布第三方完整规则列表。

默认候选仍为 1,102 条分类规则 / 1,073 个语义项 / 65 条 unsupported；全目录影子统计不能替代默认候选统计。19 个部署阻塞准确分为 14 项目录待审核、1 项不支持输入审核、1 个 Google/YouTube 顺序循环、1 项共享域名审核、目标语义未确认及部署总开关关闭。许可证发布审核单独为 false，不计入上述 19 项。

## 最终交付状态

主 Agent 最终全套测试为 **77 passed**。JavaScript 语法、Python 编译、两个探针 Bash 语法、Compose 配置、工作流 actionlint 与 Git diff 空白检查通过。本地镜像 `dns-rule-builder:local-expansion` 完成构建；此前已验证同一应用代码以非 root / 只读 / 断网方式启动并读取全部目录，最终重建包含窄窗口样式修补。

四份受保护边界文件的 SHA-256 与初始记录一致，旧 HomeGatewayConfig 未读作现网事实或修改。没有执行 git add、commit、push、远端 CI、Release/GHCR 发布、生产 SSH、NAS 安装或服务变更。本次测试页面和容器已关闭/清理；初始用户四文件改动保留。target_semantics_verified、deployment_enabled、license_release_approved 均保持 false。生产节点成员、来源审核和具体迁移仍留在已约定的后续评审边界。
