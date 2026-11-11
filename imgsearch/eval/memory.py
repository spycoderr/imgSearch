"""Memory and latency while searching (P1.9): `imgsearch bench-memory`.

One process plays the bot: it loads the searcher the way the bot does (`LazySearcher` over
`open_searcher` with the config's defaults), runs a fixed list of queries, unloads, and reports
its own peak memory footprint (which counts Metal/MPS memory), Ollama's resident memory for the
rewriter, cold and warm latency, and the footprint and RSS before loading and after unloading
(the leak check).
"""

import ctypes
import ctypes.util
import os
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np

from imgsearch.chat.service import LazySearcher
from imgsearch.interfaces import TextEmbedder
from imgsearch.search.hybrid import Searcher

# proc_pid_rusage (libproc.h): RUSAGE_INFO_V4 fields after the 16-byte uuid, all uint64.
_RUSAGE_INFO_V4 = 4
_V4_FIELDS = (
    "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size resident_size"
    " phys_footprint proc_start_abstime proc_exit_abstime child_user_time child_system_time"
    " child_pkg_idle_wkups child_interrupt_wkups child_pageins child_elapsed_abstime"
    " diskio_bytesread diskio_byteswritten cpu_time_qos_default cpu_time_qos_maintenance"
    " cpu_time_qos_background cpu_time_qos_utility cpu_time_qos_legacy"
    " cpu_time_qos_user_initiated cpu_time_qos_user_interactive billed_system_time"
    " serviced_system_time logical_writes lifetime_max_phys_footprint instructions cycles"
    " billed_energy serviced_energy interval_max_phys_footprint runnable_time"
).split()


class _RusageInfoV4(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(name, ctypes.c_uint64) for name in _V4_FIELDS]


@dataclass(frozen=True)
class Memory:
    """Bytes. `footprint` is what Activity Monitor calls Memory: it counts GPU (Metal)
    allocations, which RSS does not."""

    rss: int
    footprint: int
    peak_footprint: int  # over the process's lifetime


def process_memory(pid: int | None = None) -> Memory:
    """RSS, current and lifetime-peak footprint of a process (default: this one), macOS only."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    info = _RusageInfoV4()
    if libc.proc_pid_rusage(pid or os.getpid(), _RUSAGE_INFO_V4, ctypes.byref(info)) != 0:
        raise OSError(ctypes.get_errno(), "proc_pid_rusage failed")
    return Memory(info.resident_size, info.phys_footprint, info.lifetime_max_phys_footprint)


def ollama_models(client: httpx.Client, url: str) -> list[dict]:
    """The models Ollama has loaded (`/api/ps`): name, size (resident bytes), size_vram,
    context_length."""
    response = client.get(f"{url.rstrip('/')}/api/ps")
    response.raise_for_status()
    return [
        {k: m.get(k) for k in ("name", "size", "size_vram", "context_length")}
        for m in response.json()["models"]
    ]


def ollama_unload(client: httpx.Client, url: str, model: str) -> None:
    """Ask Ollama to unload `model` now (keep_alive 0), so the next rewrite is a cold start."""
    response = client.post(
        f"{url.rstrip('/')}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60
    )
    response.raise_for_status()


def read_queries(path: Path) -> list[str]:
    """One query per line; blank lines and lines starting with # are skipped."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


@dataclass
class Report:
    queries: int
    searches: int
    load_s: float
    cold_s: float  # first search after the load, with the rewriter unloaded first
    warm_s: list[float]  # the other searches
    before: Memory  # before the models load
    loaded: Memory  # after the last search, models still loaded
    after: Memory  # after the unload
    ollama_before: list[dict]  # what Ollama had loaded before the run (other agents, the bot)
    ollama: list[dict]  # what Ollama had loaded after the last search
    rewrite_failures: int
    rewriter: str

    def to_json(self) -> dict:
        return {**asdict(self), "rewriter_bytes": self.rewriter_bytes}

    @property
    def rewriter_bytes(self) -> int:
        return sum(m["size"] or 0 for m in self.ollama if m["name"] == self.rewriter)

    def lines(self) -> list[str]:
        gb = 1e9
        warm = self.warm_s or [float("nan")]
        rows = [
            ("Queries / searches", f"{self.queries} / {self.searches}"),
            ("Searcher load (SigLIP)", f"{self.load_s:.2f} s"),
            ("First search, cold rewriter", f"{self.cold_s:.2f} s"),
            ("Warm search, median / max", f"{statistics.median(warm):.2f} / {max(warm):.2f} s"),
            ("Search process peak footprint", f"{self.loaded.peak_footprint / gb:.2f} GB"),
            ("Search process footprint, loaded", f"{self.loaded.footprint / gb:.2f} GB"),
            (f"Ollama {self.rewriter} resident", f"{self.rewriter_bytes / gb:.2f} GB"),
            (
                "Total while searching (peak + Ollama)",
                f"{(self.loaded.peak_footprint + self.rewriter_bytes) / gb:.2f} GB",
            ),
            ("RSS before load / after unload", _pair(self.before.rss, self.after.rss)),
            (
                "Footprint before load / after unload",
                _pair(self.before.footprint, self.after.footprint),
            ),
            ("Rewrite failures", str(self.rewrite_failures)),
            ("Ollama before the run", _models(self.ollama_before)),
            ("Ollama after the last search", _models(self.ollama)),
        ]
        return [f"{name:<40} {value}" for name, value in rows]


def _pair(a: int, b: int) -> str:
    return f"{a / 1e6:.0f} / {b / 1e6:.0f} MB ({(b - a) / 1e6:+.0f} MB)"


def _models(models: list[dict]) -> str:
    if not models:
        return "none"
    return ", ".join(
        f"{m['name']} {m['size'] / 1e9:.2f} GB ctx {m['context_length']}" for m in models
    )


def measure(
    build: Callable[[], Searcher],
    queries: list[str],
    searches: int,
    ollama: httpx.Client,
    ollama_url: str,
    rewriter: str,
) -> Report:
    """Unload the rewriter from Ollama, then load the searcher as the bot does, run
    `searches` searches cycling through `queries`, and unload it. The first search is the
    cold one. `build` opens the searcher (it owns its DB connection; `LazySearcher.unload`
    closes it)."""
    ollama_before = ollama_models(ollama, ollama_url)
    ollama_unload(ollama, ollama_url, rewriter)
    lazy = LazySearcher(build)
    before = process_memory()
    start = time.perf_counter()
    lazy.get()
    load_s = time.perf_counter() - start
    times, failures = [], 0
    for i in range(searches):
        start = time.perf_counter()
        # As the bot does: no reference to the searcher outlives the call, so unload frees it.
        response = lazy.get().search(queries[i % len(queries)])
        times.append(time.perf_counter() - start)
        failures += response.query.rewrite_error is not None
    models = ollama_models(ollama, ollama_url)
    loaded = process_memory()
    lazy.unload()
    after = process_memory()
    return Report(
        queries=len(queries),
        searches=searches,
        load_s=load_s,
        cold_s=times[0],
        warm_s=times[1:],
        before=before,
        loaded=loaded,
        after=after,
        ollama_before=ollama_before,
        ollama=models,
        rewrite_failures=failures,
        rewriter=rewriter,
    )


def compare_vectors(
    reference: TextEmbedder, candidate: TextEmbedder, texts: list[str]
) -> tuple[float, float]:
    """The lowest cosine similarity and the largest absolute difference between the two
    embedders' vectors over `texts` (criteria 2 and 3: does the query side match the model
    that embedded the images?)."""
    cosines, diffs = [], []
    for text in texts:
        a, b = reference.embed_text(text), candidate.embed_text(text)
        cosines.append(float(a @ b))
        diffs.append(float(np.abs(a - b).max()))
    return min(cosines), max(diffs)
