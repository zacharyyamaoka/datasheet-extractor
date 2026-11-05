"""The queue (CONTRACT §3): `added → captured → converted → extracted`, each step a background job in a pool of
two, each stage change written to source.json before the next step starts.

- **capture**: a URL → `uv run --directory /home/bam/markdownify mdify capture <url> --keep <levels> --out
  <source>/bundle`; a file → its uploaded bytes. Either way the sha256 is now known and the source takes its id.
- **convert**: a file → `mdify <file> --out <source>/bundle` (pipeline A). A URL's capture already converted the
  HTML it kept (mdify capture builds the bundle in the same visit), so this step checks that bundle is complete.
- **extract**: extraction.py (Option 1, every product of the batch).

A restart resumes every source left in `added`, `captured` or `converted` from that stage (a half-written bundle is
discarded first); a re-extraction cut off by the restart, whose values.json was set aside and never replaced, is
marked `failed` with that reason. The watcher polls every 2 s and moves a `converted` (or `failed`, with a complete
bundle) source to `extracted` when a values.json appears from outside — the prompt's command, or any agent.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import pathlib
import subprocess
import threading
import traceback

from .. import option1
from . import extraction
from .store import FAILED, Store, read_json

MARKDOWNIFY = option1.MARKDOWNIFY
WORKERS = 2
POLL_S = 2.0
BUNDLE_FILES = ("source.json", "document.md", "index.json")


def bundle_complete(bundle_dir: pathlib.Path) -> bool:
    return all((bundle_dir / name).is_file() for name in BUNDLE_FILES)


def mdify_capture(url: str, keep: str, out: pathlib.Path) -> None:
    run = subprocess.run(["uv", "run", "--directory", str(MARKDOWNIFY), "mdify", "capture", url, "--keep", keep,
                          "--out", str(out.resolve())], capture_output=True, text=True, check=False)
    (out.parent / "capture-stderr.txt").write_text(run.stderr, encoding="utf-8")
    if run.returncode != 0:
        raise RuntimeError(f"mdify capture failed ({run.returncode}): {run.stderr[-600:]}")


def mdify_convert(path: pathlib.Path, out: pathlib.Path) -> None:
    option1.run_mdify(path, out)


class Queue:
    def __init__(self, store: Store, catalog, backend: str = "claude", llm_model: str | None = None,
                 capture=mdify_capture, convert=mdify_convert, workers: int = WORKERS, poll_s: float = POLL_S) -> None:
        self.store, self.catalog = store, catalog
        self.backend, self.llm_model = backend, llm_model
        self.capture, self.convert = capture, convert
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="source-job")
        self.poll_s = poll_s
        self.active: set[str] = set()                # source ids with a job queued or running
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self._seen: dict[str, float] = {}            # values.json mtimes the watcher has already judged
        self.watcher: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------------------------------------------------

    def start(self) -> None:
        self.resume()
        self.watcher = threading.Thread(target=self._watch, name="values-watcher", daemon=True)
        self.watcher.start()

    def stop(self, wait: bool = True) -> None:
        self.stopping.set()
        self.pool.shutdown(wait=wait, cancel_futures=True)
        if self.watcher is not None and wait:
            self.watcher.join(timeout=self.poll_s + 1)

    def idle(self) -> bool:
        with self.lock:
            return not self.active

    def resume(self) -> None:
        for source in self.store.sources():
            directory = self.store.source_dir(source["id"])
            if source["stage"] in ("added", "captured", "converted"):
                self.submit(source["id"])
            elif source["stage"] in ("extracted", "published") and not (directory / "values.json").is_file():
                self.store.advance(source["id"], FAILED, "re-extraction was cut off by a restart before it wrote "
                                   "values.json (the previous values are kept beside it as values-<sha>.json)")

    # -- jobs -------------------------------------------------------------------------------------------------------

    def submit(self, source_id: str, re_extract: bool = False, backend: str | None = None) -> bool:
        """Queue the source's remaining steps (or a re-extraction, with `backend`'s CLI). False when it already has
        a job."""
        with self.lock:
            if source_id in self.active:
                return False
            self.active.add(source_id)
        try:
            self.pool.submit(self._run, source_id, re_extract, backend or self.backend)
        except RuntimeError:                                    # the pool is shutting down
            with self.lock:
                self.active.discard(source_id)
            return False
        return True

    def _rename_active(self, old: str, new: str) -> None:
        with self.lock:
            self.active.discard(old)
            self.active.add(new)

    def _run(self, source_id: str, re_extract: bool, backend: str) -> None:
        current = source_id
        try:
            if self.stopping.is_set():
                return
            if re_extract:
                path = self.store.source_dir(current) / "values.json"
                if path.is_file():
                    self.store.archive_values(current, hashlib.sha256(path.read_bytes()).hexdigest())
                self._extract(current, backend)
                return
            source = self.store.source(current)
            if source["stage"] == "added":
                current = self._capture(source)
                if current is None:
                    return
                source = self.store.source(current)
            if self.stopping.is_set():
                return
            if source["stage"] == "captured":
                self._convert(source)
                source = self.store.source(current)
            if self.stopping.is_set():
                return
            if source["stage"] == "converted":
                self._extract(current, backend)
        except Exception as error:  # noqa: BLE001 - every failure is written to the source, never lost
            detail = "".join(traceback.format_exception_only(type(error), error)).strip()
            try:
                self.store.advance(current, FAILED, detail)
            except KeyError:
                pass
        finally:
            with self.lock:
                self.active.discard(current)
                self.active.discard(source_id)

    def _capture(self, source: dict) -> str | None:
        directory = self.store.source_dir(source["id"])
        bundle = directory / "bundle"
        self.store.remove_tree(bundle)                       # a capture cut off by a restart starts over
        if source["kind"] == "url":
            self.capture(source["url"], source["capture_keep"], bundle)
            record = read_json(bundle / "source.json")
            if not record or not record.get("sha256"):
                raise RuntimeError("mdify capture wrote no source.json with a sha256")
            sha256 = record["sha256"]
        else:
            upload = directory / "upload" / source["name"]
            if not upload.is_file():
                raise FileNotFoundError(f"the uploaded file {upload} is gone")
            sha256 = hashlib.sha256(upload.read_bytes()).hexdigest()
        try:
            new_id = self.store.identify(source["id"], sha256)
        except FileExistsError as error:
            self.store.advance(source["id"], FAILED, str(error), sha256=sha256)
            return None
        self._rename_active(source["id"], new_id)
        self.store.advance(new_id, "captured")
        return new_id

    def _convert(self, source: dict) -> None:
        directory = self.store.source_dir(source["id"])
        bundle = directory / "bundle"
        if source["kind"] == "file":
            self.store.remove_tree(bundle)
            self.convert(directory / "upload" / source["name"], bundle)
        if not bundle_complete(bundle):
            missing = [name for name in BUNDLE_FILES if not (bundle / name).is_file()]
            raise RuntimeError(f"the bundle has no {', '.join(missing)} after "
                               f"{'conversion' if source['kind'] == 'file' else 'capture'}")
        self.store.advance(source["id"], "converted", bundle=str(bundle))

    def _extract(self, source_id: str, backend: str) -> None:
        extraction.run_for(self.store, self.catalog, source_id, backend,
                           self.llm_model if backend == self.backend else None)
        self._seen[source_id] = (self.store.source_dir(source_id) / "values.json").stat().st_mtime
        self.store.advance(source_id, "extracted")

    # -- the watcher ------------------------------------------------------------------------------------------------

    def _watch(self) -> None:
        while not self.stopping.wait(self.poll_s):
            try:
                self.scan()
            except Exception:  # noqa: BLE001 - the watcher outlives a bad file
                traceback.print_exc()

    def scan(self) -> None:
        """One pass: a values.json written from outside moves its source to `extracted`."""
        for source in self.store.sources():
            source_id = source["id"]
            with self.lock:
                if source_id in self.active:
                    continue
            if source["stage"] not in ("converted", FAILED):
                continue
            path = self.store.source_dir(source_id) / "values.json"
            if not path.is_file() or self._seen.get(source_id) == path.stat().st_mtime:
                continue
            if source["stage"] == FAILED and not bundle_complete(pathlib.Path(source["bundle"])):
                continue
            self._seen[source_id] = path.stat().st_mtime
            values = read_json(path)
            if not isinstance(values, dict) or values.get("schema") != "extractor/values@0":
                self.store.advance(source_id, FAILED, "a values.json appeared that is not extractor/values@0 "
                                   "(Markdownify CONTRACT §7)")
                continue
            self.store.advance(source_id, "extracted")
