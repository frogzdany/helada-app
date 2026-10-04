import * as cdk from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cw from 'aws-cdk-lib/aws-cloudwatch';
import { SnsAction } from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { StageConfig } from './config';

export interface ObservabilityStackProps extends cdk.StackProps {
  readonly config: StageConfig;
  readonly apiFunction: lambda.IFunction;
  readonly httpApi: apigwv2.IHttpApi;
}

/** Four alarms and a cost budget, all notifying one SNS topic (email when configured). */
export class ObservabilityStack extends cdk.Stack {
  readonly alarmTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    const { config } = props;
    const five = cdk.Duration.minutes(5);

    this.alarmTopic = new sns.Topic(this, 'AlarmTopic', {
      displayName: `Helada ${config.stage} alarms`,
      enforceSSL: true,
    });
    if (config.alarmEmail) {
      this.alarmTopic.addSubscription(new subscriptions.EmailSubscription(config.alarmEmail));
    } else {
      cdk.Annotations.of(this).addWarning(
        'No alarmEmail: alarms and the budget notify nobody. Pass -c alarmEmail=you@example.com.',
      );
    }

    const alarm = (name: string, description: string, metric: cw.IMetric, threshold: number, periods = 1) =>
      new cw.Alarm(this, name, {
        alarmDescription: description,
        metric,
        threshold,
        evaluationPeriods: periods,
        comparisonOperator: cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cw.TreatMissingData.NOT_BREACHING,
      }).addAlarmAction(new SnsAction(this.alarmTopic));

    alarm('ApiErrors', 'The app function is failing', props.apiFunction.metricErrors({ period: five }), 5);
    // With a single instance, throttles mean requests are queueing behind a slow one.
    alarm('ApiThrottles', 'Requests are being turned away', props.apiFunction.metricThrottles({ period: five }), 20, 2);
    alarm('Api5xx', 'The HTTP API is answering 5xx', props.httpApi.metricServerError({ period: five, statistic: 'Sum' }), 5);
    alarm(
      'ApiSlow',
      'p95 latency is close to the 30 s limit',
      props.httpApi.metricLatency({ period: five, statistic: 'p95' }),
      20_000,
      3,
    );

    if (config.monthlyBudgetUsd) {
      // Budgets publishes to the same topic as the alarms, so the budget exists with or without an email.
      this.alarmTopic.addToResourcePolicy(
        new iam.PolicyStatement({
          sid: 'AllowBudgetsPublish',
          actions: ['sns:Publish'],
          resources: [this.alarmTopic.topicArn],
          principals: [new iam.ServicePrincipal('budgets.amazonaws.com')],
          conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
        }),
      );
      const subscribers = [{ subscriptionType: 'SNS', address: this.alarmTopic.topicArn }];
      new budgets.CfnBudget(this, 'Budget', {
        budget: {
          budgetName: `helada-${config.stage}-monthly`,
          budgetType: 'COST',
          timeUnit: 'MONTHLY',
          budgetLimit: { amount: config.monthlyBudgetUsd, unit: 'USD' },
          // Needs the `Project` and `Stage` tags activated as cost allocation tags in Billing.
          // Untagged spend (the CDK asset repository, data transfer) is not counted.
          filterExpression: {
            and: [
              { tags: { key: 'Project', values: ['helada'] } },
              { tags: { key: 'Stage', values: [config.stage] } },
            ],
          },
        },
        notificationsWithSubscribers: [
          {
            notification: { notificationType: 'ACTUAL', comparisonOperator: 'GREATER_THAN', threshold: 80 },
            subscribers,
          },
          {
            notification: { notificationType: 'FORECASTED', comparisonOperator: 'GREATER_THAN', threshold: 100 },
            subscribers,
          },
        ],
      });
    }

    new cdk.CfnOutput(this, 'AlarmTopicArn', { value: this.alarmTopic.topicArn });
  }
}
