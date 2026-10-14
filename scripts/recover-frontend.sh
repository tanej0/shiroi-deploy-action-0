#!/bin/bash
set -eo pipefail
umask 077
# System shell startup files may legitimately reference unset variables.
if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi
set -u
base=$(realpath "${YOH_DEPLOY_BASE:-$HOME/yohaku}")
app=${YOH_PM2_APP:-Yohaku}
port=${YOH_PORT:-2323}
attempts=${YOH_HEALTH_ATTEMPTS:-30}
mode=${1:-restore}
[[ $mode == restore || $mode == install-autostart ]]
[[ $port =~ ^[0-9]+$ && $attempts =~ ^[0-9]+$ ]]
state="$base/.deploy-state"
test -f "$state/runtime.config.cjs"
test -f "$base/server.js"
exec 9> "$state/lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
health() {
  local code
  code=$(curl -sS -L --max-time 5 -o /dev/null -w '%{http_code}' "http://localhost:$port/" 2>/dev/null) || return 1
  [[ $code == 200 ]]
}
if ! health; then
  YOH_PM2_APP="$app" pm2 start "$state/runtime.config.cjs" --only "$app" --update-env 9>&-
fi
healthy=false
for ((i=0; i<attempts; i++)); do
  if health; then healthy=true; break; fi
  sleep 2
done
[[ $healthy == true ]] || { echo 'Frontend failed HTTP health check.' >&2; exit 1; }
pid=$(pm2 pid "$app")
[[ $pid =~ ^[0-9]+$ && $pid -gt 0 ]]
actual=$(readlink -f "/proc/$pid/cwd")/server.js
test "$actual" = "$(readlink -f "$base/server.js")" || { echo 'Unexpected running release.' >&2; exit 1; }
pm2 save
echo 'Current frontend restored; running release and HTTP checks passed.'
if [[ $mode == install-autostart ]]; then
  command -v crontab >/dev/null
  [[ $base =~ ^/[A-Za-z0-9_./-]+$ ]]
  install -m 0700 "$(realpath "$0")" "$base/boot-frontend.sh"
  touch "$state/boot.log"
  chmod 0600 "$state/boot.log"
  cron_file=$(mktemp)
  trap 'rm -f -- "$cron_file"' EXIT
  if crontab -l > "$cron_file" 2>/dev/null; then :; else
    rc=$?
    [[ $rc == 1 ]] || exit "$rc"
  fi
  sed -i '/# codex-yohaku-autostart$/d' "$cron_file"
  printf '\n@reboot /bin/bash "%s/boot-frontend.sh" restore >> "%s/.deploy-state/boot.log" 2>&1 # codex-yohaku-autostart\n' "$base" "$base" >> "$cron_file"
  crontab "$cron_file"
  echo 'Frontend autostart installed in the deployment user crontab.'
fi
