#!/usr/bin/env bash
#
# Print the body of the dev -> main production deploy PR.
#
# Read straight from git, with no model in the loop, because the reader is
# deciding whether production needs a maintenance window. A generated summary
# has to work from a truncated diff and once announced a deploy as carrying no
# schema migrations while it carried three. `git diff --name-status` cannot make
# that mistake, and costs nothing to run again every time dev moves.
#
# Usage: deploy-pr-body.sh <base-ref> <head-ref> [tag]
set -euo pipefail

BASE=${1:?base ref}
HEAD=${2:?head ref}
TAG=${3:-}
REPO_URL=${REPO_URL:-}

# GitHub rejects a body over 65536 characters. Keep well clear of it.
#
# Capped with sed rather than head: head closes the pipe as soon as it has its
# lines, the writer dies of SIGPIPE, and pipefail turns that into a failed
# script — aborting on exactly the large deploys the cap exists for. sed reads
# its input to the end.
MAX_FILES=400
MAX_COMMITS=300

# Anything that changes how production runs rather than what it computes.
# docker-compose.yml is here because it carries the lagoon.type service
# definitions, not only local development, and scripts/ because .lagoon.yml
# names those files as the cronjob commands.
SENSITIVE='^(app/migrations/versions/|scripts/|\.lagoon\.yml$|docker-compose\.yml$|helm/|Dockerfile$|frontend/Dockerfile$|frontend/next\.config\.ts$|backend-start\.sh$|requirements\.txt$|app/core/config\.py$)'

range="$(git rev-parse --short "$BASE")...$(git rev-parse --short "$HEAD")"
commits=$(git rev-list --count "$BASE..$HEAD")
files=$(git diff --name-status "$BASE..$HEAD")
total_files=$(printf '%s\n' "$files" | grep -c . || true)

if [ -n "$TAG" ] && [ -n "$REPO_URL" ]; then
  echo "Deploys [\`$TAG\`]($REPO_URL/releases/tag/$TAG) to production."
elif [ -n "$TAG" ]; then
  echo "Deploys \`$TAG\` to production."
else
  echo "Deploys \`$HEAD\` to production."
fi
echo
echo "\`$range\` — $commits commits, $total_files files."
echo

echo "## ⚠️ Check before merging"
echo
# Last field, not second: a rename prints "R100 <old> <new>", and the path that
# ships is the destination. Documentation under a sensitive path cannot change
# how production runs.
sensitive=$(printf '%s\n' "$files" | awk '{print $NF}' | grep -E "$SENSITIVE" | grep -vE '\.md$' || true)
if [ -z "$sensitive" ]; then
  echo "Nothing that changes how production runs: no migrations, no Lagoon,"
  echo "Helm, Docker or settings changes."
else
  migrations=$(printf '%s\n' "$sensitive" | grep '^app/migrations/versions/' || true)
  if [ -n "$migrations" ]; then
    echo "**Schema migrations ($(printf '%s\n' "$migrations" | grep -c .))** — alembic runs these at deploy:"
    echo
    # shellcheck disable=SC2016 # the backticks are markdown, not a subshell
    printf '%s\n' "$migrations" | sed 's|.*/||; s|\.py$||; s|^|- `|; s|$|`|'
    echo
  fi
  rest=$(printf '%s\n' "$sensitive" | grep -v '^app/migrations/versions/' || true)
  if [ -n "$rest" ]; then
    echo "**Runtime configuration** — check for new required secrets, changed"
    echo "defaults and resource limits:"
    echo
    # shellcheck disable=SC2016 # the backticks are markdown, not a subshell
    printf '%s\n' "$rest" | sed 's|^|- `|; s|$|`|'
    echo
  fi
fi
echo

echo "## Commits"
echo
echo '```'
git log --oneline "$BASE..$HEAD" | sed -n "1,${MAX_COMMITS}p"
if [ "$commits" -gt "$MAX_COMMITS" ]; then
  echo "... and $((commits - MAX_COMMITS)) more"
fi
echo '```'
echo

echo "<details><summary>Changed files ($total_files)</summary>"
echo
echo '```'
printf '%s\n' "$files" | sed -n "1,${MAX_FILES}p"
if [ "$total_files" -gt "$MAX_FILES" ]; then
  echo "... and $((total_files - MAX_FILES)) more"
fi
echo '```'
echo
echo "</details>"
echo
echo "Written from the diff by the Release Please workflow. Refreshed on every"
echo "push to dev, so it always describes what merging this PR would deploy."
