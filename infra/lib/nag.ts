import { Validations } from 'aws-cdk-lib';
import { IConstruct } from 'constructs';

/**
 * Accept cdk-nag findings on purpose, with the reason next to the resource. Anything not
 * acknowledged fails the synth. Ids are the ones the report prints after "Acknowledge with".
 */
export function acknowledge(scope: IConstruct, reason: string, ...ids: string[]) {
  for (const id of ids) {
    Validations.of(scope).acknowledge({ id, reason });
  }
}

export const BASIC_EXECUTION_ROLE =
  'AwsSolutions-IAM4[Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole]';
