# Mihomo 策略组模板

`config/mihomo-policy-template.yaml` 描述逻辑选择组，不包含订阅节点或现网配置。它覆盖 `config/policies.yaml` 的每个策略组，并额外定义统一的 `Proxy` 选择组和 `regional_groups` 中声明的区域选择组。默认示例只有“香港节点”和“美国节点”：用途组及 Google、YouTube、Gemini、Telegram、Netflix、DisneyPlus、PrimeVideo、HBO、Hulu、Abema、Bahamut 等独立服务组可以直接选 `Proxy`、区域组或 `REJECT`，没有自动 `DIRECT` 回退；`Proxy` 也可选择这两个区域组或 `REJECT`。每个区域组使用 `__USER_SELECT_PROXY_NODE__` 作为待手动绑定的节点占位符，并保留 `REJECT`。具体现有区域组清单及节点成员要到迁移时逐项核对；此模板没有读取生产配置，也未选定真实节点。Gemini 服务默认映射到独立的 `Gemini` 组；`AI` 仍保留为合法逻辑组，供旧 rules-data 覆盖兼容。模板校验器检查区域名称重复/冲突、组引用、缺失策略组、循环、占位符作用范围和 DIRECT 成员。

生产模板保留区域组里的 `__USER_SELECT_PROXY_NODE__` 占位符，且明确返回 `deployable: false`。各用途和服务组仅提供手动选择路径，尚未选择真实生产节点。隔离测试可使用 `--test-proxy-reject` 将 `Proxy` 以及所有承载未绑定节点占位符的区域组固定到 `REJECT`，输出使用本机 `mixed-port: 17890` 与 `external-controller: 127.0.0.1:19090`，不定义出站代理。测试产物只用于隔离验证，规则顺序不变。

渲染器读取已经校验的规则版本，将该版本的 Providers 复制进新的 HomeDir，再按 manifest 的 `provider_map` 顺序生成规则。它逐项验证 provider 文件路径位于 HomeDir 内、manifest 顺序与规则顺序完全一致、自定义精确 Provider 排在普通 Provider 前，并在最后附加全局 `MATCH,DIRECT`。这个最终规则只处理未匹配的流量，不会把 `DIRECT` 加入用途组或独立服务组的候选成员。输出目录必须在版本目录之外，避免改变不可变候选的哈希内容。模板 YAML 与渲染代码属于构建身份输入；修改后重建会产生新的版本身份。

命令例：

```sh
dns-rule-builder --project-root /app render-policy <规则版本64位ID> \
  --output /tmp/mihomo-isolated/config.yaml --test-proxy-reject
```

命令会生成 `/tmp/mihomo-isolated/config.yaml` 和相邻的 `providers/` 文件，并报告 `deployable: false`。不带测试选项时，命令仍返回“未选择生产代理节点”的阻塞原因。生成过程不会读取订阅、连接设备或安装配置。上一版最终包检查已完成：20 个 Mihomo Providers 通过 v1.19.32 断网语法检查和 21 条代表性路由日志；SmartDNS 1.2026.v48.4.2 generated set 的 4 个断网 EXACT/SUFFIX 请求通过，2,623-witness 顺序 oracle 也通过。此处新增区域选择仍是本地未绑定模板，不代表重新验证了生产目标。历史 R5 全目录 syntax-only 审计不意味着所有类别可启用或每个服务顺序已获批。生产节点仍为占位符，家庭设备目标版本或生产兼容性未确认，因此此模板不能部署。
