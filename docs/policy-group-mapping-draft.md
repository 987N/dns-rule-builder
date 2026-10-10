# 策略组映射评审草案

本草案按新项目 `config/categories.yaml` 与 `config/policies.yaml` 抄列逻辑类别和 Provider 名称，供用户确定策略语义。它不读取或复刻生产 Mihomo 完整配置，不包含节点成员，也不是现网配置 diff。生产策略组、节点和兜底动作均待用户确认。

## 分类到逻辑组

| 默认状态 | 类别 ID | Provider | 建议逻辑策略组 | 配置优先级 | 决策备注 |
|---|---|---|---|---:|---|
| 开 | `openai` | `OpenAI` | `AI` | 10 | 与其他 AI 服务共享逻辑组；服务覆盖含共享统计域名风险 |
| 开 | `claude` | `Claude` | `AI` | 10 | 共享遥测域名需审阅 |
| 开 | `gemini` | `Gemini` | `AI` | 10 | 必须先于泛 Google 匹配；当前全局 precedence cycle 仍阻部署 |
| 开 | `copilot` | `Copilot` | `AI` | 10 | 具体真实路由组待确定 |
| 开 | `google` | `Google` | `Google` | 50 | 泛规则可能遮挡 YouTube 等更具体 Provider |
| 开 | `youtube` | `YouTube` | `YouTube` | 20 | 与 Google 的跨分类顺序仍有循环 blocker |
| 开 | `telegram` | `Telegram` | `Telegram` | 20 | 节点/策略映射待确认 |
| 开 | `github` | `GitHub` | `Development` | 20 | 开发服务逻辑组草案 |
| 开 | `docker` | `Docker` | `Development` | 30 | 云/CDN 精确 host 需要共享域名审阅 |
| 开 | `gitlab` | `GitLab` | `Development` | 40 | — |
| 开 | `jetbrains` | `JetBrains` | `Development` | 50 | — |
| 开 | `vercel` | `Vercel` | `Development` | 60 | — |
| 开 | `discord` | `Discord` | `Social` | 30 | — |
| 开 | `reddit` | `Reddit` | `Social` | 40 | — |
| 开 | `foreign_general` | `Foreign-General` | `Foreign-General` | 900 | 仅人工维护的自定义/批准长尾；禁止综合国外全集兜底 |
| 关 | `apple` | `Apple` | `Apple` | 30 | 宽泛资产集，审核前保持关闭 |
| 关 | `apple_domain_set` | `AppleDomainSet` | `Apple` | 30 | Apple Domain Set 格式适配候选，审核前保持关闭；与 Apple All 不同时启用 |
| 关 | `steam` | `Steam` | `Gaming` | 30 | 样本包含中国下载域名，逐项审查代理意图 |
| 关 | `microsoft` | `Microsoft` | `Microsoft` | 30 | 覆盖与服务归属待审 |
| 关 | `netflix` | `Netflix` | `Streaming` | 30 | — |
| 关 | `facebook` | `Facebook` | `Social` | 30 | — |
| 关 | `tiktok` | `TikTok` | `TikTok` | 30 | — |
| 关 | `abema` | `Abema` | `Streaming` | 40 | 来源目录存在；是否纳入待审 |
| 关 | `bahamut` | `Bahamut` | `Streaming` | 40 | 来源目录存在；是否纳入待审 |
| 关 | `npmjs` | `Npmjs` | `Development` | 70 | 对应 Npmjs 候选分类；未启用前不代表实际分流 |
| 关 | `hbo` | `HBO` | `Streaming` | 40 | Max 对应 HBO 候选，名称/覆盖边界待审 |

## Schema 3 到 Schema 4 的接口示意

以下是概念片段，只说明表达能力差异；名称和路径是示例，不可直接贴入生产配置。Schema 3 target 由固定 Provider 清单和后缀字段定义，schema-v4 bundle 则包含每个动态 Provider 文件、匹配类型和哈希清单。

Schema 3 固定后缀示意：

```yaml
schema_version: 3
providers:
  - name: OpenAI
    suffixes: [openai.com, chatgpt.com]
```

Schema 4 版本目录示意：

```yaml
# mihomo/rule-providers.yaml (generated fragment)
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
# mihomo/providers/CustomExact-OpenAI.yaml
payload:
  - DOMAIN,api.example.test
```

```yaml
# mihomo/providers/OpenAI.yaml
payload:
  - DOMAIN-SUFFIX,openai.com
```

对应规则顺序片段：

```yaml
# mihomo/rules.yaml (generated fragment)
rules:
  - RULE-SET,CustomExact-OpenAI,AI
  - RULE-SET,OpenAI,AI
```

生产目标必须先将所有自定义 exact providers 放到普通分类 Providers 前；普通 Provider 顺序由 specificity precedence graph 校验，循环会阻止部署。Schema-v4 的本地 target adapter 还检查 provider 顺序、manifest 哈希、路径安全及目标服务角色。生产 schema-3 适配器、真实配置路径、服务 reload/readback 均未安装或验收。
