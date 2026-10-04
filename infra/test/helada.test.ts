import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { BackendStack } from '../lib/backend-stack';
import { STAGES, StageConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { EdgeStack } from '../lib/edge-stack';
import { ObservabilityStack } from '../lib/observability-stack';

const env = { account: '111111111111', region: 'us-east-1' };

function build(config: StageConfig) {
  const app = new cdk.App();
  const data = new DataStack(app, 'Data', { env, config });
  const backend = new BackendStack(app, 'Backend', {
    env,
    config,
    table: data.table,
    mediaBucket: data.mediaBucket,
    mediaPrefix: DataStack.MEDIA_PREFIX,
  });
  const edge = new EdgeStack(app, 'Edge', {
    env,
    config,
    apiDomainName: backend.apiDomainName,
    originVerifySecret: backend.originVerifySecret,
  });
  const observability = new ObservabilityStack(app, 'Observability', {
    env,
    config,
    apiFunction: backend.apiFunction,
    httpApi: backend.httpApi,
  });
  return {
    data: Template.fromStack(data),
    backend: Template.fromStack(backend),
    edge: Template.fromStack(edge),
    observability: Template.fromStack(observability),
  };
}

const distributions = (t: Template) => Object.values(t.findResources('AWS::CloudFront::Distribution'));
const byHost = (t: Template, host: string) =>
  distributions(t).find((d) => d.Properties.DistributionConfig.Aliases[0] === host)!.Properties.DistributionConfig;

describe('dev', () => {
  const t = build({ ...STAGES.dev, landing: false });
  const domain = STAGES.dev.domain!;

  test('rows go to an on-demand DynamoDB table with point-in-time recovery, and the app is told where', () => {
    t.data.hasResourceProperties('AWS::DynamoDB::Table', {
      BillingMode: 'PAY_PER_REQUEST',
      KeySchema: [
        { AttributeName: 'pk', KeyType: 'HASH' },
        { AttributeName: 'sk', KeyType: 'RANGE' },
      ],
      PointInTimeRecoverySpecification: Match.objectLike({ PointInTimeRecoveryEnabled: true }),
    });
    t.backend.hasResourceProperties('AWS::Lambda::Function', {
      Environment: { Variables: Match.objectLike({ HELADA_DDB_TABLE: Match.anyValue(), HELADA_MEDIA_BUCKET: Match.anyValue() }) },
    });
  });

  test('no bucket is public', () => {
    for (const bucket of [...Object.values(t.edge.findResources('AWS::S3::Bucket')), ...Object.values(t.data.findResources('AWS::S3::Bucket'))]) {
      expect(bucket.Properties.PublicAccessBlockConfiguration).toEqual({
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      });
    }
  });

  test('the API is only reachable with the origin-verify authorizer', () => {
    t.backend.hasResourceProperties('AWS::ApiGatewayV2::Route', {
      RouteKey: '$default',
      AuthorizationType: 'CUSTOM',
    });
    t.backend.resourceCountIs('AWS::ApiGatewayV2::Route', 1);
  });

  test('the app runs as one arm64 container instance', () => {
    t.backend.hasResourceProperties('AWS::Lambda::Function', {
      PackageType: 'Image',
      Architectures: ['arm64'],
      ReservedConcurrentExecutions: 1,
    });
  });

  test('two distributions on the configured hosts', () => {
    t.edge.resourceCountIs('AWS::CloudFront::Distribution', 2);
    expect(byHost(t.edge, domain.dashboardHost)).toBeDefined();
    expect(byHost(t.edge, domain.phoneHost).DefaultRootObject).toBe('index.html');
    t.edge.hasResourceProperties('AWS::CertificateManager::Certificate', {
      DomainName: domain.dashboardHost,
      SubjectAlternativeNames: [domain.phoneHost],
    });
  });

  test('the dashboard sends API, media and webhook paths to the app', () => {
    const behaviors: Record<string, string> = Object.fromEntries(
      byHost(t.edge, domain.dashboardHost).CacheBehaviors.map((b: any) => [b.PathPattern, b.TargetOriginId]),
    );
    expect(Object.keys(behaviors).sort()).toEqual(['/api/*', '/files/*', '/static/movil/sw.js', '/webhooks/*']);
    expect(behaviors['/files/*']).toBe(behaviors['/api/*']);
    expect(behaviors['/webhooks/*']).toBe(behaviors['/api/*']);
  });

  test('the phone page reaches the API on its own host and its service worker is never cached', () => {
    const patterns = byHost(t.edge, domain.phoneHost).CacheBehaviors.map((b: any) => b.PathPattern);
    expect(patterns.sort()).toEqual(['/api/*', '/sw.js']);
  });

  test('every site upload clears the edge cache, and the origin secret is never in the template', () => {
    const deployments = Object.values(t.edge.findResources('Custom::CDKBucketDeployment'));
    expect(deployments).toHaveLength(7);
    for (const d of deployments) {
      expect(d.Properties.DistributionId).toBeDefined();
      expect(d.Properties.Prune).toBe(false);
    }
    expect(JSON.stringify(t.edge.toJSON())).toContain('{{resolve:secretsmanager:');
  });

  test('alarms and a budget exist and reach the topic', () => {
    t.observability.resourceCountIs('AWS::CloudWatch::Alarm', 4);
    t.observability.hasResourceProperties('AWS::Budgets::Budget', {
      NotificationsWithSubscribers: Match.arrayWith([
        Match.objectLike({ Subscribers: [Match.objectLike({ SubscriptionType: 'SNS' })] }),
      ]),
    });
  });
});

test('with the landing page: a third distribution on the apex and www', () => {
  const t = build({ ...STAGES.prod, landing: true });
  t.edge.resourceCountIs('AWS::CloudFront::Distribution', 3);
  expect(distributions(t.edge).map((d) => d.Properties.DistributionConfig.Aliases)).toEqual(
    expect.arrayContaining([['helada.app', 'www.helada.app'], ['m.helada.app'], ['panel.helada.app']]),
  );
  t.edge.hasResourceProperties('AWS::CertificateManager::Certificate', {
    DomainName: 'panel.helada.app',
    SubjectAlternativeNames: ['m.helada.app', 'helada.app', 'www.helada.app'],
  });
  expect(Object.values(t.edge.findResources('Custom::CDKBucketDeployment'))).toHaveLength(8);
});

test('prod data is kept and protected', () => {
  const t = build(STAGES.prod);
  t.data.hasResource('AWS::DynamoDB::Table', {
    DeletionPolicy: 'Retain',
    Properties: Match.objectLike({ DeletionProtectionEnabled: true }),
  });
  t.data.hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
});
