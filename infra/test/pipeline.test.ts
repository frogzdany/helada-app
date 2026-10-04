import { execFileSync } from 'child_process';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { PipelineStack, TRIGGER_PATHS } from '../lib/pipeline-stack';

const app = new cdk.App();
const t = Template.fromStack(
  new PipelineStack(app, 'Pipeline', {
    env: { account: '111111111111', region: 'us-east-1' },
    owner: 'frogzdany',
    repo: 'helada',
    branch: 'main',
  }),
);

const pipelines = () => Object.values(t.findResources('AWS::CodePipeline::Pipeline')).map((p) => p.Properties);
const named = (name: string) => pipelines().find((p) => p.Name === name)!;

test('dev starts on a push to main under the deployed paths; prod only when asked', () => {
  expect(pipelines().map((p) => p.Name).sort()).toEqual(['helada-dev', 'helada-prod']);
  expect(named('helada-dev').Triggers).toEqual([
    {
      ProviderType: 'CodeStarSourceConnection',
      GitConfiguration: {
        SourceActionName: 'Source',
        Push: [{ Branches: { Includes: ['main'] }, FilePaths: { Includes: TRIGGER_PATHS } }],
      },
    },
  ]);
  expect(named('helada-prod').Triggers).toBeUndefined();
  expect(named('helada-prod').Stages[0].Actions[0].Configuration.DetectChanges).toBe(false);
});

test('each pipeline hands the build a clone of the repository and the stage to deploy', () => {
  for (const stage of ['dev', 'prod']) {
    const [source, deploy] = named(`helada-${stage}`).Stages;
    expect(source.Actions[0].Configuration).toMatchObject({
      FullRepositoryId: 'frogzdany/helada',
      BranchName: 'main',
      OutputArtifactFormat: 'CODEBUILD_CLONE_REF',
    });
    expect(JSON.parse(deploy.Actions[0].Configuration.EnvironmentVariables)).toEqual([
      { name: 'STAGE', type: 'PLAINTEXT', value: stage },
    ]);
    expect(named(`helada-${stage}`).PipelineType).toBe('V2');
  }
});

test('one arm64 build project with Docker, reading the buildspec from the repository', () => {
  t.resourceCountIs('AWS::CodeBuild::Project', 1);
  t.hasResourceProperties('AWS::CodeBuild::Project', {
    Environment: Match.objectLike({ Type: 'ARM_CONTAINER', PrivilegedMode: true }),
    Source: Match.objectLike({ BuildSpec: 'infra/buildspec.yml' }),
  });
});

test('nothing that costs by the month: no KMS key, and the artifacts expire', () => {
  t.resourceCountIs('AWS::KMS::Key', 0);
  t.hasResourceProperties('AWS::S3::Bucket', {
    LifecycleConfiguration: { Rules: [Match.objectLike({ ExpirationInDays: 14, Status: 'Enabled' })] },
  });
});

// scripts/lib.sh decides what a change deploys; a wrong answer here deploys too little.
describe('what a change deploys', () => {
  const lib = path.join(__dirname, '..', 'scripts', 'lib.sh');
  const classify = (...paths: string[]) =>
    execFileSync('bash', ['-c', `. "${lib}" && classify`], { input: paths.join('\n') + '\n' }).toString().trim();

  test.each([
    [['docs/aws-architecture.md', 'evals/README.md', 'app/tests/test_cloud.py', 'infra/README.md'], 'none'],
    [['landing/index.html'], 'site'],
    [['app/static/movil/sw.js', 'README.md'], 'site'],
    [['app/backend/cloud.py'], 'backend'],
    [['model/src/helada_model/core.py'], 'backend'],
    [['Dockerfile.aws'], 'backend'],
    [['app/backend/main.py', 'app/static/index.html'], 'backend+site'],
    [['infra/lib/config.ts', 'landing/index.html'], 'all'],
    [['infra/lib/pipeline-stack.ts', 'infra/bin/pipeline.ts', 'infra/buildspec.yml', 'infra/scripts/smoke.sh'], 'none'],
  ])('%j -> %s', (paths, expected) => {
    expect(classify(...paths)).toBe(expected);
  });

  test('every deployed path also starts the dev pipeline', () => {
    const prefixes = TRIGGER_PATHS.map((p) => p.replace(/\*+$/, ''));
    for (const p of ['infra/lib/config.ts', 'Dockerfile.aws', 'app/backend/x.py', 'model/src/x.py', 'app/static/x.js', 'landing/x.html']) {
      expect(classify(p)).not.toBe('none');
      expect(prefixes.some((prefix) => p.startsWith(prefix))).toBe(true);
    }
  });
});
