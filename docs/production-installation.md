# Production installation plan

This document is the D3 installation and first manual migration plan for the separate DockerWorkspace Compose stack. That work is authorized by the current plan; execution proceeds when its acceptance, credentials, and exact-installation gates are ready. This document is not evidence that the Builder was installed or that production rules were changed. No NAS, VM104, Caddy, old stack, or production filesystem operation was performed while preparing it.

The currently published Builder version is the 17-enabled-category baseline. It is not the version selected for the first production deployment: that deployment will use the expanded Release after its source/category review is complete, together with the final reviewed immutable image digest. Select both from the final acceptance receipt; this plan does not pin a category total.

The final reviewed Builder image digest and target-agent pack must be taken from the root acceptance receipt at installation time. The PAT has passed repository/workflow read checks and is already present at its individual secret path; the root preparation script changes only that file's owner/mode. Dedicated target keys and verified `known_hosts` must exist before the script can proceed. Their contents are never copied or printed.

## Current boundary

The candidate Compose stack is `DockerWorkspace/compose/stacks/dns-rule-builder/compose.yaml`. It requires `DNS_RULE_BUILDER_IMAGE` to be set to the final reviewed immutable GHCR digest, runs as `10001:10001` with a read-only root filesystem and no added capabilities, has no Docker socket, and stores application state only in `/vol1/1000/DockerData/dns-rule-builder`. It mounts the target configuration and each secret as a separate read-only file. The web listener is limited to `127.0.0.1:18081` and the existing `snell_default` gateway `172.27.0.1:18081`; the container listens on `8080` and exposes `/healthz` for its health check.

The existing `homegateway-review` stack, its state, and the current `rules.lclwf.date` route remain available until the replacement UI and full first-migration checks pass. No Caddy configuration has been changed. The prepared upstream for the existing hostname is:

```caddyfile
rules.lclwf.date {
    reverse_proxy 172.27.0.1:18081
}
```

This replaces the upstream in the existing site block; it must not be added as a duplicate block. The route remains on port `18080` until both targets have staged and validated the candidate, the NAS standby has activated and read back successfully, the VM104 primary has activated and read back successfully, and the bounded production requests pass. The main Agent then switches the existing route as the last migration step.

The tracked target template starts with `deployment_enabled: false` and uses the existing non-secret SSH endpoint baseline: `987N@10.77.0.4` for NAS and `rules-deploy@10.255.50.2` for VM104. It pins separate new SSH key files and one strict `known_hosts` file. Target login/key setup, confirmed host keys, and the constrained forced-command installation are concrete readiness gates; after they pass, the NAS preparation script performs the scoped privileged file setup under this plan. The user has prepared the GitHub PAT; a metadata-only check confirmed repository and rules-workflow GET access returned HTTP 200. The script normalizes that file to `10001:10001`/`0600` before mounting; its content is never read into local logs or recorded. The existing Web password may be mounted from the already-authorized single file `secrets/local/homegateway-review/web_password`; it must not be copied or replaced as part of this work.

Keep the existing nine internal regional url-test groups—香港自动策略、台湾自动策略、日本自动策略、韩国自动策略、新国自动策略、美国自动策略、英国自动策略、法国自动策略、德国自动策略—and keep `ProxySelect` as the generic default selector. Do not add synthetic node groups during migration.

The 14 reviewed Direct exceptions come from the audited live Direct provider and form a local target candidate overlay tied to each target's attestation, not a rewrite of the immutable public candidate release. On both targets the overlay must contain the paired SmartDNS `nameserver` and `address` exact/suffix `-` exceptions. Keep the public release ZIP's Google parent suffix intact. On NAS, preserve the base `address /domain-set:proxy-domains/#` behavior as well. Passing these configuration semantics does not prove real Google service access.

## Prepare the NAS materials

The reviewed installation pack is a local directory whose `SHA256SUMS` covers every regular file in the pack. It contains `image-digest.txt`, `target-agent-nas.yaml`, the target-agent installer, the NAS backup helper and its Python modules under `builder/`, and the three stack files plus `targets-v4.yaml` under `compose/`. The caller supplies both the reviewed SHA-256 of the `SHA256SUMS` file and the final GHCR digest from the root receipt; the script verifies the pack contents, the application config remains `deployment_enabled: false`, and the NAS target is exactly `nas-backup`. Do not run this with a mutable checkout or an earlier image digest.

Run the script first without `APPLY` to inspect its plan. `PACK_ROOT` below is the prepared pack directory on the NAS, and `PACK_SHA256` is the reviewed hash of its `SHA256SUMS` file:

```sh
DNS_RULE_BUILDER_PACK_ROOT='/path/to/reviewed/dns-rule-builder-install-pack' \
DNS_RULE_BUILDER_PACK_SHA256='<reviewed-SHA256SUMS-sha256>' \
DNS_RULE_BUILDER_IMAGE='ghcr.io/987n/dns-rule-builder@sha256:<reviewed-64-hex-digest>' \
sh scripts/install-nas-production.sh
```

After the current plan's validation gates and backups pass, run the reviewed command once as root, using the same pack/hash/digest values:

```sh
sudo env \
  APPLY=1 CONFIRM_DNS_RULE_BUILDER_INSTALL=nas-backup \
  DNS_RULE_BUILDER_PACK_ROOT='/path/to/reviewed/dns-rule-builder-install-pack' \
  DNS_RULE_BUILDER_PACK_SHA256='<reviewed-SHA256SUMS-sha256>' \
  DNS_RULE_BUILDER_IMAGE='ghcr.io/987n/dns-rule-builder@sha256:<reviewed-64-hex-digest>' \
  sh scripts/install-nas-production.sh
```

The script refuses to apply against a test root, requires the PAT and the separate NAS/VM104 identities plus host-pinned `known_hosts`, and checks existing destination conflicts before writing. Before any installation, it creates a root-only timestamped snapshot of the existing SmartDNS Compose stack, SmartDNS rules tree, old review UI state, old controller state, and Caddyfile, plus any existing authorization/helper and overlapping Builder files. The backup helper preserves symlinks and uid/gid/modes; SQLite files detected by their header are copied through the read-only SQLite backup API, integrity-checked, and have only the backup copy's WAL/SHM sidecars removed. It records regular-file hashes and byte counts, uid/gid/modes, symlink targets and counts in a receipt containing paths and metadata only, then installs a root-owned `0644` copy at `/etc/dns-rule-builder-preparation-receipt.json`. Missing required source paths or any backup/hash/SQLite failure stops before installation. It normalizes only the five individual new secret files to `10001:10001`/`0600`; the existing `web_password` stays at its original location and is never copied. It prepares the app data directory at `10001:10001`/`0700` without recursively changing existing contents, installs the three Compose files and reviewed target config, invokes the existing target-agent installer, and appends only the dedicated forced-command NAS public key. Different existing stack/config files are refused rather than overwritten.

This command only prepares files and metadata. It does not contact either target, activate rules, start a container, alter the old controller or edit/reload Caddy. It does not install Python packages or copy the workstation GitHub token. The next production steps remain under the main Agent's staged validation plan below.

## Prepare locally and on NAS

On the trusted workstation, inspect the final root gate receipt and use its final image digest. Do not reuse an earlier digest after the application or target helper changes. The installation script's default is dry-run:

```sh
DNS_RULE_BUILDER_IMAGE='ghcr.io/987n/dns-rule-builder@sha256:<reviewed-64-hex-digest>' \
  ./stacks/dns-rule-builder/install.sh
```

It only runs `docker compose config --quiet` and prints the single-service plan. It does not create directories, read secret values, contact targets, start containers, or change Caddy. Once the image, credentials, backup and target-agent gates are met, the main Agent may execute this plan with `APPLY=1 CONFIRM_DNS_RULE_BUILDER_INSTALL=dns-rule-builder`; the script checks each secret file, the pre-created state directory and `snell_default`, then runs only `docker compose up -d --pull never --no-deps dns-rule-builder`. If the NAS requires Docker privilege, the main Agent runs the same script through interactive sudo under this plan; do not use `sudo -n`.

The narrowly scoped stop/restart commands are:

```sh
docker compose --project-name dns-rule-builder \
  --project-directory /vol1/1000/DockerData/_compose/stacks/dns-rule-builder \
  -f /vol1/1000/DockerData/_compose/stacks/dns-rule-builder/compose.yaml \
  stop dns-rule-builder

docker compose --project-name homegateway-review \
  --project-directory /vol1/1000/DockerData/_compose/stacks/homegateway-review \
  -f /vol1/1000/DockerData/_compose/stacks/homegateway-review/compose.yaml \
  up -d --pull never --no-deps review
```

Use the old review start command only for rollback, and add the same explicit interactive sudo prefix if required by NAS policy. Do not stop or start a containing project as a whole.

The NAS preparation script creates the required app-data and individual-secret directories and applies their owner/mode without recursively changing existing data. New NAS/VM104 keys and host-key-pinned `known_hosts` must be supplied and independently reviewed before preparation. Do not copy the workstation GitHub token, reuse old target keys, mount the whole secret directory, or trust unverified `ssh-keyscan` output. The existing `web_password` stays at its current path and retains its existing metadata. Do not use `sudo -n` or work around its password prompt.

The NAS script backs up existing `authorized_keys`, installs the root-owned NAS helper through `install-target-agent.sh`, and appends the exact dedicated key with the forced command. VM104's target-key provisioning and helper installation remain a separate main-Agent operation. Neither path installs an unrestricted shell key.

## Scoped backups before changing service state

Create a timestamped, mode-`0700` backup directory on each affected host outside the repositories. Keep private production snapshots on the device or its protected backup location; never put target configuration or credentials into Git. Record hashes and ownership/modes for each archive, and verify each archive can be listed and extracted into a temporary directory before relying on it.

On the NAS, archive only the review and Builder management state, the current NAS SmartDNS inputs, the existing target helper, and the active Compose/config metadata:

- `/vol1/1000/DockerData/homegateway-review/` (old review history)
- `/vol1/1000/DockerData/homegateway-rules-controller/` if present (old controller state)
- `/vol1/1000/DockerData/dns-rule-builder/` if already present (new SQLite/release state)
- `/vol1/1000/DockerData/smartdns-backup/rules/` (current link and retained versions)
- `/vol1/1000/DockerData/_compose/stacks/smartdns-backup/config/smartdns.conf`
- `/usr/local/libexec/smartdns-backup-target`
- `/etc/dns-rule-target-v4.yaml` if already installed; it contains target paths and must remain root-owned and non-writable to the restricted target user
- the exact `homegateway-review`, `smartdns-backup`, and controller Compose/config files from `/vol1/1000/DockerData/_compose/`
- `/vol1/1000/DockerData/_compose/config/network/caddy/Caddyfile`

Record a redacted `docker inspect` summary for the old review, old controller, SmartDNS, and Caddy containers (image ID/digest, state, restart policy, published ports, mounts, network names); do not save or display container environment values. Preserve the existing anonymous SmartDNS config volume by recording its volume name and mount relationship; do not remove or recreate it. The Caddy certificate/account data under `/vol1/1000/DockerData/caddy/{data,config}` is outside this change and must not be copied or modified.

Before taking the final SQLite snapshot, freeze writes by stopping only the relevant management UI after the backup window is declared. Create a transaction-consistent backup of `/vol1/1000/DockerData/dns-rule-builder/controller.sqlite3` with SQLite's online backup API or SQLite `.backup`, then check `PRAGMA integrity_check`. Archive the quiesced application directory, including any WAL/SHM sidecars if present. The old review app keeps JSON review state under its own directory; archive that directory intact. Do not run a broad Docker volume/database backup.

On VM104, save a protected exact-path archive of these current inputs before installing or activating the new target helper:

- `/etc/router-services/smartdns.conf`
- `/etc/router-services/mihomo.yaml`
- `/etc/router-services/rules/` including the current and previous links and their version directories. The observed current rule version is `a4825b3e7243e8a14116f04553b6e1e2160111508a1f12a5c7ec59755459d5be`.
- `/etc/systemd/system/smartdns.service` and `/etc/systemd/system/mihomo.service`, plus any existing drop-ins for those units
- `/var/lib/router-services/mihomo/` and `/usr/local/lib/smartdns/` because they are referenced by the running service configuration
- the exact currently installed target helper and its SSH authorization metadata, once the main Agent has confirmed their live paths

`mihomo.yaml`, target helper configuration, SSH authorization files, and private host snapshots may contain sensitive deployment details. Keep their archive on the protected device/backup location, mode `0600` for files and `0700` for its directory, and never attach it to the repository or chat. On NAS, preserve the existing SmartDNS rules and helper too; its container stack does not use a systemd SmartDNS unit.

The live NAS SmartDNS config includes both `domain-set -name proxy-domains ...` and the existing base `address /domain-set:proxy-domains/#`; preserve the complete file and rules tree. The observed schema-v3 release on both targets is `a4825b3e7243e8a14116f04553b6e1e2160111508a1f12a5c7ec59755459d5be`. Archive its complete retained version directory and base config on both devices; a copy of the `current` symlink alone is insufficient. The main Agent's live baseline also recorded NAS SmartDNS rules at `/etc/smartdns/rules/current` inside the container and the existing helper at `/usr/local/libexec/smartdns-backup-target`. Confirm their current link targets and preserve those exact values in the backup receipt before switching. The new restricted SSH command is `dns-rule-target-v4`; preserve `/etc/dns-rule-target-v4.yaml` on each target if already installed. The implementation currently proposes `/usr/local/lib/dns-rule-target-v4/` for managed files and `/usr/local/sbin/dns-rule-target-v4` for its executable; the main Agent must confirm these final install paths and include them in the backup inventory before installation. Do not infer additional live paths from the source repository.

## Installation and first-migration sequence

This plan authorizes the main Agent to prepare and install the app and perform the first manual migration once the following readiness gates pass. It does not authorize skipping a failed check. Source/image acceptance, final digest, PAT and dedicated keys, host-key pinning, target helper/root-owned configuration, complete backups, target overlay review and exact install command must be ready first.

1. Confirm the final image digest and source receipt, then inspect the final Compose render, target template and the main Agent's exact installation plan. Keep `deployment_enabled: false` until credentials, helper/config allowlists and the source-derived Direct overlay are ready to stage.
2. Create and verify the scoped application, review-state, target configuration/rules/links/base-config/helper, and Caddy backups above. Confirm the SQLite snapshot integrity and preserve the complete old schema-v3 release and base configuration on both targets, not only the old `current` links.
3. Run the reviewed NAS preparation script in dry-run, then its single root preparation action after the gates above pass. The main Agent installs the VM104 helper/config through its separately reviewed root command. Run the Compose install script first in dry-run; after readiness checks pass, the main Agent may use the already-authorized `APPLY=1` path to start only `dns-rule-builder`. Verify digest, UID/GID, read-only root, dropped capabilities, binds, network and `/healthz`; verify Web login on HTTPS only when the route is eventually switched.
4. Build the immutable candidate release and apply the 14 Direct exceptions only as a local target configuration overlay bound to target attestation. Do not edit the published release ZIP or remove its Google parent suffix. On both targets, verify the overlay contains paired SmartDNS `nameserver` and `address` exact/suffix `-` entries for the 14 reviewed exceptions. On NAS also retain and verify its base `address /domain-set:proxy-domains/#` behavior. The main Agent's isolated SmartDNS checks have confirmed these exception semantics and the NAS base override; the complete target-agent transaction and merged production validation remain a separate gate.
5. Stage the same immutable release and reviewed local overlay on both targets, then validate both targets. Compare release IDs, artifact hashes, target-attestation/overlay IDs and target validation results before any activation.
6. Activate the NAS standby first. Read back target `status`, `current` and `previous`, confirm the active release and SmartDNS service/container state, then run its bounded DNS checks.
7. Only after NAS readback succeeds, activate the VM104 primary. Read back target status, verify SmartDNS and Mihomo release IDs plus service states, and run bounded DNS/proxy requests. If any readback is ambiguous or a request fails, stop the migration and use the recorded full rollback path.
8. Run the planned limited real request set across the intended local DNS/proxy paths, including the Direct-exception semantics. Passing generated config and synthetic/parser checks alone is not live network evidence. Even after these bounded checks, do not claim that real Google service access has been proved.
9. Only after both targets have staged and validated, NAS activation/readback, VM104 activation/readback, and the limited real requests all pass, switch the existing `rules.lclwf.date` site block to `172.27.0.1:18081`. Validate before reload with `docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`, reload only the existing Caddy container through its established `SIGUSR1` method, then verify HTTPS login and the management UI. Do not expose port `18081` on the NAS LAN IP or add router port forwarding.
10. Keep the old review stack, state, base configs and schema-v3 versions available through the agreed observation period. Stop/restart only named services; do not use a broad project `down`, remove volumes, or restart the network stack.

The first manual migration is authorized by this plan after these gates pass; no additional approval question is needed for those already-authorized steps. No release publication, public image push, recurring automation, broad deployment, or unrelated network change is implied.

## Rollback

If only the Builder UI fails, use the scoped `stop dns-rule-builder` command above; do not remove its persistent directory, secrets, image, or volume. Restore the prior Caddyfile backup if the hostname was switched, run Caddy validation, and reload only the existing Caddy container. Then run the scoped old-review `up -d --pull never --no-deps review` command above with its pinned image and existing credentials/state. Do not use `docker compose down -v`.

If a new-agent activation has occurred, use its reviewed `rollback` operation only when its recorded previous release is the intended rollback point, then read back both target statuses. The initial pre-migration schema-v3 version may not be addressable through the new agent's managed release store. To return to that baseline, restore the full protected snapshot on both ends: base SmartDNS/Mihomo configuration, the complete old schema-v3 release directory, `current`/`previous` links, and helper/authorization metadata with original ownership and modes. On VM104, restore the recorded units/drop-ins, run `systemctl daemon-reload` only if they changed, and restart only `smartdns.service` and `mihomo.service`. On NAS, restore the full SmartDNS config and `rules/` tree (including `domain-set` and the base `address /domain-set:proxy-domains/#`), then recreate only `smartdns-backup` with the existing Compose stack's allowlisted `up -d --no-deps --force-recreate --pull never smartdns-backup` action; preserve its anonymous SmartDNS config volume. Finally restore the Caddyfile if switched, validate and reload only Caddy, then verify both targets' current release IDs and bounded DNS/proxy requests. Do not treat restoring a symlink alone as a full rollback.

No rollback has been run as part of D3. Archive presence and documented recovery steps do not prove that a production rollback has been exercised.
