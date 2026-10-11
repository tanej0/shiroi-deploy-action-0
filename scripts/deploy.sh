#!/bin/bash
# Runs as the existing frontend owner; no root or dependency installation.
set -euo pipefail
umask 077
base=${YOH_DEPLOY_BASE:-"$HOME/yohaku"}
base=$(realpath "$base")
app=${YOH_PM2_APP:-Yohaku}
port=${YOH_PORT:-2323}
preview_port=${YOH_PREVIEW_PORT:-12323}
attempts=${YOH_HEALTH_ATTEMPTS:-30}
[[ $port =~ ^[0-9]+$ && $preview_port =~ ^[0-9]+$ && $attempts =~ ^[0-9]+$ ]]
test "$port" -ne "$preview_port"
for tool in node pm2 curl tar sha256sum flock ss; do command -v "$tool" >/dev/null; done
state="$base/.deploy-state"
mkdir -p "$state"
exec 9> "$state/lock"
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
config="$base/ecosystem.config.js"
test -f "$config"
test -L "$base/server.js"
old_entry=$(readlink -f "$base/server.js")
pm2 describe "$app" >/dev/null
preview_pid=
switched=false

health() {
  local target_port=$1 code
  code=$(curl -sS -L --max-time 5 -o /dev/null -w '%{http_code}' "http://localhost:$target_port/" 2>/dev/null) || return 1
  [[ $code == 200 ]]
}
wait_healthy() {
  local target_port=$1 i
  for ((i=0; i<attempts; i++)); do
    if health "$target_port"; then return 0; fi
    sleep 2
  done
  return 1
}
stop_preview() {
  if [[ -n $preview_pid ]]; then
    kill "$preview_pid" 2>/dev/null || true
    wait "$preview_pid" 2>/dev/null || true
    preview_pid=
  fi
}
link_entry() {
  local target=$1
  ln -s "$target" "$base/.server.js.next.$$"
  mv -Tf "$base/.server.js.next.$$" "$base/server.js"
}
finish() {
  local rc=$?
  trap - EXIT
  stop_preview
  if [[ $rc -ne 0 && $switched == true ]]; then
    echo 'Update failed after switching; restoring the previous frontend.' >&2
    if link_entry "$old_entry" && pm2 reload "$config" --only "$app" --update-env && wait_healthy "$port" && pm2 save; then
      echo 'Previous frontend restored and HTTP health check passed.' >&2
    else
      echo 'ROLLBACK FAILED: inspect PM2 logs before retrying.' >&2
    fi
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

case "${1:-}" in
  deploy)
    test -f "$old_entry"
    health "$port" || { echo 'Current frontend is unhealthy; update aborted.' >&2; exit 1; }
    stage=${2:?staging directory required}
    release_id=${3:?release ID required}
    sha=${4:?source SHA required}
    [[ $release_id =~ ^[0-9]+-[0-9]+$ && $sha =~ ^[0-9a-f]{40}$ ]]
    (cd "$stage" && sha256sum -c release.tar.gz.sha256)
    release="$base/$release_id"
    test ! -e "$release" || { echo 'Release directory already exists.' >&2; exit 1; }
    mkdir "$release"
    tar -xzf "$stage/release.tar.gz" -C "$release"
    test "$(cat "$release/source.commit")" = "$sha"
    entry="$release/standalone/apps/web/server.js"
    entry_dir=$(dirname "$entry")
    test -f "$entry"
    test -f "$base/.env"
    ln -s "$base/.env" "$entry_dir/.env"
    # Each release keeps its own cache; preflight cannot change the live cache.
    mkdir -p "$entry_dir/.next/cache"
    if ss -ltnH | awk '{print $4}' | grep -Eq ":$preview_port$"; then
      echo 'Preview port is in use; update aborted.' >&2; exit 1
    fi
    (
      cd "$entry_dir"
      exec 9>&-
      # Next's locale middleware normalizes numeric loopback to localhost.
      # Match that origin to keep rewrites internal instead of redirecting again.
      export NODE_ENV=production HOSTNAME=localhost PORT="$preview_port"
      exec node ./server.js
    ) > "$state/preview.log" 2>&1 &
    preview_pid=$!
    candidate_ok=false
    for ((i=0; i<attempts; i++)); do
      if ! kill -0 "$preview_pid" 2>/dev/null; then break; fi
      if health "$preview_port"; then candidate_ok=true; break; fi
      sleep 2
    done
    if [[ $candidate_ok != true ]]; then
      echo 'Candidate failed health check; live frontend was not changed.' >&2
      echo "Private startup log: $state/preview.log" >&2
      exit 1
    fi
    stop_preview
    ;;
  rollback)
    test -f "$state/previous-entry"
    entry=$(realpath "$(cat "$state/previous-entry")")
    [[ $entry == "$base/"* && $entry == */standalone/apps/web/server.js ]]
    test -f "$entry"
    ;;
  *) echo 'Usage: deploy.sh deploy STAGE RUN-ATTEMPT SHA | rollback' >&2; exit 2 ;;
esac

link_entry "$entry"
switched=true
pm2 reload "$config" --only "$app" --update-env
wait_healthy "$port" || { echo 'Live health check failed.' >&2; exit 1; }
pm2 save
if [[ ${1:-} == deploy ]]; then install -m 0700 "$stage/deploy.sh" "$base/deploy.sh"; fi
printf '%s\n' "$old_entry" > "$state/previous-entry"
commit_file="${entry%/standalone/apps/web/server.js}/source.commit"
if test -f "$commit_file"; then cp "$commit_file" "$state/current-commit"; else echo unknown > "$state/current-commit"; fi
switched=false
echo 'Frontend switched successfully; HTTP health check passed.'
if [[ ${1:-} == deploy ]]; then
  rm -f -- "$stage/release.tar.gz" "$stage/release.tar.gz.sha256" "$stage/deploy.sh" "$stage/ecosystem.config.js"
  rmdir -- "$stage" || true
fi
