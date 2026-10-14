#!/bin/bash
# Build both RPMs from source, offline, without root:
#
#   omdrop        the omdrop command and receiver, from a checkout of
#                 brentkearney/omdrop-plugin at its committed HEAD, plus
#                 patches/omdrop-fedora.patch
#   fdrop  this repository
#
#   tools/build-rpm.sh [PATH-TO-omdrop-plugin]     (default: ../omdrop-plugin)
#   sudo dnf install ./dist/*.rpm
#
# OMDROP_SKIP_CHECK=1 skips omdrop's own test suite (about two minutes).
# RPM_OUT=DIR puts the packages there instead of dist/ (install.sh uses a
# fresh directory, so it installs exactly what this run built).
set -euo pipefail
umask 022

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PLUGIN=$(cd "${1:-$ROOT/../omdrop-plugin}" && pwd)
OUT=${RPM_OUT:-$ROOT/dist}
TOP=$(mktemp -d)
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{BUILD,RPMS,SRPMS,SPECS,SOURCES} "$OUT"

omdrop_version=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PLUGIN/manifest.json" | head -n 1)
snap=git$(git -C "$PLUGIN" rev-parse --short HEAD)
gnome_version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/src/fdrop/__init__.py")

# Upstream exactly as committed; local changes travel as the patch.
git -C "$PLUGIN" archive --prefix="omdrop-plugin-$omdrop_version/" HEAD \
  | gzip -n > "$TOP/SOURCES/omdrop-plugin-$omdrop_version.tar.gz"
cp "$ROOT/patches/omdrop-fedora.patch" "$TOP/SOURCES/"

tar -C "$ROOT" --transform "s,^\.,fdrop-$gnome_version," \
  --exclude=./dist --exclude=./.git --exclude='__pycache__' -czf \
  "$TOP/SOURCES/fdrop-$gnome_version.tar.gz" .

check=()
[[ -n "${OMDROP_SKIP_CHECK:-}" ]] && check=(--without check)
rpmbuild -bb --define "_topdir $TOP" --define "omdrop_version $omdrop_version" \
  --define "omdrop_snap $snap" "${check[@]}" "$ROOT/packaging/omdrop.spec"
rpmbuild -bb --define "_topdir $TOP" --define "gnome_version $gnome_version" \
  "$ROOT/packaging/fdrop.spec"

find "$TOP/RPMS" -name '*.rpm' -exec cp -v {} "$OUT/" \;
