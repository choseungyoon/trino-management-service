#!/usr/bin/env bash

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
branch=$(git branch --show-current)

case "$branch" in
  ""|main|master)
    printf '%s\n' "refusing to push protected branch: ${branch:-detached HEAD}" >&2
    exit 2
    ;;
esac

git push --set-upstream origin "$branch"
