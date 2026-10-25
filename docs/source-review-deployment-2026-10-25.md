# 来源审核与规则恢复部署记录 — 2026-10-25

## 审核与发布

用户授权审核此前的来源门禁，并继续提交、推送和部署。审核依据及精确处置见 [来源审核记录](source-review-2026-10-25.md)。

- `ec517671da2024613e3c11352d4b5d5e705dadd1`：批准 Microsoft/Xbox 移除 ASOBO 后收窄的引用范围，MIT 许可未变；精确排除 OpenAI 新 Sentry 主机和缺少一手归属证据的 Twitch CloudFront 主机。没有增加共享后缀白名单或关闭门禁。
- 本地隔离重建绑定 rules-data `390d777b0cb504aca260e395b56e7efbd142451a`、V2Fly `664598567e92d797bea11bb83cf939d1f37e9512`。发布门禁为零，两条排除均实际命中且不在编译结果中。
- 正式 rules workflow `40189356425` 成功，发布 `81b92ed68c6eb4f2d6f5e936d3789deb3ab561896d51f8d2f2a055efd9e7a58c`。相对原运行版本 `db3579959a275078edad73beab92bbe398139b123d806db290adce636b934487` 新增 24 条、移除 6 条；没有策略或类别策略映射变化。

## 离线校验路径修复

首次生成任务 `49264d5a-5a3b-4107-9a9f-402e9cab9088` 在准备阶段失败，未激活：Mihomo 离线验证使用隔离 HomeDir，原 `SAFE_PATHS` 未覆盖现有节点订阅缓存 `/var/lib/router-services/mihomo/.config/mihomo/providers`。

确认该目录位于实际服务 HomeDir `/var/lib/router-services/mihomo` 内、相关路径不是符号链接、缓存文件存在后，仅将这个现有目录加入 VM104 目标描述文件的**离线校验**白名单。没有更改运行服务的 HomeDir、SAFE_PATHS、systemd unit、订阅内容或网络权限。

在持久化修改前，以内存中的新白名单完成真实 Mihomo 校验。随后备份目标描述和活动保护记录，验证服务配置哈希、原版本及 unit guard 均未变化，仅重新确认 `target_agent_config` 的 guard 摘要；没有改写运行版本、current 指针或规则配置哈希。目标描述文件 SHA-256 从 `052b95b8e944c8edd94058802fcb3000d5bf32bbfd5cc1c42b60b1ededed0457` 更新为 `159179076aa62d123ea1c053a6fdcc9d55e52ea3ccfce4a01d77c9c4d0f97881`。

备份与失败准备材料保留在 VM104 root-only `/var/backups/dns-rule-builder/validation-path-20261025-ec51767/`。未激活的旧准备目录移入该备份，随后通过正式管理器接口重新准备同一版本。

- 准备任务 `33bbb476-0399-4cd6-a16e-a952e2a24aad` 成功。
- 准备请求 `1ae46ab9-caee-4475-9a5c-a4c8d06ce8ac` 双端 validated。
- 人工部署任务 `984a6ef3-2ff4-5ebd-a66d-5417ac13915b` 成功，顺序为 NAS 备用端、VM104 主端；两端均读回 `81b92ed6…`。

## 回退兼容与最终版本

检查发现，旧回退逻辑会把旧版本的目标描述 guard 写回活动记录，从而拒绝已核验过的本地校验配置。`d6d26c7b732a627c1832413cb76a72eb33f6b995` 修复为：回退规则时保留当前已验证的目标契约，并在写入前再次检查契约未变化。新增回归覆盖“目标维护变更后部署新版本，再回退旧规则”的场景。全仓 282 项测试通过，只有既有 Starlette/httpx 弃用警告；GitHub tests/image 工作流也通过。

仅更新 VM104 的 `target_runtime.py` 和安装清单，安装清单同步记录实际目标描述文件摘要。新 runtime SHA-256 为 `7c1326d82c312a3fd77bfca2d0748f5ff01346212678abbacfacc0ccbddd3735`。旧模块与安装清单保存在 `/var/backups/dns-rule-builder/rollback-contract-20261025-d6d26c7/`。NAS helper 未更换，NAS 管理器继续使用此前已部署的 `da11e2f` 镜像；本次审核账本由 GitHub 构建流程读取，回退修正由远端 helper 执行。

最终 rules workflow `40190439642` 成功，正式版本为：

`98b085e656944116b7537a5bf22245b3ac7f18a897be1397b90f8dd4cbc7850a`

- 生成任务：`c9f92c84-93ed-4de3-bd59-509e0185cea3`。
- 双端准备请求：`5550ecf4-008d-42e4-8732-bdeb9d1eeaab`，validated。
- 相对刚生效的 `81b92ed6…`：规则新增、移除、策略变化均为零；仅程序基线变化需要人工确认。
- 依据本次用户授权，通过正式人工接口确认；任务 `abfd0910-64c6-59c3-89cd-055743737d59` 成功。主备两端均读回最终版本。

## 最终验收

北京时间 2026-10-25 19:53 后的检查结果：

- NAS 管理器与备用 SmartDNS healthy，restart count 均为 0；管理器 HTTPS `/healthz` 返回 `ok`，无活动任务。
- VM104 SmartDNS/Mihomo 均 active，无 activation、rollback 或 strategy intent；独立 target status 返回最终版本。
- 节点订阅规范化 SHA-256 仍为 `e132e50e43a261f46ad25d15ce3136add3ea3c6f6d1ecb07c2b88a521297a2b1`，与部署前相同。规则更新保留了实际订阅配置。
- VM104 `10.255.50.2:53053` 和客户端 DNS CT106 `10.255.50.3:53`，百度/Google 的 UDP、TCP 共 8 次查询均为 NOERROR。百度返回真实地址，Google 返回 Fake-IP。
- 自动部署开关保持 `automatic_deployment_enabled: true`、维护禁用为 `false`；旧 `deployment_enabled: false` 没有改动。

本轮验证了正式生成、审核门禁、双目标准备、人工应用、服务读回和有限 DNS 请求；未实际执行生产回退、下一次定时任务或全客户端/故障切换演练。
