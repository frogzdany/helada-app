// HTTP API Lambda authorizer (simple response). Lets a request through only when it carries the
// shared header that CloudFront adds on the way to the origin, so the execute-api endpoint cannot
// be used to go around CloudFront.
const { SecretsManagerClient, GetSecretValueCommand } = require('@aws-sdk/client-secrets-manager');
const { timingSafeEqual } = require('node:crypto');

const client = new SecretsManagerClient({});
const CACHE_MS = 5 * 60 * 1000;
// A wrong header must not turn into one Secrets Manager call each: refresh early at most once a minute.
const MIN_REFRESH_MS = 60 * 1000;

let accepted = [];
let loadedAt = 0;

async function stage(VersionStage) {
  try {
    const out = await client.send(new GetSecretValueCommand({ SecretId: process.env.SECRET_ARN, VersionStage }));
    return out.SecretString;
  } catch (err) {
    // A secret that was never rotated has no previous version.
    if (VersionStage === 'AWSPREVIOUS' && err.name === 'ResourceNotFoundException') return undefined;
    throw err;
  }
}

// Current and previous are both accepted, so a rotation does not reject requests while CloudFront
// is still being updated to send the new value.
async function load() {
  const values = await Promise.all([stage('AWSCURRENT'), stage('AWSPREVIOUS')]);
  accepted = values.filter(Boolean);
  loadedAt = Date.now();
}

function same(a, b) {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

const matches = (got) => accepted.some((value) => same(got, value));

exports.handler = async (event) => {
  const got = (event.headers || {})[process.env.HEADER_NAME];
  if (!got) return { isAuthorized: false };

  const age = Date.now() - loadedAt;
  if (age > CACHE_MS) await load();
  if (matches(got)) return { isAuthorized: true };

  if (age > MIN_REFRESH_MS && age <= CACHE_MS) {
    await load();
    return { isAuthorized: matches(got) };
  }
  return { isAuthorized: false };
};
