#!/bin/bash
set -euo pipefail
source_dir=$(realpath "${1:?source directory required}")
upload_dir=${2:?upload directory required}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ ${SOURCE_SHA:-} =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid source SHA' >&2; exit 1; }
test ! -e "$upload_dir" || { echo 'Upload directory already exists' >&2; exit 1; }
test -f "$source_dir/apps/web/.next/standalone/apps/web/server.js"
test -d "$source_dir/apps/web/.next/static"
mkdir -p "$upload_dir"
upload_dir=$(realpath "$upload_dir")
package_dir=$(mktemp -d)
trap 'rm -rf -- "$package_dir"' EXIT
cp -a "$source_dir/apps/web/.next/standalone" "$package_dir/standalone"
entry_dir="$package_dir/standalone/apps/web"
mkdir -p "$entry_dir/.next/static"
cp -a "$source_dir/apps/web/.next/static/." "$entry_dir/.next/static/"
if test -d "$source_dir/apps/web/public"; then
  mkdir -p "$entry_dir/public"
  cp -a "$source_dir/apps/web/public/." "$entry_dir/public/"
fi
# Next may trace environment files. Runtime secrets stay on the server.
find "$package_dir" -name '.env*' \( -type f -o -type l \) -delete
printf '%s\n' "$SOURCE_SHA" > "$package_dir/source.commit"
tar -czf "$upload_dir/release.tar.gz" -C "$package_dir" standalone source.commit
(cd "$upload_dir" && sha256sum release.tar.gz > release.tar.gz.sha256)
install -m 0644 "$script_dir/deploy.sh" "$upload_dir/deploy.sh"
install -m 0644 "$script_dir/../pm2/ecosystem.config.js" "$upload_dir/ecosystem.config.js"
