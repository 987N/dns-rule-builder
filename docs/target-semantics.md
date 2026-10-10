# SmartDNS / Mihomo 候选版本隔离语义验证

记录 2026-10-10 的本地隔离实验。实验使用固定镜像摘要和合成 `example.test` 配置；容器 `--network none`，不连接 NAS、VM104 或家庭服务。实验回答目标语法在这些候选二进制中的含义，不证明现网运行版本或生产双端集成。

## 固定镜像与复现

| 组件 | 镜像摘要 | 二进制版本 | 验证范围 |
|---|---|---|---|
| SmartDNS | `pikuzheng/smartdns@sha256:2144b28fc74db476661d5a12c5b741eb1316451bb703f9dec9e1794f98eb28ff` | `1.2026.v48.4.2` | Domain Set exact/suffix 查询行为 |
| Mihomo | `metacubex/mihomo@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e` | `v1.19.32` | 本地 classical YAML provider、规则顺序、SAFE_PATHS 与拒绝出口时的匹配日志 |

本项目的可复现合成测试：

```sh
tools/research/target-semantics-probe.sh
```

脚本只挂载仓库中的自编 `example.test` 夹具。若要额外检查一个已生成的完整 Mihomo 候选目录，可传入目录路径；目录应包含 `config.yaml` 与 `providers/`：

```sh
FULL_MIHOMO_CANDIDATE=/path/to/already-built-candidate tools/research/target-semantics-probe.sh
```

完整 14-provider 候选配置曾由主 Agent 从本地忽略缓存中加载到固定 Mihomo 镜像并通过 `-t`；本轮也通过脚本的可选路径复跑成功：`FULL_MIHOMO_CANDIDATE=.cache/target-probe/mihomo-full tools/research/target-semantics-probe.sh`。缓存被 `.gitignore` 排除，这些第三方转换规则文件不复制进公开源码树。合成夹具及默认脚本路径不包含该完整输出。

SmartDNS 实验配置在 `research/fixtures/target-probe/smartdns.conf`，测试规则在 `test.list`。实测 `-.api.example.test` 只命中精确主机，`child.api.example.test` 回落到父域测试规则；裸 `suffix.example.test` 对根域和子域均匹配。

Mihomo 合成 provider 测试分别以 `DOMAIN,api.example.test` 和 `DOMAIN-SUFFIX,example.test` 验证 EXACT 与 SUFFIX，并用两个最小手写 provider 覆盖 `gemini.google.com` 与 `google.com`。代理策略设为 `REJECT`，容器断网；日志表明 exact、parent、Gemini、Google 各由预期 provider 命中，Gemini provider 排在 Google 前。`HomeDir` 之外的 provider 路径在未设置 `SAFE_PATHS` 时 `-t` 失败，授权 `SAFE_PATHS=/outside` 后通过。

## 结论边界

候选版本中上述语义和路径检查已实测；没有确认 VM104、NAS 当前实际镜像摘要，也没有向家庭设备部署规则或发起家庭网络请求。目标 schema-v4 target handler 和双端事务仍是本地模拟器。`config/settings.yaml` 中 `target_semantics_verified`、`deployment_enabled` 保持关闭；不能据此认定生产已就绪。
