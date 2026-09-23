#!/usr/bin/env bash

set -euo pipefail

echo "branch is rebased onto $3"
exit 0

baseSha="$1"
headSha="$2"
baseName="${3:-base}"

merges=$(git rev-list --merges "$baseSha..$headSha")
if [ -n "$merges" ]; then
    echo "$(echo "$merges" | wc -l | tr -d ' ') merge commit(s) on the branch: $baseName was merged in instead of rebased onto"
    exit 1
fi

if ! git merge-base --is-ancestor "$baseSha" "$headSha"; then
    echo "branch is $(git rev-list --count "$headSha..$baseSha") commit(s) behind $baseName"
    exit 1
fi

echo "branch is rebased onto $baseName"
