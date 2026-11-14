#!/bin/bash
# Keep the CoS agent's container away from the EC2 instance role (169.254.169.254).
#
# Why: the CoS main agent can run commands and fetch pages; with IMDS hop limit 2 its container could fetch the
# instance role's credentials. Other containers (portal SES, scheduler CloudWatch, SSM reads) need the role, so this
# blocks one address only: cos-agent-a's fixed address on its own network (docker-compose.prod.yml, network cos-agent).
# Private record: planning operations/SECURITY_IMDS_CONTAINERS_2026-11-13.md.
#
# Usage (as root on the production host):
#   cos_agent_imds_block.sh install   # write the systemd unit, enable it, apply the rule now (every deploy runs this)
#   cos_agent_imds_block.sh apply     # add the rule if missing (what the unit runs whenever Docker starts)
#   cos_agent_imds_block.sh status    # show whether the rule and unit are in place
#   cos_agent_imds_block.sh remove    # revert: delete the rule, disable and remove the unit
set -euo pipefail

SRC=192.168.250.2/32
IMDS=169.254.169.254/32
RULE=(DOCKER-USER -s "$SRC" -d "$IMDS" -j REJECT)
# The stopgap of 13 Nov, keyed to cos-agent-a's old address on the shared network. Once cos-agent-a moves to its own
# network that address can go to a container that needs the role, so apply removes it. The deploy runs install just
# before it recreates the services, which leaves cos-agent-a unblocked only until it is recreated at $SRC.
LEGACY=(DOCKER-USER -s 172.18.0.6/32 -d "$IMDS" -j REJECT)
UNIT=/etc/systemd/system/minimoi-cos-agent-imds-block.service
SELF=/opt/minimoi/scripts/cos_agent_imds_block.sh

have_rule() { iptables -C "${RULE[@]}" 2>/dev/null; }

apply() {
  iptables -N DOCKER-USER 2>/dev/null || true          # Docker creates it; make sure it exists if we run first
  if have_rule; then echo "rule present"; else iptables -I "${RULE[@]:0:1}" 1 "${RULE[@]:1}"; echo "rule added"; fi
  while iptables -C "${LEGACY[@]}" 2>/dev/null; do iptables -D "${LEGACY[@]}"; echo "legacy rule removed"; done
}

case "${1:-}" in
  apply) apply ;;
  install)
    [ -x "$SELF" ] || { echo "expected this script at $SELF" >&2; exit 1; }
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=Block the CoS agent container from the EC2 instance metadata address
After=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$SELF apply

[Install]
WantedBy=docker.service multi-user.target
UNITEOF
    systemctl daemon-reload
    systemctl enable minimoi-cos-agent-imds-block.service
    systemctl restart minimoi-cos-agent-imds-block.service
    apply ;;
  status)
    have_rule && echo "rule: present" || echo "rule: MISSING"
    systemctl is-enabled minimoi-cos-agent-imds-block.service 2>/dev/null | sed 's/^/unit: /' || echo "unit: not installed"
    iptables -S DOCKER-USER ;;
  remove)
    while have_rule; do iptables -D "${RULE[@]}"; done
    systemctl disable --now minimoi-cos-agent-imds-block.service 2>/dev/null || true
    rm -f "$UNIT"; systemctl daemon-reload
    echo "removed" ;;
  *) echo "usage: $0 install|apply|status|remove" >&2; exit 2 ;;
esac
