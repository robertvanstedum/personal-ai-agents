#!/bin/bash
# Install minimoi cron jobs on EC2. Run once after initial deploy.
# Idempotent — safe to re-run. Requires: /opt/minimoi/scripts/run_curator_cron_ec2.sh
#
# Usage (from Mac):
#   scp scripts/run_curator_cron_ec2.sh ec2-user@<EC2_IP>:/opt/minimoi/scripts/
#   scp scripts/setup_ec2_cron.sh       ec2-user@<EC2_IP>:/opt/minimoi/scripts/
#   ssh ec2-user@<EC2_IP> "bash /opt/minimoi/scripts/setup_ec2_cron.sh"

set -euo pipefail

SCRIPTS_DIR="/opt/minimoi/scripts"
LOG_DIR="/opt/minimoi/logs"

# Ensure directories exist
mkdir -p "$SCRIPTS_DIR" "$LOG_DIR"

# Ensure cron script is executable
chmod +x "$SCRIPTS_DIR/run_curator_cron_ec2.sh"

# Crontab entry: hourly; time gate + idempotency handled inside the script
CRON_LINE="0 * * * * $SCRIPTS_DIR/run_curator_cron_ec2.sh >> $LOG_DIR/curator_cron.log 2>&1"

if crontab -l 2>/dev/null | grep -qF "run_curator_cron_ec2.sh"; then
  echo "Cron entry already installed — no change."
else
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  echo "Installed: $CRON_LINE"
fi

echo ""
echo "Current crontab:"
crontab -l
