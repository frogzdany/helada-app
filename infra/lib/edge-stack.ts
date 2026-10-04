import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { ORIGIN_VERIFY_HEADER } from './backend-stack';
import { StageConfig } from './config';
import { acknowledge, BASIC_EXECUTION_ROLE } from './nag';

export interface EdgeStackProps extends cdk.StackProps {
  readonly config: StageConfig;
  readonly apiDomainName: string;
  readonly originVerifySecret: secretsmanager.ISecret;
}

const STATIC_DIR = path.join(__dirname, '..', '..', 'app', 'static');
const LANDING_DIR = path.join(__dirname, '..', '..', 'landing');
const ASSET_EXCLUDES = ['.DS_Store', '**/.DS_Store', '**/.gitignore'];

/**
 * The public sites, each a CloudFront distribution over one private bucket:
 *   dashboard: `/` and `/static/*` from S3; `/api/*`, `/files/*` and `/webhooks/*` from the HTTP API
 *   phone:     the PWA from S3, plus `/api/*` so its outbox reaches the API on its own origin
 *   landing:   the public page (`landing/`), with `www` redirected to it; only when switched on
 * CloudFront certificates must live in us-east-1, which is also where this stack runs.
 */
export class EdgeStack extends cdk.Stack {
  readonly dashboard: cloudfront.Distribution;
  readonly phone: cloudfront.Distribution;
  /** Only when `config.landing` is on. */
  readonly landing?: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: EdgeStackProps) {
    super(scope, id, props);
    const { config } = props;

    const siteBucket = new s3.Bucket(this, 'SiteBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // Site files are rebuilt from the repo on every deploy, so the bucket is never worth keeping.
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ---- domain ----------------------------------------------------------------------------
    let zone: route53.IHostedZone | undefined;
    let certificate: acm.ICertificate | undefined;
    if (config.domain) {
      zone = route53.HostedZone.fromLookup(this, 'Zone', { domainName: config.domain.zoneName });
      certificate = new acm.Certificate(this, 'Certificate', {
        domainName: config.domain.dashboardHost,
        subjectAlternativeNames: [
          config.domain.phoneHost,
          ...(config.landing ? [config.domain.landingHost, ...(config.domain.landingRedirectHosts ?? [])] : []),
        ],
        validation: acm.CertificateValidation.fromDns(zone),
      });
    }

    // ---- origins ---------------------------------------------------------------------------
    const apiOrigin = new origins.HttpOrigin(props.apiDomainName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
      readTimeout: cdk.Duration.seconds(30),
      // Resolved by CloudFormation at deploy time; never written to the template in clear text.
      customHeaders: {
        [ORIGIN_VERIFY_HEADER]: props.originVerifySecret.secretValue.unsafeUnwrap(),
        // Not checked by anyone. Changing it changes the distribution, which is what makes
        // CloudFormation read the secret again after a rotation.
        'x-helada-origin-verify-rev': String(config.originVerifyRevision),
      },
    });
    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: apiOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
    };

    const securityHeaders = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      comment: `Helada ${config.stage}: security headers for the static sites`,
      securityHeadersBehavior: {
        strictTransportSecurity: {
          accessControlMaxAge: cdk.Duration.days(365),
          includeSubdomains: false,
          override: true,
        },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.SAMEORIGIN, override: true },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
          override: true,
        },
      },
      customHeadersBehavior: {
        customHeaders: [
          // The dashboard records voice notes; nothing else on these pages needs a device API.
          { header: 'Permissions-Policy', value: 'microphone=(self), camera=(), geolocation=()', override: true },
        ],
      },
    });

    const staticBehavior = (originPath: string, fn: cloudfront.Function): cloudfront.BehaviorOptions => ({
      origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket, { originPath }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      // File names carry no hash, so objects are stored with `max-age=0, s-maxage=...`: browsers
      // revalidate every time, the edge caches until the deploy invalidates it.
      cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      responseHeadersPolicy: securityHeaders,
      compress: true,
      functionAssociations: [{ function: fn, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    });
    // Always fetched from the bucket, so a new service worker is seen at once.
    const swBehavior = (originPath: string): cloudfront.BehaviorOptions => ({
      origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket, { originPath }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      responseHeadersPolicy: securityHeaders,
      compress: true,
    });

    // Mirrors what FastAPI does for the dashboard: `/` is the page, `/movil` goes to the phone page.
    const dashboardRouter = new cloudfront.Function(this, 'DashboardRouter', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: `Helada ${config.stage}: dashboard paths`,
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri === '/movil' || uri === '/movil/') {
    return {
      statusCode: 307,
      statusDescription: 'Temporary Redirect',
      headers: { location: { value: '/static/movil/index.html' } },
    };
  }
  if (uri.endsWith('/')) request.uri = uri + 'index.html';
  return request;
}`),
    });
    const phoneRouter = new cloudfront.Function(this, 'PhoneRouter', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      comment: `Helada ${config.stage}: phone page paths`,
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  if (request.uri.endsWith('/')) request.uri += 'index.html';
  return request;
}`),
    });

    // ---- distributions ---------------------------------------------------------------------
    const common = {
      certificate,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    };

    this.dashboard = new cloudfront.Distribution(this, 'Dashboard', {
      ...common,
      comment: `Helada ${config.stage} dashboard`,
      domainNames: config.domain ? [config.domain.dashboardHost] : undefined,
      defaultBehavior: staticBehavior('/dashboard', dashboardRouter),
      additionalBehaviors: {
        '/api/*': apiBehavior,
        '/webhooks/*': apiBehavior,
        // Voice notes, photos and PDFs live on the function's disk and are served by the app.
        '/files/*': apiBehavior,
        '/static/movil/sw.js': swBehavior('/dashboard'),
      },
    });

    this.phone = new cloudfront.Distribution(this, 'Phone', {
      ...common,
      comment: `Helada ${config.stage} phone page`,
      domainNames: config.domain ? [config.domain.phoneHost] : undefined,
      defaultRootObject: 'index.html',
      defaultBehavior: staticBehavior('/movil', phoneRouter),
      additionalBehaviors: { '/api/*': apiBehavior, '/sw.js': swBehavior('/movil') },
    });

    const landingHosts = config.domain
      ? [config.domain.landingHost, ...(config.domain.landingRedirectHosts ?? [])]
      : undefined;
    if (config.landing) {
      // `www` (and any other extra host) answers with a permanent redirect to the landing host.
      const landingRouter = new cloudfront.Function(this, 'LandingRouter', {
        runtime: cloudfront.FunctionRuntime.JS_2_0,
        comment: `Helada ${config.stage}: landing page paths`,
        code: cloudfront.FunctionCode.fromInline(`
var CANONICAL = ${JSON.stringify(config.domain?.landingHost ?? '')};
function handler(event) {
  var request = event.request;
  var host = request.headers.host ? request.headers.host.value : '';
  if (CANONICAL && host !== CANONICAL && !host.endsWith('.cloudfront.net')) {
    return {
      statusCode: 301,
      statusDescription: 'Moved Permanently',
      headers: { location: { value: 'https://' + CANONICAL + request.uri } },
    };
  }
  if (request.uri.endsWith('/')) request.uri += 'index.html';
  return request;
}`),
      });
      this.landing = new cloudfront.Distribution(this, 'Landing', {
        ...common,
        comment: `Helada ${config.stage} landing page`,
        domainNames: landingHosts,
        defaultRootObject: 'index.html',
        defaultBehavior: staticBehavior('/landing', landingRouter),
      });
    }

    if (zone && config.domain) {
      if (this.landing) {
        const landing = this.landing;
        landingHosts!.forEach((host, i) => this.aliasRecords(`LandingDns${i}`, zone, host, landing));
      }
      this.aliasRecords('DashboardDns', zone, config.domain.dashboardHost, this.dashboard);
      this.aliasRecords('PhoneDns', zone, config.domain.phoneHost, this.phone);
    }

    // ---- site content ----------------------------------------------------------------------
    // Layout in the bucket:
    //   dashboard/index.html           <- app/static/index.html   (served at /)
    //   dashboard/static/**            <- app/static/**           (served at /static/**)
    //   movil/**                       <- app/static/movil/**     (the phone site)
    //   landing/**                     <- landing/**              (the landing page)
    // The gzipped model files go up in their own pass with an explicit content type; otherwise the
    // upload would label them `Content-Encoding: gzip` and browsers would unpack them in transit.
    // File names carry no hash: browsers revalidate every time, the edge caches until invalidated.
    const edgeCached = s3deploy.CacheControl.fromString('public, max-age=0, s-maxage=31536000, must-revalidate');
    const SW = 'sw.js';
    const deploy = (
      name: string,
      dir: string,
      prefix: string,
      dist: cloudfront.IDistribution,
      opts: { only?: string[]; skip?: string[]; contentType?: string; cacheControl?: s3deploy.CacheControl } = {},
    ) =>
      new s3deploy.BucketDeployment(this, name, {
        destinationBucket: siteBucket,
        destinationKeyPrefix: prefix,
        sources: [
          s3deploy.Source.asset(dir, {
            exclude: opts.only
              ? ['*', ...opts.only.map((p) => `!${p}`)]
              : [...ASSET_EXCLUDES, ...(opts.skip ?? [])],
            ignoreMode: opts.only ? cdk.IgnoreMode.GIT : undefined,
          }),
        ],
        cacheControl: [opts.cacheControl ?? edgeCached],
        contentType: opts.contentType,
        // Several deployments share each prefix; pruning would make them delete each other's files.
        prune: false,
        memoryLimit: 256,
        // A deployment only runs when its own files changed, so each one must clear the edge itself.
        distribution: dist,
        distributionPaths: ['/*'],
      });

    // The service worker goes last and is never cached at the edge: a new worker precaches the
    // shell on install, so it must not appear before the files it lists are the new ones.
    const noCache = s3deploy.CacheControl.fromString('no-cache');
    const gz = { only: ['*/', '**/*.gz'], contentType: 'application/gzip' };
    const plain = { skip: ['*.gz', '**/*.gz', SW, `**/${SW}`] };
    const sw = (file: string) => ({ only: ['*/', file], cacheControl: noCache });

    const movilDir = path.join(STATIC_DIR, 'movil');
    const dash = this.dashboard;
    // Order matters twice: the service workers depend on the rest, and they are declared last
    // because the shared upload function is attached to the first deployment declared.
    const dashboardFiles = [
      deploy('DashboardStatic', STATIC_DIR, 'dashboard/static', dash, plain),
      deploy('DashboardModels', STATIC_DIR, 'dashboard/static', dash, gz),
      deploy('DashboardIndex', STATIC_DIR, 'dashboard', dash, { only: ['index.html'] }),
    ];
    const phoneFiles = [
      deploy('PhoneStatic', movilDir, 'movil', this.phone, plain),
      deploy('PhoneModels', movilDir, 'movil', this.phone, gz),
    ];
    deploy('DashboardSw', STATIC_DIR, 'dashboard/static', dash, sw(`movil/${SW}`)).node.addDependency(...dashboardFiles);
    if (this.landing) deploy('LandingSite', LANDING_DIR, 'landing', this.landing);
    deploy('PhoneSw', movilDir, 'movil', this.phone, sw(SW)).node.addDependency(...phoneFiles);

    // ---- accepted findings ------------------------------------------------------------------
    const deployHelper = this.node.children.find((c) => c.node.id.startsWith('Custom::CDKBucketDeployment'))!;
    acknowledge(
      deployHelper,
      'CDK bucket-deployment helper: its role and runtime are generated by aws-cdk-lib; it reads the CDK asset bucket, writes the site bucket and creates invalidations (which have no resource-level permission).',
      BASIC_EXECUTION_ROLE,
      'AwsSolutions::AwsSolutions-L1',
      'AwsSolutions-IAM5[Action::s3:Abort*]',
      'AwsSolutions-IAM5[Action::s3:DeleteObject*]',
      'AwsSolutions-IAM5[Action::s3:GetBucket*]',
      'AwsSolutions-IAM5[Action::s3:GetObject*]',
      'AwsSolutions-IAM5[Action::s3:List*]',
      'AwsSolutions-IAM5[Resource::*]',
      `AwsSolutions-IAM5[Resource::<${this.getLogicalId(siteBucket.node.defaultChild as s3.CfnBucket)}.Arn>/*]`,
      `AwsSolutions-IAM5[Resource::arn:aws:s3:::cdk-hnb659fds-assets-${this.account}-${this.region}/*]`,
    );
    acknowledge(
      siteBucket,
      'Holds only public site files rebuilt from the repository; a second bucket just for access logs is not worth it.',
      'AwsSolutions::AwsSolutions-S1',
    );
    for (const dist of [this.dashboard, this.phone, ...(this.landing ? [this.landing] : [])]) {
      acknowledge(
        dist,
        'Demo-scale public site: no country to exclude, no WAF (about 8 USD a month) and no access-log bucket. The HTTP API behind it is throttled and keeps its own access log.',
        'AwsSolutions::AwsSolutions-CFR1',
        'AwsSolutions::AwsSolutions-CFR2',
        'AwsSolutions::AwsSolutions-CFR3',
      );
    }

    new cdk.CfnOutput(this, 'DashboardUrl', {
      value: `https://${config.domain?.dashboardHost ?? this.dashboard.distributionDomainName}`,
    });
    if (this.landing) {
      new cdk.CfnOutput(this, 'LandingUrl', {
        value: `https://${config.domain?.landingHost ?? this.landing.distributionDomainName}`,
      });
    }
    new cdk.CfnOutput(this, 'PhoneUrl', {
      value: `https://${config.domain?.phoneHost ?? this.phone.distributionDomainName}`,
    });
  }

  private aliasRecords(id: string, zone: route53.IHostedZone, host: string, dist: cloudfront.IDistribution) {
    const target = route53.RecordTarget.fromAlias(new targets.CloudFrontTarget(dist));
    new route53.ARecord(this, `${id}A`, { zone, recordName: host, target });
    new route53.AaaaRecord(this, `${id}Aaaa`, { zone, recordName: host, target });
  }
}
