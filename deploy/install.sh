#!/usr/bin/env bash
set -euo pipefail
stage=${1:?Provide the deployment staging directory}
if [[ $(id -u) != 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

if ! id eventpilot >/dev/null 2>&1; then
  useradd --system --home /opt/eventpilot --shell /usr/sbin/nologin eventpilot
fi
install -d -m 755 /opt/eventpilot/app/static/docs /var/www/eventpilot/vendor
install -d -m 700 /etc/eventpilot

if [[ -f /opt/eventpilot/app/main.py ]]; then
  backup="/var/backups/eventpilot/$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -m 700 "$backup"
  cp -a /opt/eventpilot/app "$backup/backend"
  cp -a /var/www/eventpilot "$backup/frontend"
  cp -a /etc/eventpilot "$backup/environment"
  [[ ! -f /etc/systemd/system/eventpilot.service ]] || cp /etc/systemd/system/eventpilot.service "$backup/"
  [[ ! -f /etc/systemd/system/eventpilot-mail.service ]] || cp /etc/systemd/system/eventpilot-mail.service "$backup/"
  [[ ! -f /etc/nginx/sites-available/eventpilot ]] || cp /etc/nginx/sites-available/eventpilot "$backup/eventpilot.nginx"
  # Preserve EventPilot data before additive tables are created by the upgrade.
  mysqldump --single-transaction --no-tablespaces eventpilot | gzip > "$backup/eventpilot.sql.gz"
  chmod 600 "$backup/eventpilot.sql.gz"
  echo "Source, configuration and database backup saved to $backup."
fi

if [[ ! -x /opt/eventpilot/venv/bin/python ]]; then
  python3 -m venv /opt/eventpilot/venv
fi
/opt/eventpilot/venv/bin/pip install --no-cache-dir -r "$stage/backend/requirements.txt"
/opt/eventpilot/venv/bin/python "$stage/deploy/bootstrap.py" "$stage"
if [[ -n ${backup:-} ]]; then
  # Retain newly provisioned encryption settings alongside the pre-upgrade copy.
  cp -a /etc/eventpilot "$backup/provisioned-environment"
fi
install -m 644 "$stage/backend/main.py" "$stage/backend/email_service.py" "$stage/backend/email_templates.py" "$stage/backend/email_worker.py" "$stage/backend/intelligence.py" "$stage/backend/demo_data.py" "$stage/backend/seed_demo.py" "$stage/backend/requirements.txt" /opt/eventpilot/app/
find "$stage/frontend" -maxdepth 1 -type f ! -name '.*' -exec install -m 644 {} /var/www/eventpilot/ \;
find "$stage/frontend/vendor" -maxdepth 1 -type f ! -name '.*' -exec install -m 644 {} /var/www/eventpilot/vendor/ \;
find "$stage/backend/static/docs" -maxdepth 1 -type f ! -name '.*' -exec install -m 644 {} /opt/eventpilot/app/static/docs/ \;
install -m 644 "$stage/deploy/create_organization_login.py" /opt/eventpilot/create_organization_login.py
install -m 644 "$stage/deploy/eventpilot.service" /etc/systemd/system/eventpilot.service
install -m 644 "$stage/deploy/eventpilot-mail.service" /etc/systemd/system/eventpilot-mail.service
install -m 644 "$stage/deploy/eventpilot.nginx" /etc/nginx/sites-available/eventpilot
ln -sfn /etc/nginx/sites-available/eventpilot /etc/nginx/sites-enabled/eventpilot
nginx -t
systemctl daemon-reload
systemctl enable eventpilot
systemctl restart eventpilot
for attempt in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:8001/api/health >/dev/null; then
    systemctl enable eventpilot-mail
    systemctl restart eventpilot-mail
    systemctl is-active --quiet eventpilot-mail
    systemctl reload nginx
    /opt/eventpilot/venv/bin/pip freeze > /opt/eventpilot/requirements.installed.txt
    echo "EventPilot API, email worker and Nginx are ready."
    exit 0
  fi
  sleep 1
done
echo "EventPilot did not become healthy; inspect journalctl -u eventpilot." >&2
exit 1
