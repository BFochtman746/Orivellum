import { promises as fs } from 'node:fs';
import path from 'node:path';
import { assertAllowedCommand, assertAllowedPath } from './policy.mjs';
import { runProcess } from './process.mjs';
import { truncate } from './utils.mjs';

export const toolDefinitions = [
  {
    type: 'function',
    function: {
      name: 'list_files',
      description: 'List permitted project files recursively. Use before editing an unfamiliar project.',
      parameters: {
        type: 'object', properties: { path: { type: 'string', description: 'Relative path, default project root.' }, maxDepth: { type: 'integer', minimum: 1, maximum: 8 } }, additionalProperties: false
      }
    }
  },
  {
    type: 'function',
    function: {
      name: 'read_file',
      description: 'Read a UTF-8 text file within the isolated worktree.',
      parameters: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'], additionalProperties: false }
    }
  },
  {
    type: 'function',
    function: {
      name: 'write_file',
      description: 'Create or replace a UTF-8 file within the isolated worktree. Do not edit hidden, secrets, or dependency paths.',
      parameters: { type: 'object', properties: { path: { type: 'string' }, content: { type: 'string' } }, required: ['path', 'content'], additionalProperties: false }
    }
  },
  {
    type: 'function',
    function: {
      name: 'run_command',
      description: 'Run a permitted non-shell command in the worktree. argv must be an executable plus arguments. Never use install, ci, publish, push, reset, clean, shell, network, or destructive commands.',
      parameters: { type: 'object', properties: { argv: { type: 'array', items: { type: 'string' }, minItems: 1 }, timeoutMs: { type: 'integer', minimum: 1000, maximum: 300000 } }, required: ['argv'], additionalProperties: false }
    }
  },
  {
    type: 'function',
    function: {
      name: 'record_note',
      description: 'Record a concise, evidence-based note in the job ledger.',
      parameters: { type: 'object', properties: { note: { type: 'string', maxLength: 2000 } }, required: ['note'], additionalProperties: false }
    }
  }
];

export function createToolExecutor({ workspace, policy, onEvent, maxOutputChars }) {
  return async function execute(name, argumentsText) {
    let args;
    try { args = typeof argumentsText === 'string' ? JSON.parse(argumentsText || '{}') : argumentsText; }
    catch { return { ok: false, error: 'Tool arguments were not valid JSON.' }; }
    try {
      if (name === 'list_files') {
        const root = assertAllowedPath(policy, workspace, args.path || '.');
        const files = await walk(root, Number(args.maxDepth || 4), workspace, policy);
        return { ok: true, files };
      }
      if (name === 'read_file') {
        const target = assertAllowedPath(policy, workspace, args.path);
        const stat = await fs.stat(target);
        if (!stat.isFile()) throw new Error('Requested path is not a file.');
        if (stat.size > policy.files.maxWriteBytes * 4) throw new Error('Requested file exceeds readable size limit.');
        return { ok: true, path: args.path, content: truncate(await fs.readFile(target, 'utf8'), maxOutputChars) };
      }
      if (name === 'write_file') {
        const target = assertAllowedPath(policy, workspace, args.path);
        const content = String(args.content || '');
        if (Buffer.byteLength(content) > policy.files.maxWriteBytes) throw new Error('Write exceeds policy size limit.');
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, content, 'utf8');
        await onEvent('file_write', `Wrote ${args.path}`, { bytes: Buffer.byteLength(content) });
        return { ok: true, path: args.path, bytes: Buffer.byteLength(content) };
      }
      if (name === 'run_command') {
        const argv = args.argv;
        const command = assertAllowedCommand(policy, argv);
        const result = await runProcess(command, argv.slice(1), {
          cwd: workspace,
          timeoutMs: Number(args.timeoutMs || policy.commands.defaultTimeoutMs),
          maxOutputChars
        });
        await onEvent('command', `${argv.join(' ')} → ${result.ok ? 'passed' : 'failed'}`, { code: result.code, timedOut: result.timedOut });
        return result;
      }
      if (name === 'record_note') {
        await onEvent('agent_note', String(args.note || '').slice(0, 2000));
        return { ok: true };
      }
      return { ok: false, error: `Unknown tool: ${name}` };
    } catch (error) {
      await onEvent('tool_policy', `${name} blocked: ${error.message}`);
      return { ok: false, error: error.message };
    }
  };
}

async function walk(directory, depth, workspace, policy) {
  if (depth < 0) return [];
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const result = [];
  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (policy.files.forbiddenPaths.includes(entry.name)) continue;
    const absolute = path.join(directory, entry.name);
    const relative = path.relative(workspace, absolute).replaceAll('\\', '/');
    if (entry.isDirectory()) {
      result.push(`${relative}/`);
      if (depth > 0) result.push(...await walk(absolute, depth - 1, workspace, policy));
    } else if (entry.isFile()) result.push(relative);
    if (result.length >= 500) return result;
  }
  return result;
}
