#!/bin/sh
# Local target installer. Defaults to a non-mutating plan. Apply only with an
# explicit target confirmation during the approved installation window.
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: install-target-agent.sh PROJECT_ROOT APPROVED_TARGET_CONFIG SSH_ACCOUNT" >&2
  exit 2
fi

project_root=$(cd "$1" && pwd -P)
config_file=$2
ssh_account=$3
case "$ssh_account" in
  ''|*[!A-Za-z0-9_-]*) echo "SSH account name is invalid" >&2; exit 2 ;;
esac
case "$config_file" in
  /*) ;;
  *) echo "target config path must be absolute" >&2; exit 2 ;;
esac
if [ ! -f "$config_file" ] || [ -L "$config_file" ]; then
  echo "approved target config must be an existing regular file" >&2
  exit 1
fi
target_id=$(python3 - "$config_file" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"(?m)^default_target_id:\s*([a-z][a-z0-9-]{1,63})\s*$", text)
if not match:
    raise SystemExit("config has no valid default_target_id")
print(match.group(1))
PY
)
printf 'Target: %s\n' "$target_id"
printf 'Would install target runtime, root-owned configuration, scoped sudoers, and forced-command entrypoint.\n'
printf 'Config SHA256: '
sha256sum "$config_file" | awk '{print $1}'
printf 'Authorized key (add manually; this script never edits authorized_keys):\n'
printf 'restrict,command="sudo -n /usr/local/sbin/dns-rule-target-v4" ssh-ed25519 <NEW_PUBLIC_KEY> dns-rule-builder-target-v4\n'
if [ "${APPLY:-0}" != 1 ]; then
  echo 'Dry run only. To install, run as root with APPLY=1 CONFIRM=<target-id>.'
  exit 0
fi
if [ "${CONFIRM:-}" != "$target_id" ]; then
  echo 'APPLY requires CONFIRM to exactly match the configured target ID.' >&2
  exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
  echo 'APPLY requires root.' >&2
  exit 1
fi
python3 -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); assert isinstance(d,dict) and d.get("schema_version")==1 and isinstance(d.get("default_target_id"),str) and isinstance(d.get("targets"),dict) and len(d["targets"])==1 and d["default_target_id"] in d["targets"]' "$config_file" || {
  echo "invalid target agent config" >&2; exit 1;
}
python3 -c 'import yaml' >/dev/null 2>&1 || {
  echo "system Python must already provide PyYAML; refusing to install packages" >&2; exit 1;
}

lib=/usr/local/lib/dns-rule-target-v4/dns_rule_builder
install -d -o root -g root -m 0755 "$lib" /usr/local/sbin
install -o root -g root -m 0644 "$project_root/src/dns_rule_builder/__init__.py" "$lib/__init__.py"
install -o root -g root -m 0644 "$project_root/src/dns_rule_builder/target_agent.py" "$lib/target_agent.py"
install -o root -g root -m 0644 "$project_root/src/dns_rule_builder/target_runtime.py" "$lib/target_runtime.py"
install -o root -g root -m 0640 "$config_file" /etc/dns-rule-target-v4.yaml
cat > /usr/local/sbin/dns-rule-target-v4 <<'EOF'
#!/bin/sh
set -eu
export PYTHONPATH=/usr/local/lib/dns-rule-target-v4
export DNS_RULE_TARGET_CONFIG=/etc/dns-rule-target-v4.yaml
exec /usr/bin/python3 -m dns_rule_builder.target_agent
EOF
chown root:root /usr/local/sbin/dns-rule-target-v4
chmod 0755 /usr/local/sbin/dns-rule-target-v4
sudoers=/etc/sudoers.d/dns-rule-target-v4
tmp_sudoers=$(mktemp)
trap 'rm -f "$tmp_sudoers"' EXIT HUP INT TERM
cat > "$tmp_sudoers" <<EOF
Defaults:$ssh_account env_keep += "SSH_ORIGINAL_COMMAND"
$ssh_account ALL=(root) NOPASSWD: /usr/local/sbin/dns-rule-target-v4 ""
EOF
chown root:root "$tmp_sudoers"
chmod 0440 "$tmp_sudoers"
visudo -cf "$tmp_sudoers"
install -o root -g root -m 0440 "$tmp_sudoers" "$sudoers"
visudo -cf "$sudoers"
python3 - "$lib" /etc/dns-rule-target-v4.yaml /usr/local/sbin/dns-rule-target-v4 <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
paths = {str(root / name): root / name for name in ["__init__.py", "target_agent.py", "target_runtime.py"]}
paths.update({name: pathlib.Path(name) for name in sys.argv[2:]})
manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
(root / "install-manifest.json").write_text(json.dumps({"schema_version": 1, "files": manifest}, sort_keys=True) + "\n", encoding="utf-8")
PY
chown root:root "$lib/install-manifest.json"
chmod 0644 "$lib/install-manifest.json"
echo "Installed target agent and one-command sudo rule for $ssh_account. Do not add a general shell key."
echo 'Add a newly generated dedicated public key as:'
echo 'restrict,command="sudo -n /usr/local/sbin/dns-rule-target-v4" ssh-ed25519 <NEW_PUBLIC_KEY> dns-rule-builder-target-v4'
