#!/usr/bin/env bash
#
# Write a version into every file that carries one.
#
# Two kinds of target. In the YAML and Python files the version lines end in an
# `x-release-please-version` marker, a convention kept from the release-please
# era because it is a good one: the marker says which value on a line is a
# version, so a chart that gains a second version cannot be bumped by accident.
# The frontend JSON files have no room for a comment, so their paths are named.
#
# Adding a chart means adding it to MARKED below.
#
# Usage: bump-version.sh <version>          e.g. bump-version.sh 2.3.0
set -euo pipefail

VERSION=${1:?version, without a leading v}

case "$VERSION" in
  v*) echo "Pass the version without a leading v: ${VERSION#v}" >&2; exit 1 ;;
  [0-9]*.[0-9]*.[0-9]*) ;;
  *) echo "Not a semver version: $VERSION" >&2; exit 1 ;;
esac

MARKER='x-release-please-version'
MARKED=(
  app/__version__.py
  helm/Chart.yaml
  helm/charts/backend/Chart.yaml
  helm/charts/frontend/Chart.yaml
)

for file in "${MARKED[@]}"; do
  before=$(grep -c "$MARKER" "$file")
  # Anchored on the marker, so no other version-shaped string can be caught.
  # Two expressions because the YAML mixes bare and quoted values.
  sed -i -E \
    -e "s/([0-9]+\.[0-9]+\.[0-9]+)([[:space:]]*#[[:space:]]*$MARKER)/$VERSION\2/" \
    -e "s/\"([0-9]+\.[0-9]+\.[0-9]+)\"([[:space:]]*#[[:space:]]*$MARKER)/\"$VERSION\"\2/" \
    "$file"
  after=$(grep -c "$VERSION.*$MARKER\|\"$VERSION\".*$MARKER" "$file")
  if [ "$before" -ne "$after" ]; then
    echo "$file: expected $before versions, wrote $after" >&2
    exit 1
  fi
  echo "bumped $file ($after)"
done

# jq rather than sed: a lockfile holds thousands of versions and only these paths
# are ours. jq also keeps the JSON valid, which a stray regex would not.
jq --arg v "$VERSION" '.version = $v' frontend/package.json > /tmp/pkg.json
mv /tmp/pkg.json frontend/package.json
echo "bumped frontend/package.json"

jq --arg v "$VERSION" '.version = $v | .packages[""].version = $v' \
  frontend/package-lock.json > /tmp/lock.json
mv /tmp/lock.json frontend/package-lock.json
echo "bumped frontend/package-lock.json"
