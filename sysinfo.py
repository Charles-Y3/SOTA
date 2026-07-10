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


def free_disk_gb(path):
    """Free space in GB on the filesystem holding `path`, or None if it
    can't be determined."""
    try:
        os.makedirs(path, exist_ok=True)
        return shutil.disk_usage(path).free / (1024 ** 3)
    except Exception:
        return None
