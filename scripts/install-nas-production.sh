#!/bin/sh
# Prepare Builder files on the NAS. This script never starts a service.
set -eu
fail() { printf 'install-nas-production: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"; }

: "${DNS_RULE_BUILDER_PACK_ROOT:?Set DNS_RULE_BUILDER_PACK_ROOT to the reviewed local pack}"
: "${DNS_RULE_BUILDER_PACK_SHA256:?Set DNS_RULE_BUILDER_PACK_SHA256 to the reviewed SHA256SUMS digest}"
: "${DNS_RULE_BUILDER_IMAGE:?Set DNS_RULE_BUILDER_IMAGE to the final immutable image digest}"
pack_root=$(cd "$DNS_RULE_BUILDER_PACK_ROOT" 2>/dev/null && pwd -P) || fail 'pack root is not a directory'
nas_root=${DNS_RULE_BUILDER_NAS_ROOT:-/}
case "$nas_root" in /*) ;; *) fail 'NAS root must be absolute' ;; esac
if [ "${APPLY:-0}" = 1 ]; then
  [ "$nas_root" = / ] || fail 'APPLY is allowed only against the actual NAS root'
  [ "${CONFIRM_DNS_RULE_BUILDER_INSTALL:-}" = nas-backup ] || fail 'APPLY requires CONFIRM_DNS_RULE_BUILDER_INSTALL=nas-backup'
  [ "$(id -u)" -eq 0 ] || fail 'APPLY requires root'
elif [ "${APPLY:-0}" != 0 ]; then fail 'APPLY must be 0 or 1'; fi
for cmd in sha256sum ssh-keygen getent python3 awk cmp; do need "$cmd"; done

case "$DNS_RULE_BUILDER_PACK_SHA256" in *[!0-9a-f]*|'') fail 'pack lock must be 64 lowercase hex characters' ;; esac
[ "${#DNS_RULE_BUILDER_PACK_SHA256}" -eq 64 ] || fail 'pack lock must be 64 lowercase hex characters'
manifest=$pack_root/SHA256SUMS
[ -f "$manifest" ] && [ ! -L "$manifest" ] || fail 'pack SHA256SUMS missing or unsafe'
manifest_hash=$(sha256sum "$manifest" | awk '{print $1}')
[ "$manifest_hash" = "$DNS_RULE_BUILDER_PACK_SHA256" ] || fail 'SHA256SUMS differs from supplied lock'
awk '
  length($0)>=67 && substr($0,1,64) ~ /^[0-9a-f]+$/ && substr($0,65,2)=="  " && substr($0,67) ~ /^[A-Za-z0-9._\/-]+$/ {
    p=substr($0,67)
    n=split(p,parts,"/")
    if (p ~ /^\// || seen[p]++) exit 1
    for (i=1; i<=n; i++) if (parts[i]=="." || parts[i]==".." || parts[i]=="") exit 1
    next
  }
  { exit 1 }
' "$manifest" || fail 'SHA256SUMS has malformed, duplicate, or unsafe paths'
(cd "$pack_root" && sha256sum --check --status "$manifest") || fail 'a packed file failed SHA256 verification'
python3 - "$pack_root" "$manifest" <<'PY' || fail 'pack entries must be regular files without symlink components'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for line in pathlib.Path(sys.argv[2]).read_text(encoding="ascii").splitlines():
    rel = line[66:]
    p = root / rel
    cur = root
    for part in pathlib.PurePosixPath(rel).parts:
        cur = cur / part
        assert not cur.is_symlink(), f"symlink pack path: {rel}"
    assert p.is_file(), f"non-file pack entry: {rel}"
PY
required='
image-digest.txt
target-agent-nas.yaml
builder/scripts/install-target-agent.sh
builder/scripts/prepare_nas_backup.py
builder/src/dns_rule_builder/__init__.py
builder/src/dns_rule_builder/target_agent.py
builder/src/dns_rule_builder/target_runtime.py
compose/stacks/dns-rule-builder/README.md
compose/stacks/dns-rule-builder/compose.yaml
compose/stacks/dns-rule-builder/install.sh
compose/config/dns-rule-builder/targets-v4.yaml
'
for rel in $required; do
  [ -f "$pack_root/$rel" ] && [ ! -L "$pack_root/$rel" ] || fail "required pack file missing or unsafe: $rel"
  awk -v want="$rel" 'substr($0,67)==want { n++ } END { exit(n==1 ? 0 : 1) }' "$manifest" || fail "required file not covered exactly once: $rel"
done
image=$(cat "$pack_root/image-digest.txt")
case "$image" in ghcr.io/987n/dns-rule-builder@sha256:*) image_hash=${image#*@sha256:} ;; *) fail 'invalid image-digest.txt reference' ;; esac
case "$image_hash" in *[!0-9a-f]*|'') fail 'image digest must be 64 lowercase hex characters' ;; esac
[ "${#image_hash}" -eq 64 ] && [ "$image" = "$DNS_RULE_BUILDER_IMAGE" ] || fail 'image digest mismatch or malformed digest'
python3 - "$pack_root/compose/config/dns-rule-builder/targets-v4.yaml" "$pack_root/target-agent-nas.yaml" <<'PY' || fail 'pack target configurations are incomplete or not deployable'
import sys, yaml
app = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
agent = yaml.safe_load(open(sys.argv[2], encoding="utf-8"))
assert isinstance(app, dict) and app.get("schema_version") == 4 and app.get("deployment_enabled") is False
assert isinstance(agent, dict) and agent.get("schema_version") == 1 and agent.get("default_target_id") == "nas-backup"
assert isinstance(agent.get("targets"), dict) and set(agent["targets"]) == {"nas-backup"}
assert "__FILL_AND_REVIEW__" not in open(sys.argv[2], encoding="utf-8").read()
PY
python3 -c 'import yaml' >/dev/null 2>&1 || fail 'PyYAML must already be available; this script installs no packages'

root_path() {
  if [ "$nas_root" = / ]; then printf '%s' "$1"; else printf '%s%s' "${nas_root%/}" "$1"; fi
}
safe_path() {
  p=$1; oldifs=$IFS; IFS=/; set -f; set -- $p; IFS=$oldifs; set +f
  cur=/
  for part do
    [ -n "$part" ] || continue
    cur=${cur%/}/$part
    [ ! -L "$cur" ] || fail "refusing symlink path component: $p"
  done
}
data_dir=/vol1/1000/DockerData/dns-rule-builder
compose_root=/vol1/1000/DockerData/_compose
secret_dir=$compose_root/secrets/local/dns-rule-builder
pat_file=$secret_dir/github_token
admin_password_file=$secret_dir/admin_password
session_file=$secret_dir/session_secret
nas_key=$secret_dir/nas_target_ed25519
vm_key=$secret_dir/vm104_target_ed25519
known_hosts=$secret_dir/known_hosts
target_config=$compose_root/config/dns-rule-builder/targets-v4.yaml
stack_dir=$compose_root/stacks/dns-rule-builder
agent_config=/etc/dns-rule-target-v4.yaml
agent_lib=/usr/local/lib/dns-rule-target-v4
agent_bin=/usr/local/sbin/dns-rule-target-v4
sudoers_file=/etc/sudoers.d/dns-rule-target-v4
for p in "$data_dir" "$compose_root" "$secret_dir" "$pat_file" "$admin_password_file" "$session_file" "$nas_key" "$vm_key" "$known_hosts" "$target_config" "$stack_dir" "$agent_config" "$agent_lib" "$agent_bin" "$sudoers_file" /etc/dns-rule-builder-preparation-receipt.json; do safe_path "$(root_path "$p")"; done
backup_sources='
/vol1/1000/DockerData/_compose/stacks/smartdns-backup
/vol1/1000/DockerData/smartdns-backup/rules
/vol1/1000/DockerData/homegateway-review
/vol1/1000/DockerData/homegateway-rules-controller
/vol1/1000/DockerData/_compose/config/network/caddy/Caddyfile
'
for p in $backup_sources; do
  host_path=$(root_path "$p"); safe_path "$host_path"
  [ -e "$host_path" ] && [ ! -L "$host_path" ] || fail "required pre-install backup source is missing or unsafe: $p"
  case "$p" in */Caddyfile) [ -f "$host_path" ] || fail "backup source is not a regular file: $p" ;; *) [ -d "$host_path" ] || fail "backup source is not a directory: $p" ;; esac
  [ -r "$host_path" ] || fail "backup source is not readable: $p"
done
for p in "$pat_file" "$admin_password_file" "$session_file" "$nas_key" "$vm_key" "$known_hosts"; do
  f=$(root_path "$p"); [ -f "$f" ] && [ ! -L "$f" ] && [ -s "$f" ] || fail "required individual secret file missing, empty, or unsafe: $p"
done
python3 - "$(root_path "$admin_password_file")" <<'PY' || fail 'admin_password must contain at least 16 non-whitespace characters'
import pathlib, sys
if len(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()) < 16:
    raise SystemExit(1)
PY
for p in "$nas_key" "$vm_key"; do ssh-keygen -y -f "$(root_path "$p")" >/dev/null 2>&1 || fail "invalid private identity: $p"; done
for host in 10.77.0.4 10.255.50.2; do ssh-keygen -F "$host" -f "$(root_path "$known_hosts")" >/dev/null 2>&1 || fail "known_hosts does not pin $host"; done
passwd_entry=$(getent passwd 987N) || fail 'NAS SSH account 987N is missing'
account_uid=$(printf '%s\n' "$passwd_entry" | awk -F: '{print $3}')
account_gid=$(printf '%s\n' "$passwd_entry" | awk -F: '{print $4}')
home_dir=$(printf '%s\n' "$passwd_entry" | awk -F: '{print $6}')
case "$account_uid:$account_gid:$home_dir" in *[!0-9:/A-Za-z._-]*|::*|:*:) fail 'NAS account record is malformed' ;; esac
case "$home_dir" in /*) ;; *) fail 'NAS account home must be absolute' ;; esac
ssh_dir=$home_dir/.ssh; authorized_keys=$ssh_dir/authorized_keys
safe_path "$(root_path "$ssh_dir")"; safe_path "$(root_path "$authorized_keys")"
ssh_dir_host=$(root_path "$ssh_dir"); authorized_host=$(root_path "$authorized_keys")
[ ! -e "$ssh_dir_host" ] || [ -d "$ssh_dir_host" ] || fail 'NAS .ssh path is not a directory'
[ ! -e "$authorized_host" ] || { [ -f "$authorized_host" ] && [ ! -L "$authorized_host" ]; } || fail 'authorized_keys is not a regular file'
python3 - "$ssh_dir_host" "$authorized_host" "$account_uid" <<'PY' || fail 'existing SSH authorization paths have unsafe ownership or write permissions'
import pathlib, sys
for raw in sys.argv[1:3]:
    p = pathlib.Path(raw)
    if not p.exists():
        continue
    st = p.stat()
    if st.st_uid != int(sys.argv[3]) or st.st_mode & 0o022:
        raise SystemExit(1)
PY
nas_public=$(ssh-keygen -y -f "$(root_path "$nas_key")" 2>/dev/null) || fail 'cannot derive NAS public key'
set -- $nas_public
[ "$#" -ge 2 ] && [ "$1" = ssh-ed25519 ] || fail 'NAS dedicated identity must be Ed25519'
key_blob=$2
forced_line="restrict,command=\"sudo -n /usr/local/sbin/dns-rule-target-v4\" ssh-ed25519 $key_blob dns-rule-builder-target-v4"
key_match=
if [ -f "$authorized_host" ]; then
  key_match=$(awk -v blob="$key_blob" '{for(i=1;i<=NF;i++) if($i==blob){print; exit}}' "$authorized_host")
  [ -z "$key_match" ] || [ "$key_match" = "$forced_line" ] || fail 'NAS key already exists with different authorization options'
fi
for rel in README.md compose.yaml install.sh; do
  src=$pack_root/compose/stacks/dns-rule-builder/$rel; dst=$(root_path "$stack_dir/$rel")
  if [ -e "$dst" ]; then [ -f "$dst" ] && [ ! -L "$dst" ] && cmp -s "$src" "$dst" || fail "refusing to overwrite existing stack file: $dst"; fi
done
app_target_src=$pack_root/compose/config/dns-rule-builder/targets-v4.yaml
app_target_dst=$(root_path "$target_config")
if [ -e "$app_target_dst" ]; then [ -f "$app_target_dst" ] && [ ! -L "$app_target_dst" ] && cmp -s "$app_target_src" "$app_target_dst" || fail 'refusing to overwrite a different app target config'; fi

printf 'NAS preparation plan (no service actions):\n'
printf '  image: %s\n  pack lock: %s\n' "$image" "$manifest_hash"
printf '  install missing Compose/config files under %s\n' "$(root_path "$compose_root")"
printf '  normalize six dedicated secret files to 10001:10001/0600; keep old review password untouched\n'
printf '  prepare app data directory %s as 10001:10001/0700\n' "$(root_path "$data_dir")"
printf '  install restricted NAS target helper and append its dedicated forced-command key\n'
printf '  make a consistent scoped NAS state backup and publish its redacted receipt before any install\n'
if [ "${APPLY:-0}" != 1 ]; then
  echo 'Dry run only. No files, metadata, targets, services, old controller, or Caddy configuration changed.'
  exit 0
fi

backup_parent=$(root_path /vol1/1000/DockerData/dns-rule-builder-install-backups)
safe_path "$backup_parent"
install -d -o root -g root -m 0700 "$backup_parent"
backup_dir=$backup_parent/pre-install-$(date -u +%Y%m%dT%H%M%SZ)-$$
mkdir -m 0700 "$backup_dir"
set -- --nas-root "$nas_root" --backup-dir "$backup_dir"
for p in \
  "$authorized_keys" "$agent_config" "$agent_lib" "$agent_bin" "$sudoers_file" \
  /usr/local/libexec/smartdns-backup-target \
  "$stack_dir/README.md" "$stack_dir/compose.yaml" "$stack_dir/install.sh" "$target_config" \
  /etc/dns-rule-builder-preparation-receipt.json; do
  set -- "$@" --optional-source "$p"
done
python3 "$pack_root/builder/scripts/prepare_nas_backup.py" "$@" >/dev/null || fail 'scoped backup or SQLite consistency check failed; no installation was applied'
receipt_src=$backup_dir/receipt.json
receipt_dst=$(root_path /etc/dns-rule-builder-preparation-receipt.json)
if [ -e "$receipt_dst" ]; then [ -f "$receipt_dst" ] && [ ! -L "$receipt_dst" ] || fail 'existing preparation receipt is not a regular file'; fi
install -o root -g root -m 0644 "$receipt_src" "$receipt_dst"
install -d -o 10001 -g 10001 -m 0700 "$(root_path "$secret_dir")"
for p in "$pat_file" "$admin_password_file" "$session_file" "$nas_key" "$vm_key" "$known_hosts"; do chown 10001:10001 "$(root_path "$p")"; chmod 0600 "$(root_path "$p")"; done
install -d -o 10001 -g 10001 -m 0700 "$(root_path "$data_dir")"
for rel in README.md compose.yaml install.sh; do
  dst=$(root_path "$stack_dir/$rel")
  if [ ! -e "$dst" ]; then
    install -d -o 1000 -g 1001 -m 0755 "$(dirname "$dst")"
    mode=0644; [ "$rel" != install.sh ] || mode=0755
    install -o 1000 -g 1001 -m "$mode" "$pack_root/compose/stacks/dns-rule-builder/$rel" "$dst"
  fi
done
if [ ! -e "$app_target_dst" ]; then install -d -o 1000 -g 1001 -m 0755 "$(dirname "$app_target_dst")"; install -o 1000 -g 1001 -m 0644 "$app_target_src" "$app_target_dst"; fi
APPLY=1 CONFIRM=nas-backup sh "$pack_root/builder/scripts/install-target-agent.sh" "$pack_root/builder" "$pack_root/target-agent-nas.yaml" 987N >/dev/null
ssh_dir_created=0; authorized_created=0
if [ ! -d "$ssh_dir_host" ]; then install -d -o "$account_uid" -g "$account_gid" -m 0700 "$ssh_dir_host"; ssh_dir_created=1; fi
if [ ! -e "$authorized_host" ]; then install -o "$account_uid" -g "$account_gid" -m 0600 /dev/null "$authorized_host"; authorized_created=1; fi
if [ -z "$key_match" ]; then printf '%s\n' "$forced_line" >> "$authorized_host"; fi
if [ "$authorized_created" = 1 ]; then chown "$account_uid:$account_gid" "$authorized_host"; chmod 0600 "$authorized_host"; fi
if [ "$ssh_dir_created" = 1 ]; then chown "$account_uid:$account_gid" "$ssh_dir_host"; chmod 0700 "$ssh_dir_host"; fi
printf 'Preparation completed. Backup: %s\n' "$backup_dir"
echo 'No container was started, no target was contacted or activated, and no old controller or Caddy route was changed.'
