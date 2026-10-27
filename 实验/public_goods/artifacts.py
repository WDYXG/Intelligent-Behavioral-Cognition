"""Compact saved experiments without discarding their replay evidence.

Completed CLI runs expose summaries and figures; detailed records live in one
records.zip. Readers also accept older unpacked runs and live/history.zip.
"""

import hashlib
from functools import wraps
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile, ZIP_DEFLATED


def read_bytes(path):
    path = Path(path)
    if path.is_file():
        return path.read_bytes()
    for parent in path.parents:
        for name in ("records.zip", "history.zip", "checks.zip"):
            archive = parent / name
            if archive.is_file():
                with ZipFile(archive) as handle:
                    try:
                        return handle.read(path.relative_to(parent).as_posix())
                    except KeyError:
                        pass
    raise FileNotFoundError(path)


def read_text(path):
    return read_bytes(path).decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


def read_rows(path):
    return [json.loads(line) for line in read_text(path).splitlines() if line.strip()]


def exists(path):
    try:
        read_bytes(path)
    except FileNotFoundError:
        return False
    return True


def compact_cli(function):
    """Compact only a newly created --output directory, after all writes stop."""
    @wraps(function)
    def wrapped(argv=None):
        arguments = list(sys.argv[1:] if argv is None else argv)
        output = None
        for index, value in enumerate(arguments):
            if value.startswith("--output="):
                output = Path(value.split("=", 1)[1])
            elif value == "--output" and index + 1 < len(arguments):
                output = Path(arguments[index + 1])
        existed = output is not None and output.exists()
        try:
            return function(argv)
        finally:
            if output is not None and not existed:
                compact_run(output)
    return wrapped


def _verify_and_remove(root, files, archive):
    """Delete loose files only after checking every archived byte against disk."""
    with ZipFile(archive) as handle:
        if handle.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        for path in files:
            if hashlib.sha256(handle.read(path.relative_to(root).as_posix())).digest() != hashlib.sha256(path.read_bytes()).digest():
                raise ValueError(f"Archive differs from source: {path}")
    for path in files:
        path.unlink()
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()


def compact_run(root):
    """Keep a root summary/comparison/figure and a single verified records.zip.

    Call only after the writer has stopped (including a recorded failed run).
    Existing archives are merged so repeated compaction preserves new reports.
    """
    root = Path(root)
    if not root.is_dir():
        return
    visible = {"report.json", "summary.json", "development_comparison.json", "contribution_curves.png"}
    files = [p for p in sorted(root.rglob("*")) if p.is_file()
             and p.name not in {".DS_Store", "records.zip", ".records.zip.tmp"}
             and not (p.parent == root and p.name in visible)]
    archive = root / "records.zip"
    if not files:
        return
    temp = root / ".records.zip.tmp"
    replaced = {p.relative_to(root).as_posix() for p in files}
    try:
        with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=6) as handle:
            if archive.exists():
                with ZipFile(archive) as previous:
                    for info in previous.infolist():
                        if info.filename not in replaced:
                            handle.writestr(info, previous.read(info.filename))
            for path in files:
                handle.write(path, path.relative_to(root).as_posix())
        # Check before replacing an existing good archive or removing anything.
        with ZipFile(temp) as handle:
            if handle.testzip() is not None or any(handle.read(p.relative_to(root).as_posix()) != p.read_bytes() for p in files):
                raise ValueError("Compact archive verification failed")
        temp.replace(archive)
        _verify_and_remove(root, files, archive)
    finally:
        temp.unlink(missing_ok=True)


def copy_tree(source, destination):
    """Copy a saved arm from a compact run for the existing recovery writer."""
    source, destination = Path(source), Path(destination)
    if source.is_dir():
        shutil.copytree(source, destination)
        return
    for parent in source.parents:
        for name in ("records.zip", "history.zip"):
            archive = parent / name
            if not archive.is_file():
                continue
            prefix = source.relative_to(parent).as_posix() + "/"
            with ZipFile(archive) as handle:
                members = [n for n in handle.namelist() if n.startswith(prefix) and not n.endswith("/")]
                if not members:
                    continue
                destination.mkdir(parents=True, exist_ok=False)
                for member in members:
                    relative = Path(member[len(prefix):])
                    if relative.is_absolute() or ".." in relative.parts:
                        raise ValueError("Unsafe archive path")
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(handle.read(member))
                return
    raise FileNotFoundError(source)
