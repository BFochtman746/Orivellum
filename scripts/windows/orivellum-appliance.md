# Orivellum Headless Appliance — Operator Manual

This box runs Orivellum (AI research assistant + image generation + text-to-speech)
headlessly — no monitor, no keyboard needed after initial setup.

---

## What runs where

| Component | Where | How it starts |
|---|---|---|
| ComfyUI (image gen) | WSL Ubuntu · podman container | `orivellum-boot.ps1` at login |
| Orivellum API + Web UI | Windows · Python (uv) | `orivellum-boot.ps1` at login |
| Auto-start trigger | Windows Task Scheduler | Fires at every login |

---

## Initial one-time setup

### 1 — Copy boot files

```
C:\OrivellumAppliance\
  orivellum-boot.ps1      ← main boot script
  register-boot.ps1       ← run once as Admin to install task
  orivellum-boot.vbs      ← created automatically by register-boot.ps1
  logs\                   ← boot logs appear here
```

### 2 — Edit orivellum-boot.ps1 top parameters (if needed)

```powershell
$OrivellumPath = "C:\Orivellum-main"   # folder with start.ps1
$WslDistro     = "Ubuntu-24.04"         # your WSL distro name
$WslUser       = "bfoch"                # your WSL username
$ContainerName = "orivellum-comfyui"   # podman container name
```

Confirm your distro name: `wsl --list --verbose`

### 3 — Register the Task Scheduler entry

```powershell
# Open PowerShell as Administrator, then:
cd C:\OrivellumAppliance
.\register-boot.ps1
```

### 4 — Enable auto-login (so it all works after a power cut)

1. Press **Win+R** → type `netplwiz` → Enter
2. Uncheck **"Users must enter a user name and password"**
3. Enter your password when prompted → OK
4. Done — Windows now logs in automatically after boot/power-cut

### 5 — Wire ComfyUI into Orivellum

Open Orivellum in a browser → **System Settings** → **Image Generation Backend** → Set URL:

```
http://172.20.205.199:8188
```

> ⚠ This IP can change if you reboot WSL before setting things up. Check current IP:
> Open Ubuntu → `hostname -I` — use the first address.

Orivellum auto-detects whether the URL is ComfyUI (port 8188) and uses the right API.

---

## Day-to-day: how the box starts

1. Power comes back on (or you reboot)
2. Windows boots → auto-logs in your user
3. Task Scheduler fires `orivellum-boot.ps1` silently in the background
4. WSL starts, podman starts ComfyUI, Orivellum starts
5. Everything is live — you access it from any device on the network

**No monitor or keyboard required after initial setup.**

---

## Checking health from another device (no monitor)

### Is Orivellum up?

```
http://<windows-ip>:8080/api/system/health
```

Should return `{"status": "ok", ...}`

### Is ComfyUI up?

```
http://172.20.205.199:8188/system_stats
```

Should return JSON with GPU/CPU stats.

### Find your Windows IP

On the Windows machine (one-time): `ipconfig` → look for IPv4 under your network adapter.
Or check your router's device list for the machine's hostname.

---

## If something isn't working after a reboot

### Check the boot log

```
C:\OrivellumAppliance\logs\boot-YYYY-MM-DD_HH-mm-ss.log
```

Look for WARNING lines. Most common issues:

| Log says | Cause | Fix |
|---|---|---|
| `ComfyUI did not respond within 120s` | Container didn't start | See below |
| `not a git repo` | Unzipped instead of cloned | One-time: clone repo, redo path |
| `Orivellum window launched` but UI unreachable | start.ps1 failed | Check start.ps1 manually |

### ComfyUI didn't start

Open Ubuntu terminal and run:

```bash
podman ps -a
# If container shows Exited:
podman start orivellum-comfyui
# If container is missing:
# Re-run your original podman create command
```

### Manually trigger the boot script

```powershell
Start-ScheduledTask -TaskName "OrivellumAppliance"
# or run it directly:
C:\OrivellumAppliance\orivellum-boot.ps1
```

### Restart just Orivellum

```powershell
cd C:\Orivellum-main
.\start.ps1
```

### WSL IP changed after reboot

```bash
# In Ubuntu:
hostname -I   # first address is the new IP
```

Update System Settings → Image Generation Backend with the new IP.

---

## Vision (image understanding in chat)

Set the vision model in **System Settings → Vision Model** to your local model name
(e.g. `llava`, `qwen2-vl`, `llama3.2-vision`), then click **Test Vision** to confirm.

Attach images in mobile chat via the 📷 button in the message bar.

---

## Text to Speech

Orivellum has three TTS engines in fallback order:

1. **Kokoro ONNX** — high quality, auto-downloads on first use (~500 MB)
2. **espeak-ng + ffmpeg** — lightweight, always available on Linux/WSL
3. **Your AI server** — if it supports OpenAI TTS API

Use the **Read Aloud** button on any Library document to hear it read back.
Use the 🔊 button in chat (coming soon on mobile) for voice replies.

### Making TTS work on Windows

Orivellum runs on Windows but TTS uses Linux tools (espeak-ng). Two options:

**Option A — Run Orivellum inside WSL** (recommended for full TTS):
```bash
# In Ubuntu, from Orivellum folder:
uv run python -m orivellum.api.main
```

**Option B — Install espeak-ng on Windows** and add it to PATH:
Download from https://github.com/espeak-ng/espeak-ng/releases

---

## Making the WSL IP permanent (optional, for later)

Add a Windows port-forwarding rule so `localhost:8188` always reaches ComfyUI,
even when the WSL IP changes:

```powershell
# Run as Administrator — do this after each reboot if IP changes,
# or add to orivellum-boot.ps1's Step 1:
$wslIp = (wsl -d Ubuntu-24.04 -u bfoch -- hostname -I).Trim().Split()[0]
netsh interface portproxy add v4tov4 listenport=8188 listenaddress=127.0.0.1 `
    connectport=8188 connectaddress=$wslIp protocol=tcp
```

Add this block to `orivellum-boot.ps1` after the WSL IP detection step to
make localhost:8188 work reliably without changing the System Settings URL.

---

## Quick-reference card

| Task | Command |
|---|---|
| Check WSL IP | `wsl -d Ubuntu-24.04 -- hostname -I` |
| Check ComfyUI | `curl http://172.20.205.199:8188/system_stats` |
| Check Orivellum | `curl http://localhost:8080/api/system/health` |
| View boot log | `notepad C:\OrivellumAppliance\logs\boot-*.log` |
| Restart everything | `Start-ScheduledTask -TaskName OrivellumAppliance` |
| Restart Orivellum only | `cd C:\Orivellum-main; .\start.ps1` |
| Start ComfyUI manually | `wsl -d Ubuntu-24.04 -u bfoch -- bash -lic "podman start orivellum-comfyui"` |
