import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { readJson } from './utils.mjs';

const sourceDirectory = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(sourceDirectory, '..');

const defaults = {
  host: '127.0.0.1',
  port: 4310,
  dataRoot: path.join(packageRoot, 'data'),
  templateRoot: path.join(packageRoot, 'templates', 'static-site'),
  lemonade: {
    baseUrl: 'http://127.0.0.1:13305/api/v1',
    model: 'AUTO-DETECT',
    timeoutMs: 120000,
    apiKey: 'lemonade'
  },
  agent: { maxToolRounds: 36, maxRepairRounds: 3, maxCommandOutputChars: 16000 },
  preview: { enabled: true }
};

function merge(base, overrides) {
  return {
    ...base,
    ...overrides,
    lemonade: { ...base.lemonade, ...(overrides?.lemonade || {}) },
    agent: { ...base.agent, ...(overrides?.agent || {}) },
    preview: { ...base.preview, ...(overrides?.preview || {}) }
  };
}

export async function loadConfig() {
  const configPath = process.env.FORGE_CONFIG || path.join(packageRoot, 'config', 'factory.config.json');
  const fileConfig = await readJson(configPath, {});
  const config = merge(defaults, fileConfig);
  config.host = process.env.FORGE_HOST || config.host;
  // Accept PORT (Replit standard) or FORGE_PORT; FORGE_PORT takes precedence.
  config.port = Number(process.env.FORGE_PORT || process.env.PORT || config.port);
  config.dataRoot = path.resolve(process.env.FORGE_DATA_ROOT || config.dataRoot);
  config.templateRoot = path.resolve(process.env.FORGE_TEMPLATE_ROOT || config.templateRoot);
  config.lemonade.baseUrl = (process.env.LEMONADE_BASE_URL || config.lemonade.baseUrl).replace(/\/$/, '');
  config.lemonade.model = process.env.LEMONADE_MODEL || config.lemonade.model;
  config.lemonade.apiKey = process.env.LEMONADE_API_KEY || config.lemonade.apiKey;
  // In production Replit environments, allow 0.0.0.0 only when PORT is set by
  // the platform — Replit's proxy layer acts as the gateway (equivalent to VPN).
  // In all other cases, enforce loopback-only to match the security boundary.
  const isReplitRuntime = Boolean(process.env.PORT && !process.env.FORGE_HOST);
  if (!isReplitRuntime && !['127.0.0.1', '::1', 'localhost'].includes(config.host)) {
    throw new Error('Forge refuses non-loopback binding. Use Orivellum and a VPN reverse path instead.');
  }
  if (isReplitRuntime) config.host = '0.0.0.0';
  await fs.access(config.templateRoot);
  return config;
}

export { packageRoot };
