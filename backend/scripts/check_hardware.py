"""Recommend a local Ollama model for this machine.

    python backend/scripts/check_hardware.py            # on the host
    docker compose exec backend python -m scripts.check_hardware   # what Docker sees

On Windows, Docker Desktop's WSL 2 VM gets a share of your RAM (by default
half); run it inside the container to see what the model will actually get.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

MODELS = [
    # (min RAM GB available to Docker, model, size, note)
    (12, "qwen3:4b", "~2.6 GB", "best answers; ~4-10 tokens/s on a modern laptop CPU"),
    (6, "qwen3:1.7b", "~1.4 GB", "default: good balance of quality and speed on CPU"),
    (4, "gemma3:1b", "~0.8 GB", "for low-memory machines"),
    (0, "qwen3:0.6b", "~0.5 GB", "smallest; answers are basic, extractive fallback does more work"),
]


def total_ram_gb() -> float | None:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError):
        pass
    if platform.system() == "Windows":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / 1024**3
    return None


def nvidia_gpu() -> str | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> None:
    ram = total_ram_gb()
    cpus = os.cpu_count()
    gpu = nvidia_gpu()
    print(f"OS: {platform.system()} {platform.release()} | CPUs: {cpus} | RAM: {ram:.1f} GB" if ram else f"CPUs: {cpus} | RAM: unknown")
    print(f"NVIDIA GPU: {gpu or 'none detected (CPU inference)'}")
    usable = (ram or 8) - 3  # leave room for the OS, ChromaDB, the backend and the embedding model
    choice = next(m for m in MODELS if usable >= m[0])
    print(f"\nRecommended OLLAMA_MODEL={choice[1]}  ({choice[2]}; {choice[3]})")
    if gpu:
        print("A GPU was found: larger models (e.g. qwen3:8b) are practical; see docs/docker.md for GPU setup.")
    print("\nAlternatives:")
    for _, name, size, note in MODELS:
        print(f"  {name:<12} {size:<9} {note}")
    print("\nSet it in .env (OLLAMA_MODEL=...) and run: docker compose up -d")


if __name__ == "__main__":
    main()
