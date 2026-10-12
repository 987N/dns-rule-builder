# 新增类别发布与管理器更新收据 — 2026-10-12

用户授权提交、推送与部署。本轮代码与正式规则已经发布，NAS 管理器已更新，新规则尚未完成双端准备和激活。NAS helper 的 root 更新需要管理员在自己的终端输入 sudo 密码；Codex 执行会话未显示给用户，桌面控制工具禁止操作 Terminal。人工部署开关保持关闭，未把管理器健康误记为规则已激活。

## 已完成

- 代码提交并推送：`d22d97443403dd7e7938b6b797e3e20be12135d2`，本地完整测试 191 项通过。
- [测试 CI](https://github.com/987N/dns-rule-builder/actions/runs/38202527344)、[镜像 CI](https://github.com/987N/dns-rule-builder/actions/runs/38202527416)、[正式规则发布](https://github.com/987N/dns-rule-builder/actions/runs/38202558576)成功。
- 正式规则：[rules-bf54299122de955e3e048add2621ee61f39c7975637c93ca80dc0c541fe4edc2](https://github.com/987N/dns-rule-builder/releases/tag/rules-bf54299122de955e3e048add2621ee61f39c7975637c93ca80dc0c541fe4edc2)，数据提交 `0fae7627a84fd2f4d097ba472995ce21f29bfc77`。外置 SHA256SUMS、包内产物、完整 manifest、代码与数据身份通过校验；85 个启用类别、3,944 条分类规则、3,546 个独立语义、100 个 Provider、0 个未解决冲突。
- NAS 运行镜像：`ghcr.io/987n/dns-rule-builder@sha256:c98f3c689bb2866323b388cd123d4f3cb7f36efd6664d2f5202caa14167ca030`。Linux/amd64 和 OCI revision 对应上述代码提交；仅重建 `dns-rule-builder`，容器 healthy，HTTPS `rules.lclwf.date/healthz` 返回 `ok`。
- 管理器专用凭据实际触发的[构建](https://github.com/987N/dns-rule-builder/actions/runs/38202937953)成功，复用同一正式 Release；管理器任务 `63ff4b3a-ef41-481a-bd38-444acaef42e0` 成功，候选已提升至 `bf542991…`，`stale=false`，已保存的数据版本一致。任务收据在 NAS `/data/receipts/build-20261012-d22d974.json`（容器内路径）。
- VM104 只更新 `target_runtime.py` 和安装清单，属主 root、模式 `0644`，新 SHA256 `4fbb36365db9626e98dc1b644b5b31a4da82183448bbad51477634495c29bb07`。没有重启 SmartDNS/Mihomo，没有变更 target 配置、入口或访问控制。

## 恢复材料

- 管理器在线 SQLite 备份 `/vol1/1000/DockerData/dns-rule-builder/backups/pre-update-20261012-d22d974/controller.sqlite3`，完整性检查 `ok`，同目录保留 candidate/previous 链接目标。
- NAS 栈和 target 配置备份 `/vol1/1000/DockerData/_compose/stacks/dns-rule-builder/pre-update-20261012-d22d974/`，目录 `0700`，含原 image-digest、Compose、install 脚本与 target 配置。原管理器镜像 digest 为 `66f3b197be352fb1f2a379e21a4512ef60fb5b4ac6d0ff53bfd4a1a5d0196721`；需要恢复管理器时使用旧 digest 和同一 Compose 单服务重建，保留持久化数据。
- VM104 helper 的旧模块、清单与入口保留在 root-only `/var/backups/dns-rule-builder/helper-update-20261012-d22d974/`。当前活动规则未改变，不需要为本轮管理器更新回退 DNS 规则。

## 尚未完成的规则部署

双端受限 SSH 实际 status 仍读回 `215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea`；NAS helper SHA256 仍为 `e63dd7d6e0cf0008b207776af79d17b5b97afdaaa6752e53e172d00b08088952`。必须先完成已暂存且哈希锁定的 NAS helper 更新，再对新 Release 执行双端准备验证，短时开启人工开关并按备用 → 主路径激活，随后关闭开关、读回并检查真实最小请求。新 Release 的公开 manifest 保留恰好两个标准门禁：目标语义验证与人工部署总开关；来源审核与发布门禁已通过。

用户需要在自己的终端运行：

```sh
ssh -t 987N@10.77.0.4 'sudo /usr/bin/python3 /tmp/dns-helper-update-d22d974/update-helper.py'
```

该脚本核对旧模块哈希、创建 root-only helper 备份，仅原子替换模块和安装清单；不重启服务、不打开开关、不激活规则。密码只在 sudo 提示处输入，不进入聊天、日志或版本库。Codex 中未完成的 sudo 会话已取消。

本轮规则激活前留存了网关、VM104 主 SmartDNS 与 NAS 备用的 4 个代表域名 UDP/TCP A 共 24 次基线查询：22 次 NOERROR，NAS 对 `music.youtube.com` 的两次 NXDOMAIN 符合既有拒绝行为。这是更新前的有限基线，没有执行新规则激活后的 DNS 或真实业务验收；不覆盖全家客户端或故障矩阵。
