# 服务规则来源目录研究（R1 研究快照 + R3 配置状态）

本报告记录候选来源路径、语法计数、日期、覆盖风险和审核状态；不复制完整上游域名列表，也不表示现网已部署。R1 表格保留研究快照；当前逐服务默认值及所选来源以 [`config/service-catalog.yaml`](../config/service-catalog.yaml) 为准，运行映射见 [`config/categories.yaml`](../config/categories.yaml)。当前目录有 141 项：124 个配置类别（包括为读取旧配置保留、禁止启用的 Apple 聚合别名）、17 个默认启用类别（原有 15 项加 TMDB、Fanart）。TMDB 与两个精确 Fanart 主机为默认开启的元数据候选；其余新增候选默认关闭。启用中的历史类别仍标为 legacy 待审核，Builder 会将未批准目录状态作为 deployment blocker。

G1 已将 15 个默认规则输入配置为固定 v2fly key（OpenAI、Docker另有官方 allowlist 精确人工依赖）；人工维护的 Fanart 与 Foreign-General 维持各自语义。主 Agent 已独立重跑并验收新旧快照差异：1,559 条支持语义、18 条 unsupported、6 条有意精确排除、9 条人工精确补充，最终 1,562 条有效语义，相对 BM 基线新增 634、移除 172。unsupported 仅出现在 OpenAI、Google、YouTube；其记录保留在忽略的本地收据中。完整度、路由顺序和发布许可资产门禁仍未全部解除。本页旧表只作 R1 来源研究记录，不代表本轮最终覆盖。阶段证据、partial 说明与逐项 key 对照见[来源迁移评审](source-transition-review.md)。

R5 本地语义审计已完成：124 个配置类别中 123 个有源；parser 解析 7,319 条规则并生成 123 个 Providers，全量 syntax-only Mihomo `-t` 通过。审计生成并保留 1,422 条 unsupported 记录；这不是默认构建的 65 条 unsupported 统计的替代。syntax-only 校验（包括 Apple 兼容宽泛类别的影子 parser）不意味着这些类别可启用或顺序已批准。真实 Mihomo REJECT 选择日志及 SmartDNS `network none` 仅覆盖代表性规则，不证明真实服务访问。详见[扩展验收记录](expansion-acceptance.md)及 README 中的复现命令。

## 快照与完整性

| 来源 | 固定版本 | commit 日期（UTC） | 归档 SHA-256 | 完整清单数 |
|---|---|---|---|---:|
| blackmatrix7 | `83f37120c155f67c8eef43221dabe15cc3c95fec` | `2026-10-09 22:09:47` | `e4249eb848059306541eea99ce5ecc1c0a988810afc716cb76db456159c6bf99` | 669个目录，665个目录有同名主 `.list` |
| v2fly | `c6adacf29cc4fb05e04651fcf03e00989ef9f797` | `2026-10-09 10:11:47` | `d5a59ddc7b1577741ab910b1b5b0aa662f1456d41477bbfe056599f9e97f8ef0` | 1,545个 `data/` key |

清单由 [完整归档库存工具](../tools/research/catalog_inventory.py) 从固定 Codeload archive 读取，下载后先核对完整 SHA-256，再检查 tar 内每条路径、拒绝绝对路径、`..`、链接和特殊文件，最后只将源码展开至忽略的 `.cache/`。没有用 GitHub Contents API 的有限目录页推断缺项。blackmatrix7 有4个目录没有同名主 `.list`：`Assassin'sCreed`、`EasyPrivacy`、`AdGuardSDNSFilter`、`Cloud`；因此目录总数和可统计主文件数分别列明。

日期不可混用：blackmatrix7 表中 `UPDATED` 文件头、README 的“最后更新时间”是各服务规则数据自己的日期；它们可能不同（例如 IMDB、EHGallery）。Git 提交日期只是整份仓库的快照时间，不表示其中每个服务刚更新。v2fly data key 没有统一规则更新头；表中仅给固定仓库 commit 日期，未将其伪称为每个 key 的文件更新时间。逐路径 git blame/commit 时间尚未采集。

## 分类模型与来源选择

配置采用“用途组 + 可选的独立服务组”。用途组目前为 AI、Development、Communication、Social、Streaming、Music、Gaming、Media-Metadata、Cloud、Knowledge、Payments、Apple-Overseas、Adult、Foreign-General；仅有不同路由/冲突顺序需求的服务再有独立逻辑组。节点成员仍由用户后续确定。未匹配域名保持 Real-IP/direct。`Foreign-General` 只作为人工维护入口，不从 v2fly `geolocation-!cn`、blackmatrix7 `Proxy/Global` 或全量聚合分类导入兜底。Apple 常用服务保持默认直连；Apple-Overseas 是可选代理选择组，默认关闭并需用户批准；TikTok 按用户偏好排除。

一个服务可能同时存在于两个来源，但目录不自动合并。R1 初始研究的默认策略是优先选择 blackmatrix7、缺少独立项时选择 v2fly；G1 已决定把 15 个默认规则输入转为固定 v2fly key，当前实施和覆盖复核状态见[来源迁移评审](source-transition-review.md)。人工来源须使用项目受控文件。合并多个来源必须有额外评审。目录逐项保存 `selected_source`/`source_refs`、`review_state`、`default_enabled`、`route_intent`、路径语法计数、源文件头日期、README 日期、仓库 commit 日期、许可证、覆盖风险、依赖与证据。上游文件日期与仓库 commit 时间分开记录。机器目录中的 `missing: true` 表示没有任何已选的可构建来源；`upstream_key_missing: true` 只表示固定上游没有独立 key。Fanart 属于后者：它没有独立上游 key，但项目受控来源 `config/curated/fanart.list` 提供两条已审核精确主机，因此不是缺失来源。宽泛聚合、direct/reference-only 和无可构建来源的条目不会创建可开启的运行类别。

用途组与服务组按已评审映射配置。独立 Google、YouTube、Telegram、Netflix、DisneyPlus、PrimeVideo、HBO、Hulu、Abema、Bahamut 组保留分开路由能力；Apple、Microsoft 兼容选择组保留在策略模板中，`Streaming` 用途组当前映射 Dailymotion、Twitch、Vimeo。模板选择成员为 Proxy/REJECT，不含生产节点。常用 Apple 与国内直连候选在目录中可查，但不映射为代理 Provider。首版 TikTok 不在机器目录或运行类别中。

## 候选服务及实际来源

BM counts 为同名主文件中所有 Surge 规则类型计数；它们只是源规则数量，并非全部都能转为 DNS 域名规则。v2fly counts 是该 key 文件直接书写的语法数，`include` 尚未展开；重复与 include 带来的最终数量另由适配器审计。裸域名在 v2fly 的语义是普通 `domain`（suffix），`full` 为精确域名；keyword/regexp 不会被近似转换为 suffix。

| 用途组 | 服务 | blackmatrix7实际路径 / 类型计数 / 文件头日期 | v2fly实际key / 类型计数 | 风险或状态 |
|---|---|---|---|---|
| AI | `openai` | `rule/Surge/OpenAI/OpenAI.list` — DOMAIN×7, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×24, IP-ASN×1, IP-CIDR×2 — `2025-06-06 09:20:00` / README `2025-06-06 09:20:00` | `data/openai` — bare-domain×13, full×9, regexp×1 | `shared-with-copilot`, `contains-unsupported-rule-types` |
| AI | `claude` | `rule/Surge/Claude/Claude.list` — DOMAIN×1, DOMAIN-SUFFIX×2 — `2025-06-06 09:20:01` / README `2025-06-06 09:20:01` | — | `shared-telemetry-review` |
| AI | `gemini` | `rule/Surge/Gemini/Gemini.list` — DOMAIN×3, DOMAIN-KEYWORD×3, DOMAIN-SUFFIX×7 — `2025-06-06 09:20:02` / README `2025-06-06 09:20:02` | `data/google-gemini` — include×1 | `overlap-with-google` |
| AI | `copilot` | `rule/Surge/Copilot/Copilot.list` — DOMAIN×20, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×26, IP-ASN×2, IP-CIDR×2 — `2025-06-06 09:20:02` / README `2025-06-06 09:20:02` | `data/github-copilot` — bare-domain×1, full×5 | `shared-with-openai`, `contains-unsupported-rule-types` |
| AI | `anthropic` | `rule/Surge/Anthropic/Anthropic.list` — DOMAIN×1, DOMAIN-SUFFIX×2 — `2024-01-08 16:02:59` / README `2024-01-08 16:02:59` | `data/anthropic` — bare-domain×8, full×1 | `blackmatrix-header-old` |
| AI | `perplexity` | — | `data/perplexity` — bare-domain×3, full×2 | `v2fly-syntax-adapter-required` |
| AI | `xai` | — | `data/xai` — bare-domain×4 | `v2fly-syntax-adapter-required` |
| AI | `cursor` | — | `data/cursor` — bare-domain×4 | `v2fly-syntax-adapter-required` |
| AI | `huggingface` | — | `data/huggingface` — bare-domain×3 | `v2fly-syntax-adapter-required` |
| Development | `netlify` | — | `data/netlify` — bare-domain×7, full×3 | `v2fly-syntax-adapter-required` |
| Development | `github` | `rule/Surge/GitHub/GitHub.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×30 — `2025-06-12 02:06:19` / README `2025-06-12 02:06:19` | `data/github` — bare-domain×29, full×27, include×2 | `unsupported-keyword-in-blackmatrix` |
| Development | `docker` | `rule/Surge/Docker/Docker.list` — DOMAIN×3, DOMAIN-SUFFIX×4 — `2025-06-06 09:17:43` / README `2025-06-06 09:17:43` | `data/docker` — bare-domain×4, full×2 | `shared-cloudfront-hosts` |
| Development | `gitlab` | `rule/Surge/GitLab/GitLab.list` — DOMAIN-SUFFIX×6 — `2025-06-06 09:17:44` / README `2025-06-06 09:17:44` | `data/gitlab` — bare-domain×4, full×1 | 待审核 |
| Development | `jetbrains` | `rule/Surge/Jetbrains/Jetbrains.list` — DOMAIN-SUFFIX×14 — `2025-06-06 09:17:44` / README `2025-06-06 09:17:44` | `data/jetbrains` — bare-domain×22, include×1 | 待审核 |
| Development | `vercel` | `rule/Surge/Vercel/Vercel.list` — DOMAIN-SUFFIX×27 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | `data/vercel` — bare-domain×27 | 待审核 |
| Development | `npmjs` | `rule/Surge/Npmjs/Npmjs.list` — DOMAIN-SUFFIX×3 — `2024-01-08 16:02:53` / README `2024-01-08 16:02:53` | `data/npmjs` — bare-domain×3 | `blackmatrix-header-old` |
| Development | `python` | `rule/Surge/Python/Python.list` — DOMAIN-SUFFIX×6 — `2025-06-06 09:17:50` / README `2025-06-06 09:17:50` | `data/python` — bare-domain×6 | 待审核 |
| Development | `pypi` | — | — | 无独立来源；`manual-source-candidate`, `no-independent-key-in-pinned-sources` |
| Development | `anaconda` | `rule/Surge/Anaconda/Anaconda.list` — DOMAIN-SUFFIX×4 — `2024-01-08 16:02:59` / README `2024-01-08 16:02:59` | `data/anaconda` — bare-domain×4 | `blackmatrix-header-old` |
| Development | `sourceforge` | `rule/Surge/SourceForge/SourceForge.list` — DOMAIN-SUFFIX×4 — `2025-06-06 09:17:47` / README `2025-06-06 09:17:47` | `data/sourceforge` — bare-domain×4 | 待审核 |
| Development | `atlassian` | `rule/Surge/Atlassian/Atlassian.list` — DOMAIN-SUFFIX×5 — `2025-06-06 09:17:42` / README `2025-06-06 09:17:42` | `data/atlassian` — bare-domain×9, include×1 | 待审核 |
| Development | `figma` | `rule/Surge/Figma/Figma.list` — DOMAIN-SUFFIX×1 — `2024-01-08 16:02:54` / README `2024-01-08 16:02:54` | `data/figma` — bare-domain×1 | `blackmatrix-header-old` |
| Development | `jfrog` | `rule/Surge/Jfrog/Jfrog.list` — DOMAIN-SUFFIX×3 — `2025-06-06 09:17:45` / README `2025-06-06 09:17:45` | `data/jfrog` — bare-domain×3, full×1 | 待审核 |
| Development | `gitbook` | `rule/Surge/GitBook/GitBook.list` — DOMAIN-SUFFIX×3 — `2025-06-06 09:17:44` / README `2025-07-30 02:09:08` | `data/gitbook` — bare-domain×2 | 待审核 |
| Development | `hashicorp` | `rule/Surge/HashiCorp/HashiCorp.list` — DOMAIN-SUFFIX×8 — `2025-06-06 09:17:53` / README `2025-06-06 09:17:53` | `data/hashicorp` — bare-domain×8 | 待审核 |
| Development | `linux` | `rule/Surge/Linux/Linux.list` — DOMAIN-SUFFIX×1 — `2024-01-08 16:02:53` / README `2024-01-08 16:02:53` | `data/linux` — bare-domain×1 | `blackmatrix-header-old` |
| Development | `ubuntu` | `rule/Surge/Ubuntu/Ubuntu.list` — DOMAIN-SUFFIX×54 — `2025-06-06 09:18:24` / README `2025-06-06 09:18:24` | `data/ubuntu` — bare-domain×4 | `contains-cn-domain` |
| Development | `electron` | `rule/Surge/Electron/Electron.list` — DOMAIN-SUFFIX×1 — `2024-01-08 16:02:53` / README `2024-01-08 16:02:53` | `data/electron` — bare-domain×1 | `blackmatrix-header-old` |
| Development | `sublime_text` | `rule/Surge/SublimeText/SublimeText.list` — DOMAIN-SUFFIX×4 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | — | `blackmatrix-header-old` |
| Development | `apifox` | `rule/Surge/Apifox/Apifox.list` — DOMAIN-SUFFIX×2 — `2024-01-08 16:02:59` / README `2024-01-08 16:02:59` | `data/apifox` — bare-domain×2 | `contains-cn-domain`, `blackmatrix-header-old` |
| Communication | `discord` | `rule/Surge/Discord/Discord.list` — DOMAIN-SUFFIX×29 — `2025-06-06 09:16:55` / README `2025-06-06 09:16:55` | `data/discord` — bare-domain×26, full×2 | 待审核 |
| Social | `reddit` | `rule/Surge/Reddit/Reddit.list` — DOMAIN-SUFFIX×8 — `2025-06-06 09:17:47` / README `2025-07-30 02:09:12` | `data/reddit` — bare-domain×12 | 待审核 |
| Social | `linkedin` | `rule/Surge/LinkedIn/LinkedIn.list` — DOMAIN×3, DOMAIN-SUFFIX×9 — `2024-01-08 16:03:00` / README `2024-01-08 16:03:00` | `data/linkedin` — bare-domain×9, full×3 | `contains-cn-domains`, `blackmatrix-header-old` |
| Social | `snapchat` | `rule/Surge/Snap/Snap.list` — DOMAIN-SUFFIX×6 — `2025-06-06 09:17:47` / README `2025-06-06 09:17:47` | `data/snap` — bare-domain×8, full×1 | 待审核 |
| Social | `threads` | `rule/Surge/Threads/Threads.list` — DOMAIN-SUFFIX×1 — `2024-01-08 16:02:59` / README `2024-01-08 16:02:59` | `data/threads` — bare-domain×2 | `blackmatrix-header-old` |
| Social | `instagram` | `rule/Surge/Instagram/Instagram.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×3 — `2025-06-06 09:16:56` / README `2025-06-06 09:16:56` | `data/instagram` — bare-domain×74 | `contains-unsupported-rule-types` |
| Social | `pinterest` | `rule/Surge/Pinterest/Pinterest.list` — DOMAIN-SUFFIX×23 — `2025-06-06 09:17:46` / README `2025-06-06 09:17:46` | `data/pinterest` — bare-domain×52 | 待审核 |
| Social | `tumblr` | `rule/Surge/Tumblr/Tumblr.list` — DOMAIN-SUFFIX×3 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | `data/tumblr` — bare-domain×1 | 待审核 |
| Social | `facebook` | `rule/Surge/Facebook/Facebook.list` — DOMAIN-KEYWORD×2, DOMAIN-SUFFIX×544, IP-ASN×1, IP-CIDR×23 — `2025-06-06 09:16:55` / README `2025-08-01 02:08:08` | `data/facebook` — bare-domain×396, full×1 | `large-list`, `ip-and-keyword-rules`, `domestic-service-impact-review` |
| Social | `x_twitter` | `rule/Surge/Twitter/Twitter.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×26, IP-CIDR×6 — `2025-09-28 02:08:26` / README `2025-09-28 02:08:26` | `data/twitter` — bare-domain×24 | `contains-ip-rules` |
| Communication | `telegram` | `rule/Surge/Telegram/Telegram.list` — DOMAIN×2, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×22, IP-ASN×5, IP-CIDR×6, IP-CIDR6×4, OR×1, PROCESS-NAME×5 — `2025-09-10 02:08:26` / README `2025-09-10 02:08:26` | `data/telegram` — bare-domain×21 | `contains-unsupported-rule-types` |
| Streaming | `netflix` | `rule/Surge/Netflix/Netflix.list` — DOMAIN×3, DOMAIN-KEYWORD×4, DOMAIN-SUFFIX×30, IP-CIDR×1115, IP-CIDR6×4, PROCESS-NAME×1, USER-AGENT×1 — `2025-06-06 09:16:54` / README `2025-07-30 02:08:18` | `data/netflix` — bare-domain×23, full×1, regexp×4 | `large-ip-list`, `app-rules-not-dns-rules` |
| Streaming | `disney` | `rule/Surge/Disney/Disney.list` — DOMAIN-SUFFIX×172, PROCESS-NAME×2, USER-AGENT×1 — `2025-07-30 02:08:18` / README `2026-04-08 02:22:25` | `data/disney` — bare-domain×135, full×2, include×8 | `contains-app-rules` |
| Streaming | `prime_video` | `rule/Surge/PrimeVideo/PrimeVideo.list` — DOMAIN×9, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×6, USER-AGENT×2 — `2025-06-06 09:17:50` / README `2025-06-06 09:17:50` | `data/primevideo` — bare-domain×13, full×10 | `contains-app-rules` |
| Streaming | `hulu` | `rule/Surge/Hulu/Hulu.list` — DOMAIN×1, DOMAIN-SUFFIX×57, PROCESS-NAME×1 — `2025-06-06 09:16:58` / README `2025-06-06 09:16:58` | `data/hulu` — bare-domain×47, full×1 | `region-variants-do-not-union` |
| Streaming | `hbo` | `rule/Surge/HBO/HBO.list` — DOMAIN-SUFFIX×47, PROCESS-NAME×1, USER-AGENT×2 — `2025-06-06 09:16:58` / README `2025-06-06 09:16:58` | `data/hbo` — bare-domain×65 | `region-variants-share-cdn` |
| Streaming | `abema` | `rule/Surge/Abema/Abema.list` — DOMAIN×5, DOMAIN-SUFFIX×15 — `2024-01-08 16:02:56` / README `2024-01-08 16:02:56` | `data/abema` — bare-domain×16, full×5 | `blackmatrix-header-old` |
| Streaming | `bahamut` | `rule/Surge/Bahamut/Bahamut.list` — DOMAIN×1, DOMAIN-SUFFIX×6, USER-AGENT×1 — `2025-06-06 09:16:56` / README `2025-06-06 09:16:56` | `data/bahamut` — bare-domain×3, full×2 | `contains-app-rules` |
| Streaming | `dailymotion` | `rule/Surge/Dailymotion/Dailymotion.list` — DOMAIN-SUFFIX×5 — `2025-06-06 09:16:57` / README `2025-06-06 09:16:57` | `data/dailymotion` — bare-domain×3 | 待审核 |
| Streaming | `twitch` | `rule/Surge/Twitch/Twitch.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×8, IP-CIDR×11, IP-CIDR6×1, PROCESS-NAME×1 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | `data/twitch` — bare-domain×8, full×26 | `contains-ip-keyword-and-process-rules` |
| Streaming | `vimeo` | `rule/Surge/Vimeo/Vimeo.list` — DOMAIN-SUFFIX×16 — `2025-06-06 09:17:00` / README `2025-06-06 09:17:00` | `data/vimeo` — bare-domain×16, regexp×1 | `v2fly-regexp-present` |
| Music | `spotify` | `rule/Surge/Spotify/Spotify.list` — DOMAIN×6, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×20, IP-CIDR×2, PROCESS-NAME×1, USER-AGENT×1 — `2025-06-06 09:16:55` / README `2025-06-06 09:16:55` | `data/spotify` — bare-domain×17, full×11 | `contains-app-and-ip-rules` |
| Music | `deezer` | `rule/Surge/Deezer/Deezer.list` — DOMAIN-SUFFIX×2, USER-AGENT×1 — `2025-06-06 09:16:57` / README `2025-06-06 09:16:57` | `data/deezer` — bare-domain×2 | `contains-app-rules` |
| Music | `tidal` | `rule/Surge/TIDAL/TIDAL.list` — DOMAIN-SUFFIX×4, USER-AGENT×2 — `2025-06-06 09:17:00` / README `2025-06-06 09:17:00` | `data/tidal` — bare-domain×3 | `contains-app-rules` |
| Music | `qobuz` | `rule/Surge/Qobuz/Qobuz.list` — DOMAIN-SUFFIX×31, IP-CIDR×2 — `2025-06-06 09:16:59` / README `2025-06-06 09:16:59` | — | `contains-ip-rules` |
| Music | `soundcloud` | `rule/Surge/SoundCloud/SoundCloud.list` — DOMAIN-SUFFIX×3, USER-AGENT×1 — `2025-06-06 09:17:00` / README `2025-07-30 02:08:23` | `data/soundcloud` — bare-domain×3 | `contains-app-rules` |
| Music | `pandora` | `rule/Surge/Pandora/Pandora.list` — DOMAIN-SUFFIX×2, USER-AGENT×1 — `2025-06-06 09:16:59` / README `2025-06-06 09:16:59` | — | `contains-app-rules` |
| Music | `lastfm` | `rule/Surge/LastFM/LastFM.list` — DOMAIN×2, DOMAIN-SUFFIX×29 — `2025-06-06 09:17:45` / README `2025-06-06 09:17:45` | `data/lastfm` — bare-domain×1, full×3 | `contains-cn-domain` |
| Music | `joox` | `rule/Surge/JOOX/JOOX.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×3, IP-CIDR×2, PROCESS-NAME×1, USER-AGENT×2 — `2025-06-06 09:16:58` / README `2025-06-06 09:16:58` | — | `contains-ip-keyword-and-app-rules` |
| Music | `kkbox` | `rule/Surge/KKBOX/KKBOX.list` — DOMAIN-SUFFIX×9 — `2025-06-06 09:16:58` / README `2025-06-06 09:16:58` | `data/kkbox` — bare-domain×5 | 待审核 |
| Apple-Overseas | `apple_music` | `rule/Surge/AppleMusic/AppleMusic.list` — DOMAIN×6, DOMAIN-SUFFIX×3, PROCESS-NAME×1, USER-AGENT×1 — `2025-06-06 09:15:11` / README `2025-06-06 09:15:11` | — | `common-apple-service-default-direct` |
| Gaming | `steam` | `rule/Surge/Steam/Steam.list` — DOMAIN-KEYWORD×3, DOMAIN-SUFFIX×51 — `2025-06-06 09:15:08` / README `2025-06-06 09:15:08` | `data/steam` — bare-domain×41, full×19 | `chinese-download-domains`, `overlaps-steamcn`, `unsupported-keywords` |
| Gaming | `epic` | `rule/Surge/Epic/Epic.list` — DOMAIN-SUFFIX×15 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | `data/epicgames` — bare-domain×28, include×1, regexp×3 | 待审核 |
| Gaming | `nintendo` | `rule/Surge/Nintendo/Nintendo.list` — DOMAIN-SUFFIX×125, IP-CIDR×1 — `2026-05-04 02:19:07` / README `2026-05-04 02:19:07` | `data/nintendo` — bare-domain×124 | `contains-cn-domains`, `contains-ip-rules` |
| Gaming | `xbox` | `rule/Surge/Xbox/Xbox.list` — DOMAIN×4, DOMAIN-SUFFIX×38 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | `data/xbox` — bare-domain×25, include×4 | `contains-cn-domain` |
| Gaming | `playstation` | `rule/Surge/PlayStation/PlayStation.list` — DOMAIN-SUFFIX×4 — `2025-06-06 09:15:08` / README `2025-06-06 09:15:08` | `data/playstation` — bare-domain×4 | 待审核 |
| Gaming | `blizzard` | `rule/Surge/Blizzard/Blizzard.list` — DOMAIN-SUFFIX×39, IP-CIDR×23 — `2026-10-10 06:05:35` / README `2025-06-06 09:15:08` | `data/blizzard` — bare-domain×16, full×9 | `contains-cn-domains`, `contains-ip-rules` |
| Gaming | `riot` | `rule/Surge/Riot/Riot.list` — DOMAIN-SUFFIX×55 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | `data/riot` — bare-domain×53, full×1 | `contains-cn-domains` |
| Gaming | `ubisoft` | `rule/Surge/Ubisoft/Ubisoft.list` — DOMAIN×1 — `2024-01-08 16:02:54` / README `2024-01-08 16:02:54` | `data/ubisoft` — bare-domain×4, full×27 | `blackmatrix-header-old` |
| Gaming | `ea` | `rule/Surge/EA/EA.list` — DOMAIN×2, DOMAIN-SUFFIX×163 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | `data/ea` — bare-domain×154, full×2, include×1 | `contains-cn-domains` |
| Gaming | `gog` | `rule/Surge/Gog/Gog.list` — DOMAIN×1, DOMAIN-SUFFIX×3 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | `data/gog` — bare-domain×5, full×3 | 待审核 |
| Gaming | `hoyoverse` | `rule/Surge/HoYoverse/HoYoverse.list` — DOMAIN-SUFFIX×11 — `2025-06-06 09:18:25` / README `2025-06-06 09:18:25` | `data/hoyoverse` — bare-domain×9 | 待审核 |
| Gaming | `two_k_games` | `rule/Surge/2KGames/2KGames.list` — DOMAIN-SUFFIX×4 — `2024-01-08 16:02:56` / README `2024-01-08 16:02:56` | `data/2kgames` — bare-domain×6 | `blackmatrix-header-old` |
| Gaming | `game_aggregate` | `rule/Surge/Game/Game.list` — DOMAIN×8, DOMAIN-KEYWORD×4, DOMAIN-SUFFIX×539, IP-CIDR×46 — `2026-10-10 06:05:35` / README `2025-06-06 09:15:08` | — | `very-broad`, `mixed-cn-and-global`, `do-not-enable-as-proxy-fallback` |
| Gaming | `steam_cn` | `rule/Surge/SteamCN/SteamCN.list` — DOMAIN×12, DOMAIN-SUFFIX×5 — `2025-06-06 09:15:09` / README `2025-06-06 09:15:09` | — | `preserve-domestic-direct`, `overlaps-steam` |
| Media-Metadata | `tmdb` | `rule/Surge/Tmdb/Tmdb.list` — DOMAIN-SUFFIX×2 — `2024-01-08 16:02:54` / README `2024-01-08 16:02:54` | `data/tmdb` — bare-domain×2, full×2 | `used-by-moviepilot` |
| Media-Metadata | `imdb` | `rule/Surge/IMDB/IMDB.list` — DOMAIN-SUFFIX×4 — `2024-01-08 16:01:01` / README `2024-01-09 02:17:00` | `data/imdb` — bare-domain×5 | `blackmatrix-header-readme-date-differs` |
| Media-Metadata | `douban` | `rule/Surge/DouBan/DouBan.list` — DOMAIN-SUFFIX×3 — `2025-06-06 09:17:33` / README `2025-06-06 09:17:33` | `data/douban` — bare-domain×3 | `domestic-service-direct-review` |
| Media-Metadata | `fanart` | — | — | 无独立上游 key；R3 已选项目受控 `fanart.list`（两个精确主机），默认开启并纳入构建身份；`used-by-moviepilot-when-enabled` |
| Media-Metadata | `tvdb` | — | — | 无独立来源；`manual-source-candidate`, `no-independent-key-in-pinned-sources`, `moviepilot-integration-candidate` |
| Media-Metadata | `moviepilot_integration` | — | — | 无独立来源；`manual-integration-candidate`, `dependencies-include-tmdb-fanart-and-image-hosts` |
| Adult | `adult_aggregate` | — | `data/category-porn` — bare-domain×5972, full×1, include×73, regexp×137 | `very-broad-aggregate`, `review-each-service-before-enable` |
| Adult | `pornhub` | — | `data/pornhub` — bare-domain×9 | `adult-content`, `manual-review-before-enable` |
| Adult | `xvideos` | — | `data/xvideos` — bare-domain×5 | `adult-content`, `manual-review-before-enable` |
| Adult | `xnxx` | — | `data/xnxx` — bare-domain×5 | `adult-content`, `manual-review-before-enable` |
| Adult | `xhamster` | — | `data/xhamster` — bare-domain×18, full×1 | `adult-content`, `manual-review-before-enable` |
| Adult | `spankbang` | — | `data/spankbang` — bare-domain×3 | `adult-content`, `manual-review-before-enable` |
| Adult | `javdb` | — | `data/javdb` — bare-domain×4, regexp×1 | `adult-content`, `v2fly-regexp-present`, `manual-review-before-enable` |
| Adult | `javbus` | — | `data/javbus` — bare-domain×3 | `adult-content`, `manual-review-before-enable` |
| Adult | `missav` | — | `data/missav` — bare-domain×7 | `adult-content`, `manual-review-before-enable` |
| Adult | `jable` | — | `data/jable` — bare-domain×3 | `adult-content`, `manual-review-before-enable` |
| Adult | `dmm` | `rule/Surge/DMM/DMM.list` — DOMAIN-SUFFIX×18, IP-CIDR×2 — `2025-06-06 09:18:49` / README `2025-06-06 09:18:49` | `data/dmm-porn` — bare-domain×2 | `adult-content`, `contains-ip-rules`, `manual-review-before-enable` |
| Adult | `ehgallery` | `rule/Surge/EHGallery/EHGallery.list` — DOMAIN-SUFFIX×6, IP-CIDR×1 — `2025-06-06 09:17:53` / README `2025-07-30 02:09:18` | `data/ehentai` — bare-domain×8 | `adult-content`, `contains-ip-rules`, `manual-review-before-enable` |
| Adult | `japonx` | `rule/Surge/Japonx/Japonx.list` — DOMAIN-KEYWORD×2, DOMAIN-SUFFIX×8 — `2025-06-06 09:16:58` / README `2025-06-06 09:16:58` | `data/japonx` — bare-domain×4 | `adult-content`, `keyword-rule-in-blackmatrix`, `manual-review-before-enable` |
| Adult | `picacg` | `rule/Surge/Picacg/Picacg.list` — DOMAIN-SUFFIX×10 — `2024-01-08 16:02:55` / README `2024-01-08 16:02:55` | `data/picacg` — bare-domain×11, full×2 | `adult-content`, `manual-review-before-enable` |
| Adult | `vikacg` | `rule/Surge/VikACG/VikACG.list` — DOMAIN-SUFFIX×2 — `2025-06-06 09:19:14` / README `2025-06-06 09:19:14` | — | `adult-content`, `manual-review-before-enable` |
| Adult | `anonv` | `rule/Surge/Anonv/Anonv.list` — DOMAIN-SUFFIX×2 — `2024-01-08 16:02:59` / README `2024-01-08 16:02:59` | — | `adult-content`, `blackmatrix-header-old`, `manual-review-before-enable` |
| Cloud | `aws` | — | `data/aws` — bare-domain×51, include×1, regexp×1 | `cloud-provider`, `shared-infrastructure` |
| Cloud | `azure` | — | `data/azure` — bare-domain×149, full×2 | `cloud-provider`, `shared-infrastructure` |
| Cloud | `cloudflare` | `rule/Surge/Cloudflare/Cloudflare.list` — DOMAIN-SUFFIX×43, IP-CIDR×15, IP-CIDR6×7 — `2025-06-06 09:16:55` / README `2025-07-30 02:08:19` | `data/cloudflare` — bare-domain×55, include×2 | `shared-cdn-dns-and-security-hosts` |
| Cloud | `digitalocean` | `rule/Surge/DigitalOcean/DigitalOcean.list` — DOMAIN-SUFFIX×4 — `2025-06-06 09:18:54` / README `2025-06-06 09:18:54` | `data/digitalocean` — bare-domain×6 | `cloud-provider` |
| Cloud | `dropbox` | `rule/Surge/Dropbox/Dropbox.list` — DOMAIN-SUFFIX×17 — `2025-06-06 09:17:43` / README `2025-07-30 02:09:08` | `data/dropbox` — bare-domain×15, full×1 | `cloud-storage` |
| Cloud | `onedrive` | `rule/Surge/OneDrive/OneDrive.list` — DOMAIN-KEYWORD×3, DOMAIN-SUFFIX×13, PROCESS-NAME×2 — `2026-01-26 02:06:39` / README `2026-01-26 02:06:39` | `data/onedrive` — bare-domain×11 | `common-microsoft-service-direct-review` |
| Cloud | `google_drive` | `rule/Surge/GoogleDrive/GoogleDrive.list` — DOMAIN×1, DOMAIN-SUFFIX×4, PROCESS-NAME×1, USER-AGENT×3 — `2025-06-06 09:15:10` / README `2025-06-06 09:15:10` | — | `common-google-service-overlap` |
| Cloud | `icloud` | `rule/Surge/iCloud/iCloud.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×60 — `2026-06-30 02:43:26` / README `2026-06-30 02:43:26` | `data/icloud` — bare-domain×52, full×1, include×1 | `common-apple-service-default-direct` |
| Cloud | `oracle_cloud` | `rule/Surge/Oracle/Oracle.list` — DOMAIN-SUFFIX×25 — `2025-06-06 09:17:46` / README `2025-06-06 09:17:46` | `data/oracle` — bare-domain×13, include×2 | `cloud-provider` |
| Cloud | `vultr` | — | `data/vultr` — bare-domain×1 | `cloud-provider` |
| Cloud | `alibaba_cloud` | — | `data/alibabacloud` — bare-domain×8, full×4 | `regional-domestic-provider-review` |
| Knowledge | `wikipedia` | `rule/Surge/Wikipedia/Wikipedia.list` — DOMAIN-SUFFIX×12 — `2025-06-06 09:16:55` / README `2025-06-06 09:16:55` | — | `broad-category-avoid-inference` |
| Knowledge | `wikimedia` | `rule/Surge/Wikimedia/Wikimedia.list` — DOMAIN-SUFFIX×20 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | `data/wikimedia` — bare-domain×18 | `broad-service-family-review` |
| Knowledge | `stack_exchange` | `rule/Surge/Stackexchange/Stackexchange.list` — DOMAIN-SUFFIX×20 — `2025-06-06 09:17:47` / README `2025-06-06 09:17:47` | `data/stackexchange` — bare-domain×25 | `broad-network-of-sites` |
| Knowledge | `quora` | — | `data/quora` — bare-domain×3, full×1, include×1 | `regional-access-review` |
| Knowledge | `medium` | — | `data/medium` — bare-domain×2 | `mixed-user-content` |
| Knowledge | `notion` | `rule/Surge/Notion/Notion.list` — DOMAIN×1, DOMAIN-SUFFIX×5 — `2025-06-06 09:17:46` / README `2025-07-30 02:09:10` | `data/notion` — bare-domain×7 | `common-productivity-service` |
| Knowledge | `readthedocs` | — | `data/readthedocs` — bare-domain×4 | `developer-doc-hosting` |
| Knowledge | `coursera` | — | `data/coursera` — bare-domain×4 | `education-service` |
| Knowledge | `udemy` | — | `data/udemy` — bare-domain×2 | `education-service` |
| Knowledge | `khan_academy` | — | `data/khanacademy` — bare-domain×2 | `education-service` |
| Knowledge | `zhihu` | `rule/Surge/Zhihu/Zhihu.list` — DOMAIN-SUFFIX×2, IP-CIDR×4, IP-CIDR6×1 — `2025-06-06 09:18:49` / README `2025-06-06 09:18:49` | `data/zhihu` — bare-domain×6 | `domestic-service-direct-review` |
| Payments | `paypal` | `rule/Surge/PayPal/PayPal.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×246, USER-AGENT×1 — `2025-06-06 09:16:55` / README `2025-07-30 02:08:19` | `data/paypal` — bare-domain×244, full×1 | `financial-service-domestic-access-review` |
| Payments | `stripe` | `rule/Surge/Stripe/Stripe.list` — DOMAIN-KEYWORD×1 — `2025-06-06 09:20:02` / README `2025-06-06 09:20:02` | `data/stripe` — bare-domain×8 | `shared-infrastructure-review` |
| Payments | `visa` | `rule/Surge/VISA/VISA.list` — DOMAIN-SUFFIX×99 — `2025-06-06 09:17:48` / README `2025-06-06 09:17:48` | `data/visa` — bare-domain×160 | `financial-service-domestic-access-review` |
| Payments | `mastercard` | — | `data/mastercard` — bare-domain×184 | `financial-service-domestic-access-review` |
| Payments | `unionpay` | `rule/Surge/UnionPay/UnionPay.list` — DOMAIN-SUFFIX×16 — `2025-06-06 09:17:39` / README `2025-07-30 02:09:03` | `data/unionpay` — bare-domain×14 | `domestic-service-direct` |
| Payments | `alipay` | `rule/Surge/AliPay/AliPay.list` — DOMAIN-SUFFIX×21 — `2025-06-06 09:17:31` / README `2025-06-06 09:17:31` | — | `domestic-service-direct` |
| Apple-Overseas | `apple_aggregate` | `rule/Surge/Apple/Apple.list` — DOMAIN-KEYWORD×7, IP-CIDR×10, IP-CIDR6×3, PROCESS-NAME×13, USER-AGENT×23 — `2026-10-04 04:27:39` / README `2026-03-26 02:21:00` | `data/apple` — bare-domain×686, full×165, include×9 | `broad-apple-set-default-direct` |
| Apple-Overseas | `apple_dev` | `rule/Surge/AppleDev/AppleDev.list` — DOMAIN-SUFFIX×40 — `2025-06-06 09:18:50` / README `2025-06-06 09:18:50` | `data/apple-dev` — bare-domain×28, full×15, include×1 | `common-apple-service-default-direct` |
| Apple-Overseas | `apple_id` | `rule/Surge/AppleID/AppleID.list` — DOMAIN×4 — `2024-01-08 16:02:50` / README `2024-01-08 16:02:50` | — | `common-apple-service-default-direct` |
| Apple-Overseas | `apple_tv` | `rule/Surge/AppleTV/AppleTV.list` — DOMAIN×6, DOMAIN-SUFFIX×1, PROCESS-NAME×1, USER-AGENT×2 — `2025-06-06 09:15:11` / README `2025-06-06 09:15:11` | — | `common-apple-service-default-direct` |
| Apple-Overseas | `apple_hardware` | `rule/Surge/AppleHardware/AppleHardware.list` — DOMAIN-SUFFIX×226 — `2025-06-06 09:18:50` / README `2025-06-06 09:18:50` | — | `common-apple-service-default-direct` |
| Apple-Overseas | `apple_media` | `rule/Surge/AppleMedia/AppleMedia.list` — DOMAIN-SUFFIX×52 — `2025-06-06 09:18:50` / README `2025-06-06 09:18:50` | — | `common-apple-service-default-direct` |
| Apple-Overseas | `apple_tv_plus` | — | `data/apple-tvplus` — bare-domain×1, full×7 | `common-apple-service-default-direct` |
| Streaming | `youtube` | `rule/Surge/YouTube/YouTube.list` — DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×179, IP-CIDR×2, IP-CIDR6×1, USER-AGENT×7 — `2025-06-06 09:15:09` / README `2025-07-30 02:06:30` | `data/youtube` — bare-domain×177, full×1 | `google-service-overlap`, `application-cdn-coverage-review` |
| Knowledge | `google_services` | `rule/Surge/Google/Google.list` — DOMAIN×1, DOMAIN-KEYWORD×5, DOMAIN-SUFFIX×684, IP-CIDR×4, IP-CIDR6×1, PROCESS-NAME×5, USER-AGENT×3 — `2026-05-12 02:38:11` / README `2026-05-12 02:38:11` | `data/google` — bare-domain×539, full×105, include×18 | `broad-service-set`, `overlaps-gemini-youtube-and-cloud` |
| Cloud | `microsoft_services` | `rule/Surge/Microsoft/Microsoft.list` — DOMAIN×1, DOMAIN-KEYWORD×4, DOMAIN-SUFFIX×663, PROCESS-NAME×2, USER-AGENT×3 — `2025-12-08 02:06:20` / README `2025-12-08 02:06:20` | `data/microsoft` — bare-domain×288, full×55, include×8 | `broad-service-set`, `domestic-service-direct-review` |
| Music | `youtube_music` | `rule/Surge/YouTubeMusic/YouTubeMusic.list` — DOMAIN×1, USER-AGENT×4 — `2025-06-06 09:15:10` / README `2025-06-06 09:15:10` | — | `google-service-overlap` |
| Streaming | `hulu_jp` | `rule/Surge/HuluJP/HuluJP.list` — DOMAIN-SUFFIX×5 — `2025-06-06 09:17:41` / README `2025-06-06 09:17:41` | — | `region-variant-do-not-union` |
| Streaming | `hulu_usa` | `rule/Surge/HuluUSA/HuluUSA.list` — DOMAIN-SUFFIX×52, PROCESS-NAME×1 — `2025-06-06 09:17:41` / README `2025-06-06 09:17:41` | — | `region-variant-do-not-union` |
| Streaming | `hbo_asia` | `rule/Surge/HBOAsia/HBOAsia.list` — DOMAIN×16, DOMAIN-KEYWORD×1, DOMAIN-SUFFIX×2, USER-AGENT×1 — `2025-06-06 09:17:52` / README `2025-06-06 09:17:52` | — | `shared-cdn-with-other-hbo-variants` |
| Streaming | `hbo_hk` | `rule/Surge/HBOHK/HBOHK.list` — DOMAIN×1, DOMAIN-SUFFIX×23 — `2025-06-06 09:17:41` / README `2025-06-06 09:17:41` | — | `shared-cdn-with-other-hbo-variants` |
| Streaming | `hbo_usa` | `rule/Surge/HBOUSA/HBOUSA.list` — DOMAIN×1, DOMAIN-SUFFIX×9, PROCESS-NAME×1, USER-AGENT×1 — `2025-06-06 09:17:41` / README `2025-06-06 09:17:41` | — | `shared-cdn-with-other-hbo-variants` |

## 规则类型与迁移风险

- Surge adapter 将 DOMAIN / DOMAIN-SUFFIX 转成 EXACT / SUFFIX；KEYWORD、IP、IP-ASN、UA、PROCESS、OR 等进入 unsupported 报告并阻部署。v2fly adapter 已实现 `domain:` / 裸域名 → SUFFIX、`full:` → EXACT，以及 include 属性筛选、affiliation、正负属性和来源链处理；`keyword:` 与 `regexp:` 仍保持 unsupported/block，不能把字符串匹配或正则扩大成整个后缀。
- v2fly `include:` 是源列表间展开，不是规则标签；`include:key @attr @-attr` 按属性过滤。`@attr` 挂在域名规则上；`&target` 是 affiliation，用于源端把一条规则加入另一命名列表，生成数据时会被消费，不是运行时路由策略。adapter 保留 include DAG、属性筛选、affiliation 的目标关系和来源链，并检测环、缺 key 与未识别语法。适配器已实现不代表各服务的来源路由、覆盖或代理意图已经审核。
- 同一服务存在多来源时不做默认 union；目录见到两个源不等于其范围一致。尤其 AI 共享 telemetry/身份/云 CDN，HBO 变体共享 CDN；这些冲突应落审核项。
- 国内及服务区域风险：Steam 主列表含 SteamCN/中国下载流量，Game 是极宽泛的混合列表并有大量国内域名；游戏公司、社交和音乐列表也有 `.cn`。Apple 主服务仍按常用直连处理；不得用后缀/TLD 单独推断直连或代理，按精确规则及实测确定。
- Netflix 有 1,115 条 IPv4 规则以及 UA/process/IP 类型，单纯域名文件会出现明显覆盖差异；Facebook、游戏综合表同样包含大量IP或宽泛类别。Broad sources 保持单列、默认关闭。
- Adult 服务只根据上游独立 key / 类别名和语法计数登记，没有读取站点页面。各服务保持独立条目，`category-porn` 作为聚合参考而非默认部署来源；每项需人工审核、默认关闭。

## MoviePilot 元数据候选

MoviePilot 官方配置说明列出 TMDB API 主机 `api.themoviedb.org`（可配置备用 `api.tmdb.org`）、图片主机 `image.tmdb.org`，并提供 Fanart 服务端点 `webservice.fanart.tv`；Fanart API 也说明图片资产响应。TMDB 使用上游独立 `Tmdb/Tmdb.list` 中的两个后缀；Fanart 使用项目受控来源文件中的精确 `webservice.fanart.tv` 与 `assets.fanart.tv` 两条规则，不使用通配符。两项现为已审核、默认开启，但这是来源/用途审核结论，不代表已验证家庭网络连通，也不代表规则已部署。MoviePilot 实际启用的插件路径尚未验证；TVDB、动态集成和整体入口仍缺少可审计来源，不伪造 source ref 或完整外国兜底。MoviePilot 证据：[官方 Wiki 配置说明](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/configuration.md)、[官方网络准备说明](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/ready.md)、[Fanart API](https://github.com/fanart-tv/fanart.tv-api)。

## 许可证与再发布状态

blackmatrix7 根许可证为 GPL-2.0，副本已有 `licenses/blackmatrix7-GPL-2.0.txt`；v2fly 仓库根许可证为 MIT，副本见 [`licenses/v2fly-MIT.txt`](../licenses/v2fly-MIT.txt)。G1 已对 15 个默认 v2fly key 的文件/include 闭包完成比例化冲突声明检查，并核对人工规则数据仅按 MIT 授权；不据此扩张程序源码许可证，也不逐条域名作律师审核。未选入默认来源的 BM 规则仍不纳入再发布。具体许可通知如何打包、哈希是否与 manifest/release identity 绑定，仍待 G3 实施和验收；该研究目录不包含完整上游规则集合。
