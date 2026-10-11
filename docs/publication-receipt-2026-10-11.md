# GitHub 正式发布验收（2026-10-11）

按用户授权将 `6f67fbee9d27e84331c844234871fee687da43c0` 正常推送到公开仓库 `987N/dns-rule-builder/main`，未强推。数据分支保持 `a08559c430fe43723f6f187046f0cecc27b0a5cd`，旧六字段状态兼容读取，没有为可选审核字段改写数据分支。

- [测试 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129112138) 成功；镜像工作流中的独立测试亦为 108 项通过。
- [镜像 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129112155) 成功，发布 `ghcr.io/987n/dns-rule-builder@sha256:f3e17d9e222173b1f57973dd894f80e66fb5df797a1bd1eb091d66d45d714603`。本机使用空 Docker 认证配置匿名拉取 Linux/amd64 镜像，并在断网、只读、UID 10001、移除 capabilities 的容器内验证包与区域模板可读取。该镜像尚不包含后续 D1/D2 生产适配，不能作为最终生产管理镜像。
- [正式规则 workflow](https://github.com/987N/dns-rule-builder/actions/runs/38129145252) 首次及第二次 attempt 均成功，使用 request UUID `63c11201-0f78-4fed-bcc3-9076141e5d3e`，`smoke_test=false`。
- [正式不可变 Release](https://github.com/987N/dns-rule-builder/releases/tag/rules-66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961) 的 ID 为 `66046223e20804e22a3bf27e09c73ae9871b9426cdd9a8aa95d1e4d11b21a961`，API 报告 immutable=true。重复构建复用同一 Release 和全部 8 个资产 ID/digest，没有创建替代版本。

下载 ZIP、manifest、SHA256SUMS、LICENSE/NOTICE 和审核摘要后，逐项验证外置校验及全部 40 个包内产物哈希、manifest 身份、Provider 成员映射和顺序。单独匿名下载公开 manifest 与包内副本一致。上游 v2fly revision 为已审核的 `c6adacf29cc4fb05e04651fcf03e00989ef9f797`，人工数据 revision 与发布代码 SHA 一致。

正式候选保持 17 个默认启用类别、16 个非空类别、1,564 条保留出处的类别规则、1,337 个独立语义/SmartDNS 项、20 个 Provider 片段、18 条经精确签名处置的 unsupported。272 项重叠已裁决，未解决冲突为 0。`review_required=false`、`publish_allowed=true`、`deployment_allowed=false`，只保留目标 SmartDNS/Mihomo 语义未确认和部署总开关关闭两项生产门禁。没有改写已发布 manifest。

本机独立证据位于忽略目录 `.cache/production-publish-download/`、`.cache/production-publish-request.json` 和 `.cache/production-publish-assets-{before,after}.json`。ZIP 内含全部许可和审核材料，不依赖这些本机证据。此收据证明代码、镜像与规则公开发布，不证明 NAS 安装、设备规则激活或真实国外服务访问。
