#!/usr/bin/env python3
"""PKLOS System Inventory Collector v0.1.0 — cross-platform.

Works in:
  • Windows PowerShell / CMD (native)
  • WSL (Windows Subsystem for Linux)
  • Linux

On Windows / WSL-with-Windows-bridge:
  CIM data is read by spawning `powershell.exe Get-CimInstance`.
  VRAM is probed from the Lemonade / Ollama API.

On Linux (non-WSL):
  Hardware is read from /proc/cpuinfo, /proc/meminfo, dmidecode, lscpu.
  VRAM is probed from the Lemonade / Ollama API.

Usage:
  python scripts/collect_inventory.py \\
      --api-url https://YOUR-HOST \\
      --api-key YOUR_KEY

  python scripts/collect_inventory.py --dry-run   # print JSON, don't POST
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ── helpers ────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 10) -> str:
    """Run a command and return stdout as a string, '' on failure."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            errors="replace",
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _probe_api(url: str, timeout: int = 3) -> dict | None:
    """GET a JSON endpoint; return parsed dict or None."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ── Windows CIM via powershell.exe (works in WSL too) ─────────────────────────

_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
try {
  $cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
  $cs   = Get-CimInstance Win32_ComputerSystem
  $mems = Get-CimInstance Win32_PhysicalMemory
  $gpu  = Get-CimInstance Win32_VideoController | Select-Object -First 1
  $os   = Get-CimInstance Win32_OperatingSystem
  $bios = Get-CimInstance Win32_BIOS
  $disk = Get-CimInstance Win32_DiskDrive

  @{
    cpu  = @{ Name=$cpu.Name; Cores=$cpu.NumberOfCores; Threads=$cpu.NumberOfLogicalProcessors; MaxMHz=$cpu.MaxClockSpeed }
    mem  = @{ TotalBytes=$cs.TotalPhysicalMemory; DimmSum=($mems | Measure-Object -Property Capacity -Sum).Sum }
    gpu  = @{ Name=$gpu.Name; Processor=$gpu.VideoProcessor }
    os   = @{ Caption=$os.Caption; Version=$os.Version; Build=$os.BuildNumber }
    bios = @{ Mfr=$bios.Manufacturer; Ver=$bios.SMBIOSBIOSVersion }
    disk = @{ TotalBytes=($disk | Measure-Object -Property Size -Sum).Sum }
  } | ConvertTo-Json -Depth 3
} catch {
  Write-Output '{\"error\": \"cim_failed\"}'
}
"""


def _collect_windows() -> dict:
    """Use powershell.exe to collect CIM data (works on Windows and WSL)."""
    ps_exe = "powershell.exe" if platform.system() != "Windows" else "powershell"
    raw = _run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", _PS_TEMPLATE], timeout=30)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # powershell may add a BOM or extra whitespace
        try:
            data = json.loads(raw.lstrip("\ufeff").strip())
        except Exception:
            return {}

    if data.get("error"):
        return {}

    result: dict = {}

    # CPU
    cpu = data.get("cpu") or {}
    if cpu.get("Name"):
        result["cpu"] = {
            "Name": (cpu.get("Name") or "").strip(),
            "NumberOfCores": cpu.get("Cores"),
            "NumberOfLogicalProcessors": cpu.get("Threads"),
            "MaxClockSpeed": cpu.get("MaxMHz"),
        }

    # RAM — two sources (primary + DIMM sum corroboration)
    mem = data.get("mem") or {}
    if mem.get("TotalBytes"):
        result["memory"] = {
            "TotalPhysicalMemory": mem["TotalBytes"],
            "PhysicalMemoryCapacitySum": mem.get("DimmSum") or mem["TotalBytes"],
        }

    # GPU — NO AdapterRAM (INV-REQ-001)
    gpu = data.get("gpu") or {}
    if gpu.get("Name"):
        result["gpu"] = {
            "Name": (gpu.get("Name") or "").strip(),
            "VideoProcessor": gpu.get("Processor") or "",
            # AdapterRAM deliberately excluded
        }

    # OS
    os_d = data.get("os") or {}
    if os_d.get("Caption"):
        result["os"] = {
            "Caption": (os_d.get("Caption") or "").strip(),
            "Version": os_d.get("Version") or "",
            "BuildNumber": str(os_d.get("Build") or ""),
        }

    # BIOS
    bios = data.get("bios") or {}
    if bios.get("Mfr"):
        result["bios"] = {
            "Manufacturer": (bios.get("Mfr") or "").strip(),
            "SMBIOSBIOSVersion": bios.get("Ver") or "",
        }

    # Storage
    disk = data.get("disk") or {}
    if disk.get("TotalBytes"):
        result["storage"] = {"TotalBytes": disk["TotalBytes"]}

    return result


# ── Linux /proc reader ─────────────────────────────────────────────────────────

def _read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def _collect_linux() -> dict:
    """Collect hardware facts from /proc files and lscpu."""
    result: dict = {}

    # CPU — from lscpu first, fallback to /proc/cpuinfo
    lscpu_raw = _run(["lscpu"])
    cpu_info = _read_file("/proc/cpuinfo")

    cpu_name = ""
    cores = None
    threads = None
    max_mhz = None

    if lscpu_raw:
        for line in lscpu_raw.splitlines():
            k, _, v = line.partition(":")
            k = k.strip().lower()
            v = v.strip()
            if k == "model name":
                cpu_name = v
            elif k in ("cpu(s)", "on-line cpu(s) list"):
                try:
                    threads = int(v.split("-")[-1]) + 1 if "-" in v else int(v)
                except Exception:
                    pass
            elif k == "core(s) per socket":
                try:
                    sockets = 1
                    for l2 in lscpu_raw.splitlines():
                        if l2.strip().lower().startswith("socket(s)"):
                            sockets = int(l2.partition(":")[2].strip())
                    cores = int(v) * sockets
                except Exception:
                    pass
            elif k == "cpu max mhz":
                try:
                    max_mhz = float(v)
                except Exception:
                    pass

    if not cpu_name and cpu_info:
        m = re.search(r"^model name\s*:\s*(.+)$", cpu_info, re.MULTILINE)
        if m:
            cpu_name = m.group(1).strip()

    if not threads and cpu_info:
        threads = cpu_info.count("processor\t:")

    if cpu_name:
        result["cpu"] = {
            "Name": cpu_name,
            "NumberOfCores": cores,
            "NumberOfLogicalProcessors": threads,
            "MaxClockSpeed": int(max_mhz) if max_mhz else None,
        }

    # RAM — from /proc/meminfo (MemTotal)
    mem_info = _read_file("/proc/meminfo")
    if mem_info:
        m = re.search(r"^MemTotal:\s+(\d+)\s+kB", mem_info, re.MULTILINE)
        if m:
            total_bytes = int(m.group(1)) * 1024
            result["memory"] = {
                "TotalPhysicalMemory": total_bytes,
                "PhysicalMemoryCapacitySum": total_bytes,  # single-source on Linux
            }

    # GPU — from lspci
    lspci = _run(["lspci"])
    if lspci:
        for line in lspci.splitlines():
            low = line.lower()
            if "vga" in low or "display" in low or "3d" in low or "gpu" in low:
                gpu_name = re.sub(r"^[\w:.]+ ", "", line).strip()
                result["gpu"] = {"Name": gpu_name, "VideoProcessor": ""}
                break

    # OS
    os_info = _read_file("/etc/os-release")
    uname = _run(["uname", "-r"])
    if os_info:
        pretty = ""
        for line in os_info.splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
                break
        result["os"] = {
            "Caption": pretty or platform.system(),
            "Version": uname,
            "BuildNumber": uname,
        }

    # Storage — from /proc/partitions or df
    df_out = _run(["df", "--total", "-B1", "--output=size", "/"])
    if df_out:
        lines = [l.strip() for l in df_out.splitlines() if l.strip().isdigit()]
        if lines:
            result["storage"] = {"TotalBytes": int(lines[-1])}

    return result


# ── VRAM — Lemonade / Ollama API probe (platform-independent) ─────────────────

_LEMONADE_PORTS = [13305, 11434, 8080, 1234]


def _probe_vram() -> dict:
    """Probe the Lemonade/Ollama API for usable VRAM (A0 ground-truth source)."""
    for port in _LEMONADE_PORTS:
        data = _probe_api(f"http://localhost:{port}/api/memory")
        if data and data.get("total"):
            print(f"  VRAM: Lemonade on port {port} → "
                  f"{data['total'] / (1024**3):.0f} GiB (A0)")
            return {
                "source": f"lemonade_api:{port}",
                "total_bytes": int(data["total"]),
                "free_bytes": int(data.get("free", 0)),
            }
    print("  VRAM: no runtime API reachable — marking UNAVAILABLE")
    return {"source": "unavailable"}


def _probe_models() -> list[str]:
    """Try Lemonade /v1/models for installed model names."""
    for port in _LEMONADE_PORTS:
        data = _probe_api(f"http://localhost:{port}/v1/models")
        if data and data.get("data"):
            names = [m.get("id", "") for m in data["data"] if m.get("id")]
            if names:
                print(f"  Models: {len(names)} found via port {port}")
                return names
    return []


# ── Main ───────────────────────────────────────────────────────────────────────

def build_payload(subject: str) -> dict:
    """Collect hardware facts and return the inventory payload dict."""
    system = platform.system()
    is_wsl = "microsoft" in platform.uname().release.lower() if hasattr(platform, "uname") else False

    print(f"\nPKLOS System Inventory Collector v0.1.0")
    print(f"Platform: {system}" + (" (WSL)" if is_wsl else ""))
    print(f"Subject:  {subject}\n")

    # Choose collection method
    if system == "Windows" or is_wsl:
        print("  Hardware: reading from Windows CIM (powershell.exe)...")
        hw = _collect_windows()
        if not hw:
            print("  WARNING: CIM collection failed — falling back to /proc")
            hw = _collect_linux()
    else:
        print("  Hardware: reading from /proc + lscpu...")
        hw = _collect_linux()

    # Print what we found
    if hw.get("cpu"):
        c = hw["cpu"]
        print(f"  CPU:  {c.get('Name','?')} — {c.get('NumberOfCores','?')} cores / "
              f"{c.get('NumberOfLogicalProcessors','?')} threads")
    if hw.get("memory"):
        gib = (hw["memory"].get("TotalPhysicalMemory") or 0) / (1024**3)
        print(f"  RAM:  {gib:.0f} GiB")
    if hw.get("gpu"):
        print(f"  GPU:  {hw['gpu'].get('Name','?')}")
    if hw.get("os"):
        print(f"  OS:   {hw['os'].get('Caption','?')}")
    if hw.get("storage"):
        tb = (hw["storage"].get("TotalBytes") or 0) / (1024**4)
        print(f"  Disk: {tb:.1f} TiB")

    # VRAM and models (platform-independent API probes)
    vram = _probe_vram()
    models = _probe_models()

    return {
        "collector_version": "0.1.0",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subject": subject,
        "cpu":     hw.get("cpu", {}),
        "memory":  hw.get("memory", {}),
        "gpu":     hw.get("gpu", {}),
        "vram":    vram,
        "os":      hw.get("os", {}),
        "bios":    hw.get("bios", {}),
        "storage": hw.get("storage", {}),
        "installed_models": models,
    }


def post_payload(payload: dict, api_url: str, api_key: str) -> None:
    api_url = api_url.rstrip("/")
    endpoint = f"{api_url}/api/pklos/inventory"
    print(f"\nPOSTing to {endpoint} ...")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        print("\nSUCCESS")
        print(f"  Claims written:   {body.get('claims_written', '?')}")
        print(f"  Verified (A0/A1): {body.get('claims_verified', '?')}")
        print(f"  Conflicted:       {body.get('claims_conflicted', '?')}")
        print(f"  Unavailable:      {body.get('claims_unavailable', '?')}")
        violations = body.get("violations") or []
        if violations:
            print(f"\n  Policy notes:")
            for v in violations:
                print(f"    {v}")
        print("\nInventory stored. Ask the AI about your specs and it will")
        print("cite verified A0 values instead of guessing.")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"\nHTTP {e.code}: {body[:200]}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nPOST failed: {exc}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="PKLOS inventory collector — posts hardware facts to Orivellum"
    )
    parser.add_argument("--api-url", default="http://localhost:8080",
                        help="Base URL of the Orivellum server (default: http://localhost:8080)")
    parser.add_argument("--api-key", default=os.environ.get("ORIVELLUM_API_KEY", ""),
                        help="API key / session secret (or set ORIVELLUM_API_KEY env var)")
    parser.add_argument("--subject", default="device:a01",
                        help="Canonical device identifier (default: device:a01)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the JSON payload without posting it")
    args = parser.parse_args()

    payload = build_payload(args.subject)

    if args.dry_run:
        print("\n── DRY RUN — payload (not posted) ──────────────────────────────")
        print(json.dumps(payload, indent=2))
        return

    if not args.api_key:
        print("\nERROR: --api-key is required (or set ORIVELLUM_API_KEY)")
        sys.exit(1)

    post_payload(payload, args.api_url, args.api_key)


if __name__ == "__main__":
    main()
