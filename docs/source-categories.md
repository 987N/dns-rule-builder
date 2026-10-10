# 上游来源分类清单（R0）

来源为 `blackmatrix7/ios_rule_script@83f37120c155f67c8eef43221dabe15cc3c95fec` 的 `rule/Surge/<目录>/<主文件>.list`。日期列明确取源文件头 `# UPDATED`（如果未标注则为“未标注”）；README 的“最后更新时间”不等同于该规则文件时间。项目 commit 时间为 2026-10-09 22:09:47 UTC；数量及 directive 分类由本地快照直接统计，包含非域名规则。启用建议只是候选，不代表代理意图已审核。

## 首批来源候选

| 逻辑类别 | 实际路径 | 来源更新时间 | 总规则 / DOMAIN / DOMAIN-SUFFIX | 其它类型及备注 | 首批状态 |
|---|---|---:|---:|---|---|
| AI / OpenAI | `OpenAI/OpenAI.list` | 2025-06-06 09:20:00 | 35 / 7 / 24 | KEYWORD 1、IP-CIDR 2、IP-ASN 1；`OpenAI_Resolve.list` 是不同 no-resolve 行为的变体 | 解析域名子集；审核更新时间与KEYWORD遗漏 |
| AI / Claude | `Claude/Claude.list` | 2025-06-06 09:20:01 | 3 / 1 / 2 | 无 | 候选启用 |
| AI / Gemini | `Gemini/Gemini.list` | 2025-06-06 09:20:02 | 13 / 3 / 7 | KEYWORD 3；含 `apis.google.com` 等跨类规则 | 部分覆盖并审核冲突 |
| AI / Copilot | `Copilot/Copilot.list` | 2025-06-06 09:20:02 | 51 / 20 / 26 | KEYWORD 1、IP-CIDR 2、IP-ASN 2；Resolve 为 variant | 部分覆盖并审核冲突 |
| Google | `Google/Google.list` | 2026-05-12 02:38:11 | 703 / 1 / 684 | KEYWORD 5、IP-CIDR 4、IP-CIDR6 1、PROCESS 5、USER-AGENT 3；Resolve 为 variant | 启用前拆解范围及共享基础设施审核 |
| YouTube | `YouTube/YouTube.list` | 2025-06-06 09:15:09 | 190 / 0 / 179 | README 标 2025-07-30，与文件头不同；KEYWORD 1、IP-CIDR 2、IP-CIDR6 1、USER-AGENT 7；Resolve 为 variant | 域名子集候选；需优先于 Google |
| Telegram | `Telegram/Telegram.list` | 2025-09-10 02:08:26 | 46 / 2 / 22 | KEYWORD 1、IP-CIDR(6) 10、IP-ASN 5、PROCESS 5、OR 1；来源引用 Telegram CIDR | 域名子集；不能代表 Telegram 全流量 |
| Development / GitHub | `GitHub/GitHub.list` | 2025-06-12 02:06:19 | 31 / 0 / 30 | KEYWORD 1 | 域名子集候选 |
| Development / Docker | `Docker/Docker.list` | 2025-06-06 09:17:43 | 7 / 3 / 4 | 无 | 候选启用 |
| Development / GitLab | `GitLab/GitLab.list` | 2025-06-06 09:17:44 | 6 / 0 / 6 | 无 | 候选启用 |
| Development / JetBrains | `Jetbrains/Jetbrains.list` | 2025-06-06 09:17:44 | 14 / 0 / 14 | 实际目录大小写为 `Jetbrains` | 候选启用 |
| Development / Vercel | `Vercel/Vercel.list` | 2025-06-06 09:17:48 | 27 / 0 / 27 | 无 | 候选启用 |
| Social / Discord | `Discord/Discord.list` | 2025-06-06 09:16:55 | 29 / 0 / 29 | 全为 DOMAIN-SUFFIX | 候选启用 |
| Social / Reddit | `Reddit/Reddit.list` | 2025-06-06 09:17:47 | 8 / 0 / 8 | README 标 2025-07-30，与文件头不同；全为 DOMAIN-SUFFIX | 候选启用 |

## 存在但首批关闭或待审

| 逻辑类别 | 路径 / 日期 | 发现 | 暂缓原因 |
|---|---|---|---|
| Apple | `Apple/Apple_All.list`、`Apple_Domain.list`、`Apple_Resolve.list`、`Apple_All_No_Resolve.list`；主文件头 `Apple.list` 2026-10-04，README 2026-03-26；`Apple_All.list` 文件头 2026-10-10 | 主 `Apple.list` 只有 56 条且全是 KEYWORD/IP/PROCESS/USER-AGENT；Apple_All 有 1,616 条，Apple_Domain 有约 1,560 条裸域名，范围非常广。多文件头与 README 时间不同 | 需要针对服务、国内服务、误收和确切裸域语义审查 |
| Steam | `Steam/Steam.list`，2025-06-06 09:15:08 | 54 条；51 suffix + 3 keyword | 样本含中国下载相关域名，不能直接把整类指向代理 |
| Microsoft | `Microsoft/Microsoft.list`，2025-12-08 02:06:20 | 673 条，663 suffix，另含 exact/keyword/agent/process | 范围宽，可能包含本地/国内微软服务，待裁定 |
| TikTok | `TikTok/TikTok.list`，2025-08-10 02:08:00 | 33 条，含 DOMAIN、SUFFIX、KEYWORD、PROCESS、USER-AGENT | 逐项排除字节国内服务并确认意图 |
| Netflix | `Netflix/Netflix.list`，文件头 2025-06-06 09:16:54；README 2025-07-30 | 1,158 条中 1,115 条 IP-CIDR，少量域名 | v1 域名输出不等价于其 IP 覆盖；标记部分而非完整 |
| Facebook | `Facebook/Facebook.list`，文件头 2025-06-06 09:16:55；README 2025-08-01 | 570 条，含 IP 与 `Facebook_Resolve.list` 变体 | 域名子集与共享基础设施风险 |
| PayPal | `PayPal/PayPal.list`，2025-07-30 02:08:19 | 248 条，246 suffix、1 keyword | 支付和身份共享域名高风险，人工审查 |
| Stripe | `Stripe/Stripe.list`，2025-06-06 09:20:02 | 仅 1 条且为 KEYWORD | 没有可直接转换的域名规则 |
| WhatsApp | 实际目录 `Whatsapp/Whatsapp.list`，2025-06-06 09:16:56 | 17 条，11 条 IP-CIDR；`Whatsapp_Resolve.list` variant | 仅域名部分覆盖 |
| Disney | `Disney/Disney.list`，文件头 2025-07-30 02:08:18；README 2026-04-08 | 175 条；suffix 172、process 2、user-agent 1 | 文件头较 README 旧，需确认生成时效和播放范围 |
| Streaming | Hulu、Spotify、Twitch、PrimeVideo 等各自目录 | 存在服务类别，但更新时间/格式/共享域名各异 | 逐类审核后再启用 |

## 常见类别名称映射和确实未发现项

扫描到 669 个 `rule/Surge/` 子目录。`Abema`、`AbemaTV`、`Bahamut`、`HBO` 均实际存在；目录 `Npmjs` 是 npm 的候选来源。`Max` 不存在同名目录，存在 `HBO`、`HBOAsia`、`HBOHK`、`HBOUSA`，但是否覆盖 Max 服务需人工确认。`StackOverflow` 不存在同名目录，存在 `Stackexchange` 候选。以下名称未在目录中发现同名或已确认别名：Perplexity、xAI、Cursor、HuggingFace、Netlify、PyPI。不得因缺少同名目录而宣称业务数据绝对不存在；后续 inventory 必须保留路径与人工别名判定。

## 证据定位

- 固定归档：`.cache/ios_rule_script-83f37120.tar.gz`，SHA-256 记录于 `research.md`。
- 上游 commit：[commit 页面](https://github.com/blackmatrix7/ios_rule_script/commit/83f37120c155f67c8eef43221dabe15cc3c95fec)。
- 规则目录：[Surge rules](https://github.com/blackmatrix7/ios_rule_script/tree/83f37120c155f67c8eef43221dabe15cc3c95fec/rule/Surge)。
- 示例源文件：[OpenAI](https://github.com/blackmatrix7/ios_rule_script/blob/83f37120c155f67c8eef43221dabe15cc3c95fec/rule/Surge/OpenAI/OpenAI.list)、[Apple Domain Set](https://github.com/blackmatrix7/ios_rule_script/blob/83f37120c155f67c8eef43221dabe15cc3c95fec/rule/Surge/Apple/Apple_Domain.list)、[Gemini](https://github.com/blackmatrix7/ios_rule_script/blob/83f37120c155f67c8eef43221dabe15cc3c95fec/rule/Surge/Gemini/Gemini.list)、[Steam](https://github.com/blackmatrix7/ios_rule_script/blob/83f37120c155f67c8eef43221dabe15cc3c95fec/rule/Surge/Steam/Steam.list)。

