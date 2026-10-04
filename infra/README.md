# Helada infrastructure (AWS CDK)

Serverless deployment of the landing page, the phone page and the dashboard, as two stages (`dev`, `prod`). The design and its limits are in [`../docs/aws-architecture.md`](../docs/aws-architecture.md).

## Layout

| Path | What |
|---|---|
| `bin/helada.ts` | Entry point: wires the four stacks for one stage |
| `lib/config.ts` | Everything that differs between `dev` and `prod` |
| `lib/data-stack.ts` | The DynamoDB table (rows) and the media bucket (files) |
| `../app/backend/cloud.py` | The app's side of it: loads state from the table and bucket at start, stores changes before each response |
| `lib/backend-stack.ts` | Container image, the app function, the HTTP API and its origin check |
| `lib/edge-stack.ts` | CloudFront distributions, site bucket and upload, certificate, DNS |
| `lib/observability-stack.ts` | Alarms and budget |
| `lib/nag.ts` | Helper to accept a cdk-nag finding with its reason |
| `lib/pipeline-stack.ts`, `bin/pipeline.ts` | The pipelines that deploy both stages from GitHub (their own CDK app) |
| `buildspec.yml`, `scripts/` | What a pipeline run does, and the same steps for a laptop: plan, checks, status |
| `../Dockerfile.aws` | The image the function runs (FastAPI through Lambda Web Adapter, arm64) |

> The pipelines read the repository named by `owner` and `repo` in `bin/pipeline.ts`; to deploy from a fork, change those two values. `cdk.context.json` is not committed: the first `cdk synth` with AWS credentials looks up the hosted zone and writes it.

## Commands

```bash
npm ci
npm test                                         # CDK assertions
npx cdk synth  -c stage=dev                      # also runs the cdk-nag checks
```

`-c stage=prod` selects production. `-c nag=false` skips the checks while iterating.

## Pipeline

Merging to `main` deploys to dev; production is one command away. Nothing is built on a laptop.

| Pipeline | Starts | Does |
|---|---|---|
| `helada-dev` | A push to `main` that changes `app/`, `model/`, `landing/`, `infra/` or `Dockerfile.aws` | Tests, deploys what changed, checks the sites, records the commit |
| `helada-prod` | Only when asked: `scripts/release.sh promote` | Deploys a commit that dev already runs, checks the sites, records the commit |

Both run `buildspec.yml` in one CodeBuild project (`helada-deploy`, arm64). A run compares its commit with the one the stage runs (kept in Parameter Store, `/helada/<stage>/deployed-commit`) and deploys only what the changed files need (`scripts/lib.sh`):

| Changed | Deploys | A run takes |
|---|---|---|
| `app/static`, `landing` | Edge stack | about 3 minutes (estimate) |
| `app/backend`, `model/src`, `Dockerfile.aws`, lock files | Backend stack (new image, new instance) | about 5 minutes (estimate) |
| `infra/lib`, `infra/bin`, CDK settings | Every stack | 7 minutes with a new image (measured 2026-10-03) |
| Anything else (docs, tests) | Nothing; the commit is recorded | 1.5 minutes (measured) |

A run spends about a minute before the tests (start the build, clone, install) and 40 seconds on them; the image takes a minute to build and push, CloudFormation 2 to 3 minutes for every stack, and the checks 30 seconds while the new instance starts.

Promoting to production reuses the image dev built and skips the tests: every stack took 4 minutes (measured).

```bash
scripts/release.sh status          # what dev and prod run, what main still has to deploy
scripts/ci-deploy.sh dev --plan    # what a run would deploy for the commit checked out here
scripts/release.sh watch dev       # wait for the run of origin/main and report it (build log if it failed)
scripts/release.sh run dev         # start dev by hand: after a failed run, or when a push started none
scripts/release.sh promote         # start production for the commit dev runs
scripts/release.sh watch prod <sha>
scripts/smoke.sh dev               # the read-only checks a run ends with
scripts/release.sh clean           # exit 0 when main is committed, pushed and running on both stages
```

Runs of one pipeline never overlap; pushes that arrive during a run are deployed together by the next one. To go back, promote an older commit (`scripts/release.sh promote <sha>`) or revert on `main`.

**Setting it up (once).** `npm run diff:pipeline`, then `npm run deploy:pipeline`. The stack creates the GitHub connection as *pending*: open the `ConnectionConsole` address it prints, choose `helada-github`, *Update pending connection*, and install the AWS connector on the repository named in `bin/pipeline.ts`. Both pipelines start once when they are created and fail at Source until that is done; that is expected. A push that reaches GitHub before the connection is approved starts nothing: start that one with `scripts/release.sh run dev`. The first real run finds no recorded commit and deploys every stack, so `main` must already hold everything the stages run.

The pipelines do not update themselves: after changing `lib/pipeline-stack.ts` or `bin/pipeline.ts`, run `npm run deploy:pipeline` again. `buildspec.yml` and `scripts/` are read from the commit being deployed.

There is no CodeDeploy step. It shifts traffic between two versions of a function, and this app has to run as one instance (two would overwrite each other's rows).

## Pushing changes by hand

The pipeline is the usual way. A deploy from a laptop still works, for trying something on dev before it is merged; it does not change the recorded commit, so the next pipeline run deploys over it.

Docker must be running for anything that touches the app. Each script shows the changes and asks before touching IAM. They use the `default` AWS profile unless `AWS_PROFILE` is set.

| You changed | Run |
|---|---|
| Phone page or dashboard files (`app/static`) | `npm run deploy:dev:site` |
| Backend code (`app/backend`, `model/`) | `npm run deploy:dev:app` |
| Infrastructure, or both of the above | `npm run deploy:dev` |
| Want to see what would change first | `npm run diff:dev` |

`app/static` is also baked into the image, but the sites are served from S3, so a static change needs only `deploy:dev:site`; it uploads the files and clears the edge cache. After changing the phone page run `app/scripts/build_movil_pack.py --stamp` first so the service worker version changes. `npm run deploy:prod` deploys production (`m.helada.app`, `panel.helada.app`).

Deploying the app replaces the running instance; the new one loads its data back from DynamoDB and S3. `https://<dashboard host>/api/storage` shows what was restored and written.

## Deploy flow

1. **Test.** `npm test` here; `uv run pytest -q` in `../app` if backend code changed.
2. **Stamp the phone page** if it changed: `uv run python scripts/build_movil_pack.py --stamp` in `../app`.
3. **Look at the change:** `npm run diff:dev`. Check for removals and replacements before going on.
4. **Deploy** with the script that fits (table above). An image change takes 3 to 6 minutes.
5. **Check the result:**

   ```bash
   B=https://panel-dev.helada.app
   curl -s $B/api/storage          # last_error must be null; restored_rows > 0 on a new instance
   curl -s -o /dev/null -w '%{http_code}\n' $B/api/parcels
   curl -s -o /dev/null -w '%{http_code}\n' https://m-dev.helada.app/sw.js
   ```

   Then open the dashboard, send a text and a voice note in the simulator, and reload the phone page.

Things that look like failures and are not:

- The first request after an image change takes 7 to 25 seconds while Lambda pulls the image; a 503 in that window clears by itself.
- The first voice note after an image change runs past 30 seconds (the speech model is read from the new image for the first time) and its reply is lost. Send one sample voice note in the simulator after every app deploy and wait a minute; the next ones take 7 to 12 seconds.
- The function runs one request at a time. While a slow one runs (a voice note takes 7 to 11 seconds), other requests are turned away.

If it does fail, read the function's log: the log group is in `aws lambda get-function-configuration --function-name <ApiFunctionName output> --query LoggingConfig.LogGroup`.

## Good to know

- Pass `-c alarmEmail=you@example.com` to get alarms and budget notices by email (confirm the subscription email afterwards). Without it they exist but notify nobody, and the synth prints a warning.
- The budget counts resources tagged `Project=helada` and the stage; activate the `Project` and `Stage` cost allocation tags in Billing for it to match.
- Rotating the origin-verify secret: put a new value in the secret, bump `originVerifyRevision` in `lib/config.ts`, deploy the Edge stack. The authorizer accepts the current and the previous value, so requests keep working during the switch.

## Switches (in `lib/config.ts`)

| Setting | Default | Meaning |
|---|---|---|
| `landing` | `true` | Publish `landing/` on the landing host (`helada.app` in prod, `dev.helada.app` in dev) |
| `domain` | `helada.app` hosts | Remove it to use the default CloudFront names |
| `api.memoryMb` | `3008` | Speech-to-text needs about 1.3 GB on top of the app, and more memory means more CPU: at 2048 a voice note did not finish in 30 s |

## Removing a stage

`npx cdk destroy -c stage=dev --all` deletes everything in dev, data included. In prod the table and the media bucket are kept, and the table is protected from deletion. The image versions in the CDK asset repository stay (clean with `cdk gc`).
