# 生产迁移评审计划（P5）

此计划用于后续评审和授权，不执行现网修改。策略组成员、节点选取和实际规则映射仍由用户在策略组重整阶段决定；当前配置值只是逻辑策略名，不是生产路由决策。

## 当前阻断项

主 Agent 对锁定上游构建的验收记录了 1,098 条服务规则、1,069 个全局语义项、65 条 unsupported 项、24 个重叠项。构建报告仍阻止自动部署：YouTube/Google provider precedence 形成循环；有 1 种新规则类型未裁决；5 个已知共享域名尚待审核。许可审查也未批准公开再发布第三方规则产物，正常规则 Release 发布门禁保持关闭。候选二进制语义虽完成隔离测试，VM104/NAS 实际版本仍需基线核对。详见候选 build reports 和[语义验证记录](target-semantics.md)。

生产迁移不得把静态 build 成功等同部署许可；必须先裁决上述输入/映射/许可证问题，得到 `deployment_allowed=true` 的规则候选，并单独确认安全的 target 维护窗口与回退路径。

## 分阶段操作步骤

1. **建立实时基线。** 从当前运行配置和版本记录 VM104 (`10.255.50.2`) 主 SmartDNS+唯一 Mihomo、NAS (`10.77.0.4`) 备用 SmartDNS、各服务实际 provider 路径、进程参数、持久卷、最近正常 DNS 请求及规则哈希。记录 target schema-3 清单、旧 Forgejo controller 状态和当前唯一写入者。不能以旧截图或历史镜像标签代替现场读取。
2. **准备可恢复材料。** 备份两端完整配置、规则文件、provider 目录、服务启动参数和旧 controller 数据；对备份计算哈希并在隔离位置确认可读。预备已知可用的 schema-3 规则与恢复步骤。没有可读备份和验证通路不得继续。
3. **先解决规则决策。** 用户审定[策略组映射草案](policy-group-mapping-draft.md)、YouTube/Google 循环、共享域名、unsupported 规则、直连例外、宽泛类别以及许可证/来源声明。构建和 explain/diff 报告应能准确说明每条规则的最终 provider 与目标策略组。任何未裁决 blocker 保持自动部署关闭。
4. **安装但不激活适配。** 在维护窗口以人工控制方式安装 schema-v4 target 适配，并保持其生产连接/部署开关关闭。不得由 GitHub CI 访问家庭网络。检查普通 UID、配置文件权限、路径 allowlist、动态 provider 装载路径和 EXACT/SUFFIX 表达。
5. **冻结旧写入者并完成双端 staging/validation。** 在第一次新端 activation 前，暂停旧 Forgejo controller 的定时部署和写入入口，保留只读 review；核实没有在途旧部署，记录其最后一次写入和规则哈希。之后按相同 release id stage NAS SmartDNS 与 VM104 SmartDNS+Mihomo，运行目标程序语法验证、manifest/文件 SHA 校验和策略顺序核验。任何一端失败且尚未激活时，可在明确确认新端未写设备后恢复旧写入；不得新旧并行写。
6. **手工串行激活。** 暂停期内由新管理器成为唯一写入者。明确设备间非原子窗口，先激活 NAS 备用 SmartDNS，再 readback 确认版本；之后激活 VM104 的 SmartDNS 与唯一 Mihomo，再 readback。DNSDist 仍只按既有主备关系使用两端 SmartDNS，不改 OPNsense、DNSDist upstream 或 Fake-IP 路由。
7. **最小真实验收。** 记录版本读回、A/AAAA、UDP/TCP、CNAME 及可用时的 HTTPS/SVCB/TXT 查询；再从代表性客户端验证 direct、代理策略、EXACT/SUFFIX 和例外行为。DNS 查询成功不代表所有家庭应用或故障场景已覆盖。
8. **失败回退。** 若备端激活失败，停止主端激活；若主端 staging/激活/readback 失败，先读取两端实际运行版本，再恢复至备份旧 release，回退后逐端 readback 并做有限 DNS 验证。只有两端旧版本均已读回、且新 manager 已停止写入后，才可恢复旧 controller 写入；若版本未知或回退无法确认，两个写入者都保持冻结，转人工处置。
9. **观察与收尾。** 人工部署和真实请求验收期间，继续只由新管理器写入，旧 controller 保持只读 review。观察稳定后再按单独批准停用旧容器并保留恢复材料。完成一段人工部署观察期后，若用户另行决定，再讨论启用自动部署。

## 范围外与决策保留

本次迁移不新增 NAS Mihomo，不触碰 OPNsense、Dnsmasq、DNSDist、DNS 上游和 Fake-IP 路由，不让 GitHub Actions 连接家中设备。策略组真实成员、节点和优先级需要用户确认；P5 不代用户锁定这些值。任何真实部署、旧控制器停用、自动化启用都需要各自明确的生产变更授权。
