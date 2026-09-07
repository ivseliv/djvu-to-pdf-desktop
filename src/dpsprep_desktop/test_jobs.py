from pathlib import Path

import pytest

from .core import ConversionSettings
from .jobs import create_job, job_size, read_manifest, recoverable_jobs, remove_job, write_manifest


def test_job_can_be_recovered_and_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('DPSPREP_JOBS_ROOT', str(tmp_path / 'jobs'))
    settings = ConversionSettings(source=Path('book.djvu'), output=Path('book.pdf'))
    job = create_job(settings, '1.2.3')
    (job / 'payload').write_bytes(b'1234')
    write_manifest(job, {'state': 'failed'})

    manifest = read_manifest(job)
    assert manifest is not None
    assert manifest['state'] == 'failed'
    assert recoverable_jobs() == [job]
    assert job_size(job) >= 4

    remove_job(job)
    assert not job.exists()
