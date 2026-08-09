#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 VERSION CHANGELOG_PATH OUTPUT_PATH" >&2
  exit 2
fi

version=$1
changelog_path=$2
output_path=$3

awk -v version="$version" '
  $0 ~ "^## \\[" version "\\]" { found=1; next }
  found && /^## \[/ { exit }
  found { print }
' "$changelog_path" >"$output_path"

if [[ ! -s "$output_path" ]] || [[ -z "$(tr -d '[:space:]' <"$output_path")" ]]; then
  echo "::error::No non-empty CHANGELOG.md section found for ${version}" >&2
  exit 1
fi
