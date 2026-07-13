"""Best-effort detection of the host machine's RAM and free disk space, used
to warn before downloading a model that's unlikely to run well here."""

import ctypes
import os
import shutil


def total_ram_gb():
    """Total physical RAM in GB, or None if it can't be determined."""
    try:
        if os.name == "nt":
            class _MemStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStatusEx()
            stat.dwLength = ctypes.sizeof(_MemStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullTotalPhys / (1024 ** 3)
            return None
        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names \
                and "SC_PHYS_PAGES" in os.sysconf_names:
            return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except Exception:
        pass
    return None


def free_ram_gb():
    """Currently-available (not just total) physical RAM in GB, or None if
    it can't be determined. Used to recommend an LLM quality tier that
    actually fits what's free right now, not just what the machine has
    installed."""
    try:
        if os.name == "nt":
            class _MemStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MemStatusEx()
            stat.dwLength = ctypes.sizeof(_MemStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return stat.ullAvailPhys / (1024 ** 3)
            return None

        if os.path.isfile("/proc/meminfo"):
            # Linux: MemAvailable already accounts for reclaimable
            # cache/buffers (what "free -h"'s "available" column shows);
            # MemFree alone under-counts by excluding those.
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    key, _, rest = line.partition(":")
                    info[key.strip()] = rest.strip()
            for key in ("MemAvailable", "MemFree"):
                if key in info:
                    kb = int(info[key].split()[0])
                    return kb / (1024 ** 2)
            return None

        # macOS: no os.sysconf equivalent for "available" RAM — approximate
        # from vm_stat's free + inactive pages (inactive is reclaimable).
        import re
        import subprocess

        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        ).stdout
        page_size_m = re.search(r"page size of (\d+) bytes", out)
        page_size = int(page_size_m.group(1)) if page_size_m else 4096
        pages = {}
        for line in out.splitlines():
            m = re.match(r"Pages (\w[\w ]*\w):\s*(\d+)\.?", line)
            if m:
                pages[m.group(1)] = int(m.group(2))
        free_pages = pages.get("free", 0) + pages.get("inactive", 0)
        if free_pages:
            return free_pages * page_size / (1024 ** 3)
        return None
    except Exception:
        return None


def free_disk_gb(path):
    """Free space in GB on the filesystem holding `path`, or None if it
    can't be determined."""
    try:
        os.makedirs(path, exist_ok=True)
        return shutil.disk_usage(path).free / (1024 ** 3)
    except Exception:
        return None
