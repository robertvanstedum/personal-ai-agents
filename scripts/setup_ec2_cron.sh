#!/bin/bash
# Install minimoi cron jobs on EC2. Run after initial deploy or a schedule change.
# Idempotent — safe to re-run. Requires both run scripts under /opt/minimoi/scripts.
#
# Usage (from Mac):
#   scp scripts/run_curator_cron_ec2.sh scripts/run_intelligence_cron_ec2.sh \
#       scripts/setup_ec2_cron.sh ec2-user@<EC2_IP>:/opt/minimoi/scripts/
#   ssh ec2-user@<EC2_IP> "bash /opt/minimoi/scripts/setup_ec2_cron.sh"

set -euo pipefail

SCRIPTS_DIR="/opt/minimoi/scripts"
LOG_DIR="/opt/minimoi/logs"

# Ensure directories exist
mkdir -p "$SCRIPTS_DIR" "$LOG_DIR"

# Ensure cron scripts are executable
chmod +x \
  "$SCRIPTS_DIR/run_curator_cron_ec2.sh" \
  "$SCRIPTS_DIR/run_intelligence_cron_ec2.sh"

# Curator starts on the hour. AI Observations checks fifteen minutes later and
# waits for the next hour if that day's briefing is still running.
CURATOR_CRON="0 * * * * $SCRIPTS_DIR/run_curator_cron_ec2.sh >> $LOG_DIR/curator_cron.log 2>&1"
INTELLIGENCE_CRON="15 * * * * $SCRIPTS_DIR/run_intelligence_cron_ec2.sh >> $LOG_DIR/intelligence_cron.log 2>&1"

install_if_missing() {
  local match="$1"
  local line="$2"
  if crontab -l 2>/dev/null | grep -qF "$match"; then
    echo "$match already installed — no change."
  else
    (crontab -l 2>/dev/null; echo "$line") | crontab -
    echo "Installed: $line"
  fi
}

install_if_missing "run_curator_cron_ec2.sh" "$CURATOR_CRON"
install_if_missing "run_intelligence_cron_ec2.sh" "$INTELLIGENCE_CRON"

echo ""
echo "Current crontab:"
crontab -l
