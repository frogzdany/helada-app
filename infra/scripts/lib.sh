# Shared by the scripts in this folder (source it, do not run it).

REGION=us-east-1
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# On a laptop the `AWS_PROFILE` profile is used (`default` when unset); in CodeBuild the project's role is used.
if [ -z "${CODEBUILD_BUILD_ID:-}" ]; then
  export AWS_PROFILE="${AWS_PROFILE:-default}"
fi
export AWS_DEFAULT_REGION="$REGION"

need_stage() {
  case "${1:-}" in
    dev | prod) ;;
    *) echo "stage must be dev or prod, got '${1:-}'" >&2; exit 2 ;;
  esac
}

# Where the pipeline writes the commit a stage runs, after its checks passed.
commit_param() { echo "/helada/$1/deployed-commit"; }

# Prints the commit recorded for a stage, or nothing when there is none yet.
deployed_commit() {
  aws ssm get-parameter --name "$(commit_param "$1")" --query Parameter.Value --output text 2>/dev/null || true
}

# Reads changed paths on stdin and prints what has to be deployed for them:
#   all           infrastructure changed: every stack
#   backend       the image or the function: Backend stack (3 to 6 minutes more, and a new instance)
#   site          static files only: Edge stack (about a minute, the function is not touched)
#   backend+site  both of the above
#   none          nothing that is deployed (docs, tests, these scripts)
# The pipeline stack is deployed by hand (`npm run deploy:pipeline`), so its files count as none.
classify() {
  local all=0 backend=0 site=0 p
  while IFS= read -r p; do
    case "$p" in
      infra/lib/pipeline-stack.ts | infra/bin/pipeline.ts) ;;
      infra/lib/* | infra/bin/* | infra/package.json | infra/package-lock.json | infra/cdk.json | infra/cdk.context.json | infra/tsconfig.json)
        all=1 ;;
      Dockerfile.aws | Dockerfile.aws.dockerignore | app/backend/* | app/pyproject.toml | app/uv.lock | app/scripts/seed_demo_media.py | model/pyproject.toml | model/README.md | model/src/*)
        backend=1 ;;
      app/static/* | landing/*)
        site=1 ;;
    esac
  done
  if [ $all = 1 ]; then echo all
  elif [ $backend = 1 ] && [ $site = 1 ]; then echo backend+site
  elif [ $backend = 1 ]; then echo backend
  elif [ $site = 1 ]; then echo site
  else echo none
  fi
}

# What going from commit $1 to commit $2 needs. Everything when $1 is unknown (first run, or a
# commit that is not in this clone).
plan_between() {
  local from="$1" to="$2"
  if [ -z "$from" ] || ! git -C "$REPO_ROOT" cat-file -e "$from^{commit}" 2>/dev/null; then
    echo all
    return
  fi
  git -C "$REPO_ROOT" diff --name-only "$from" "$to" | classify
}

# True when the pipeline stack's own files differ between two commits.
pipeline_changed() {
  git -C "$REPO_ROOT" diff --name-only "$1" "$2" 2>/dev/null |
    grep -q -E '^infra/(lib/pipeline-stack\.ts|bin/pipeline\.ts)$'
}
