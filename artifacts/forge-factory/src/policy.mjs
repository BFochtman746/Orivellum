import path from 'node:path';
import { readJson, resolveWithin } from './utils.mjs';

export async function loadPolicy(packageRoot) {
  return readJson(path.join(packageRoot, 'config', 'default-policy.json'));
}

export function assertAllowedPath(policy, workspace, candidate) {
  const resolved = resolveWithin(workspace, candidate);
  const relative = path.relative(workspace, resolved).replaceAll('\\', '/');
  const segments = relative.split('/');
  const forbidden = policy.files.forbiddenPaths || [];
  if (segments.some((segment) => forbidden.includes(segment))) {
    throw new Error(`Forbidden path: ${candidate}`);
  }
  return resolved;
}

export function assertAllowedCommand(policy, argv) {
  if (!Array.isArray(argv) || argv.length === 0 || typeof argv[0] !== 'string') {
    throw new Error('Command must be a non-empty argv array.');
  }
  const command = path.basename(argv[0]);
  if (!policy.commands.allow.includes(command)) {
    throw new Error(`Command is not allowed: ${command}`);
  }
  const joined = argv.slice(1).join(' ');
  if ((policy.commands.denyArguments || []).some((denied) => joined.split(/\s+/).includes(denied))) {
    throw new Error(`Command arguments require approval: ${joined}`);
  }
  const args = argv.slice(1);
  if (command === 'node' && !isSafeNodeInvocation(args)) throw new Error('Only node --check, node --test, and workspace script-file invocations are allowed.');
  if (command === 'npm' && !isSafeNpmInvocation(args)) throw new Error('Only npm test and npm run <approved-script> are allowed.');
  if (command === 'git' && !['status', 'diff', 'log', 'rev-parse'].includes(args[0])) throw new Error('Only read-only Git commands are allowed to the builder.');
  return command;
}

function isSafeNodeInvocation(args) {
  if (!args.length) return false;
  if (args[0] === '--check' || args[0] === '--test') return true;
  return args.length === 1 && /\.(?:m?js|cjs)$/i.test(args[0]) && !args[0].startsWith('-');
}

function isSafeNpmInvocation(args) {
  if (args[0] === 'test' && args.length === 1) return true;
  return args[0] === 'run' && ['lint', 'test', 'build'].includes(args[1]) && args.length === 2;
}
