# 第三方数据来源与许可证说明

本文件说明随规则数据再发布时采用的来源与通知口径，不更改项目程序源码的许可证，也不表示已创建或发布 GitHub Release。

## 默认规则数据：v2fly/domain-list-community

当前批准的普通数据来源标准适用于固定快照 `c6adacf29cc4fb05e04651fcf03e00989ef9f797`（归档 SHA-256：`d5a59ddc7b1577741ab910b1b5b0aa662f1456d41477bbfe056599f9e97f8ef0`）。该版本根目录的 MIT 许可证副本见 [`licenses/v2fly-MIT.txt`](licenses/v2fly-MIT.txt)。来源仓库：[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community/tree/c6adacf29cc4fb05e04651fcf03e00989ef9f797)。

本轮有 15 个默认类别从 v2fly 固定数据 key 取规则：`openai`、`anthropic`、`google-gemini`、`github-copilot`、`google`、`telegram`、`github`、`docker`、`gitlab`、`jetbrains`、`vercel`、`discord`、`reddit`、`youtube`、`tmdb`；类别与 key 的对应关系见[来源迁移评审](docs/source-transition-review.md)。该来源为固定提交及校验过的归档。G1 按比例化标准核对所选 key 和递归 include 闭包，并检查对应文件没有冲突许可或禁止再发布声明；不逐条审核每个贡献者权利，也不把该判断扩展为对整个仓库作结论。来源中涉及身份、支付、遥测、分析或错误报告的共享基础设施另按业务用途审查。上游 README 说明这是社区维护的域名分类数据，来源收录不构成代理建议。

G1 固定快照审计汇总见本机忽略路径 `.cache/root-g1-source-audit/summary.json`，并由主 Agent 独立重跑复核：15 个类别；BM 基线 1,100 条支持语义；v2fly 1,559 条支持语义及 18 条 unsupported 记录；6 条明确排除和 9 条人工精确补充后共 1,562 条有效语义；相对基线新增 634、移除 172。OpenAI 1、Google 16、YouTube 1 条 unsupported 语法保留在收据中。它是固定输入的本地迁移审计，不是完整业务覆盖或生产验证。

候选规则 ZIP 在 `rules/provenance/` 下包含适用的第三方通知、source review summary、每个选中来源的许可证及适用的 NOTICE。summary 记录来源批准台账摘要、选定 profile、许可来源信息与发布阻塞；它及 ZIP 内文件摘要由候选 manifest 纳入 release identity。发布脚本先验证候选，再从这个已验证候选复制 ZIP、manifest、来源审核摘要和许可/NOTICE 副本作为独立展示资产。`SHA256SUMS` 列出这些外部资产的 SHA-256，便于完整性核对；它不是数字签名，也不证明签发者身份。此说明描述本地候选/发布流程，不表示已创建或发布 GitHub Release。

## 人工维护的数据

MIT 仅适用于 `config/curated/` 中明确维护的人工规则数据及其选择元数据，不适用于 Python 程序源码或仓库其余内容。该数据使用项目维护的 [`licenses/manual-curated-MIT.txt`](licenses/manual-curated-MIT.txt) 和 [`licenses/manual-curated-NOTICE.md`](licenses/manual-curated-NOTICE.md)；生成的候选将其按 `manual_curated` 来源范围随 provenance 一并记录。程序源码许可保持独立。

## 未选入默认产物的来源

固定 Blackmatrix7 快照 `83f37120c155f67c8eef43221dabe15cc3c95fec` 保留为关闭候选和研究材料，不作为本轮 15 个 BM 默认输入的发布来源。其根目录标注 GPL-2.0，但 README 另含资源转载限制、学习研究用途说明，并说明其规则整合自互联网与外部开源项目（[固定 README](https://github.com/blackmatrix7/ios_rule_script/blob/83f37120c155f67c8eef43221dabe15cc3c95fec/README.md)）。这些说明与公开发布默认规则的适用关系没有在本轮裁定，因此不把该来源纳入默认发布输入。此记录不构成对该仓库或其规则作法律结论。

来源审核记录：[来源迁移评审](docs/source-transition-review.md)。厂商官方网络文档用于核对业务依赖与特别共享风险，不证明本机真实流量、完整域名覆盖或生产可用性。链接核对日期：2026-10-11。
