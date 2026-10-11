# 生产迁移评审计划（P5）

此计划用于后续评审和授权，不执行现网修改。用户于 2026-10-11 确定保留区域代理分组，由各用途和服务策略组手动选择香港节点、美国节点等区域组。既有区域组的完整清单、内部节点和地区筛选配置留待生产迁移前核对保留，不在本地模板中虚构。用户明确要求暂不切换和部署。

## 当前阻断项

早期来源、共享依赖、Provider 顺序和许可门禁已在 G1–G4 本地实现中解决。默认 17 类别候选包含 1,564 条保留出处的类别规则、1,337 个独立语义项、20 个 Provider 片段和 18 条按精确签名审核后保留报告的 unsupported 输入；`review_required=false`、`publish_allowed=true`。部署仍仅被生产目标语义尚未确认及部署总开关关闭阻止。隔离二进制检查不证明 VM104/NAS 实际版本或配置集成兼容。详见[门禁收敛验收](gate-acceptance.md)。

生产迁移不得把静态 build 成功等同部署许可；必须先裁决上述输入/映射/许可证问题，得到 `deployment_allowed=true` 的规则候选，并单独确认安全的 target 维护窗口与回退路径。

## 分阶段操作步骤

1. **建立实时基线。** 从当前运行配置和版本记录 VM104 (`10.255.50.2`) 主 SmartDNS+唯一 Mihomo、NAS (`10.77.0.4`) 备用 SmartDNS、各服务实际 provider 路径、进程参数、持久卷、最近正常 DNS 请求及规则哈希。记录 target schema-3 清单、旧 Forgejo controller 状态和当前唯一写入者。不能以旧截图或历史镜像标签代替现场读取。
2. **准备可恢复材料。** 备份两端完整配置、规则文件、provider 目录、服务启动参数和旧 controller 数据；对备份计算哈希并在隔离位置确认可读。预备已知可用的 schema-3 规则与恢复步骤。没有可读备份和验证通路不得继续。
3. **核对策略组生产整合。** 按用户已确定的手动区域选择方式，核对[策略组映射草案](policy-group-mapping-draft.md)与现网区域组清单、内部节点配置、直连例外和实际业务需要。已完成的 G1–G4 来源与规则审核不重复作为待批准事项；后续出现新语法、共享依赖范围变化或策略冲突仍须审核。构建和 explain/diff 报告应准确说明每条规则的最终 provider 与目标策略组，任何新增未裁决 blocker 保持自动部署关闭。
4. **安装但不激活适配。** 在维护窗口以人工控制方式安装 schema-v4 target 适配，并保持其生产连接/部署开关关闭。不得由 GitHub CI 访问家庭网络。检查普通 UID、配置文件权限、路径 allowlist、动态 provider 装载路径和 EXACT/SUFFIX 表达。
5. **冻结旧写入者并完成双端 staging/validation。** 在第一次新端 activation 前，暂停旧 Forgejo controller 的定时部署和写入入口，保留只读 review；核实没有在途旧部署，记录其最后一次写入和规则哈希。之后按相同 release id stage NAS SmartDNS 与 VM104 SmartDNS+Mihomo，运行目标程序语法验证、manifest/文件 SHA 校验和策略顺序核验。任何一端失败且尚未激活时，可在明确确认新端未写设备后恢复旧写入；不得新旧并行写。
6. **手工串行激活。** 暂停期内由新管理器成为唯一写入者。明确设备间非原子窗口，先激活 NAS 备用 SmartDNS，再 readback 确认版本；之后激活 VM104 的 SmartDNS 与唯一 Mihomo，再 readback。DNSDist 仍只按既有主备关系使用两端 SmartDNS，不改 OPNsense、DNSDist upstream 或 Fake-IP 路由。
7. **最小真实验收。** 记录版本读回、A/AAAA、UDP/TCP、CNAME 及可用时的 HTTPS/SVCB/TXT 查询；再从代表性客户端验证 direct、代理策略、EXACT/SUFFIX 和例外行为。DNS 查询成功不代表所有家庭应用或故障场景已覆盖。
8. **失败回退。** 若备端激活失败，停止主端激活；若主端 staging/激活/readback 失败，先读取两端实际运行版本，再恢复至备份旧 release，回退后逐端 readback 并做有限 DNS 验证。只有两端旧版本均已读回、且新 manager 已停止写入后，才可恢复旧 controller 写入；若版本未知或回退无法确认，两个写入者都保持冻结，转人工处置。
9. **观察与收尾。** 人工部署和真实请求验收期间，继续只由新管理器写入，旧 controller 保持只读 review。观察稳定后再按单独批准停用旧容器并保留恢复材料。完成一段人工部署观察期后，若用户另行决定，再讨论启用自动部署。

## 范围外与决策保留

本次迁移不新增 NAS Mihomo，不触碰 OPNsense、Dnsmasq、DNSDist、DNS 上游和 Fake-IP 路由，不让 GitHub Actions 连接家中设备。手动选择区域组的方式已确定；既有区域组节点和实际配置仍待核对。任何真实部署、旧控制器停用、自动化启用都需要各自明确的生产变更授权。
