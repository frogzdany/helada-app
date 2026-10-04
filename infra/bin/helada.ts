#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { BackendStack } from '../lib/backend-stack';
import { stackName, stageConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { EdgeStack } from '../lib/edge-stack';
import { ObservabilityStack } from '../lib/observability-stack';

const app = new cdk.App();
const config = stageConfig(app.node.tryGetContext('stage'), app.node.tryGetContext('alarmEmail'));
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: config.region };

const data = new DataStack(app, stackName(config, 'Data'), {
  env,
  config,
  description: `Helada ${config.stage}: DynamoDB table and media bucket`,
  terminationProtection: config.retainData,
});

const backend = new BackendStack(app, stackName(config, 'Backend'), {
  env,
  config,
  description: `Helada ${config.stage}: the app function and its HTTP API`,
  table: data.table,
  mediaBucket: data.mediaBucket,
  mediaPrefix: DataStack.MEDIA_PREFIX,
});

new EdgeStack(app, stackName(config, 'Edge'), {
  env,
  config,
  description: `Helada ${config.stage}: CloudFront distributions, static sites, DNS`,
  apiDomainName: backend.apiDomainName,
  originVerifySecret: backend.originVerifySecret,
});

new ObservabilityStack(app, stackName(config, 'Observability'), {
  env,
  config,
  description: `Helada ${config.stage}: alarms and budget`,
  apiFunction: backend.apiFunction,
  httpApi: backend.httpApi,
});

cdk.Tags.of(app).add('Project', 'helada');
cdk.Tags.of(app).add('Stage', config.stage);

// Security and best-practice checks on every synth (the report lands in cdk.out);
// `-c nag=false` skips them while iterating.
if (app.node.tryGetContext('nag') !== 'false') {
  cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(app));
}
