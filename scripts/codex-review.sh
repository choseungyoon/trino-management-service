#!/usr/bin/env bash

set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2

if ! command -v codex >/dev/null 2>&1; then
  printf '%s\n' 'Codex CLI is unavailable; status: IMPLEMENTED_UNREVIEWED' >&2
  exit 2
fi

scope=(--uncommitted)
if [[ "${1:---uncommitted}" == "--base" ]]; then
  [[ -n "${2:-}" ]] || {
    printf '%s\n' 'usage: codex-review.sh [--uncommitted | --base <ref>]' >&2
    exit 2
  }
  scope=(--base "$2")
elif [[ "${1:---uncommitted}" != "--uncommitted" ]]; then
  printf '%s\n' 'usage: codex-review.sh [--uncommitted | --base <ref>]' >&2
  exit 2
fi

codex exec review \
  "${scope[@]}" \
  --ephemeral \
  --model gpt-5.6-sol \
  -c model_reasoning_effort=medium
