"""Persistent desktop conversion jobs and explicit cleanup."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .core import ConversionSettings


def jobs_root() -> Path:
    override = os.environ.get('DPSPREP_JOBS_ROOT')
    root = Path(override) if override else (
        Path('/var/tmp/djvu-to-pdf-jobs') if os.name == 'posix' else Path(tempfile.gettempdir()) / 'djvu-to-pdf-jobs'
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_job(settings: ConversionSettings, version: str) -> Path:
    job = jobs_root() / f'job-{uuid.uuid4().hex}'
    job.mkdir()
    write_manifest(job, {
        'version': version,
        'state': 'preparing',
        'source': str(settings.source),
        'output': str(settings.output),
        'preserve_text': bool(settings.preserve_text),
        'image_mode': str(settings.image_mode),
        'quality': int(settings.quality),
        'pool_size': settings.pool_size,
    })
    return job


def write_manifest(job: Path, updates: dict[str, object]) -> None:
    manifest_path = job / 'manifest.json'
    manifest: dict[str, object] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            manifest = {}
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


def recoverable_jobs() -> list[Path]:
    return sorted(
        (job for job in jobs_root().glob('job-*') if read_manifest(job) is not None),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def job_size(job: Path) -> int:
    return sum(path.stat().st_size for path in job.rglob('*') if path.is_file())


def remove_job(job: Path) -> None:
    root = jobs_root().resolve()
    target = job.resolve()
    if target.parent != root or not target.name.startswith('job-'):
        raise ValueError(f'Refusing to remove unexpected job directory: {target}')
    shutil.rmtree(target, ignore_errors=False)
