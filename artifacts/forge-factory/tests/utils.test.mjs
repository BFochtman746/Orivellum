import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { mkdtemp, rm } from 'node:fs/promises';
import { assertAllowedCommand, assertAllowedPath } from '../src/policy.mjs';
import { resolveWithin, sha256, slug } from '../src/utils.mjs';

const policy = {
  files: { forbiddenPaths: ['.git', '.env'], maxWriteBytes: 100 },
  commands: { allow: ['node', 'git'], denyArguments: ['push', 'reset'] }
};

test('workspace paths cannot escape their root', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'forge-utils-'));
  try {
    assert.equal(resolveWithin(root, 'safe/file.txt'), path.join(root, 'safe/file.txt'));
    assert.throws(() => resolveWithin(root, '../outside.txt'));
    assert.throws(() => assertAllowedPath(policy, root, '.git/config'));
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('command policy blocks destructive Git arguments', () => {
  assert.equal(assertAllowedCommand(policy, ['node', '--check', 'app.js']), 'node');
  assert.throws(() => assertAllowedCommand(policy, ['git', 'push']));
  assert.throws(() => assertAllowedCommand(policy, ['bash', '-c', 'echo unsafe']));
});

test('stable utility transforms are deterministic', () => {
  assert.equal(slug('North Star / Advisory'), 'north-star-advisory');
  assert.equal(sha256('forge'), sha256('forge'));
  assert.notEqual(sha256('forge'), sha256('forge-2'));
});
