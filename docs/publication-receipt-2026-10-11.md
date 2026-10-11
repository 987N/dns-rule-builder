# GitHub 正式发布验收（2026-10-11）

按用户授权将 `6f67fbee9d27e84331c844234871fee687da43c0` 正常推送到公开仓库 `987N/dns-rule-builder/main`，未强推。数据分支保持 `a08559c430fe43723f6f187046f0cecc27b0a5cd`，旧六字段状态兼容读取，没有为可选审核字段改写数据分支。

- [测试 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129112138) 成功；镜像工作流中的独立测试亦为 108 项通过。
- [镜像 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129112155) 成功，发布 `ghcr.io/987n/dns-rule-builder@sha256:f3e17d9e222173b1f57973dd894f80e66fb5df797a1bd1eb091d66d45d714603`。本机使用空 Docker 认证配置匿名拉取 Linux/amd64 镜像，并在断网、只读、UID 10001、移除 capabilities 的容器内验证包与区域模板可读取。该镜像尚不包含后续 D1/D2 生产适配，不能作为最终生产管理镜像。
- [正式规则 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129145252) 首次及第二次 attempt 均成功，使用 request UUID `63c11201-0f78-4fed-bcc3-9076141e5d3e`，`smoke_test=false`。
- [正式不可变 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961) 的 ID 为 `66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961`，API 报告 immutable=true。重复构建复用同一 Release 和全部 8 个资产 ID/digest，没有创建替代版本。

下载 ZIP、manifest、SHA256SUMS、LICENSE/NOTICE 和审核摘要后，逐项验证外置校验及全部 40 个包内产物哈希、manifest 身份、Provider 成员映射和顺序。单独匿名下载公开 manifest 与包内副本一致。上游 v2fly revision 为已审核的 `c6adacf29cc4fb05e04651fcf03e00989ef9f797`，人工数据 revision 与发布代码 SHA 一致。

正式候选保持 17 个默认启用类别、16 个非空类别、1,564 条保留出处的类别规则、1,337 个独立语义/SmartDNS 项、20 个 Provider 片段、18 条经精确签名处置的 unsupported。272 项重叠已裁决，未解决冲突为 0。`review_required=false`、`publish_allowed=true`、`deployment_allowed=false`，只保留目标 SmartDNS/Mihomo 语义未确认和部署总开关关闭两项生产门禁。没有改写已发布 manifest。

本机独立证据位于忽略目录 `.cache/production-publish-download/`、`.cache/production-publish-request.json` 和 `.cache/production-publish-assets-{before,after}.json`。ZIP 内含全部许可和审核材料，不依赖这些本机证据。此收据证明代码、镜像与规则公开发布，不证明 NAS 安装、设备规则激活或真实国外服务访问。

## 扩展独立服务后的正式发布

用户选择在生产切换前扩大独立服务覆盖。扩展和部署适配已正常推送，最终代码为 `442e2a4b3d988802313027469d9f44f1c6d98bbe`；数据分支仍为上述旧六字段 commit。

- [最终代码测试](https://github.com/987N/dns-rule-builder/actions/runs/38133863331) 和 [最终镜像工作流](https://github.com/987N/dns-rule-builder/actions/runs/38133863273) 成功。镜像固定为 `ghcr.io/987n/dns-rule-builder@sha256:e85468bb6b8303362e1e8a58694bfe3c251e2eacdaf7ed1887a5a1fa12c4bed3`，包括真实 SSH target、持久化人工部署任务及安装适配。
- 主 Agent 使用空 Docker 认证配置匿名拉取 Linux/amd64 镜像。在断网、只读、移除全部 capabilities、UID 10001 的容器内，实际 HTTP 登录、未登录拒绝、CSRF 拒绝、人工开关默认关闭和模板检查通过。
- [正式规则工作流](https://github.com/987N/dns-rule-builder/actions/runs/38147896192) 两次执行均成功。request UUID 为 `dc9db1ff-9727-4e25-8ac3-7bf5bc17ca55`，`smoke_test=false`；主 Agent 比较两次 API 记录，确认复用相同 Release 及全部 8 项资产的 ID、大小和 digest。
- [扩展后的正式不可变 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-30350ace656e062e85acdb9f03786762e3293b84cc940dadc2aaf01ff389799a) 已发布，API 报告 immutable=true、draft=false，共 8 项资产。匿名下载全部资产，校验 API 资产 digest、SHA256SUMS、包内外 manifest 字节一致、83 项产物哈希和 Provider 映射。代码、数据与来源身份符合本次构建。

该正式包启用 60 类，59 类非空；有 2,694 条保留分类和来源的规则、2,416 个独立域名语义（2,070 SUFFIX、346 EXACT）、63 个 Provider 片段、27 条经审核保留报告的不兼容规则。321 个重叠已裁决，未解决冲突为 0。发布许可通过，部署仍只保留目标语义尚未验证与部署总开关关闭两项门禁。

对下载的正式包再次用固定版本的 Mihomo/SmartDNS、Linux/amd64 和断网容器验证，35 个代表域名的 Provider 路由及精确/后缀行为通过。此结果不是实际视频播放、成人网站访问或字幕下载成功的证明。部分 CDN 和区域播放依赖的限制仍见覆盖研究报告；60 类独立服务清单不等于完整国外网站库。

本机证据：`.cache/expanded-production-publish-download/`、`.cache/expanded-production-publish-verification.json`、`.cache/root-formal-expanded-binary-proof/`。生产规则、反代和旧写入者尚未切换。用户再次询问覆盖范围后，选择先使用当前清单、以后发现不足再补，因此继续当前 Release 的安装准备。
