#!/usr/bin/env bash
# Remove the TV Monitor service and code. Pass --purge to also delete the database and config.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo $0)" >&2; exit 1; }

systemctl disable --now tvmonitor 2>/dev/null || true
rm -f /etc/systemd/system/tvmonitor.service
systemctl daemon-reload
rm -rf /opt/tvmonitor
if [ "${1:-}" = "--purge" ]; then
  rm -rf /var/lib/tvmonitor /etc/tvmonitor
  id tvmonitor >/dev/null 2>&1 && userdel tvmonitor
  echo "Removed TV Monitor, its data and config."
else
  echo "Removed TV Monitor. Data kept in /var/lib/tvmonitor and config in /etc/tvmonitor (use --purge to delete)."
fi
