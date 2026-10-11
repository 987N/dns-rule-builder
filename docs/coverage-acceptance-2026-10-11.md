# 国外独立服务扩充验收（2026-10-11）

用户明确选择先补齐常用国外独立服务、审核后重新发布，再进行生产切换。本轮将默认启用类别从 17 项扩到 60 项：新增流媒体、音乐、成人独立网站、下载、社交、开发、新闻与字幕服务。TikTok 未加入，Apple 广泛集合未启用，未导入综合国外全集。具体来源范围、精确共享主机裁决与覆盖限制见[研究报告](../research/coverage-expansion-2026-10-11.md)。

主 Agent 独立通过 GitHub API 下载上一正式版本 `66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961` 的 ZIP、manifest、SHA256SUMS，验证资产大小、哈希、包内外 manifest 字节、完整版本身份和发布门禁，将其作为新构建基线。扩充构建得到 2,694 条保留类别及出处的规则、2,416 个独立语义及 SmartDNS 项、59 个非空类别、63 个 Provider 片段。原基线规则无减少，新增 1,130 条类别规则、1,079 个独立语义项。321 项重叠已裁决，未解决策略冲突为 0。

本地审核候选 `4d8b5786a9ba968d28f8d2d99db1328c89840883b2fd992432de46c11c9640c0` 经完整 verifier 校验，`review_required=false`、`publish_allowed=true`、`deployment_allowed=false`，部署阻塞仍恰好为目标语义未确认和部署总开关关闭。主 Agent 使用真实正式基线另行锁定测试身份构建，也通过同样门禁；本地候选 ID 不作为未来正式 CI Release ID。

隔离实验使用修正人工来源 revision 前的候选 `be29f4ff5be2e5e731930aa8ff254147316249c3891dae74dd28c010d95acc70`；主 Agent 校验两版全部产物哈希完全一致，仅版本身份元数据改变。固定 Mihomo 1.19.32 镜像与 NAS SmartDNS 1.2026.v48.4.2 镜像在断网隔离环境运行实际候选产物。所有生成的 Provider 文件、RuleSet 顺序和 SmartDNS Domain Set 均保持原字节；Mihomo 通过语法校验，并由 35 个代表请求的路由日志验证 Google/YouTube/Gemini 重叠、Hulu 优先级、流媒体、成人网站、下载、字幕及人工精确主机。SmartDNS 验证精确主机子域回到测试默认结果，以及后缀主域/子域匹配。测试使用拒绝策略和静态测试地址，仅证明规则匹配，不证明真实业务访问。

正式规则 workflow 增加上一正式 Release 的校验及导入，保障临时 Runner 上的 30% 类别下降门禁。没有正式基线与下载或校验失败明确区分；失败终止构建。专项测试覆盖损坏资产、错误身份、路径、已有目录保护及真实下降门禁。

主 Agent 完成最终 156 项本地测试、JavaScript 和安装脚本语法检查。固定代码、配置、来源、数据及输入时间重复构建两次，版本 ID 与全部 83 个产物哈希一致。安装准备脚本默认 dry-run，真实 APPLY 先生成保留 uid/gid/mode 的私有快照，并校验 SQLite 一致性和备份可读性；本地测试不代表 NAS 已执行该脚本。

本阶段未激活 NAS/VM104 规则，也未修改 Caddy、旧 controller、DNS 上游、DNSDist、OPNsense 或 Fake-IP 路由。正式发布及最终镜像结果另行追加到[发布收据](publication-receipt-2026-10-11.md)，生产验证使用独立收据。
