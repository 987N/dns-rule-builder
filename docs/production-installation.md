# Production installation plan

This document records the D3 installation and first manual migration for the separate DockerWorkspace Compose stack. The migration has completed target activation and bounded DNS/HTTP checks; see the [production migration receipt](production-migration-receipt-2026-10-11.md) for the authoritative command results and current Caddy/old-review status. The managed production rules are no longer on the legacy version.

The formal Builder Release `rules-215d045c6b3fb94f017bba7721e22f1c774824d1564d530baccb5213234c09ea` is active on both NAS and VM104. It was built from code `c20c91e` and image `ghcr.io/987n/dns-rule-builder@sha256:66f3b197be352fb1f2a379e21a4512ef60fb5b4ac6d0ff53bfd4a1a5d0196721`. It contains 60 enabled categories, 59 nonempty categories, 2,416 domain rules and 63 Providers. Main and gateway checks returned 84 DNS `NOERROR` results; 22 EXACT/SUFFIX probes passed, and limited HTTPS requests returned Baidu `200`, Google `204`, and OpenAI `401`. These are bounded checks, not universal service or client coverage.

The deployment switch is back to `false`; daily builds are enabled to download releases. NAS retains the earlier `442` helper, while VM104 has the recorded `f336e50` helper snapshot. The later source SmartDNS idempotency fix has not been installed on either target; the active release and checks above do not imply that the newest helper source is deployed.

The final reviewed Builder image digest and target-agent pack must be taken from the root acceptance receipt at installation time. The PAT has passed repository/workflow read checks and is already present at its individual secret path; the root preparation script changes only that file's owner/mode. A separate `admin_password` file is required for the Builder UI and must contain at least 16 non-whitespace characters after trimming its ends. Dedicated target keys and verified `known_hosts` must exist before the script can proceed. Their contents are never copied or printed.

## Current boundary

The candidate Compose stack is `DockerWorkspace/compose/stacks/dns-rule-builder/compose.yaml`. It requires `DNS_RULE_BUILDER_IMAGE` to be set to the final reviewed immutable GHCR digest, runs as `10001:10001` with a read-only root filesystem and no added capabilities, has no Docker socket, and stores application state only in `/vol1/1000/DockerData/dns-rule-builder`. It mounts the target configuration and each secret as a separate read-only file. The web listener is limited to `127.0.0.1:18081` and the existing `snell_default` gateway `172.27.0.1:18081`; the container listens on `8080` and exposes `/healthz` for its health check.

The `homegateway-review` state and its prior deployment materials are retained for rollback. The final status of the existing `rules.lclwf.date` route and whether the old review container has been stopped are recorded in the production migration receipt. The intended upstream for the existing hostname is:

```caddyfile
rules.lclwf.date {
    reverse_proxy 172.27.0.1:18081
}
```

This replaces the upstream in the existing site block; it must not be added as a duplicate block. The main Agent performs this only after both targets have activated and read back successfully and bounded production requests pass. For the single-file Caddy bind mount, preserve the existing file inode by writing the reviewed content in place, validate the configuration, then reload the existing Caddy container with `SIGUSR1`; do not atomically replace the bound file. Consult the receipt for the completed cutover result.

The tracked target template is returned to `deployment_enabled: false` after activation and uses the existing non-secret SSH endpoint baseline: `987N@10.77.0.4` for NAS and `rules-deploy@10.255.50.2` for VM104. Daily builds are enabled to download releases; deployment still requires the explicit switch. It pins separate SSH key files and one strict `known_hosts` file. The NAS preparation script normalizes the PAT and independent Builder `admin_password` to `10001:10001`/`0600`; it checks the password's trimmed length without printing or recording its value. The old `homegateway-review/web_password` is neither mounted nor changed.

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

The script refuses to apply against a test root, requires the PAT, an independent Builder `admin_password` of at least 16 characters after trimming, and the separate NAS/VM104 identities plus host-pinned `known_hosts`; it checks existing destination conflicts before writing. Before any installation, it creates a root-only timestamped snapshot of the existing SmartDNS Compose stack, SmartDNS rules tree, old review UI state, old controller state, and Caddyfile, plus any existing authorization/helper and overlapping Builder files. The backup helper preserves symlinks and uid/gid/modes; SQLite files detected by their header are copied through the read-only SQLite backup API, integrity-checked, and have only the backup copy's WAL/SHM sidecars removed. It records regular-file hashes and byte counts, uid/gid/modes, symlink targets and counts in a receipt containing paths and metadata only, then installs a root-owned `0644` copy at `/etc/dns-rule-builder-preparation-receipt.json`. Missing required source paths or any backup/hash/SQLite failure stops before installation. It normalizes all six individual Builder secret files to `10001:10001`/`0600`; the old `homegateway-review/web_password` stays in place with its original owner and mode and is never copied, mounted or modified. It prepares the app data directory at `10001:10001`/`0700` without recursively changing existing contents, installs the three Compose files and reviewed target config, invokes the existing target-agent installer, and appends only the dedicated forced-command NAS public key. Different existing stack/config files are refused rather than overwritten.

This command only prepares files and metadata. It does not contact either target, activate rules, start a container, alter the old controller or edit/reload Caddy. It does not install Python packages or copy the workstation GitHub token. The next production steps remain under the main Agent's staged validation plan below.

## Prepare locally and on NAS

On the trusted workstation, inspect the final root gate receipt and use its final image digest. Do not reuse an earlier digest after the application or target helper changes. The installation script's default is dry-run:

```sh
DNS_RULE_BUILDER_IMAGE='ghcr.io/987n/dns-rule-builder@sha256:<reviewed-64-hex-digest>' \
  ./stacks/dns-rule-builder/install.sh
```

It only runs `docker compose config --quiet` and prints the single-service plan. It does not create directories, read secret values, contact targets, start containers, or change Caddy. Once the image, credentials, backup and target-agent gates are met, the main Agent may execute this plan with `APPLY=1 CONFIRM_DNS_RULE_BUILDER_INSTALL=dns-rule-builder`; the script checks each individual secret is a regular non-symlink file with mode `0600`, plus the pre-created state directory and `snell_default`. It does not require the invoking account to read files owned `10001:10001`; the actual container startup and login are the proof that the app can use its mounted secrets. The script then runs only `docker compose up -d --pull never --no-deps dns-rule-builder`. If the NAS requires Docker privilege, the main Agent runs the same script through interactive sudo under this plan; do not use `sudo -n`.

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

The NAS preparation script creates the required app-data and individual-secret directories and applies their owner/mode without recursively changing existing data. Supply an independent Builder `admin_password` with at least 16 characters after trimming leading/trailing whitespace. New NAS/VM104 keys and host-key-pinned `known_hosts` must be supplied and independently reviewed before preparation. Do not copy the workstation GitHub token, reuse old target keys, mount the whole secret directory, or trust unverified `ssh-keyscan` output. The existing `homegateway-review/web_password` stays at its current path and retains its existing metadata; it is not used by Builder. Do not use `sudo -n` or work around its password prompt.

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

The pre-migration NAS SmartDNS config included both `domain-set -name proxy-domains ...` and the existing base `address /domain-set:proxy-domains/#`; the complete file and rules tree were included in the protected backup. The pre-migration schema-v3 release on both targets was `a4825b3e7243e8a14116f04553b6e1e2160111508a1f12a5c7ec59755459d5be`; its retained version directories and base config are rollback material, and a copy of the old `current` symlink alone is insufficient. The baseline recorded NAS SmartDNS rules at `/etc/smartdns/rules/current` inside the container and the existing helper at `/usr/local/libexec/smartdns-backup-target`. Current helper paths, hashes and the protected backup are recorded in the migration receipt; do not infer device paths from the source repository. The later SmartDNS idempotency source fix has not yet been installed on either target.

## Installation and first-migration sequence

The first manual migration was authorized by this plan and has activated the same accepted Release on both targets. Its detailed record is in the [production migration receipt](production-migration-receipt-2026-10-11.md); the sequence below documents the controls and evidence boundaries and must not be replayed as a new installation.

1. Confirmed the final image digest and source receipt, Compose render, target template, credential ownership and complete target-helper configuration before deployment.
2. Created protected backups for the application, review state, target configuration/rules/links/base configuration/helper and Caddy. The SQLite snapshot passed integrity validation; the complete old schema-v3 release and base configuration were preserved on both targets.
3. Prepared NAS, installed VM104 helper files, and started the management service using the reviewed image. The installation result and checks are recorded in the receipt.
4. Kept the published Release immutable and applied the 14 reviewed Direct exceptions only as target-attested local overlays. Paired SmartDNS `nameserver` and `address` exact/suffix `-` entries and the NAS base `address /domain-set:proxy-domains/#` were preserved.
5. Staged and validated the same Release and reviewed overlay on both targets, comparing release IDs, artifact hashes and target attestations.
6. Activated NAS as standby and read back its release and service state.
7. Activated VM104 as primary and read back SmartDNS/Mihomo release and service state.
8. Verified 84 DNS `NOERROR` results across main/gateway checks, 22 EXACT/SUFFIX probes, and bounded HTTPS responses from Baidu (`200`), Google (`204`) and OpenAI (`401`). These checks do not prove all clients, services or failure scenarios.
9. The main Agent is applying the existing `rules.lclwf.date` cutover after the above checks; the receipt is authoritative for whether it completed. Write the bound Caddyfile in place and preserve its inode, validate with `docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`, then reload the existing Caddy container with `SIGUSR1`. The old-review container state is also recorded in the receipt. Do not expose port `18081` on the NAS LAN IP or add router port forwarding.
10. Retain the old review state, base configs and schema-v3 versions as rollback material through the observation period. The migration receipt records whether the old review container is stopped. Do not use a broad project `down`, remove volumes, or restart the network stack.

The release's deployment switch is now `false`, while daily builds remain enabled to download releases. No claim is made that the newer SmartDNS idempotency source fix is installed on the devices.

## Rollback

If only the Builder UI fails, use the scoped `stop dns-rule-builder` command above; do not remove its persistent directory, secrets, image, or volume. Restore the prior Caddyfile backup if the hostname was switched, run Caddy validation, and reload only the existing Caddy container. Then run the scoped old-review `up -d --pull never --no-deps review` command above with its pinned image and existing credentials/state. Do not use `docker compose down -v`.

If a new-agent activation has occurred, use its reviewed `rollback` operation only when its recorded previous release is the intended rollback point, then read back both target statuses. The initial pre-migration schema-v3 version may not be addressable through the new agent's managed release store. To return to that baseline, restore the full protected snapshot on both ends: base SmartDNS/Mihomo configuration, the complete old schema-v3 release directory, `current`/`previous` links, and helper/authorization metadata with original ownership and modes. On VM104, restore the recorded units/drop-ins, run `systemctl daemon-reload` only if they changed, and restart only `smartdns.service` and `mihomo.service`. On NAS, restore the full SmartDNS config and `rules/` tree (including `domain-set` and the base `address /domain-set:proxy-domains/#`), then recreate only `smartdns-backup` with the existing Compose stack's allowlisted `up -d --no-deps --force-recreate --pull never smartdns-backup` action; preserve its anonymous SmartDNS config volume. Finally restore the Caddyfile if switched, validate and reload only Caddy, then verify both targets' current release IDs and bounded DNS/proxy requests. Do not treat restoring a symlink alone as a full rollback.

No rollback has been run as part of D3. Archive presence and documented recovery steps do not prove that a production rollback has been exercised.
