"""Detect the machine a benchmark runs on.

Best-effort and dependency-free: every probe degrades to a readable string or
None rather than raising, so a benchmark never fails because hardware detection
did. Covers the three machine types this tool targets:

- Apple Silicon Mac (unified memory, Metal)
- Windows/Linux laptop with a discrete NVIDIA GPU
- Older laptop with an integrated/old GPU
"""

from __future__ import annotations

import platform
import re
import shutil
import socket
import subprocess
from typing import Dict, List, Optional


def _run(cmd: List[str], timeout: int = 5) -> Optional[str]:
    """Run a command, return stripped stdout, or None on any failure."""
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _total_ram_gb() -> Optional[float]:
    system = platform.system()
    try:
        if system == "Darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            return round(int(out) / (1024**3), 1) if out else None
        if system == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024**2), 1)
        if system == "Windows":
            out = _run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalSize", "/value"]
            )
            if out:
                m = re.search(r"TotalPhysicalSize=(\d+)", out)
                if m:
                    return round(int(m.group(1)) / (1024**3), 1)
    except (ValueError, OSError):
        return None
    return None


def _cpu_name() -> Optional[str]:
    system = platform.system()
    if system == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    # Windows and fallbacks
    return platform.processor() or None


def _gpus() -> List[str]:
    """Return a list of human-readable GPU descriptions."""
    gpus: List[str] = []
    system = platform.system()

    # NVIDIA, any OS: name + VRAM in MiB.
    nv = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader",
        ]
    )
    if nv:
        for line in nv.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2:
                gpus.append(f"{parts[0]} ({parts[1]})")
            elif parts and parts[0]:
                gpus.append(parts[0])

    if gpus:
        return gpus

    if system == "Darwin":
        # Apple Silicon: the GPU is part of the SoC; report chip + core count.
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        disp = _run(["system_profiler", "SPDisplaysDataType"], timeout=15)
        cores = None
        if disp:
            m = re.search(r"Total Number of Cores:\s*(\d+)", disp)
            if m:
                cores = m.group(1)
        if chip:
            return [f"{chip} GPU" + (f" ({cores} cores)" if cores else "")]
        return ["Apple integrated GPU"]

    if system == "Linux":
        lspci = _run(["lspci"])
        if lspci:
            for line in lspci.splitlines():
                if re.search(r"VGA|3D|Display", line):
                    gpus.append(line.split(":", 2)[-1].strip())

    if system == "Windows":
        out = _run(["wmic", "path", "win32_VideoController", "get", "name"])
        if out:
            for line in out.splitlines()[1:]:
                if line.strip():
                    gpus.append(line.strip())

    return gpus or ["unknown"]


def _hostname() -> str:
    return socket.gethostname().split(".")[0] or "host"


def _slugify(text: str) -> str:
    """Lowercase, keep alphanumerics, collapse the rest into single dashes."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text or "")).strip("-").lower()
    return s or "x"


def _short_cpu(cpu: Optional[str]) -> str:
    """Compact CPU tag for a folder name: 'Apple M4 Pro' -> 'applem4pro'.

    Drops marketing noise so the same chip yields the same tag across runs.
    """
    if not cpu:
        return "cpu"
    cleaned = re.sub(
        r"\b(\d+th gen|gen|intel|core|amd|ryzen|cpu|processor|with|radeon|graphics|r)\b",
        " ",
        cpu,
        flags=re.IGNORECASE,
    )
    return _slugify(cleaned)[:16]


def slug(hw: Dict) -> str:
    """A stable, recognizable folder name for a machine's results.

    Shape: <os>-<arch>-<cpu>-<host>, e.g. `darwin-arm64-applem4-mac-mini`.
    Same machine -> same slug across re-runs, so sessions group together.
    """
    parts = [
        _slugify(platform.system()),
        _slugify(hw.get("arch")),
        _short_cpu(hw.get("cpu")),
        _slugify(hw.get("host")),
    ]
    return "-".join(p for p in parts if p)


def detect(machine_label: Optional[str] = None) -> Dict:
    """Return a hardware profile dict for this machine.

    Everything is probed automatically. `machine_label` is an optional friendly
    name for the report; when omitted it defaults to the hostname. The stable
    folder identity comes from `slug()`, not the label.
    """
    host = _hostname()
    hw = {
        "host": host,
        "label": machine_label or host,
        "os": f"{platform.system()} {platform.release()}",
        "os_version": platform.version(),
        "arch": platform.machine(),
        "cpu": _cpu_name(),
        "ram_gb": _total_ram_gb(),
        "gpus": _gpus(),
        "python": platform.python_version(),
    }
    hw["slug"] = slug(hw)
    return hw
