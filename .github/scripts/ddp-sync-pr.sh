#!/usr/bin/env bash
# Keeps one standing PR from the mirror branch sync/upstream into main (PLAN-fork-management.md §8.4).
# Identical in all three code forks; UPSTREAM_REPO and TRACKED_PATHS come from the workflow's env.
set -euo pipefail
git remote add upstream "https://github.com/${UPSTREAM_REPO}.git"
git fetch --quiet upstream main
pr=$(gh pr list --head sync/upstream --state open --json number -q '.[0].number')
behind=$(git rev-list --count --first-parent origin/main..upstream/main)
if [ "$behind" -eq 0 ]; then
  if [ -n "$pr" ]; then gh pr close "$pr" --comment "main now contains upstream; nothing to merge."; fi
  echo "up to date"; exit 0
fi
read -ra paths <<< "$TRACKED_PATHS"               # a space-separated list in the workflow env, one pathspec per word
tracked=$(git diff --name-only origin/main...upstream/main -- "${paths[@]}" | wc -l | tr -d ' ')
set +e   # git exits 1 for conflicts AND for a bad ref, so judge the output, not the exit code
mt=$(git merge-tree --write-tree --name-only --no-messages origin/main upstream/main 2>/tmp/mt.err)
set -e
[[ "$(printf '%s\n' "$mt" | head -1)" =~ ^[0-9a-f]{40,64}$ ]] || { echo "merge-tree failed: $(cat /tmp/mt.err)"; exit 1; }
conflicts=$(printf '%s\n' "$mt" | sed 1d | grep -v '^$' || true)   # a real result starts with a tree id; conflicts are the lines after it
git push --force origin upstream/main:refs/heads/sync/upstream   # sync/upstream is owned by this job alone: an exact mirror of upstream, never committed to by hand
title="Sync upstream (${behind} commits)"
body="Upstream mainline commits: ${behind}. Files in tracked paths: ${tracked}.
Would conflict today: ${conflicts:-none}
Procedure: PLAN-fork-management.md §5.B, then the smoke test in §5.B.5."
if [ -z "$pr" ]; then gh pr create --base main --head sync/upstream --title "$title" --body "$body"
else gh pr edit "$pr" --title "$title" --body "$body"; fi
