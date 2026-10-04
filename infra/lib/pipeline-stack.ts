import * as cdk from 'aws-cdk-lib';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as codepipeline from 'aws-cdk-lib/aws-codepipeline';
import * as actions from 'aws-cdk-lib/aws-codepipeline-actions';
import * as connections from 'aws-cdk-lib/aws-codestarconnections';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { StageName } from './config';
import { acknowledge } from './nag';

export interface PipelineStackProps extends cdk.StackProps {
  /** GitHub owner and repository the pipelines read. */
  readonly owner: string;
  readonly repo: string;
  readonly branch: string;
}

/** Paths whose change on the branch starts the dev pipeline. What is then deployed is decided in scripts/lib.sh. */
export const TRIGGER_PATHS = ['app/**', 'model/**', 'landing/**', 'infra/**', 'Dockerfile.aws*'];

/**
 * Deploys from the repository instead of from a laptop. One CodeBuild project runs
 * `infra/buildspec.yml`; two pipelines feed it:
 *   helada-dev   starts when the branch changes under TRIGGER_PATHS
 *   helada-prod  starts only when asked, for a commit that dev already runs (scripts/release.sh promote)
 * There is no CodeDeploy step: its job is to shift traffic between two versions, and the app must
 * run as a single instance (see backend-stack.ts).
 * This stack is deployed by hand (`npm run deploy:pipeline`); the pipelines never update themselves.
 */
export class PipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    // Created as "pending": someone has to open it once in the console and authorize the GitHub app.
    const connection = new connections.CfnConnection(this, 'GitHub', {
      connectionName: 'helada-github',
      providerType: 'GitHub',
    });

    const project = new codebuild.PipelineProject(this, 'Deploy', {
      projectName: 'helada-deploy',
      description: 'Helada: test, deploy one stage with the CDK, check it (infra/buildspec.yml)',
      buildSpec: codebuild.BuildSpec.fromSourceFilename('infra/buildspec.yml'),
      environment: {
        // arm64 like the function, so the image is built without emulation.
        buildImage: codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0,
        computeType: codebuild.ComputeType.MEDIUM,
        // The image is built with Docker inside the build.
        privileged: true,
      },
      // Kept on the build host for a short while: a second deploy soon after the first reuses the layers.
      cache: codebuild.Cache.local(codebuild.LocalCacheMode.DOCKER_LAYER),
      timeout: cdk.Duration.minutes(30),
      logging: {
        cloudWatch: {
          logGroup: new logs.LogGroup(this, 'DeployLogs', {
            retention: logs.RetentionDays.ONE_MONTH,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
          }),
        },
      },
    });

    // The build deploys through the roles `cdk bootstrap` created, like a laptop does.
    const bootstrapRoles = `arn:${this.partition}:iam::${this.account}:role/cdk-${cdk.DefaultStackSynthesizer.DEFAULT_QUALIFIER}-*-role-${this.account}-${this.region}`;
    project.addToRolePolicy(new iam.PolicyStatement({ actions: ['sts:AssumeRole'], resources: [bootstrapRoles] }));
    // The commit each stage runs (scripts/lib.sh), and the site addresses the checks read.
    const commitParams = `arn:${this.partition}:ssm:${this.region}:${this.account}:parameter/helada/*`;
    project.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['ssm:GetParameter', 'ssm:PutParameter'], resources: [commitParams] }),
    );
    const stacks = `arn:${this.partition}:cloudformation:${this.region}:${this.account}:stack/Helada-*/*`;
    project.addToRolePolicy(new iam.PolicyStatement({ actions: ['cloudformation:DescribeStacks'], resources: [stacks] }));

    // With a clone as the source, the artifact is only a pointer to the commit.
    const artifacts = new s3.Bucket(this, 'Artifacts', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(14) }],
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const pipeline = (stage: StageName, onPush: boolean) => {
      const source = new codepipeline.Artifact();
      const sourceAction = new actions.CodeStarConnectionsSourceAction({
        actionName: 'Source',
        connectionArn: connection.attrConnectionArn,
        owner: props.owner,
        repo: props.repo,
        branch: props.branch,
        output: source,
        // A real clone, so the build can compare the commit with the one the stage runs.
        codeBuildCloneOutput: true,
        triggerOnPush: onPush,
      });
      return new codepipeline.Pipeline(this, `${stage}Pipeline`, {
        pipelineName: `helada-${stage}`,
        pipelineType: codepipeline.PipelineType.V2,
        // Runs of one pipeline never overlap; pushes that arrive during a run are deployed as one.
        executionMode: codepipeline.ExecutionMode.SUPERSEDED,
        artifactBucket: artifacts,
        // One account: no KMS key (about 1 USD a month) just for the artifacts.
        crossAccountKeys: false,
        restartExecutionOnUpdate: false,
        stages: [
          { stageName: 'Source', actions: [sourceAction] },
          {
            stageName: 'Deploy',
            actions: [
              new actions.CodeBuildAction({
                actionName: 'Deploy',
                project,
                input: source,
                environmentVariables: { STAGE: { value: stage } },
              }),
            ],
          },
        ],
        triggers: onPush
          ? [
              {
                providerType: codepipeline.ProviderType.CODE_STAR_SOURCE_CONNECTION,
                gitConfiguration: {
                  sourceAction,
                  pushFilter: [{ branchesIncludes: [props.branch], filePathsIncludes: TRIGGER_PATHS }],
                },
              },
            ]
          : undefined,
      });
    };
    const pipelines = [pipeline('dev', true), pipeline('prod', false)];

    // ---- accepted findings ------------------------------------------------------------------
    const artifactsArn = `<${this.getLogicalId(artifacts.node.defaultChild as s3.CfnBucket)}.Arn>/*`;
    const projectId = `<${this.getLogicalId(project.node.defaultChild as codebuild.CfnProject)}>`;
    const artifactAccess = [
      'AwsSolutions-IAM5[Action::s3:Abort*]',
      'AwsSolutions-IAM5[Action::s3:DeleteObject*]',
      'AwsSolutions-IAM5[Action::s3:GetBucket*]',
      'AwsSolutions-IAM5[Action::s3:GetObject*]',
      'AwsSolutions-IAM5[Action::s3:List*]',
      `AwsSolutions-IAM5[Resource::${artifactsArn}]`,
    ];
    for (const p of pipelines) {
      acknowledge(p, 'Generated by aws-cdk-lib: the pipeline and its actions read and write the artifact bucket of this stack only.', ...artifactAccess);
    }
    acknowledge(
      project,
      'The build reads the artifact bucket of this stack and writes its own log group and test reports (generated by aws-cdk-lib). It deploys by assuming the roles `cdk bootstrap` created in this account and region, reads the outputs of the Helada stacks, and keeps the deployed commit under /helada/ in Parameter Store; each wildcard is the narrowest name those have.',
      ...artifactAccess,
      `AwsSolutions-IAM5[Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${projectId}:*]`,
      `AwsSolutions-IAM5[Resource::arn:aws:codebuild:${this.region}:${this.account}:report-group/${projectId}-*]`,
      `AwsSolutions-IAM5[Resource::${bootstrapRoles.replace('arn:' + this.partition, 'arn:aws')}]`,
      `AwsSolutions-IAM5[Resource::${commitParams.replace('arn:' + this.partition, 'arn:aws')}]`,
      `AwsSolutions-IAM5[Resource::${stacks.replace('arn:' + this.partition, 'arn:aws')}]`,
    );
    acknowledge(
      project,
      'The artifact is a pointer to a commit of the repository; S3-managed encryption is enough, and a KMS key would cost about 1 USD a month.',
      'AwsSolutions::AwsSolutions-CB4',
    );
    acknowledge(
      artifacts,
      'Holds pointers to commits for 14 days; a second bucket just for its access log is not worth it.',
      'AwsSolutions::AwsSolutions-S1',
    );

    new cdk.CfnOutput(this, 'ConnectionArn', { value: connection.attrConnectionArn });
    new cdk.CfnOutput(this, 'ConnectionConsole', {
      value: `https://${this.region}.console.aws.amazon.com/codesuite/settings/connections?region=${this.region}`,
    });
  }
}
