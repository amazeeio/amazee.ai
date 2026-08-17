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

# GitHub rejects a body over 65536 characters, and a rejected body leaves the
# deploy PR unopened or unrefreshed. Budget in characters, because that is what
# GitHub counts: a line cap says nothing about the size of a body whose paths
# are deep and whose commit subjects are long.
#
# MAX_CHARS must stay well above WARN_BUDGET: the warning section is the one
# part that is never sacrificed, so it is bounded by WARN_BUDGET rather than by
# what is left over, and a MAX_CHARS below it could not hold even that. 60000
# against 16000 leaves the supporting lists ~43000 and keeps ~5500 spare under
# GitHub's own 65536.
MAX_CHARS=60000
WARN_BUDGET=16000

# Anything that changes how production runs rather than what it computes.
# docker-compose.yml is here because it carries the lagoon.type service
# definitions, not only local development, and scripts/ because .lagoon.yml
# names those files as the cronjob commands.
SENSITIVE='^(app/migrations/versions/|scripts/|\.lagoon\.yml$|docker-compose\.yml$|helm/|Dockerfile$|frontend/Dockerfile$|frontend/next\.config\.ts$|backend-start\.sh$|requirements\.txt$|app/core/config\.py$)'

# Print whole lines from stdin while they fit the character budget, then say how
# many were dropped. awk reads to the end of its input, unlike head, which closes
# the pipe and kills the writer with SIGPIPE — under pipefail that aborted the
# whole script on exactly the large deploys a cap exists for.
fit() {
  awk -v budget="$1" '
    {
      n = length($0) + 1
      if (used + n > budget) { skipped++; next }
      used += n
      print
    }
    END { if (skipped) printf "... and %d more\n", skipped }
  '
}

range="$(git rev-parse --short "$BASE")...$(git rev-parse --short "$HEAD")"
commits=$(git rev-list --count "$BASE..$HEAD")
files=$(git diff --name-status "$BASE..$HEAD")
total_files=$(printf '%s\n' "$files" | grep -c . || true)

# Last field, not second: a rename prints "R100 <old> <new>", and the path that
# ships is the destination. Documentation under a sensitive path cannot change
# how production runs.
sensitive=$(printf '%s\n' "$files" | awk '{print $NF}' | grep -E "$SENSITIVE" | grep -vE '\.md$' || true)
migrations=$(printf '%s\n' "$sensitive" | grep '^app/migrations/versions/' || true)
rest=$(printf '%s\n' "$sensitive" | grep -v '^app/migrations/versions/' || true)

# The header and the warning are what the approver must see, so build them first
# and let the supporting lists live on whatever budget they leave.
head_part=$(mktemp)
{
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
  if [ -z "$sensitive" ]; then
    echo "Nothing that changes how production runs: no migrations, no Lagoon,"
    echo "Helm, Docker, cron or settings changes."
  else
    if [ -n "$migrations" ]; then
      echo "**Schema migrations ($(printf '%s\n' "$migrations" | grep -c .))** — alembic runs these at deploy:"
      echo
      # shellcheck disable=SC2016 # the backticks are markdown, not a subshell
      printf '%s\n' "$migrations" | sed 's|.*/||; s|\.py$||; s|^|- `|; s|$|`|' |
        fit $((WARN_BUDGET / 2))
      echo
    fi
    if [ -n "$rest" ]; then
      echo "**Runtime configuration** — check for new required secrets, changed"
      echo "defaults, cron commands and resource limits:"
      echo
      # shellcheck disable=SC2016 # the backticks are markdown, not a subshell
      printf '%s\n' "$rest" | sed 's|^|- `|; s|$|`|' | fit $((WARN_BUDGET / 2))
      echo
    fi
  fi
} > "$head_part"

footer="Written from the diff by the Release Please workflow. Refreshed on every
push to dev, so it always describes what merging this PR would deploy."

# 400 covers the headings, the code fences and the <details> wrapper. Floor at
# zero rather than at some comfortable minimum: with a budget of zero `fit`
# prints only its "... and N more" line, which keeps the total under MAX_CHARS
# whatever the warning section cost.
left=$((MAX_CHARS - $(wc -c < "$head_part") - ${#footer} - 400))
[ "$left" -lt 0 ] && left=0
commit_budget=$((left * 2 / 5))
file_budget=$((left - commit_budget))

cat "$head_part"
rm -f "$head_part"
echo
echo "## Commits"
echo
echo '```'
git log --oneline "$BASE..$HEAD" | fit "$commit_budget"
echo '```'
echo
echo "<details><summary>Changed files ($total_files)</summary>"
echo
echo '```'
printf '%s\n' "$files" | fit "$file_budget"
echo '```'
echo
echo "</details>"
echo
echo "$footer"
