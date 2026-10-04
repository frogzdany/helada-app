#!/usr/bin/env bash
# The pipeline from a laptop: where things are, follow a run, send a commit to production.
#
#   release.sh status                 what each stage runs, what is waiting, and the state of this checkout
#   release.sh watch <dev|prod> [sha] wait for the run of a commit (default: origin/main) and report it
#   release.sh run dev [sha]          start the dev pipeline by hand (default: origin/main): a failed run, a push that started none
#   release.sh promote [sha]          start the prod pipeline for a commit (default: the one dev runs)
#   release.sh clean                  exit 0 only when nothing is left half done (see the end of this file)
set -euo pipefail
. "$(dirname "$0")/lib.sh"

g() { git -C "$REPO_ROOT" "$@"; }
short() { g rev-parse --short "$1" 2>/dev/null || echo "${1:0:7}"; }

# Latest run of a pipeline for a commit: "<status> <execution id>", or nothing.
run_for() {
  aws codepipeline list-pipeline-executions --pipeline-name "helada-$1" --max-items 20 \
    --query "pipelineExecutionSummaries[?sourceRevisions[0].revisionId=='$2'] | [0].[status,pipelineExecutionId]" \
    --output text 2>/dev/null | grep -v '^None' || true
}

last_run() {
  aws codepipeline list-pipeline-executions --pipeline-name "helada-$1" --max-items 1 \
    --query "pipelineExecutionSummaries[0].[status,sourceRevisions[0].revisionId]" --output text 2>/dev/null |
    head -1 | cut -c1-20 | tr '\t' ' ' || true
}

status() {
  g fetch --quiet origin main || echo "(could not fetch origin; main may be stale)"
  local main stage commit behind pending run
  main="$(g rev-parse origin/main)"
  echo "main      $(short "$main")  $(g log -1 --format=%s "$main")"
  for stage in dev prod; do
    commit="$(deployed_commit "$stage")"
    if [ -z "$commit" ]; then
      printf '%-9s nothing recorded yet\n' "$stage"
      continue
    fi
    behind="$(g rev-list --count "$commit..$main" 2>/dev/null || echo '?')"
    pending="$(plan_between "$commit" "$main")"
    printf '%-9s %s  %s\n' "$stage" "$(short "$commit")" "$(g log -1 --format='%s (%cr)' "$commit" 2>/dev/null || echo 'commit not in this clone')"
    if [ "$behind" = 0 ]; then
      echo "          up to date with main"
    else
      echo "          $behind commit(s) behind main; to deploy: $pending"
    fi
    run="$(last_run "$stage")"
    [ -n "$run" ] && echo "          last run: $run"
  done
  echo
  echo "checkout  $(g rev-parse --abbrev-ref HEAD) at $(short HEAD); $(g status --porcelain | wc -l | tr -d ' ') uncommitted path(s);" \
    "$(g rev-list --count origin/main..HEAD) ahead of and $(g rev-list --count HEAD..origin/main) behind origin/main"
}

watch() {
  local stage="$1" sha="${2:-}" run state id i
  need_stage "$stage"
  [ -n "$sha" ] || { g fetch --quiet origin main; sha="$(g rev-parse origin/main)"; }
  sha="$(g rev-parse "$sha")"
  # A push takes a few seconds to start a run; one that only touches undeployed paths starts none.
  for ((i = 0; i < 12; i++)); do
    run="$(run_for "$stage" "$sha")"
    [ -n "$run" ] && break
    sleep 10
  done
  if [ -z "$run" ]; then
    echo "no run of helada-$stage for $(short "$sha") after 2 minutes."
    echo "A push that changes nothing under app/, model/, landing/, infra/ or Dockerfile.aws starts none."
    echo "If it should have started one, start it: scripts/release.sh run $stage $(short "$sha")"
    return 3
  fi
  id="${run#*$'\t'}"
  echo "helada-$stage run $id for $(short "$sha")"
  for ((i = 0; i < 120; i++)); do
    state="$(aws codepipeline get-pipeline-execution --pipeline-name "helada-$stage" --pipeline-execution-id "$id" \
      --query pipelineExecution.status --output text)"
    case "$state" in
      InProgress) sleep 15 ;;
      Succeeded)
        echo "Succeeded. $stage runs $(short "$(deployed_commit "$stage")")."
        return 0 ;;
      *)
        echo "$state. End of the build log:"
        build_log "$id" "$stage"
        return 1 ;;
    esac
  done
  echo "still running after 30 minutes; look at it in the console"
  return 1
}

# Last lines of the CodeBuild log of a pipeline run (works with AWS CLI v1, which has no `logs tail`).
build_log() {
  local build group stream
  build="$(aws codepipeline list-action-executions --pipeline-name "helada-$2" --filter "pipelineExecutionId=$1" \
    --query "actionExecutionDetails[?actionName=='Deploy'] | [0].output.executionResult.externalExecutionId" --output text)"
  if [ -z "$build" ] || [ "$build" = None ]; then
    echo "(the run failed before the build started: check the Source action, usually the GitHub connection)"
    return
  fi
  read -r group stream < <(aws codebuild batch-get-builds --ids "$build" \
    --query 'builds[0].logs.[groupName,streamName]' --output text)
  aws logs get-log-events --log-group-name "$group" --log-stream-name "$stream" --limit 60 \
    --query 'events[].message' --output text | tr '\t' '\n' | grep -v '^$' | tail -45
}

# start <stage> <sha>: one run of a pipeline for exactly that commit. Prints the execution id.
start() {
  local stage="$1" sha="$2" from
  from="$(deployed_commit "$stage")"
  echo "$stage: ${from:+$(short "$from")}${from:-nothing recorded} -> $(short "$sha"); to deploy: $(plan_between "$from" "$sha")"
  aws codepipeline start-pipeline-execution --name "helada-$stage" \
    --source-revisions "actionName=Source,revisionType=COMMIT_ID,revisionValue=$sha" \
    --query pipelineExecutionId --output text
}

run() {
  [ "${1:-}" = dev ] || { echo "run is for dev; production goes through 'promote'" >&2; exit 2; }
  local sha="${2:-}"
  [ -n "$sha" ] || { g fetch --quiet origin main; sha=origin/main; }
  start dev "$(g rev-parse "$sha")"
}

promote() {
  local sha="${1:-$(deployed_commit dev)}"
  [ -n "$sha" ] || { echo "dev has no recorded commit; nothing to promote" >&2; exit 1; }
  start prod "$(g rev-parse "$sha")"
}

# Clean means: on main, nothing uncommitted, level with origin/main, both stages run that commit
# (or differ from it only in files that are not deployed), and no run is in progress.
clean() {
  local bad=0 main stage commit pending
  g fetch --quiet origin main
  main="$(g rev-parse origin/main)"
  no() { echo "not clean: $1"; bad=1; }
  [ "$(g rev-parse --abbrev-ref HEAD)" = main ] || no "the checkout is on $(g rev-parse --abbrev-ref HEAD), not main"
  [ -z "$(g status --porcelain)" ] || no "$(g status --porcelain | wc -l | tr -d ' ') uncommitted path(s)"
  [ "$(g rev-parse HEAD)" = "$main" ] || no "HEAD is not origin/main"
  for stage in dev prod; do
    commit="$(deployed_commit "$stage")"
    pending="$(plan_between "$commit" "$main")"
    [ -n "$commit" ] && commit="$(short "$commit")"
    [ "$pending" = none ] || no "$stage runs ${commit:-nothing recorded}; main still has to deploy: $pending"
    case "$(last_run "$stage")" in
      InProgress*) no "a helada-$stage run is in progress" ;;
      Failed*) no "the last helada-$stage run failed" ;;
    esac
  done
  [ $bad = 0 ] && echo "clean: main $(short "$main") is what dev and prod run, and nothing is pending"
  return $bad
}

main() {
  case "${1:-status}" in
    status) status ;;
    watch) watch "${2:-}" "${3:-}" ;;
    run) run "${2:-}" "${3:-}" ;;
    promote) promote "${2:-}" ;;
    clean) clean ;;
    *) sed -n '2,8p' "$0"; return 2 ;;
  esac
}
# On one line with `exit`: bash reads a script as it goes, so a watch that outlives an edit of this
# file (a pull, a branch switch) would otherwise pick up at the wrong place.
main "$@"; exit $?
