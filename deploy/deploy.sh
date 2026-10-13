#!/usr/bin/env bash
set -euo pipefail
project=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
host=${EVENTPILOT_HOST:-ubuntu@3.110.130.114}
key=${EVENTPILOT_SSH_KEY:-"/Users/sportforward/Desktop/eventpilot/sportsfriends.pem"}
ssh_options=(-i "$key" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
instance_id=$(ssh "${ssh_options[@]}" "$host" '
  metadata_token=$(curl --fail --silent --max-time 5 -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" http://169.254.169.254/latest/api/token)
  curl --fail --silent --max-time 5 -H "X-aws-ec2-metadata-token: $metadata_token" http://169.254.169.254/latest/meta-data/instance-id
')
if [[ "$instance_id" != "i-06b48111a6c256e97" ]]; then
  echo "Deployment stopped: this script targets the Sportforward instance only." >&2
  exit 1
fi
stage=$(ssh "${ssh_options[@]}" "$host" 'umask 077; mktemp -d /tmp/eventpilot-deploy.XXXXXX')
trap 'ssh "${ssh_options[@]}" "$host" "sudo rm -rf -- '$stage'" >/dev/null 2>&1 || true' EXIT
COPYFILE_DISABLE=1 tar --no-xattrs --exclude '._*' --exclude '.DS_Store' -C "$project" -czf - backend/main.py backend/email_service.py backend/email_templates.py backend/email_worker.py backend/intelligence.py backend/demo_data.py backend/seed_demo.py backend/requirements.txt backend/static/docs frontend deploy/bootstrap.py deploy/create_organization_login.py deploy/install.sh deploy/eventpilot.service deploy/eventpilot-mail.service deploy/eventpilot.nginx |
  ssh "${ssh_options[@]}" "$host" "tar -xzf - -C '$stage'"
if [[ -f "$project/backend/.env" ]]; then
  scp "${ssh_options[@]}" "$project/backend/.env" "$host:$stage/source.env"
else
  # Upgrades preserve the protected server environment without copying secrets locally.
  ssh "${ssh_options[@]}" "$host" "umask 077; touch '$stage/source.env'"
fi
ssh "${ssh_options[@]}" "$host" "sudo bash '$stage/deploy/install.sh' '$stage'"
