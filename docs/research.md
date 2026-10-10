# DNS Rule Builder R0 研究记录

日期：2026-10-10  
研究快照：`blackmatrix7/ios_rule_script@83f37120c155f67c8eef43221dabe15cc3c95fec`  
归档 SHA-256：`e4249eb848059306541eea99ce5ecc1c0a988810afc716cb76db456159c6bf99`

## 研究范围与结论

已从上游锁定 commit 的 GitHub codeload 归档检查 `rule/Surge/` 样本、分类 README、源目录和许可证。归档保存在被忽略的 `.cache/ios_rule_script-83f37120.tar.gz`。目录名大小写敏感，应使用实际 `GitHub/`、`Jetbrains/`、`Whatsapp/` 等路径。commit 时间是 2026-10-09 22:09:47 UTC；文件自己的 `UPDATED`/README 时间差异明显，仓库 commit 新不代表所有服务规则都新。

首批来源候选和各文件更新日期、规则类型、缺项见 [来源分类清单](source-categories.md)。适配首版建议把有类型的主 Surge `.list` 作为唯一输入；只解析 `DOMAIN` 和 `DOMAIN-SUFFIX`，其余语法作为 unsupported 记录。`*_Resolve.list` 是可选目标兼容版本，不能作为额外来源合并，否则会重复计数。Apple 等 `_Domain.list` 裸域名产物必须由专用格式适配器处理，不能从扩展名推断匹配类型。

## 格式与语义证据

- `rule/Surge/OpenAI/OpenAI.list` 带 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、IP 和 IP-ASN。其 README 说明 `OpenAI_Resolve.list` 的差异是 IP-CIDR(6) 是否带 `no-resolve`，两者不是互补分片。
- `rule/Surge/Apple/Apple.list` 在该快照只有 keyword、IP、process 和 user-agent，未含域名项；`Apple_All.list` 含完整类型规则，`Apple_Domain.list` 是约 1,560 行以 `.domain` 起始的裸域名文件。其声明有 1,551 个 DOMAIN-SUFFIX 和 9 个 DOMAIN。该来源宽泛且混合历史营销/仿冒名字，首批关闭，人工审查后再启用。
- Gemini 示例包含 `DOMAIN-SUFFIX,gemini.google.com` 和 `DOMAIN-SUFFIX,apis.google.com`，Google 泛分类会覆盖它们的域名空间。不能靠清除父子重复项决定代理策略；应保留分类规则并按优先级排序，对无法证明正确的跨策略重叠阻止部署。
- Steam 数据是单独服务类别，但服务标签不能证明每条规则都适合代理。经完整样本检查，Steam 规则包含中国下载相关域名；该类别留待逐项审核，不把来源类别直接等同代理意图。

## 规则目标格式

Apple Domain Set 格式已用同一锁定快照做静态等价检查：Apple_Domain.list 共 1,560 行，其中 1,551 行以前置点号开头、9 行不带前置点号。去掉前置点号并分别解释为 SUFFIX/EXACT 后，与 Apple_All.list 中的域名规则集合完全相同（1560/1560）。因此程序只会在配置显式指定 surge-domain-set 时按此方式解释裸域名，不能据扩展名猜测；此数据格式等价不代表目标 DNS 程序行为已实测。

SmartDNS 官方文档描述 `-.example.com` 为只匹配主域名形式，`*.example.com` 只匹配子域名，`*-a.example.com` 匹配主域和子域；`domain-set` 可用 `/domain-set:<name>/` 被 `domain-rules` 等选项引用。来源：[SmartDNS Domain Rules](https://pymumu.github.io/smartdns/en/config/domain-rule/)、[SmartDNS 配置参数](https://pymumu.github.io/smartdns/en/configuration/)。

Mihomo 官方 Meta-Docs 定义本地 provider 的 `type: file`、`behavior: classical`、`format: yaml`、`payload` 示例，且 provider 路径默认限制在 `HomeDir`，其他位置要通过 `SAFE_PATHS` 授权。Mihomo 规则按 `rules` 列表顺序匹配。来源：[Mihomo Rule Providers](https://wiki.metacubex.one/config/rule-providers/)、[官方文档源文件](https://github.com/MetaCubeX/Meta-Docs/blob/main/docs/config/rule-providers/index.md)、[Rules](https://wiki.metacubex.one/config/rules/)。

除上述静态模型外，现已完成两种固定镜像的隔离二进制实验，复现命令和夹具见[目标语义验证记录](target-semantics.md)及 `tools/research/target-semantics-probe.sh`。SmartDNS `1.2026.v48.4.2` 的 `-.` exact 与裸域名 suffix 行为已在 `--network none` 容器中核实；Mihomo `v1.19.32` 的本地 classical YAML provider、EXACT/SUFFIX 顺序、Gemini 优先于 Google 及 `SAFE_PATHS` 限制也已在隔离容器核实。Mihomo 完整 14-provider 候选配置的 `-t` 检查由本地父 Agent 使用非仓库缓存执行通过。上述是候选二进制/配置的隔离结果，不是现网 VM104、NAS 或家庭流量验证；实际部署目标版本仍未确认，因此 `target_semantics_verified` 和生产部署总开关继续为 false。

## 重叠、共享域名与代理意图

本次确认的具体重叠至少包括 Gemini 与 Google 父域匹配（`gemini.google.com`、`apis.google.com`）及 YouTube 的 Google 生态域名。共享身份/CDN/统计域名风险不能由服务名自动消除。构建器应报告匹配到多个候选策略的域名、保留来源和具体规则，并把未裁定重叠标为审核阻断。自定义 EXACT 规则需生成置顶 `CustomExact-<category>` provider；其它 provider 顺序来自可配置逻辑策略映射和优先级。生产实际组名和成员仍待用户在策略组重整阶段确定。

`Foreign-General` 仅收用户自定义及逐项批准的独立长尾规则；不可从 `Global`、`Proxy`、`geolocation-!cn` 等综合集合导入。首版不接入 v2fly 或 Sukka：前者的 `domain:`、`full:`、`include:` 属性和服务归属需要独立适配/审计，单纯数据分类不能推导代理意图；Sukka 目录与分类也需后续锁定快照核对。

## 许可证与再发布

此快照根目录提供 GPL-2.0 `LICENSE`，同时根 README 声明禁止公众号/自媒体转载或发布、仅供学习研究，并提示仓库聚合外部项目内容且应查看各目录来源。GPL 文本许可与 README 声明的适用范围、聚合内容各自许可证以及把生成规则文件公开发布到 GitHub Release 的合规关系，需要逐个文件来源和专业判断；本研究不作法律结论。实施前默认**不公开发布第三方规则产物**，先补充来源归属与许可证清单并完成审查。CI 及公开源码可先在本地开发，不视为产物再发布已获批准。

## 尚未完成的风险核查

| 项目 | 当前证据/状态 | 门禁 |
|---|---|---|
| SmartDNS EXACT 与 Domain Set | 固定摘要的候选 `1.2026.v48.4.2` 隔离实测通过；VM104/NAS 实际二进制未确认 | 在备份和主目标确认实际版本后重复隔离/目标校验，生产开关仍关闭 |
| Mihomo provider 实际载入、顺序、SAFE_PATHS | 固定摘要的候选 `v1.19.32` 隔离实测通过；完整 14-provider `-t` 由本地缓存候选通过；VM104 当前版本未确认 | 核对目标机版本、配置路径与真实 staging 后再评估，当前证据不放行生产 |
| 上游文件更新可靠性 | commit 日期新，服务更新时间从 2025 至 2026；时间字段说明事实不等于维护 SLA | CI 记录文件哈希和日期；过期/归零/大幅变化进入审核 |
| 共享基础设施、国内服务误收 | 有 Gemini/Google 重叠、Apple 宽泛集合、Steam 国内下载相关域名实例 | 不自动消除冲突；人工确认代理意图 |
| 公开 Release 的来源许可 | GPL-2.0 与额外声明、目录级外部来源待核对 | 发布第三方产物保持关闭 |
| AAAA/CNAME/HTTPS/SVCB/客户端覆盖 | 静态规则只能判断域名列表；无现场请求/应用验收 | 将记录解析类型与真实应用流量覆盖分开，生产迁移阶段另测 |

