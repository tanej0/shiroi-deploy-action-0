#!/bin/bash
set -euo pipefail
base=${YOH_DEPLOY_BASE:-"$HOME/yohaku"}
test -f "$base/deploy.sh" || { echo 'Rollback is available after the first update with the new workflow.' >&2; exit 1; }
exec bash "$base/deploy.sh" rollback
