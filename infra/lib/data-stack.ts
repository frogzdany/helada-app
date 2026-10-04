import * as cdk from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { StageConfig } from './config';
import { acknowledge } from './nag';

export interface DataStackProps extends cdk.StackProps {
  readonly config: StageConfig;
}

/**
 * What must outlive a function instance: the DynamoDB table with every row the app writes, and the
 * bucket with its media files (voice notes, photos, PDFs). Kept apart from the compute stacks so
 * those can be replaced freely without touching data. See app/backend/cloud.py for how they are used.
 */
export class DataStack extends cdk.Stack {
  readonly table: dynamodb.Table;
  readonly mediaBucket: s3.Bucket;

  /** Media keys start with this (must match `S3Files` in app/backend/cloud.py). */
  static readonly MEDIA_PREFIX = 'data';

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const { config } = props;
    const removalPolicy = config.retainData ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY;

    // One table for all of the app's rows: pk = the app's table name, sk = the row's primary key.
    // On-demand billing: nothing is paid while nobody uses it.
    this.table = new dynamodb.Table(this, 'Table', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      // Restore to any second of the last 35 days.
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: config.retainData,
      removalPolicy,
    });

    this.mediaBucket = new s3.Bucket(this, 'MediaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: config.retainData,
      lifecycleRules: [
        {
          // Farmers' voice notes and photos are personal data: not kept longer than needed.
          id: 'expire-farmer-uploads',
          prefix: `${DataStack.MEDIA_PREFIX}/media/in/`,
          expiration: cdk.Duration.days(config.inboundMediaExpiryDays),
        },
        {
          id: 'housekeeping',
          abortIncompleteMultipartUploadAfter: cdk.Duration.days(7),
          noncurrentVersionExpiration: cdk.Duration.days(30),
        },
      ],
      removalPolicy,
      autoDeleteObjects: !config.retainData,
    });
    acknowledge(
      this.mediaBucket,
      'Private bucket read and written only by the app function; a second bucket just for access logs is not worth it at this scale.',
      'AwsSolutions::AwsSolutions-S1',
    );

    new cdk.CfnOutput(this, 'TableName', { value: this.table.tableName });
    new cdk.CfnOutput(this, 'MediaBucketName', { value: this.mediaBucket.bucketName });
  }
}
