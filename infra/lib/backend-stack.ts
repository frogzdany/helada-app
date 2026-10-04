import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaAuthorizer, HttpLambdaResponseType } from 'aws-cdk-lib/aws-apigatewayv2-authorizers';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { StageConfig } from './config';
import { acknowledge, BASIC_EXECUTION_ROLE } from './nag';

export interface BackendStackProps extends cdk.StackProps {
  readonly config: StageConfig;
  readonly table: dynamodb.ITable;
  readonly mediaBucket: s3.IBucket;
  readonly mediaPrefix: string;
}

/** Repo root: the Docker build context. */
const REPO_ROOT = path.join(__dirname, '..', '..');

/** Header CloudFront adds towards the API; checked by the authorizer. */
export const ORIGIN_VERIFY_HEADER = 'x-helada-origin-verify';

/**
 * The app: the FastAPI container as one Lambda function, behind an HTTP API that only CloudFront
 * can call. The app works on a SQLite file and a media folder on the function's /tmp; every row it
 * writes is also stored in DynamoDB and every media file in S3 (app/backend/cloud.py), and a new
 * instance loads them back. SQLite is one instance's working copy, so the function runs as one.
 */
export class BackendStack extends cdk.Stack {
  readonly apiFunction: lambda.DockerImageFunction;
  readonly httpApi: apigwv2.HttpApi;
  readonly originVerifySecret: secretsmanager.Secret;
  /** `<api-id>.execute-api.<region>.amazonaws.com`, the CloudFront origin. */
  readonly apiDomainName: string;

  constructor(scope: Construct, id: string, props: BackendStackProps) {
    super(scope, id, props);
    const { config } = props;
    const retention = config.logRetentionDays as logs.RetentionDays;
    const logGroup = (name: string) =>
      new logs.LogGroup(this, name, { retention, removalPolicy: cdk.RemovalPolicy.DESTROY });

    // ---- function --------------------------------------------------------------------------
    this.apiFunction = new lambda.DockerImageFunction(this, 'ApiFunction', {
      // Same allowlist as Dockerfile.aws.dockerignore: it keeps the asset hash stable when
      // unrelated files change and keeps data/, .venv/ and infra/cdk.out out of the staged copy.
      code: lambda.DockerImageCode.fromImageAsset(REPO_ROOT, {
        file: 'Dockerfile.aws',
        platform: Platform.LINUX_ARM64,
        ignoreMode: cdk.IgnoreMode.DOCKER,
        exclude: [
          '*',
          '!Dockerfile.aws',
          '!Dockerfile.aws.dockerignore',
          '!app/pyproject.toml',
          '!app/uv.lock',
          '!app/backend',
          '!app/static',
          '!app/scripts/seed_demo_media.py',
          '!model/pyproject.toml',
          '!model/README.md',
          '!model/src',
          '**/__pycache__',
          '**/*.pyc',
          '**/.DS_Store',
        ],
      }),
      architecture: lambda.Architecture.ARM_64,
      memorySize: config.api.memoryMb,
      timeout: cdk.Duration.seconds(config.api.timeoutSeconds),
      // One instance: two would each work on their own copy and overwrite each other's rows.
      reservedConcurrentExecutions: 1,
      // Room for the database, voice notes, photos and PDFs of a session.
      ephemeralStorageSize: cdk.Size.gibibytes(2),
      environment: {
        HELADA_STAGE: config.stage,
        HELADA_TZ: 'America/Mexico_City',
        // Switches on the durable store in app/backend/cloud.py.
        HELADA_DDB_TABLE: props.table.tableName,
        HELADA_MEDIA_BUCKET: props.mediaBucket.bucketName,
        // The image allows 40 s of speech synthesis per request; keep it inside the 30 s the API has.
        HELADA_TTS_BUDGET_S: '20',
        // Loading a large image and the stored data can take longer than the 10 s Lambda allows for
        // start-up; with this the adapter finishes waiting inside the first request instead.
        AWS_LWA_ASYNC_INIT: 'true',
        ...(config.domain ? { HELADA_PUBLIC_URL: `https://${config.domain.dashboardHost}` } : {}),
      },
      loggingFormat: lambda.LoggingFormat.JSON,
      logGroup: logGroup('ApiLogs'),
      description: `Helada ${config.stage} app (FastAPI through Lambda Web Adapter)`,
    });

    props.table.grantReadWriteData(this.apiFunction);
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
        resources: [props.mediaBucket.arnForObjects(`${props.mediaPrefix}/*`)],
      }),
    );
    this.apiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:ListBucket'],
        resources: [props.mediaBucket.bucketArn],
        conditions: { StringLike: { 's3:prefix': [`${props.mediaPrefix}/*`] } },
      }),
    );

    // ---- HTTP API --------------------------------------------------------------------------
    this.originVerifySecret = new secretsmanager.Secret(this, 'OriginVerifySecret', {
      description: `Helada ${config.stage}: header value CloudFront sends to the API origin`,
      generateSecretString: { excludePunctuation: true, passwordLength: 48 },
    });

    const authorizerFn = new lambda.Function(this, 'OriginVerifyFunction', {
      runtime: lambda.Runtime.NODEJS_24_X,
      architecture: lambda.Architecture.ARM_64,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, 'functions', 'origin-verify')),
      memorySize: 128,
      timeout: cdk.Duration.seconds(5),
      environment: {
        SECRET_ARN: this.originVerifySecret.secretArn,
        HEADER_NAME: ORIGIN_VERIFY_HEADER,
      },
      logGroup: logGroup('OriginVerifyLogs'),
    });
    this.originVerifySecret.grantRead(authorizerFn);

    this.httpApi = new apigwv2.HttpApi(this, 'HttpApi', {
      apiName: `helada-${config.stage}`,
      description: 'Helada API. Reached only through CloudFront.',
      defaultIntegration: new HttpLambdaIntegration('ApiIntegration', this.apiFunction),
      defaultAuthorizer: new HttpLambdaAuthorizer('OriginVerify', authorizerFn, {
        responseTypes: [HttpLambdaResponseType.SIMPLE],
        identitySource: [`$request.header.${ORIGIN_VERIFY_HEADER}`],
        resultsCacheTtl: cdk.Duration.hours(1),
      }),
    });

    const accessLogs = logGroup('ApiAccessLogs');
    const stage = this.httpApi.defaultStage!.node.defaultChild as apigwv2.CfnStage;
    stage.defaultRouteSettings = {
      throttlingRateLimit: config.api.throttleRate,
      throttlingBurstLimit: config.api.throttleBurst,
    };
    stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      // `path` has no query string, so phone numbers in query strings stay out of the log.
      format: JSON.stringify({
        requestId: '$context.requestId',
        edgeIp: '$context.identity.sourceIp',
        time: '$context.requestTime',
        method: '$context.httpMethod',
        path: '$context.path',
        status: '$context.status',
        latencyMs: '$context.responseLatency',
        integrationStatus: '$context.integrationStatus',
        integrationError: '$context.integrationErrorMessage',
        error: '$context.error.message',
        authorizerError: '$context.authorizer.error',
      }),
    };

    this.apiDomainName = `${this.httpApi.apiId}.execute-api.${this.region}.${this.urlSuffix}`;

    // ---- accepted findings ------------------------------------------------------------------
    for (const f of [this.apiFunction, authorizerFn]) {
      acknowledge(
        f.role!,
        'AWSLambdaBasicExecutionRole grants CloudWatch Logs write actions only (on any log group); accepted for the stock Lambda role.',
        BASIC_EXECUTION_ROLE,
      );
    }
    // The finding names the bucket by the export it is imported through.
    const mediaArnImport = this.resolve(
      cdk.Stack.of(props.mediaBucket).exportValue(props.mediaBucket.bucketArn),
    )['Fn::ImportValue'];
    acknowledge(
      this.apiFunction.role!,
      'Object access is limited to the data/ prefix of the media bucket; the wildcard is the object key.',
      `AwsSolutions-IAM5[Resource::${mediaArnImport}/${props.mediaPrefix}/*]`,
    );
    acknowledge(
      this.originVerifySecret,
      'Rotated by hand: new secret value, bump originVerifyRevision, deploy Edge. The authorizer accepts the current and the previous value during the switch. See infra/README.md.',
      'AwsSolutions::AwsSolutions-SMG4',
    );

    new cdk.CfnOutput(this, 'ApiFunctionName', { value: this.apiFunction.functionName });
    new cdk.CfnOutput(this, 'HttpApiId', { value: this.httpApi.apiId });
  }
}
