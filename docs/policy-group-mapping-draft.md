# 策略组映射评审草案

本草案根据运行配置和机器可读目录生成，供确定逻辑策略及服务映射。它不读取或复刻生产 Mihomo 完整配置，不包含节点成员，也不是可直接部署的现网 diff。节点成员和策略选择器留待用户确认。

当前目录有 141 项，其中 124 个配置类别（含为旧配置读取保留、禁止启用的 Apple 聚合别名），17 个默认启用；TikTok 已删除。关闭类别仍是候选，不会因来源映射而默认启用。R4 的 19 个 deployment blockers 是历史基线；最终候选 `review_required=false`、`publish_allowed=true`，仅生产 target semantics 未确认与 deployment 总开关关闭两项部署阻塞。公开发布许可门禁已通过，但本轮没有实际发布。R5 全目录本地语义审计已通过：124 类别中 123 有源、解析 7,319 条规则并生成 123 个 Providers，全量 syntax-only `-t` 通过，另保留 1,422 条 unsupported 记录。65 条 unsupported 是早期默认构建基线的历史统计；最终候选为 18 条精确签名审核的 unsupported。全目录审计不代表全部类别可启用或来源/服务顺序已获批准；详见[扩展验收记录](expansion-acceptance.md)。

## 运行类别映射

| 默认 | 类别 | Provider | 用途组 | 逻辑策略组 | 优先级 | 来源 / 评审状态 |
|---|---|---|---|---|---:|---|
| 开 | `openai` | `OpenAI` | AI | `AI` | 10 | `v2fly:openai` + `manual_curated:openai-exact-dependencies.list`；来源已审核；精确排除与补充 host 见来源审批记录 |
| 开 | `claude` | `Claude` | AI | `AI` | 10 | `v2fly:anthropic`；来源已审核 |
| 开 | `gemini` | `Gemini` | AI | `Gemini` | 10 | `v2fly:google-gemini`；来源已审核；Google 重叠按独立策略组处理 |
| 开 | `copilot` | `Copilot` | AI | `AI` | 10 | `v2fly:github-copilot`；来源已审核 |
| 开 | `google` | `Google` | Knowledge | `Google` | 50 | `v2fly:google`；来源已审核；Google 家族范围按单独已审核 key 锁定 |
| 开 | `telegram` | `Telegram` | Communication | `Telegram` | 20 | `v2fly:telegram`；来源已审核 |
| 开 | `github` | `GitHub` | Development | `Development` | 20 | `v2fly:github`；来源已审核 |
| 开 | `docker` | `Docker` | Development | `Development` | 30 | `v2fly:docker` + `manual_curated:docker-exact-dependencies.list`；来源已审核；仅记录审核过的精确业务 host |
| 开 | `gitlab` | `GitLab` | Development | `Development` | 40 | `v2fly:gitlab`；来源已审核 |
| 开 | `jetbrains` | `JetBrains` | Development | `Development` | 50 | `v2fly:jetbrains`；来源已审核 |
| 开 | `vercel` | `Vercel` | Development | `Development` | 60 | `v2fly:vercel`；来源已审核 |
| 开 | `discord` | `Discord` | Communication | `Communication` | 30 | `v2fly:discord`；来源已审核 |
| 开 | `reddit` | `Reddit` | Social | `Social` | 40 | `v2fly:reddit`；来源已审核 |
| 开 | `foreign_general` | `Foreign-General` | — | `Foreign-General` | 900 | `人工维护`；无需 catalog candidate |
| 关 | `apple` | `Apple` | Apple-Overseas | `Apple` | 30 | `blackmatrix7:Apple/Apple_All.list`；禁止聚合；broad-apple-set-default-direct；unsupported-source-directives |
| 关 | `apple_domain_set` | `AppleDomainSet` | Apple-Overseas | `Apple` | 30 | `blackmatrix7:Apple/Apple_Domain.list`；禁止聚合；broad-apple-set-default-direct；unsupported-source-directives |
| 关 | `steam` | `Steam` | Gaming | `Gaming` | 30 | `blackmatrix7:Steam/Steam.list`；legacy 待审；chinese-download-domains；overlaps-steamcn |
| 关 | `microsoft` | `Microsoft` | Cloud | `Microsoft` | 30 | `blackmatrix7:Microsoft/Microsoft.list`；legacy 待审；broad-service-set；domestic-service-direct-review |
| 关 | `netflix` | `Netflix` | Streaming | `Netflix` | 30 | `blackmatrix7:Netflix/Netflix.list`；legacy 待审；large-ip-list；app-rules-not-dns-rules |
| 关 | `facebook` | `Facebook` | Social | `Social` | 30 | `blackmatrix7:Facebook/Facebook.list`；legacy 待审；large-list；ip-and-keyword-rules |
| 关 | `abema` | `Abema` | Streaming | `Abema` | 40 | `blackmatrix7:Abema/Abema.list`；legacy 待审；blackmatrix-header-old |
| 关 | `bahamut` | `Bahamut` | Streaming | `Bahamut` | 40 | `blackmatrix7:Bahamut/Bahamut.list`；legacy 待审；contains-app-rules；unsupported-source-directives |
| 关 | `npmjs` | `Npmjs` | Development | `Development` | 70 | `blackmatrix7:Npmjs/Npmjs.list`；legacy 待审；blackmatrix-header-old |
| 关 | `hbo` | `HBO` | Streaming | `HBO` | 40 | `blackmatrix7:HBO/HBO.list`；legacy 待审；region-variants-share-cdn；unsupported-source-directives |
| 关 | `anthropic` | `Anthropic` | AI | `AI` | 10 | `blackmatrix7:rule/Surge/Anthropic/Anthropic.list`；pending；blackmatrix-header-old |
| 关 | `perplexity` | `Perplexity` | AI | `AI` | 10 | `v2fly:perplexity`；pending；adapter 已实现；来源/路由待 review |
| 关 | `xai` | `Xai` | AI | `AI` | 10 | `v2fly:xai`；pending；adapter 已实现；来源/路由待 review |
| 关 | `cursor` | `Cursor` | AI | `AI` | 10 | `v2fly:cursor`；pending；adapter 已实现；来源/路由待 review |
| 关 | `huggingface` | `Huggingface` | AI | `AI` | 10 | `v2fly:huggingface`；pending；adapter 已实现；来源/路由待 review |
| 关 | `netlify` | `Netlify` | Development | `Development` | 30 | `v2fly:netlify`；pending；adapter 已实现；来源/路由待 review |
| 关 | `python` | `Python` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Python/Python.list`；pending |
| 关 | `anaconda` | `Anaconda` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Anaconda/Anaconda.list`；pending；blackmatrix-header-old |
| 关 | `sourceforge` | `Sourceforge` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/SourceForge/SourceForge.list`；pending |
| 关 | `atlassian` | `Atlassian` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Atlassian/Atlassian.list`；pending |
| 关 | `figma` | `Figma` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Figma/Figma.list`；pending；blackmatrix-header-old |
| 关 | `jfrog` | `Jfrog` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Jfrog/Jfrog.list`；pending |
| 关 | `gitbook` | `Gitbook` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/GitBook/GitBook.list`；pending |
| 关 | `hashicorp` | `Hashicorp` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/HashiCorp/HashiCorp.list`；pending |
| 关 | `linux` | `Linux` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Linux/Linux.list`；pending；blackmatrix-header-old |
| 关 | `ubuntu` | `Ubuntu` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Ubuntu/Ubuntu.list`；pending；contains-cn-domain |
| 关 | `electron` | `Electron` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Electron/Electron.list`；pending；blackmatrix-header-old |
| 关 | `sublime_text` | `SublimeText` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/SublimeText/SublimeText.list`；pending；blackmatrix-header-old |
| 关 | `apifox` | `Apifox` | Development | `Development` | 30 | `blackmatrix7:rule/Surge/Apifox/Apifox.list`；pending；contains-cn-domain；blackmatrix-header-old |
| 关 | `linkedin` | `Linkedin` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/LinkedIn/LinkedIn.list`；pending；contains-cn-domains；blackmatrix-header-old |
| 关 | `snapchat` | `Snapchat` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Snap/Snap.list`；pending |
| 关 | `threads` | `Threads` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Threads/Threads.list`；pending；blackmatrix-header-old |
| 关 | `instagram` | `Instagram` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Instagram/Instagram.list`；pending；contains-unsupported-rule-types；unsupported-source-directives |
| 关 | `pinterest` | `Pinterest` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Pinterest/Pinterest.list`；pending |
| 关 | `tumblr` | `Tumblr` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Tumblr/Tumblr.list`；pending |
| 关 | `x_twitter` | `XTwitter` | Social | `Social` | 40 | `blackmatrix7:rule/Surge/Twitter/Twitter.list`；pending；contains-ip-rules；unsupported-source-directives |
| 关 | `disney` | `Disney` | Streaming | `DisneyPlus` | 50 | `blackmatrix7:rule/Surge/Disney/Disney.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `prime_video` | `PrimeVideo` | Streaming | `PrimeVideo` | 50 | `blackmatrix7:rule/Surge/PrimeVideo/PrimeVideo.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `hulu` | `Hulu` | Streaming | `Hulu` | 50 | `blackmatrix7:rule/Surge/Hulu/Hulu.list`；pending；region-variants-do-not-union；unsupported-source-directives |
| 关 | `dailymotion` | `Dailymotion` | Streaming | `Streaming` | 50 | `blackmatrix7:rule/Surge/Dailymotion/Dailymotion.list`；pending |
| 关 | `twitch` | `Twitch` | Streaming | `Streaming` | 50 | `blackmatrix7:rule/Surge/Twitch/Twitch.list`；pending；contains-ip-keyword-and-process-rules；unsupported-source-directives |
| 关 | `vimeo` | `Vimeo` | Streaming | `Streaming` | 50 | `blackmatrix7:rule/Surge/Vimeo/Vimeo.list`；pending；v2fly-regexp-present |
| 关 | `spotify` | `Spotify` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/Spotify/Spotify.list`；pending；contains-app-and-ip-rules；unsupported-source-directives |
| 关 | `deezer` | `Deezer` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/Deezer/Deezer.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `tidal` | `Tidal` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/TIDAL/TIDAL.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `qobuz` | `Qobuz` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/Qobuz/Qobuz.list`；pending；contains-ip-rules；unsupported-source-directives |
| 关 | `soundcloud` | `Soundcloud` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/SoundCloud/SoundCloud.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `pandora` | `Pandora` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/Pandora/Pandora.list`；pending；contains-app-rules；unsupported-source-directives |
| 关 | `lastfm` | `Lastfm` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/LastFM/LastFM.list`；pending；contains-cn-domain |
| 关 | `joox` | `Joox` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/JOOX/JOOX.list`；pending；contains-ip-keyword-and-app-rules；unsupported-source-directives |
| 关 | `kkbox` | `Kkbox` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/KKBOX/KKBOX.list`；pending |
| 关 | `epic` | `Epic` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Epic/Epic.list`；pending |
| 关 | `nintendo` | `Nintendo` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Nintendo/Nintendo.list`；pending；contains-cn-domains；contains-ip-rules |
| 关 | `xbox` | `Xbox` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Xbox/Xbox.list`；pending；contains-cn-domain |
| 关 | `playstation` | `Playstation` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/PlayStation/PlayStation.list`；pending |
| 关 | `blizzard` | `Blizzard` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Blizzard/Blizzard.list`；pending；contains-cn-domains；contains-ip-rules |
| 关 | `riot` | `Riot` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Riot/Riot.list`；pending；contains-cn-domains |
| 关 | `ubisoft` | `Ubisoft` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Ubisoft/Ubisoft.list`；pending；blackmatrix-header-old |
| 关 | `ea` | `Ea` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/EA/EA.list`；pending；contains-cn-domains |
| 关 | `gog` | `Gog` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/Gog/Gog.list`；pending |
| 关 | `hoyoverse` | `Hoyoverse` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/HoYoverse/HoYoverse.list`；pending |
| 关 | `two_k_games` | `TwoKGames` | Gaming | `Gaming` | 60 | `blackmatrix7:rule/Surge/2KGames/2KGames.list`；pending；blackmatrix-header-old |
| 开 | `tmdb` | `TMDB` | Media-Metadata | `Media-Metadata` | 15 | `v2fly:tmdb`；来源已审核；静态依赖证据见 MoviePilot 小节 |
| 关 | `imdb` | `Imdb` | Media-Metadata | `Media-Metadata` | 15 | `blackmatrix7:rule/Surge/IMDB/IMDB.list`；pending；blackmatrix-header-readme-date-differs |
| 关 | `douban` | `Douban` | Media-Metadata | `Media-Metadata` | 15 | `blackmatrix7:rule/Surge/DouBan/DouBan.list`；pending；domestic-service-direct-review |
| 开 | `fanart` | `Fanart` | Media-Metadata | `Media-Metadata` | 15 | `manual_curated:fanart.list`；MIT 许可的项目维护精确规则；来源已审核 |
| 关 | `pornhub` | `Pornhub` | Adult | `Adult` | 95 | `v2fly:pornhub`；pending；adult-content；manual-review-before-enable |
| 关 | `xvideos` | `Xvideos` | Adult | `Adult` | 95 | `v2fly:xvideos`；pending；adult-content；manual-review-before-enable |
| 关 | `xnxx` | `Xnxx` | Adult | `Adult` | 95 | `v2fly:xnxx`；pending；adult-content；manual-review-before-enable |
| 关 | `xhamster` | `Xhamster` | Adult | `Adult` | 95 | `v2fly:xhamster`；pending；adult-content；manual-review-before-enable |
| 关 | `spankbang` | `Spankbang` | Adult | `Adult` | 95 | `v2fly:spankbang`；pending；adult-content；manual-review-before-enable |
| 关 | `javdb` | `Javdb` | Adult | `Adult` | 95 | `v2fly:javdb`；pending；adult-content；v2fly-regexp-present |
| 关 | `javbus` | `Javbus` | Adult | `Adult` | 95 | `v2fly:javbus`；pending；adult-content；manual-review-before-enable |
| 关 | `missav` | `Missav` | Adult | `Adult` | 95 | `v2fly:missav`；pending；adult-content；manual-review-before-enable |
| 关 | `jable` | `Jable` | Adult | `Adult` | 95 | `v2fly:jable`；pending；adult-content；manual-review-before-enable |
| 关 | `dmm` | `Dmm` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/DMM/DMM.list`；pending；adult-content；contains-ip-rules |
| 关 | `ehgallery` | `Ehgallery` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/EHGallery/EHGallery.list`；pending；adult-content；contains-ip-rules |
| 关 | `japonx` | `Japonx` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/Japonx/Japonx.list`；pending；adult-content；keyword-rule-in-blackmatrix |
| 关 | `picacg` | `Picacg` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/Picacg/Picacg.list`；pending；adult-content；manual-review-before-enable |
| 关 | `vikacg` | `Vikacg` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/VikACG/VikACG.list`；pending；adult-content；manual-review-before-enable |
| 关 | `anonv` | `Anonv` | Adult | `Adult` | 95 | `blackmatrix7:rule/Surge/Anonv/Anonv.list`；pending；adult-content；blackmatrix-header-old |
| 关 | `aws` | `Aws` | Cloud | `Cloud` | 70 | `v2fly:aws`；pending；cloud-provider；shared-infrastructure |
| 关 | `azure` | `Azure` | Cloud | `Cloud` | 70 | `v2fly:azure`；pending；cloud-provider；shared-infrastructure |
| 关 | `cloudflare` | `Cloudflare` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/Cloudflare/Cloudflare.list`；pending；shared-cdn-dns-and-security-hosts；unsupported-source-directives |
| 关 | `digitalocean` | `Digitalocean` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/DigitalOcean/DigitalOcean.list`；pending；cloud-provider |
| 关 | `dropbox` | `Dropbox` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/Dropbox/Dropbox.list`；pending；cloud-storage |
| 关 | `onedrive` | `Onedrive` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/OneDrive/OneDrive.list`；pending；common-microsoft-service-direct-review；unsupported-source-directives |
| 关 | `google_drive` | `GoogleDrive` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/GoogleDrive/GoogleDrive.list`；pending；common-google-service-overlap；unsupported-source-directives |
| 关 | `oracle_cloud` | `OracleCloud` | Cloud | `Cloud` | 70 | `blackmatrix7:rule/Surge/Oracle/Oracle.list`；pending；cloud-provider |
| 关 | `vultr` | `Vultr` | Cloud | `Cloud` | 70 | `v2fly:vultr`；pending；cloud-provider |
| 关 | `wikipedia` | `Wikipedia` | Knowledge | `Knowledge` | 80 | `blackmatrix7:rule/Surge/Wikipedia/Wikipedia.list`；pending；broad-category-avoid-inference |
| 关 | `wikimedia` | `Wikimedia` | Knowledge | `Knowledge` | 80 | `blackmatrix7:rule/Surge/Wikimedia/Wikimedia.list`；pending；broad-service-family-review |
| 关 | `stack_exchange` | `StackExchange` | Knowledge | `Knowledge` | 80 | `blackmatrix7:rule/Surge/Stackexchange/Stackexchange.list`；pending；broad-network-of-sites |
| 关 | `quora` | `Quora` | Knowledge | `Knowledge` | 80 | `v2fly:quora`；pending；regional-access-review；unsupported-source-directives |
| 关 | `medium` | `Medium` | Knowledge | `Knowledge` | 80 | `v2fly:medium`；pending；mixed-user-content |
| 关 | `notion` | `Notion` | Knowledge | `Knowledge` | 80 | `blackmatrix7:rule/Surge/Notion/Notion.list`；pending；common-productivity-service |
| 关 | `readthedocs` | `Readthedocs` | Knowledge | `Knowledge` | 80 | `v2fly:readthedocs`；pending；developer-doc-hosting |
| 关 | `coursera` | `Coursera` | Knowledge | `Knowledge` | 80 | `v2fly:coursera`；pending；education-service |
| 关 | `udemy` | `Udemy` | Knowledge | `Knowledge` | 80 | `v2fly:udemy`；pending；education-service |
| 关 | `khan_academy` | `KhanAcademy` | Knowledge | `Knowledge` | 80 | `v2fly:khanacademy`；pending；education-service |
| 关 | `apple_dev` | `AppleDev` | Apple-Overseas | `Apple-Overseas` | 90 | `blackmatrix7:rule/Surge/AppleDev/AppleDev.list`；pending；common-apple-service-default-direct |
| 关 | `apple_tv` | `AppleTv` | Apple-Overseas | `Apple-Overseas` | 90 | `blackmatrix7:rule/Surge/AppleTV/AppleTV.list`；pending；common-apple-service-default-direct；unsupported-source-directives |
| 关 | `apple_tv_plus` | `AppleTvPlus` | Apple-Overseas | `Apple-Overseas` | 90 | `v2fly:apple-tvplus`；pending；common-apple-service-default-direct |
| 关 | `youtube_music` | `YoutubeMusic` | Music | `Music` | 55 | `blackmatrix7:rule/Surge/YouTubeMusic/YouTubeMusic.list`；pending；google-service-overlap；unsupported-source-directives |
| 关 | `hulu_jp` | `HuluJp` | Streaming | `Hulu` | 50 | `blackmatrix7:rule/Surge/HuluJP/HuluJP.list`；pending；region-variant-do-not-union |
| 关 | `hulu_usa` | `HuluUsa` | Streaming | `Hulu` | 50 | `blackmatrix7:rule/Surge/HuluUSA/HuluUSA.list`；pending；region-variant-do-not-union；unsupported-source-directives |
| 关 | `hbo_asia` | `HboAsia` | Streaming | `HBO` | 50 | `blackmatrix7:rule/Surge/HBOAsia/HBOAsia.list`；pending；shared-cdn-with-other-hbo-variants；unsupported-source-directives |
| 关 | `hbo_hk` | `HboHk` | Streaming | `HBO` | 50 | `blackmatrix7:rule/Surge/HBOHK/HBOHK.list`；pending；shared-cdn-with-other-hbo-variants |
| 关 | `hbo_usa` | `HboUsa` | Streaming | `HBO` | 50 | `blackmatrix7:rule/Surge/HBOUSA/HBOUSA.list`；pending；shared-cdn-with-other-hbo-variants；unsupported-source-directives |
| 开 | `youtube` | `YouTube` | Streaming | `YouTube` | 20 | `v2fly:youtube`；来源已审核；Google/YouTube 双向顺序冲突仍待目标语义裁定 |

## 逻辑策略组与选择边界

用途组为 AI、Development、Social、Communication、Streaming、Music、Gaming、Media-Metadata、Cloud、Knowledge、Payments、Apple-Overseas、Adult、Foreign-General。独立服务策略组用于 Google、YouTube、Gemini、Telegram、Netflix、DisneyPlus、PrimeVideo、HBO、Hulu、Abema、Bahamut。Google、YouTube、Gemini 均为独立策略组；Gemini 默认映射到 `Gemini`，同时保留 `AI` 作为可兼容旧 rules-data 覆盖的合法组。Apple、Microsoft 兼容选择组仍保留在模板中；`Streaming` 用途组当前映射 Dailymotion、Twitch、Vimeo。用户已确定保留区域分组，由这些策略组手动选择香港节点、美国节点等区域组；模板保留 Proxy/REJECT 兼容入口。区域内部节点未绑定，完整现网区域清单和节点待迁移时核对；暂不切换和部署。

节点成员及各组的最终节点选择器未确定。`Foreign-General` 只接受人工维护规则，不作为上游全量集合兜底。未收录域名保持 Real-IP/direct。

## 默认直连与聚合限制

- 常用 Apple、Apple Music、iCloud、Apple ID、硬件与媒体域名标记为 direct/reference-only；不会创建可开启代理的运行类别。旧 `apple`、`apple_domain_set` 类别保留以兼容已有数据，但默认关闭，catalog 标为 prohibited aggregate，Builder 会拒绝启用。AppleDev、AppleTV、AppleTV+ 仅作为关闭的 Apple-Overseas 候选。
- Game、Apple 全集、Adult 全集和其他宽泛聚合源仅保留研究记录，不建立运行类别。SteamCN、支付宝、银联、知乎和国内云服务为 direct/reference-only；Payments 服务不创建可部署类别。
- TikTok 不在服务目录、运行类别、策略组映射或测试种子中。R0 归档清单仅作为历史扫描证据保留。
- Adult 有 15 个独立配置类别，均默认关闭：Pornhub、XVideos、XNXX、xHamster、SpankBang、MissAV、Jable、JavDB、JavBus、DMM、EHGallery、Japonx、PicaCG、VikACG、AnonV。JavDB 包含 regexp，覆盖标记 partial，不能作为完整服务规则。

## MoviePilot 元数据

TMDB、Fanart 默认启用并落在 `Media-Metadata`。TMDB 使用固定 blackmatrix7 `Tmdb/Tmdb.list`，实际构建解释 `api.themoviedb.org` 与 `image.tmdb.org` 时匹配其父后缀；源文件头日期是 2024-01-08，仓库 commit 日期另列，不能把它们混为一谈。Fanart 不存在独立上游目录，使用项目受控的精确规则 `webservice.fanart.tv` 与 `assets.fanart.tv`，规则由 `manual_curated` 源哈希和代码 commit 锁定。

证据：[MoviePilot 配置](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/configuration.md)、[MoviePilot 网络准备](https://github.com/jxxghp/MoviePilot-Wiki/blob/main/ready.md)、[Fanart API](https://github.com/fanart-tv/fanart.tv-api)。这仅验证已记录的静态产品依赖；MoviePilot 实际启用插件路径未验证，TVDB 与动态集成缺少可审计来源，也不证明家庭网络请求通过。

## 未解决的 Google / YouTube 顺序循环

当前固定规则集合存在双向 Provider 先后依赖：YouTube 的 `video.google.com` 与 `youtube*.googleapis.com` 要求 YouTube 在 Google 前；Google 的 `beacons.gvt2.com`、`beacons2.gvt2.com`、`beacons3.gvt2.com`、`gcp.gvt2.com` 与 `redirector.*.gvt1.com` 又要求 Google 先于 YouTube。单一 Provider 排序无法同时满足。构建器保留 cycle blocker；在对相关服务做明确范围拆分或目标规则语义裁定前，不允许部署，不能用优先级宣称冲突已解决。

默认发布来源已锁定为来源审批记录中的 15 个 v2fly key，并按逐项来源 profile 审核；OpenAI、Docker 的人工补充只采用已审核的精确业务依赖。排除的 telemetry 不进入默认规则；旧 Fathom 与共享 CloudFront host 风险不代表当前仍有同名 deployment blocker。不得通过删掉未排除的跨类来源规则或泛化覆盖来消除冲突。

## Schema 3 到 Schema 4 接口示意

下面只示意协议能力，不是生产配置 diff。Schema 3 固定 Provider 清单和后缀字符串；Schema 4 由 Builder 生成带 manifest 哈希的动态 classical providers，可表达 EXACT。

```yaml
# schema 3 的固定字段示意
providers:
  - name: OpenAI
    suffixes: [openai.com]
```

```yaml
# schema 4 生成片段示意
rule-providers:
  CustomExact-OpenAI:
    type: file
    behavior: classical
    format: yaml
    path: ./providers/CustomExact-OpenAI.yaml
  OpenAI:
    type: file
    behavior: classical
    format: yaml
    path: ./providers/OpenAI.yaml
```

```yaml
# 对应规则 payload
payload:
  - DOMAIN,api.example.test
  - DOMAIN-SUFFIX,openai.com
```

生产 target 适配、真实配置路径、节点成员、策略组重整和 reload/readback 尚未部署。P5 生产迁移方案见 [production-migration-plan.md](production-migration-plan.md)。
