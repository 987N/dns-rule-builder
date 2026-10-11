# 规则审核与发布门禁收敛验收

本轮由 Luna 分阶段实施，主 Agent 独立验收。目标是默认构建 review_required=false、publish_allowed=true，同时保留 target_semantics_verified=false、deployment_enabled=false。没有生产节点选择、生产服务变更或外部发布授权。

## 初始状态

开始时工作树已有上一轮目录与策略组扩展，未提交改动和新文件清单已记录在忽略的 `.cache/gate-start-baseline.diff` 与 `.cache/gate-start-untracked.json`。不回退这些内容。生产边界四份文件哈希与上一轮记录一致。

旧默认候选 `5223a1c68156a0bc48f55cfa9f26600d68cb0f38b1182b57ff4f5f32c51e27f3` 的 19 项部署门禁与独立许可发布门禁作为比较基线；上一轮隔离语义和真实 CI 历史不代替本轮验收。

## 阶段验收

### G1 来源收敛

主 Agent 独立重跑 `tools/research/source-transition-audit.py`，证据在 `.cache/root-g1-source-audit/summary.json`。15 个迁移类别的 blackmatrix7 原始可解析规则为 1,100 条，v2fly 为 1,559 条；审核排除 6 条、人工补充 9 条 EXACT，得到 1,562 条类别规则。相较旧来源，语义新增 634 项、移除 172 项；18 条不能表达的输入保留报告。这些是固定快照差异，不表示两套来源等价或真实访问覆盖。

独立核对默认启用数仍为 17、16 个非空上游/人工类别的审批 profile、6 条排除和 18 条 unsupported 记录。许可副本与固定 v2fly 根 LICENSE 字节一致，人工 MIT/NOTICE 哈希及所选 include 文件哈希均匹配。实际 key 闭包已从全仓分类索引范围分离；不使用整个来源文件内容哈希作为普通域名增删的重新审核条件。

G1 通过数据与来源范围验收；编译顺序、审核执行及包内许可绑定将在 G2/G3 验收，当前不宣称最终发布门禁清除。

### G2 编译与审核接口

主 Agent 运行完整测试，84 项通过。独立校验两个旧真实 CI schema-4 版本 `cef8fb0…` 与 `9e8c849…`，均通过，未重写旧产物。

另外创建不含 blackmatrix7 缓存的 shadow 项目，应用原有空 schema-1 rules-data，再使用锁定 v2fly 与人工来源构建。候选 `bc2946fae2e53eec5ab7ba80526709ccfb6c91581c88385f12bf597d27459dcd` 有 1,564 条保留出处的类别规则、1,337 个独立域名语义、20 个 Provider 片段；6 条基线排除仍然生效，18 条 unsupported 均按精确审核记录保留报告。review_required=false，仅剩目标语义未确认与部署总开关关闭两个部署阻塞。发布许可仍待 G3。

独立 oracle 不调用 Explain 或编译器排序函数，而以规则匹配范围、CustomExact 和显式优先级计算策略，逐一对照实际 Provider 文件顺序；2,623 个规则主域/子域/未收录域 witness 全部匹配。旧产物上的 `beacons.gvt2.com` 错序可被此检查捕获，新候选命中 Google。

### G3 发布许可与完整性

主 Agent 独立运行 95 项测试，全部通过；仍有一条既有 Starlette/httpx 弃用提示。许可审批不再能被旧总布尔开关绕过：普通域名增删继承 profile，许可/NOTICE、附加声明、include selector 与共享基础设施范围变化重新审核。LICENSE/NOTICE 与审核摘要放入包内 provenance，参与 artifact 哈希和 release identity；发布资产来自已经校验的候选。

独立构造并重算哈希的缺失许可用例曾暴露验证漏洞，已由 Luna 修复：删除 v2fly 许可及其 provenance 记录的候选，现在被拒绝为 `source license provenance does not cover selected source references`。新增回归同时覆盖删去 profile observation、上传失败、重复发布和本地资产预检失败时不创建 GitHub client。两份旧真实产物仍验证通过。

新默认构建已得到 review_required=false、publish_allowed=true、deployment_allowed=false，且只剩两个约定部署门禁。配置说明与最终断网二进制检查收束后，下一节记录最终版本与独立包校验。

### G4 最终候选与独立验收（2026-10-11）

最终候选为 `0b8f836df17bcd1551eee1c189a538281e1720779a01a6017135bd17e2899787`。默认启用类别保持 17 个（Foreign-General 人工集合为空，16 个非空类别），包含 1,564 条保留出处的类别规则、1,337 个独立语义/SmartDNS 项、20 个 Provider 片段和 40 个包内产物。272 项重叠已按规则语义与显式优先级裁决，未裁决冲突为 0。18 条 unsupported 按精确签名审核后保留报告，不转换为宽泛后缀；这仍是部分覆盖。

最终状态为 `review_required=false`、`publish_allowed=true`、`deployment_allowed=false`。部署阻塞只剩 `target SmartDNS/Mihomo semantics not verified` 与 `deployment master switch is disabled`；配置中的目标语义确认和部署开关仍关闭。Google、YouTube、Gemini 分别使用独立策略组；AI 保留为旧数据的合法覆盖选项。生产节点仍为占位，未选择实际节点。

Schema-1 rules-data 可选 `unsupported_dispositions` 已贯通 CLI、controller、Web 草稿、远端状态同步、审核报告及版本身份；旧六字段数据补空默认值，不能清除代码管理的审核基线。主 Agent 用没有 blackmatrix7 缓存的 shadow 项目应用旧六字段状态并重建，得到与最终候选完全相同的 release ID，审核/发布状态和两项部署门禁一致。

主 Agent 独立运行最终 102 项自动化测试，全部通过，仅保留既有 Starlette/httpx 弃用提示。覆盖精确审核新增/消失/非法审批、来源与许可范围变化、Provider 成员遗漏/重复/策略映射/路径/哈希损坏，以及本地模拟上传失败、重复发布和许可缺失时禁止构造远端发布客户端。两份历史真实 CI schema-4 产物 `cef8fb0db2ee2a5b7a83bc0aad07d71a72e1b009e45f7f948df81fddfba9e61c`、`9e8c8494b7b6f0fce742ad852da710c2f0a2403261517c6350d8df061e089f31` 再次校验通过。

最终实际 Provider 文件顺序通过独立 2,623 项匹配 oracle。固定 Mihomo v1.19.32 镜像 `sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e` 在 `network none` 下通过 20 个 Provider 的语法检查和 21 项代表性路由日志；固定 SmartDNS 1.2026.v48.4.2 镜像 `sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff` 以最终域名集合通过 4 项 EXACT/SUFFIX 请求。测试仅添加隔离本地响应及 REJECT 节点占位；规则文件来自最终候选。这些是本地规则语义验证，不代表真实服务访问成功或生产目标已确认。

最终管理镜像 `dns-rule-builder:local-gates-final-20261011` 在本机 OrbStack 以 UID 10001、只读根文件系统、`network none`、移除全部 capabilities、无 Docker socket 运行。健康接口、未登录 401、登录、旧 SQLite 六字段草稿 GET 补空和带 CSRF 的 PUT 往返均通过；验证使用临时测试凭据和临时数据，测试容器已清理，没有连接 NAS。

同输入双构建的 manifest 字节、版本 ID 和全部产物哈希一致；发布器从校验后的包生成两份逐字节一致的本地资产。ZIP 包含 manifest 与全部 40 个哈希绑定产物，包括来源 LICENSE、人工 NOTICE 和审核摘要；外置展示副本和 SHA256SUMS 逐一校验。资产位于 `.cache/root-gate-deliverable-assets/`，独立报告位于 `.cache/root-gate-deliverable-report.json`、`.cache/root-gate-final.json` 与 `.cache/root-gate-container-report.json`，隔离请求日志位于 `.cache/root-gate-deliverable-binary/`。

项目 diff 检查通过，生产边界四份文件哈希与初始记录一致；保留已有未提交修改。未执行 git add、commit、push、远端 CI、Release/GHCR 发布或生产服务操作。G1–G4 本地实现与验收完成，候选可进入后续另行授权的发布流程；当前没有部署授权或生产就绪结论。

## 区域选择方式确认及提交前复验（2026-10-11）

用户在 G4 交付后授权本地提交构建验证结果，并确定保留区域代理分组，由用途/服务策略组手动选择香港节点、美国节点等区域组；同时明确暂不切换和部署。模板新增可选 `regional_groups`，以香港节点、美国节点作为未绑定的本地示例，保留 Proxy 兼容入口。区域内部节点和完整现网区域清单尚未读取或替换，后续迁移时核对保留。旧 schema-1 模板仍兼容。

主 Agent 独立运行 108 项测试，全部通过；新工作树候选为 `eaa00566385b5a637094aa09ebf83e9b6c14325d9ed581c34bdee9ce67c4849f`。默认类别、全部规则内容、20 个 Provider 顺序和 18 条 unsupported 处置未改变，同输入双构建 manifest 字节和产物哈希一致。新候选仍为 review_required=false、publish_allowed=true、deployment_allowed=false，且只有两项约定部署阻塞。

2,623 项独立匹配 oracle 再次通过；固定 Mihomo 在断网环境通过 20 个 Provider 语法检查和 21 项代表路由，其中隔离模板将 Gemini 手动选择路径设为香港组、Google 设为美国组，区域节点替换为测试 REJECT。SmartDNS 4 项 EXACT/SUFFIX 请求通过。两份旧真实 CI manifest 继续校验通过，生产边界文件哈希不变。本次没有重复宣称旧管理镜像包含新模板，也没有连接生产设备。

提交范围包含已完成的来源扩展、规则审核、编译器及许可发布校验、区域选择模板、测试和验收文档；忽略目录中的临时凭据、缓存及 ZIP 不进入 Git。可跟踪的构建验证摘要与全部 40 个产物哈希见 [build-validation-2026-10-11.json](build-validation-2026-10-11.json)，本地规则包位于 `.cache/regional-selection-assets/rules.zip`。此证据对应提交前工作树代码/配置哈希，不是远端 CI 或生产部署收据。此次仅授权本地 commit，未授权 push、远端构建发布、写入者切换或部署。
