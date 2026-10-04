#!/usr/bin/env bash
# What the pipeline runs for one stage (see ../buildspec.yml): work out what changed since the
# commit the stage runs, test, deploy only that, check the result, record the commit.
#
#   ci-deploy.sh <dev|prod>          run it (in CodeBuild)
#   ci-deploy.sh <dev|prod> --plan   only print what a run would deploy for HEAD (safe anywhere)
#
# FORCE_TARGET=all|backend|site|backend+site overrides the detection.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

STAGE="${1:-}"
need_stage "$STAGE"
PLAN_ONLY="${2:-}"

# The pipeline hands CodeBuild a full clone checked out at the commit to deploy.
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
LAST="$(deployed_commit "$STAGE")"
TARGET="${FORCE_TARGET:-$(plan_between "$LAST" "$COMMIT")}"

echo "stage:    $STAGE"
echo "commit:   $COMMIT"
echo "replaces: ${LAST:-nothing recorded (first run: every stack)}"
echo "deploys:  $TARGET"
if [ -n "$LAST" ] && pipeline_changed "$LAST" "$COMMIT"; then
  echo "note:     the pipeline's own files changed; it is updated by hand with 'npm run deploy:pipeline'"
fi
[ "$PLAN_ONLY" = "--plan" ] && exit 0

# Production only takes what dev has already run: the same commit, or one from before it.
if [ "$STAGE" = prod ]; then
  DEV="$(deployed_commit dev)"
  if [ -z "$DEV" ] || ! git -C "$REPO_ROOT" merge-base --is-ancestor "$COMMIT" "$DEV"; then
    echo "refused: $COMMIT has not been deployed to dev (dev runs ${DEV:-nothing recorded})" >&2
    exit 1
  fi
fi

cd "$REPO_ROOT/infra"

# Tests run on the way to dev; prod only takes commits that passed there.
# The three suites are independent, so they run at once (about 35 s instead of 70).
if [ "$STAGE" = dev ]; then
  (npx tsc --noEmit && npx jest) &
  INFRA_TESTS=$!
  (cd ../model && uv run pytest -q) &
  MODEL_TESTS=$!
  (cd ../app && uv run pytest -q) &
  APP_TESTS=$!
  FAILED_TESTS=""
  wait $INFRA_TESTS || FAILED_TESTS="$FAILED_TESTS infra"
  wait $MODEL_TESTS || FAILED_TESTS="$FAILED_TESTS model"
  wait $APP_TESTS || FAILED_TESTS="$FAILED_TESTS app"
  if [ -n "$FAILED_TESTS" ]; then
    echo "tests failed:$FAILED_TESTS; nothing was deployed" >&2
    exit 1
  fi
fi

if [ "$TARGET" != none ]; then
  case "$TARGET" in
    all) STACKS=(--all) ;;
    backend) STACKS=(--exclusively "Helada-$STAGE-Backend") ;;
    site) STACKS=(--exclusively "Helada-$STAGE-Edge") ;;
    backend+site) STACKS=(--exclusively "Helada-$STAGE-Backend" "Helada-$STAGE-Edge") ;;
    *) echo "unknown target '$TARGET'" >&2; exit 2 ;;
  esac

  # One synth (it also runs the cdk-nag checks); the diff and the deploy both read its output.
  OUT="cdk.out.ci-$STAGE"
  npx cdk synth -c "stage=$STAGE" -o "$OUT" --quiet
  # The diff stays in the build log as the record of what this run changed.
  if [ "$TARGET" = all ]; then
    npx cdk diff --app "$OUT" --no-change-set
  else
    npx cdk diff --app "$OUT" --no-change-set "${STACKS[@]}"
  fi
  BUILDX_NO_DEFAULT_ATTESTATIONS=1 npx cdk deploy --app "$OUT" --require-approval never --method direct --concurrency 2 "${STACKS[@]}"

  ./scripts/smoke.sh "$STAGE"
fi

aws ssm put-parameter --name "$(commit_param "$STAGE")" --type String --overwrite --value "$COMMIT" >/dev/null
echo "recorded: $STAGE runs $COMMIT"
