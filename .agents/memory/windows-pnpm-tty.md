---
name: Windows pnpm TTY quirk
description: pnpm installed via WinGet is a .ps1 wrapper, not a .exe; must use powershell.exe to launch it, and must give it a real TTY window.
---

## Rule
`pnpm` on WinGet-installed Windows is a PowerShell `.ps1` script that wraps the real `pnpm.exe` buried inside `@pnpm/exe`. Two consequences:

1. **cmd.exe cannot run it** — running `pnpm.cmd` or `pnpm.ps1` from a batch file via `cmd.exe /c` silently exits with code 0 without launching Vite.
2. **Redirected stdout kills Vite** — even when run via `powershell.exe`, if stdout is redirected (`-RedirectStandardOutput`) pnpm detaches Vite as a background child and itself exits immediately.

## Fix
Use `Start-Process -FilePath "powershell.exe"` with **no** `-NoNewWindow` and **no** `-RedirectStandardOutput` so a new console window opens with a real TTY:

```powershell
$webProc = Start-Process -FilePath "powershell.exe" `
  -ArgumentList "-NoProfile", "-Command", "& '$pnpmExe' run dev" `
  -WorkingDirectory $uiDir `
  -PassThru
```

Then health-check the port (`Invoke-WebRequest http://127.0.0.1:5173/`) instead of monitoring the pnpm process, since the process object is the new window's `powershell.exe`, not Vite itself.

**Why:** `Get-Command pnpm` on this machine returns a path ending in `.ps1`. Passing that path to `cmd.exe` or `Start-Process -FilePath` directly fails; only `powershell.exe -File` or `& $pnpmExe` from within PowerShell works.

**How to apply:** Any time start.ps1 needs to spawn pnpm on Windows, use `powershell.exe` as the outer launcher and open a new window.
