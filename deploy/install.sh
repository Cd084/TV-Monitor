#!/usr/bin/env bash
# Install or upgrade TV Monitor as a systemd service.
#
#   sudo ./deploy/install.sh [--user plex] [--port 9494] [--open-firewall]
#
# Code + virtualenv:  /opt/tvmonitor
# Database:           /var/lib/tvmonitor
# Config (env file):  /etc/tvmonitor/tvmonitor.env
# Re-running upgrades the code and keeps your data and config.
set -euo pipefail

SERVICE_USER="tvmonitor"
PORT="9494"
PORT_SET=0
OPEN_FIREWALL=0
APP_DIR="/opt/tvmonitor"
DATA_DIR="/var/lib/tvmonitor"
CONF_DIR="/etc/tvmonitor"
UNIT="/etc/systemd/system/tvmonitor.service"

while [ $# -gt 0 ]; do
  case "$1" in
    --user) SERVICE_USER="$2"; shift 2 ;;
    --port) PORT="$2"; PORT_SET=1; shift 2 ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo $0)" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required (or use the Dockerfile)." >&2; exit 1; }

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Python 3.9+ with venv
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || { echo "python3 not found. Install it (dnf install python3 / apt install python3)." >&2; exit 1; }
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is required (found $("$PYTHON" -V))." >&2; exit 1
fi
if ! "$PYTHON" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "The Python venv module is missing. Install it first:" >&2
  echo "  Debian/Ubuntu: apt install python3-venv     Fedora/RHEL/Rocky: dnf install python3" >&2
  exit 1
fi

# Service account
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  if [ "$SERVICE_USER" != "tvmonitor" ]; then
    echo "User '$SERVICE_USER' does not exist." >&2; exit 1
  fi
  NOLOGIN="$(command -v nologin || echo /bin/false)"
  useradd --system --no-create-home --home-dir "$DATA_DIR" --shell "$NOLOGIN" tvmonitor
  echo "Created system user 'tvmonitor'."
fi

# Code
echo "Installing to $APP_DIR ..."
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/tvmonitor"
cp -r "$SRC_DIR/tvmonitor" "$SRC_DIR/run.py" "$SRC_DIR/requirements.txt" "$APP_DIR/"
find "$APP_DIR/tvmonitor" -name '__pycache__' -prune -exec rm -rf {} +
[ -x "$APP_DIR/venv/bin/python" ] || "$PYTHON" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"

# Data + config
mkdir -p "$DATA_DIR" "$CONF_DIR"
chown "$SERVICE_USER": "$DATA_DIR"
chmod 750 "$DATA_DIR"
if [ ! -f "$CONF_DIR/tvmonitor.env" ]; then
  cat > "$CONF_DIR/tvmonitor.env" <<EOF
# TV Monitor settings. Restart after editing: systemctl restart tvmonitor
TVMONITOR_HOST=0.0.0.0
TVMONITOR_PORT=$PORT
TVMONITOR_DATA=$DATA_DIR
# Uncomment both to require a login for the web UI:
#TVMONITOR_USER=admin
#TVMONITOR_PASSWORD=change-me
EOF
fi
chown "root:$(id -gn "$SERVICE_USER")" "$CONF_DIR/tvmonitor.env"
chmod 640 "$CONF_DIR/tvmonitor.env"
# --port on a re-install updates the existing config
if [ "$PORT_SET" -eq 1 ]; then
  sed -i "s/^TVMONITOR_PORT=.*/TVMONITOR_PORT=$PORT/" "$CONF_DIR/tvmonitor.env"
fi
PORT="$(sed -n 's/^TVMONITOR_PORT=//p' "$CONF_DIR/tvmonitor.env" | tail -1)"
PORT="${PORT:-9494}"

# systemd unit
cat > "$UNIT" <<EOF
[Unit]
Description=TV Monitor - missing TV episode tracker
Wants=network-online.target
After=network-online.target remote-fs.target local-fs.target

[Service]
Type=simple
User=$SERVICE_USER
EnvironmentFile=$CONF_DIR/tvmonitor.env
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/run.py
Restart=on-failure
RestartSec=10
# Read-only view of the system except the database folder.
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tvmonitor >/dev/null 2>&1
systemctl restart tvmonitor

# Firewall
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  if [ "$OPEN_FIREWALL" -eq 1 ]; then
    firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null
    echo "Opened port $PORT/tcp in firewalld."
  else
    echo "firewalld is running. To reach the UI from other machines run:"
    echo "  firewall-cmd --permanent --add-port=$PORT/tcp && firewall-cmd --reload"
  fi
elif command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "ufw is active. To reach the UI from other machines run:  ufw allow $PORT/tcp"
fi

sleep 2
if systemctl is-active --quiet tvmonitor; then
  echo
  echo "TV Monitor is running as '$SERVICE_USER': http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
  echo "Logs: journalctl -u tvmonitor -f"
  [ "$SERVICE_USER" = "tvmonitor" ] && \
    echo "Note: the 'tvmonitor' user needs read access to your TV folders (or reinstall with --user plex)."
else
  echo "The service failed to start. Check: journalctl -u tvmonitor -n 50" >&2
  exit 1
fi
