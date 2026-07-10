"""Byte-level download progress for huggingface_hub downloads.

huggingface_hub accepts a `tqdm_class` override for its internal progress
bars. We give it a tqdm subclass that, instead of printing, aggregates bytes
across every byte-unit bar it creates (a download can involve several small
files plus one large one) and reports the running total/downloaded pair.
"""

import threading

from tqdm import tqdm as _tqdm_base


class _NullFile:
    """Swallows tqdm's console writes. Needed because a --windowed build has
    sys.stdout/sys.stderr set to None, which crashes tqdm's default target."""

    def write(self, *_args, **_kwargs):
        pass

    def flush(self, *_args, **_kwargs):
        pass

    def isatty(self):
        return False


def make_progress_tqdm_class(on_progress):
    """Returns a tqdm subclass suitable for huggingface_hub's `tqdm_class`.

    on_progress(downloaded_bytes, total_bytes) is called on every update,
    aggregated across all concurrently-open byte-unit progress bars. Caller
    is responsible for throttling (e.g. only acting on percent changes).
    """
    state = {}
    lock = threading.Lock()

    def _emit():
        with lock:
            downloaded = sum(n for n, _ in state.values())
            total = sum(t for _, t in state.values())
        if total:
            on_progress(downloaded, total)

    class _ProgressTqdm(_tqdm_base):
        def __init__(self, *args, **kwargs):
            self._tracked = kwargs.get("unit") in ("B", "iB")
            kwargs["file"] = _NullFile()
            super().__init__(*args, **kwargs)
            if self._tracked:
                with lock:
                    state[id(self)] = (self.n or 0, self.total or 0)
                _emit()

        def update(self, n=1):
            result = super().update(n)
            if self._tracked:
                with lock:
                    state[id(self)] = (self.n or 0, self.total or 0)
                _emit()
            return result

        def close(self):
            if self._tracked:
                with lock:
                    if self.total:
                        state[id(self)] = (self.total, self.total)
                _emit()
            super().close()

    return _ProgressTqdm
