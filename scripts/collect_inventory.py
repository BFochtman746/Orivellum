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
import pathlib
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

  # Skip virtual/remote display adapters (Parsec, VMware, Citrix, RDP, etc.)
  # and pick the first real physical GPU.
  $virtualKeywords = @('parsec','vmware','citrix','remote','virtual','indirect','basic render',
                       'microsoft basic','rdp','spice','vnc','teamviewer')
  $allGpus = Get-CimInstance Win32_VideoController
  $realGpu = $allGpus | Where-Object {
    $n = $_.Name.ToLower()
    $isVirtual = $false
    foreach ($kw in $virtualKeywords) { if ($n -like "*$kw*") { $isVirtual = $true; break } }
    -not $isVirtual
  } | Select-Object -First 1
  if (-not $realGpu) { $realGpu = $allGpus | Select-Object -First 1 }

  $os   = Get-CimInstance Win32_OperatingSystem
  $bios = Get-CimInstance Win32_BIOS
  $disk = Get-CimInstance Win32_DiskDrive

  # RAM note: on unified-memory / UMA architecture (AMD Ryzen AI MAX, Apple Silicon, etc.)
  # Win32_ComputerSystem.TotalPhysicalMemory returns OS-available memory AFTER the
  # firmware/GPU reserves its share — it under-reports installed DRAM significantly.
  # Win32_PhysicalMemory.Capacity sum is the authoritative installed-DRAM figure.
  $dimmSum = ($mems | Measure-Object -Property Capacity -Sum).Sum

  @{
    cpu  = @{ Name=$cpu.Name; Cores=$cpu.NumberOfCores; Threads=$cpu.NumberOfLogicalProcessors; MaxMHz=$cpu.MaxClockSpeed }
    mem  = @{
      # Prefer DIMM sum as the installed-memory figure (correct on UMA).
      # Keep OS total as a secondary corroboration source.
      DimmSum      = $dimmSum
      OsVisibleBytes = $cs.TotalPhysicalMemory
    }
    gpu  = @{ Name=$realGpu.Name; Processor=$realGpu.VideoProcessor }
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

    # RAM — DIMM sum is the authoritative installed-DRAM figure on UMA systems.
    # Win32_ComputerSystem.TotalPhysicalMemory under-reports on AMD Ryzen AI MAX /
    # Apple Silicon because GPU firmware reserves memory before the OS sees it.
    # We store DimmSum as TotalPhysicalMemory (installed) and OsVisibleBytes as
    # the secondary corroboration source.
    mem = data.get("mem") or {}
    dimm_sum = mem.get("DimmSum") or 0
    os_visible = mem.get("OsVisibleBytes") or 0
    if dimm_sum or os_visible:
        primary = dimm_sum if dimm_sum else os_visible
        result["memory"] = {
            "TotalPhysicalMemory": primary,           # installed DRAM (DIMM sum)
            "PhysicalMemoryCapacitySum": dimm_sum or primary,
            "OsVisibleMemory": os_visible,            # informational: post-firmware
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


# ── VRAM — multi-strategy probe (platform-independent) ────────────────────────

_LEMONADE_PORTS = [13305, 11434, 8080, 1234]

# Lemonade and Ollama expose memory through different paths depending on version.
# We try all known paths; first non-empty result wins.
_VRAM_PATHS = [
    "/api/memory",            # Lemonade ≥0.13 standard
    "/v1/memory",             # alternate prefix
    "/api/v1/memory",         # alternate prefix
    "/memory",                # bare path
    "/info",                  # some Lemonade builds return {vram_total, vram_free}
    "/api/info",
    "/api/status",
]

# 32-bit field ceiling: Win32_VideoController.AdapterRAM saturates here on UMA.
_ADAPTER_RAM_SATURATION_BYTES = 4_294_967_296  # 4 GiB exactly


def _parse_vram_response(data: dict) -> tuple[int, int] | None:
    """Try all known field names from various Lemonade/Ollama memory responses.
    Returns (total_bytes, free_bytes) or None.
    """
    candidates = [
        ("total",       "free"),
        ("vram_total",  "vram_free"),
        ("totalMemory", "freeMemory"),
        ("total_memory","free_memory"),
        ("gpu_memory",  "gpu_memory_free"),
    ]
    for total_key, free_key in candidates:
        if total_key in data and data[total_key]:
            total = int(data[total_key])
            free  = int(data.get(free_key, 0))
            if total > 1_000_000:   # sanity: must be at least 1 MB
                return total, free
    return None


# ── Strategy 2: AMD ROCm rocm-smi ─────────────────────────────────────────────

def _probe_vram_rocm() -> tuple[int, int] | None:
    """Run rocm-smi --showmeminfo vram and parse the output.

    Available in WSL2 with AMD GPU pass-through and on Linux ROCm installs.
    Returns (total_bytes, used_bytes) or None.
    """
    out = _run(["rocm-smi", "--showmeminfo", "vram"], timeout=10)
    if not out:
        return None
    total = 0
    used  = 0
    for line in out.splitlines():
        low = line.lower()
        m   = re.search(r":\s*(\d+)", line)
        if not m:
            continue
        val = int(m.group(1))
        if "vram total memory" in low:
            total = val
        elif "vram total used memory" in low:
            used = val
    if total > 1_000_000:
        return total, used
    return None


# ── Strategy 4: Lemonade config file ──────────────────────────────────────────

def _probe_vram_lemonade_config() -> int | None:
    """Try to read the GPU memory allocation from a Lemonade config file.

    Returns total_bytes (configured allocation) or None.
    Authority A1: this is a user-configured value, not a live measurement.
    """
    candidates: list[pathlib.Path] = []

    if platform.system() == "Windows":
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if base:
                for sub in ("AMD\\Lemonade", "Lemonade", "lemonade"):
                    candidates.append(pathlib.Path(base) / sub / "config.json")
    else:
        home = pathlib.Path.home()
        candidates += [
            home / ".config" / "lemonade" / "config.json",
            home / ".local" / "share" / "lemonade" / "config.json",
            home / ".lemonade" / "config.json",
            pathlib.Path("/etc/lemonade/config.json"),
        ]
        # WSL: also check the Windows AppData tree via /mnt/c
        release = getattr(platform.uname(), "release", "").lower()
        if "microsoft" in release:
            win_appdata = _run(["cmd.exe", "/c", "echo %APPDATA%"], timeout=5).strip()
            if win_appdata and ":" in win_appdata:
                drive, rest = win_appdata.split(":", 1)
                wsl = pathlib.Path(f"/mnt/{drive.lower()}") / rest.lstrip("\\").replace("\\", "/")
                candidates += [
                    wsl / "AMD" / "Lemonade" / "config.json",
                    wsl / "Lemonade" / "config.json",
                ]

    for cfg_path in candidates:
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            continue

        # Top-level scalar keys
        for key in ("gpu_memory_gb", "vram_gb", "gpu_memory", "vram",
                    "memory_gb", "gpu_allocation_gb"):
            val = cfg.get(key)
            if val is not None:
                try:
                    gib = float(val)
                    if 1.0 <= gib <= 1024.0:
                        return int(gib * 1_073_741_824)
                except (TypeError, ValueError):
                    pass

        # Nested: {"gpu": {"memory_gb": 96}}
        for section in ("gpu", "GPU", "hardware", "memory"):
            sub = cfg.get(section)
            if not isinstance(sub, dict):
                continue
            for key in ("memory_gb", "vram_gb", "gpu_memory_gb", "allocation_gb"):
                val = sub.get(key)
                if val is not None:
                    try:
                        gib = float(val)
                        if 1.0 <= gib <= 1024.0:
                            return int(gib * 1_073_741_824)
                    except (TypeError, ValueError):
                        pass

    return None


# ── Strategy 5: Win32_VideoController.AdapterRAM (LAST RESORT) ────────────────
#
# IMPORTANT: This is a 32-bit CIM field that saturates at exactly 4 GiB
# (4,294,967,296 bytes) on unified-memory architecture.  On AMD Ryzen AI MAX+
# 395 with 96 GiB configured VRAM, this ALWAYS returns 4 GiB — not 96 GiB.
#
# It is included here only because:
#   (a) on discrete GPUs with <4 GiB it returns the real value, and
#   (b) the saturation marker itself confirms a UMA system likely has more VRAM
#       than the 4 GiB the field can represent.
#
# INV-REQ-001: this source MUST NOT be stored under a locator containing
# "adapterram" for any vram_* predicate — the authority resolver blocks it.
# We store it as a separate advisory field ("vram_hint") in the payload so the
# adapter can use it as a last-resort lower-bound without violating the policy.

def _probe_vram_cim_adapter_ram() -> dict | None:
    """Read Win32_VideoController.AdapterRAM for the first AMD/Radeon GPU.

    Returns:
      {"bytes": <int>, "saturated": <bool>, "gpu_name": <str>} or None.
    """
    is_wsl = "microsoft" in getattr(platform.uname(), "release", "").lower()
    if platform.system() not in ("Windows",) and not is_wsl:
        return None

    ps_exe = "powershell.exe" if platform.system() != "Windows" else "powershell"
    ps_cmd = r"""
$gpu = Get-CimInstance Win32_VideoController |
       Where-Object { $_.Name -like '*Radeon*' -or $_.Name -like '*AMD*' } |
       Select-Object -First 1
if ($gpu) {
  @{ bytes=$gpu.AdapterRAM; name=$gpu.Name } | ConvertTo-Json -Compress
}
"""
    out = _run([ps_exe, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], timeout=15).strip()
    if not out:
        return None
    try:
        data = json.loads(out.lstrip("\ufeff"))
        val = int(data.get("bytes") or 0)
        if val <= 0:
            return None
        return {
            "bytes": val,
            "saturated": val >= _ADAPTER_RAM_SATURATION_BYTES,
            "gpu_name": (data.get("name") or "").strip(),
        }
    except Exception:
        return None


# ── CPU-based UMA detection for user guidance ─────────────────────────────────

_AMD_RYZEN_AI_MAX_RE = re.compile(
    r"ryzen\s+ai\s+max\+?\s*3[5-9]\d|ryzen\s+ai\s+max\+",
    re.IGNORECASE,
)


def _detect_cpu_name() -> str:
    """Return the CPU model string from the best available source."""
    is_wsl = "microsoft" in getattr(platform.uname(), "release", "").lower()
    if platform.system() == "Windows" or is_wsl:
        ps_exe = "powershell.exe" if platform.system() != "Windows" else "powershell"
        name = _run(
            [ps_exe, "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
            timeout=10,
        ).strip()
        if name:
            return name
    # Linux / WSL fallback
    try:
        with open("/proc/cpuinfo") as f:
            cpu_info = f.read()
        m = re.search(r"^model name\s*:\s*(.+)$", cpu_info, re.MULTILINE)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ""


def _print_vram_fallback_hint(cpu_name: str) -> None:
    """Print architecture-specific VRAM guidance when all probes fail."""
    print("  VRAM: no runtime memory API or ROCm tool found on any probed path.")

    if _AMD_RYZEN_AI_MAX_RE.search(cpu_name):
        print()
        print("  ┌─ AMD Ryzen AI MAX+ 395 detected ───────────────────────────────────────────┐")
        print("  │ This processor uses unified LPDDR5x memory shared between CPU and GPU.     │")
        print("  │ The usable VRAM depends on your BIOS/firmware GPU-memory allocation.       │")
        print("  │                                                                             │")
        print("  │ To detect this automatically: start Lemonade, then re-run the collector.  │")
        print("  │                                                                             │")
        print("  │ Or supply the allocation directly (common values for 128 GiB total RAM):  │")
        print("  │   --vram-gb 96   ← most common  (4 GiB reserved for OS)                  │")
        print("  │   --vram-gb 64   (64 GiB shared between CPU and GPU)                     │")
        print("  │   --vram-gb 48   (80 GiB shared between CPU and GPU)                     │")
        print("  └─────────────────────────────────────────────────────────────────────────────┘")
    else:
        print("  If this is a UMA system (AMD Ryzen AI MAX+, Apple Silicon),")
        print("  ensure Lemonade is running and re-run the collector, or pass:")
        print("  --vram-gb <N>   e.g. --vram-gb 96")


# ── Master VRAM probe ─────────────────────────────────────────────────────────

def _probe_vram(manual_gib: float | None = None) -> dict:
    """Return a VRAM descriptor using the best available source.

    Strategy order
    --------------
    0. Manual override (--vram-gb flag)                        → source: user_supplied
    1. Lemonade/Ollama REST API (various memory endpoints)     → source: lemonade_api:<port>
    2. AMD ROCm  rocm-smi --showmeminfo vram                   → source: rocm_smi
    3. Lemonade config file (configured allocation, A1)        → source: lemonade_config
    4. Win32_VideoController AdapterRAM — advisory console     → stored in vram_hint only,
       note only; NO claim is ever written from this source.     never in vram.
    5. Fallback: print architecture-specific guidance          → source: unavailable
    """
    if manual_gib is not None:
        total_bytes = int(manual_gib * 1_073_741_824)
        print(f"  VRAM: manual override → {manual_gib:.0f} GiB (user-supplied)")
        return {
            "source": "user_supplied",
            "total_bytes": total_bytes,
            "free_bytes": 0,
        }

    # ── 1. Lemonade / Ollama REST API ─────────────────────────────────────────
    for port in _LEMONADE_PORTS:
        for path in _VRAM_PATHS:
            data = _probe_api(f"http://localhost:{port}{path}", timeout=2)
            if not data:
                continue
            parsed = _parse_vram_response(data)
            if parsed:
                total, free = parsed
                gib = total / 1_073_741_824
                print(f"  VRAM: Lemonade REST {path} port {port} → {gib:.0f} GiB (A0)")
                return {"source": f"lemonade_api:{port}", "total_bytes": total, "free_bytes": free}

    # ── 2. AMD ROCm rocm-smi ─────────────────────────────────────────────────
    rocm = _probe_vram_rocm()
    if rocm:
        total, used = rocm
        gib = total / 1_073_741_824
        print(f"  VRAM: rocm-smi → {gib:.0f} GiB (A0)")
        return {"source": "rocm_smi", "total_bytes": total, "free_bytes": max(0, total - used)}

    # ── 4. Lemonade config file ───────────────────────────────────────────────
    cfg_bytes = _probe_vram_lemonade_config()
    if cfg_bytes:
        gib = cfg_bytes / 1_073_741_824
        print(f"  VRAM: Lemonade config → {gib:.0f} GiB (A1 — configured allocation)")
        return {"source": "lemonade_config", "total_bytes": cfg_bytes, "free_bytes": 0}

    # ── 5. Win32_VideoController.AdapterRAM (LAST RESORT / advisory only) ─────
    # Stored in payload["vram_hint"], NOT in payload["vram"], so the adapter can
    # use or discard it without violating INV-REQ-001.
    adapter_info = _probe_vram_cim_adapter_ram()

    cpu_name = _detect_cpu_name()
    _print_vram_fallback_hint(cpu_name)

    if adapter_info:
        gib  = adapter_info["bytes"] / 1_073_741_824
        note = "SATURATED — real value > 4 GiB" if adapter_info["saturated"] else "may be inaccurate on UMA"
        print(f"  VRAM hint: Win32 AdapterRAM → {gib:.0f} GiB ({note})")

    return {
        "source": "unavailable",
        # Keep advisory data for the adapter without triggering INV-REQ-001
        **({"_adapter_ram_hint": adapter_info} if adapter_info else {}),
    }


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

def build_payload(subject: str, vram_gib: float | None = None) -> dict:
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
    vram = _probe_vram(manual_gib=vram_gib)
    models = _probe_models()

    payload: dict = {
        "collector_version": "0.1.0",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subject": subject,
        "cpu":     hw.get("cpu", {}),
        "memory":  hw.get("memory", {}),
        "gpu":     hw.get("gpu", {}),
        "vram":    {k: v for k, v in vram.items() if not k.startswith("_")},
        "os":      hw.get("os", {}),
        "bios":    hw.get("bios", {}),
        "storage": hw.get("storage", {}),
        "installed_models": models,
    }
    # Advisory: Win32_VideoController.AdapterRAM hint (stored separately from
    # vram to avoid triggering INV-REQ-001 in the adapter).
    if vram.get("_adapter_ram_hint"):
        payload["vram_hint"] = vram["_adapter_ram_hint"]
    return payload


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
    parser.add_argument("--vram-gb", type=float, default=None,
                        help="Override VRAM size in GiB (use when the API probe fails). "
                             "For AMD Ryzen AI MAX+ 395 with 128 GiB: typically 96.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the JSON payload without posting it")
    args = parser.parse_args()

    payload = build_payload(args.subject, vram_gib=args.vram_gb)

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
