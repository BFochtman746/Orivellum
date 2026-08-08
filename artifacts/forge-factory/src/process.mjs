import { spawn } from 'node:child_process';
import { truncate } from './utils.mjs';

export async function runProcess(command, args = [], options = {}) {
  const { cwd, timeoutMs = 120000, env = {}, maxOutputChars = 16000 } = options;
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd,
      shell: false,
      windowsHide: true,
      env: { ...process.env, ...env }
    });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill('SIGTERM');
      // Escalate to SIGKILL after 5 s if the process ignores SIGTERM.
      // This prevents a hung subprocess from blocking the entire gate timeout.
      setTimeout(() => { try { child.kill('SIGKILL'); } catch { /* already exited */ } }, 5000);
    }, timeoutMs);
    child.stdout.on('data', (chunk) => { stdout = truncate(`${stdout}${chunk}`, maxOutputChars); });
    child.stderr.on('data', (chunk) => { stderr = truncate(`${stderr}${chunk}`, maxOutputChars); });
    child.on('error', (error) => {
      clearTimeout(timer);
      resolve({ command, args, code: null, signal: null, timedOut, stdout, stderr: truncate(`${stderr}${error.message}`, maxOutputChars), ok: false });
    });
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      resolve({ command, args, code, signal, timedOut, stdout, stderr, ok: !timedOut && code === 0 });
    });
  });
}
