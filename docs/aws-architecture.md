# Helada on AWS

Region `us-east-1`, domain `helada.app` (a Route 53 zone in the account).
Modelled in CDK under `infra/`. Stages: `dev` and `prod`.

## 1. What it is

A serverless setup with no servers to run and almost no fixed cost:

- The **phone page** and the **dashboard page** are static files in one private S3 bucket, served by CloudFront.
- The **app** (FastAPI) runs as one Lambda function from a container image, behind an API Gateway HTTP API that only CloudFront can call.
- **Data is durable**: every row the app writes is stored in a DynamoDB table and every media file (voice notes, photos, PDFs) in an S3 bucket.

The app's own code is unchanged. It still works on a local SQLite file, the same code that runs offline on a laptop or a Raspberry Pi. One added module, `app/backend/cloud.py`, keeps that file in step with DynamoDB and S3 when the app runs on AWS.

## 2. Components

| Piece | AWS service | Notes |
|---|---|---|
| DNS and TLS | Route 53, ACM | dev: `dev.helada.app` (landing), `m-dev.helada.app` (phone), `panel-dev.helada.app` (dashboard). prod: `helada.app`, `m.helada.app`, `panel.helada.app`. One certificate per stage |
| Sites | CloudFront (3 distributions: landing, phone, dashboard) over one private S3 bucket | HTTP/2 and 3, TLS 1.2+, security headers. `www.helada.app` redirects to the landing page |
| API front | API Gateway HTTP API | One `$default` route; a small authorizer function checks a secret header that CloudFront adds, so the API cannot be called directly; throttled per stage |
| App | Lambda, container image, arm64 | FastAPI through Lambda Web Adapter; 3008 MB, one instance at a time. The API waits 30 s for an answer; the function may run up to 120 s so slow work still finishes |
| Rows | DynamoDB, one on-demand table | `pk` = the app's table name, `sk` = the row's key. Point-in-time recovery (35 days). Retained and deletion-protected in prod |
| Media | S3, private bucket | Farmers' uploads expire after 30 days (dev) or 90 (prod) |
| Secret | Secrets Manager (1 secret) | The origin-verify header value |
| Watching | CloudWatch, SNS, Budgets | 4 alarms (errors, throttles, 5xx, latency) and a monthly budget, all to one topic |

## 3. Diagram and request flows

![Helada on AWS: one serverless stage behind CloudFront, deployed as dev and prod by one pipeline](helada-aws-architecture.drawio.png)

The numbers on the diagram are explained in its own legend. The source is [`helada-aws-architecture.drawio`](helada-aws-architecture.drawio) (open it in draw.io; the PNG also carries the diagram and can be opened the same way). The list below gives the same flows page by page.

1. **Phone page**: phone → Route 53 → CloudFront `m.helada.app` → S3 `movil/`. The service worker caches the shell and the model runs on the device. The forecast comes straight from Open-Meteo.
2. **Phone outbox**: phone → CloudFront `m.helada.app/api/*` → HTTP API → app function (same origin, so no CORS).
3. **Dashboard page**: browser → CloudFront `panel.helada.app` → S3 `dashboard/`. A CloudFront Function maps `/` to `index.html` and redirects `/movil`.
4. **Dashboard API and media**: browser → CloudFront `/api/*`, `/files/*` → HTTP API (authorizer checks the secret header) → app function.
5. **Writes**: app function → DynamoDB (changed rows) and S3 (new media), before the response is sent.
6. **New instance**: app function ← DynamoDB (all rows) and S3 (media files), before it takes its first request.
7. **Forecast**: app function → Open-Meteo.
8. **Operations**: function logs and API access logs → CloudWatch; alarms and budget → SNS → email.

```
                    Route 53 (helada.app)
                           |
          +----------------+-----------------+
          |                                  |
   CloudFront: phone                  CloudFront: dashboard         (ACM certificate)
   m.helada.app                       panel.helada.app
     |         |                        |              |
     | /api/*  |                 / , /static/*    /api/*, /files/*, /webhooks/*
     v         |                        v              |
  S3 movil/    +-----------+       S3 dashboard/       |
                           v                           v
                 API Gateway HTTP API  <- authorizer function + secret (origin check)
                           |
                 Lambda "app" (FastAPI container, arm64)  ----> Open-Meteo (forecast)
                     |                    |
                     v                    v
              DynamoDB table         S3 media bucket
              (every row)            (voice notes, photos, PDFs)
```

## 4. How the data layer works

- **While running.** SQLite triggers note every inserted, updated or deleted row. Before the last byte of each response, the changed rows are written to DynamoDB and new or removed media files are synced to S3. Lambda freezes the process right after a response, which is why this happens before it, not after.
- **When a new instance starts.** Before the app is created, all rows are loaded from DynamoDB into a fresh SQLite file and the media files are copied back. The audit hash chain continues where it left off.
- **If DynamoDB cannot be reached.** At start the instance refuses to come up rather than run blank. During a request, the response still goes out and the changes are written with the next request.
- **Status.** `GET /api/storage` shows the table, the bucket, how much was restored and written, and the last error if any.

## 5. Limits to know

- **One instance at a time.** SQLite is one instance's working copy, so two instances would overwrite each other's rows. The function is capped at one: requests queue behind a slow one, and under load some are turned away (there is an alarm). The path to many instances is to read and write DynamoDB directly in the request handlers; the table layout already matches the app's tables.
- **Start-up grows with the data.** A new instance loads every row and file. Fine for a pilot with thousands of rows; at larger scale load on demand instead.
- **A write is durable when the response is sent, not before.** A crash in the middle of a request loses that request's changes only.
- **30 seconds per answer, about 4.5 MB per upload or download.** Measured on dev: a page or API call takes about 0.3 s, the first call on a new instance about 7 s, a voice note 7 to 11 s (speech-to-text runs inside the function).
- **WhatsApp/SMS through Twilio is not wired.** The webhook path is routed, but the app has no Twilio credentials, so it answers 403. The demo uses the simulated channel.
- **No sign-in.** Anyone with the URL can use the dashboard, including "reset demo", which clears the stored data too.

## 6. Cost (monthly, list prices, demo traffic)

| Item | Cost |
|---|---|
| CloudFront, S3, Lambda, DynamoDB, CloudWatch alarms | 0 (inside the free tiers at this traffic) |
| HTTP API | about 0.25 USD per dashboard tab left open 8 h on working days (it polls every 2.5 s) |
| Secrets Manager (1 secret) | 0.40 USD |
| Image storage in ECR | about 0.10 USD per kept version |
| Route 53 zone (already paid) | 0.50 USD |
| **Total per stage** | **about 1 to 2 USD** |

Nothing bills while idle except the secret, the stored image and the zone. DynamoDB on-demand costs about 0.63 USD per million writes and 0.13 USD per million reads beyond the free tier. Estimates, not quotes.

## 7. When to add more

| When | Add | Cost |
|---|---|---|
| More than one instance is needed | Query DynamoDB directly in the handlers, drop the single-instance cap | none |
| Voice notes or alert runs hit the 30 s limit | A second function fed by an SQS queue for speech, PDFs and sending | near zero |
| Alerts should go out by themselves every evening | EventBridge Scheduler calling the app at 17:30 and during the 18:00 to 20:00 window | free tier |
| Real WhatsApp/SMS | Twilio credentials in a secret, read by the app | 0.40 USD |
| The site is attacked or scraped | AWS WAF on CloudFront | about 8 USD |
| Staff need accounts | Cognito sign-in in front of `/api/*` | free under 10,000 users |

See `infra/README.md` for how to deploy and push changes.

## 8. State of the deployment

`dev` is deployed and checked end to end on 2026-10-03: both sites load, a chat written through the API was in DynamoDB and came back on a new instance with the audit chain intact, voice notes are transcribed, and the API rejects calls that do not come through CloudFront. `prod` is deployed since the same day: landing page `https://helada.app`, phone page `https://m.helada.app`, dashboard `https://panel.helada.app`.

Changes reach both stages through two pipelines (CodePipeline and CodeBuild, `infra/lib/pipeline-stack.ts`): a merge to `main` deploys to `dev`, and production starts only on request, for a commit `dev` already runs. `infra/scripts/release.sh status` shows the commit each stage runs; the rest is in `infra/README.md`, section "Pipeline".

