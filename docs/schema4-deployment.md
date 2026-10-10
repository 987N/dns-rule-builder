# Schema-v4 部署接口与本地模拟器

## 边界

Schema-v4 定义在新项目内，用来验证经过 manifest 校验的规则版本如何包装成 target payload，以及如何持久化双目标部署步骤。它不安装生产适配器、不建立 SSH/HTTP 设备连接、不修改旧仓库或现有 target schema。`config/targets-v4.yaml` 的 `deployment_enabled` 默认 `false`，代码调用也默认拒绝新部署。

生产角色固定为：

| target | 角色 | 可管理服务 |
|---|---|---|
| `nas-backup` | backup | SmartDNS |
| `vm104-main` | primary | SmartDNS 与唯一 Mihomo |

NAS 不创建第二个 Mihomo。Schema 3 的现有 target 固定 Provider 清单且只接受 canonical lowercase suffixes，无法表达动态 provider 和精确规则；不得把 schema-v4 产物直接交给旧接口。回退到 schema 3 前，必须先明确产物只含其可表达的规则，不能静默丢弃 EXACT 或动态 Provider。

## 校验协议

`target_manifest()` 重新验证候选 manifest 和所需文件 SHA-256，并按 target service 集合只输出对应服务文件。Mihomo primary 还要求 provider 配置、`RULE-SET` 顺序和 manifest `mihomo_rule_order` 完全一致；每个 provider 必须是受管理目录中的 `type: file`、`behavior: classical`、`format: yaml`。不支持的路径、软链接、缺失文件和摘要不符会拒绝构造 envelope。

SmartDNS 包含 EXACT 与 SUFFIX 目标表示。EXACT 的 `-.host` 行为及 Mihomo provider 行为已有固定候选二进制隔离证据，详情见[语义验证记录](target-semantics.md)。这些候选测试不确认 VM104/NAS 当前目标版本。

## 双目标状态机

`DeploymentCoordinator` 持久化 `stageboth → validateboth → backupactivate → mainactivate → succeeded`。激活之前先记录 intent；任何激活后失败都按反序读取真实版本并尝试回退，回退后再次 readback。进程重启时，`rolling_back` 只续做回退；`failed_rolled_back`、`rollback_failed`、`manual_intervention` 为终态，不能重试时重新激活。版本读取异常或回退 readback 不匹配会停止自动流程并标记人工介入。

`SimulatedTarget` 可在 stage、validate、activate、rollback 和 readback 等步骤注入失败，供本地测试恢复行为。模拟事件不能替代真实双设备 staging、配置验证、服务 reload、状态读取或 DNS 请求验收。即使状态机模拟成功，生产部署仍须另行确定防断联通道、超时与备份恢复材料。
