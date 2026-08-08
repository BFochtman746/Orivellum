import { createHash, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';

export const nowIso = () => new Date().toISOString();

export function createId(prefix) {
  return `${prefix}-${new Date().toISOString().slice(0, 10).replaceAll('-', '')}-${randomUUID().slice(0, 8).toUpperCase()}`;
}

export function slug(value) {
  return String(value || 'site').toLowerCase().trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 56) || 'site';
}

export async function ensureDir(directory) {
  await fs.mkdir(directory, { recursive: true });
  return directory;
}

export async function readJson(filePath, fallback = undefined) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT' && fallback !== undefined) return fallback;
    throw error;
  }
}

export async function writeJsonAtomic(filePath, value) {
  await ensureDir(path.dirname(filePath));
  const temporary = `${filePath}.${process.pid}.${randomUUID()}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await fs.rename(temporary, filePath);
}

export async function appendJsonLine(filePath, value) {
  await ensureDir(path.dirname(filePath));
  await fs.appendFile(filePath, `${JSON.stringify(value)}\n`, 'utf8');
}

export function resolveWithin(root, requestedPath = '.') {
  const resolvedRoot = path.resolve(root);
  const resolvedPath = path.resolve(resolvedRoot, requestedPath);
  if (resolvedPath !== resolvedRoot && !resolvedPath.startsWith(`${resolvedRoot}${path.sep}`)) {
    throw new Error(`Path escapes permitted workspace: ${requestedPath}`);
  }
  return resolvedPath;
}

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

export function truncate(value, max = 16000) {
  const text = String(value ?? '');
  return text.length > max ? `${text.slice(0, max)}\n…[truncated ${text.length - max} characters]` : text;
}

export function errorRecord(error) {
  return { name: error?.name || 'Error', message: error?.message || String(error), stack: error?.stack };
}

export function safeName(value, max = 120) {
  return String(value || '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').trim().slice(0, max) || 'untitled';
}
