"""Persistent desktop conversion jobs and explicit cleanup."""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING


if os.name == 'nt':
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .core import ConversionSettings


def jobs_root() -> Path:
    override = os.environ.get('DPSPREP_JOBS_ROOT')
    root = Path(override) if override else (
        Path('/var/tmp/djvu-to-pdf-jobs') if os.name == 'posix' else Path(tempfile.gettempdir()) / 'djvu-to-pdf-jobs'
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _render_settings(settings: ConversionSettings) -> dict[str, object]:
    return {
        'source': str(settings.source.absolute()),
        'preserve_text': bool(settings.preserve_text),
        'image_mode': str(settings.image_mode),
        'quality': int(settings.quality),
        'max_dpi': settings.max_dpi,
    }


def _source_stamp(source: Path) -> list[int]:
    info = source.stat()
    return [info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino]


def matches_job(job: Path, settings: ConversionSettings) -> bool:
    manifest = read_manifest(job) or {}
    if any(manifest.get(key) != value for key, value in _render_settings(settings).items()):
        return False
    if 'source_stamp' in manifest:
        return manifest['source_stamp'] == _source_stamp(settings.source)
    # Older macOS jobs stored the complete input but no source identity.
    # Compare its bytes before reusing any cached pages or a finished PDF.
    staged_source = job / f'source{settings.source.suffix.lower()}'
    return staged_source.is_file() and filecmp.cmp(settings.source, staged_source, shallow=False)


def create_job(settings: ConversionSettings, version: str) -> Path:
    stamp = _source_stamp(settings.source)
    job = jobs_root() / f'job-{uuid.uuid4().hex}'
    job.mkdir(mode=0o700)
    write_manifest(job, {
        **_render_settings(settings),
        'source_stamp': stamp,
        'version': version,
        'state': 'preparing',
        'output': str(settings.output.absolute()),
        'overwrite': settings.overwrite,
        'pool_size': settings.pool_size,
    })
    return job


def write_manifest(job: Path, updates: dict[str, object]) -> None:
    manifest_path = job / 'manifest.json'
    manifest = read_manifest(job) or {}
    manifest.update(updates)
    temporary = job / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(manifest_path)


def read_manifest(job: Path) -> dict[str, object] | None:
    try:
        data = json.loads((job / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class JobBusyError(OSError):
    """Another GUI owns the job."""


@contextmanager
def lock_job(job: Path) -> Iterator[int]:
    # Keep the lock outside the job so that Windows can delete the job while
    # it is locked. The OS releases the lock automatically if the GUI exits.
    lock = job.with_name(f'.{job.name}.lock')
    try:
        with lock.open('a+b') as handle:
            try:
                if os.name == 'nt':
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise JobBusyError(f'Задание уже используется: {job.name}') from error
            if not job.is_dir():
                raise FileNotFoundError(job)
            yield handle.fileno()
    finally:
        if not job.exists():
            lock.unlink(missing_ok=True)


def recoverable_jobs() -> list[Path]:
    candidates = []
    for job in jobs_root().glob('job-*'):
        if not job.is_dir() or job.is_symlink():
            continue
        try:
            with lock_job(job):
                if read_manifest(job) is not None:
                    candidates.append((job.stat().st_mtime, job))
        except (JobBusyError, FileNotFoundError):
            continue
    return [job for _, job in sorted(candidates, reverse=True)]


def job_size(job: Path) -> int:
    total = 0
    for path in job.rglob('*'):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def remove_job(job: Path) -> None:
    root = jobs_root().resolve()
    target = job.resolve()
    if job.is_symlink() or target.parent != root or not target.name.startswith('job-'):
        raise ValueError(f'Refusing to remove unexpected job directory: {target}')
    shutil.rmtree(target, ignore_errors=False)
