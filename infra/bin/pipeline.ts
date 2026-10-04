#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { PipelineStack } from '../lib/pipeline-stack';

// Its own app, apart from bin/helada.ts: the pipelines deploy the stages, and nothing a pipeline
// runs should be able to change the pipeline. Deployed by hand: `npm run deploy:pipeline`.
const app = new cdk.App();

new PipelineStack(app, 'Helada-Pipeline', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
  description: 'Helada: the pipelines that deploy dev and prod from GitHub',
  owner: 'frogzdany',
  repo: 'helada',
  branch: 'main',
});

cdk.Tags.of(app).add('Project', 'helada');

if (app.node.tryGetContext('nag') !== 'false') {
  cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(app));
}
