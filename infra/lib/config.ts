/**
 * Per-stage settings. Pick one with `cdk <cmd> -c stage=dev|prod` (default: dev).
 * Everything that differs between environments lives here, so the stacks stay free of `if (prod)`.
 */
export type StageName = 'dev' | 'prod';

export interface DomainConfig {
  /** Route 53 public hosted zone that already exists in the target account. */
  readonly zoneName: string;
  /** Host of the landing page. */
  readonly landingHost: string;
  /** Extra hosts that answer with a redirect to the landing host (`www`). */
  readonly landingRedirectHosts?: string[];
  /**
   * Host of the phone page (the farmer-facing PWA). Its own host on purpose: the page's service
   * worker claims the whole root of its host, so it cannot share one with the landing page.
   */
  readonly phoneHost: string;
  /** Host of the dashboard (extension officers). */
  readonly dashboardHost: string;
}

export interface StageConfig {
  readonly stage: StageName;
  readonly region: string;
  /** Leave undefined to serve from the default *.cloudfront.net names. */
  readonly domain?: DomainConfig;

  readonly api: {
    /** Speech-to-text (whisper small) needs about 1.3 GB on top of the app; more memory also means more CPU. */
    readonly memoryMb: number;
    /**
     * The HTTP API stops waiting at 30 s, but the function keeps going up to this. The first voice
     * note on a new instance has to load the speech model and runs past 30 s: with a longer limit
     * the work still finishes (the chat shows the reply on its next poll) and the model stays loaded.
     */
    readonly timeoutSeconds: number;
    /** Stage-level throttle, requests per second and burst. */
    readonly throttleRate: number;
    readonly throttleBurst: number;
  };

  /** Publish `landing/` on the landing host. */
  readonly landing: boolean;

  /**
   * Bump to make CloudFront pick up a rotated origin-verify secret (CloudFormation does not
   * re-read a secret whose value changed unless the resource itself changes).
   */
  readonly originVerifyRevision: number;

  /** Keep the table and the media bucket when the stack is deleted, and protect the table from deletion. */
  readonly retainData: boolean;
  /** Days before farmers' uploads (voice notes, photos) are deleted from the media bucket. */
  readonly inboundMediaExpiryDays: number;

  readonly logRetentionDays: number;

  /**
   * Receives alarms and budget notices. Usually given on the command line (`-c alarmEmail=you@x`)
   * so no address is committed. Without it the alarms and the budget still exist but notify nobody.
   */
  readonly alarmEmail?: string;
  /** Monthly AWS Budgets limit for resources tagged with this project and stage. */
  readonly monthlyBudgetUsd?: number;
}

const ZONE = 'helada.app';

export const STAGES: Record<StageName, StageConfig> = {
  dev: {
    stage: 'dev',
    region: 'us-east-1',
    domain: {
      zoneName: ZONE,
      landingHost: `dev.${ZONE}`,
      phoneHost: `m-dev.${ZONE}`,
      dashboardHost: `panel-dev.${ZONE}`,
    },
    api: { memoryMb: 3008, timeoutSeconds: 120, throttleRate: 20, throttleBurst: 40 },
    landing: true,
    originVerifyRevision: 1,
    retainData: false,
    inboundMediaExpiryDays: 30,
    logRetentionDays: 14,
    monthlyBudgetUsd: 5,
  },
  prod: {
    stage: 'prod',
    region: 'us-east-1',
    domain: {
      zoneName: ZONE,
      landingHost: ZONE,
      landingRedirectHosts: [`www.${ZONE}`],
      phoneHost: `m.${ZONE}`,
      dashboardHost: `panel.${ZONE}`,
    },
    api: { memoryMb: 3008, timeoutSeconds: 120, throttleRate: 50, throttleBurst: 100 },
    landing: true,
    originVerifyRevision: 1,
    retainData: true,
    inboundMediaExpiryDays: 90,
    logRetentionDays: 30,
    monthlyBudgetUsd: 10,
  },
};

export function stageConfig(name: string | undefined, alarmEmail?: string): StageConfig {
  const key = (name ?? 'dev') as StageName;
  const cfg = STAGES[key];
  if (!cfg) {
    throw new Error(`Unknown stage "${name}". Use one of: ${Object.keys(STAGES).join(', ')}`);
  }
  return alarmEmail ? { ...cfg, alarmEmail } : cfg;
}

/** `Helada-dev-Backend`, `Helada-prod-Edge`, ... */
export function stackName(cfg: StageConfig, part: string): string {
  return `Helada-${cfg.stage}-${part}`;
}
