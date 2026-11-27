import os
import stat
from pathlib import Path

import pytest

from .core import ConversionSettings
from .jobs import (
    JobBusyError,
    _posix_jobs_root,
    create_job,
    job_size,
    lock_job,
    matches_job,
    read_manifest,
    recoverable_jobs,
    remove_job,
    write_manifest,
)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX user-owned working directories')
def test_private_jobs_root_and_legacy_recovery(tmp_path: Path) -> None:
    legacy = tmp_path / 'djvu-to-pdf-jobs'
    legacy.mkdir(mode=0o755)
    assert _posix_jobs_root(tmp_path) == legacy
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o700
    legacy.chmod(0o777)
    user_root = _posix_jobs_root(tmp_path)
    assert user_root.name == f'djvu-to-pdf-jobs-{os.getuid()}'
    assert stat.S_IMODE(user_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o777


def test_job_can_be_recovered_and_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('DPSPREP_JOBS_ROOT', str(tmp_path / 'jobs'))
    source = tmp_path / 'book.djvu'
    source.write_bytes(b'original')
    settings = ConversionSettings(source=source, output=tmp_path / 'book.pdf')
    job = create_job(settings, '1.2.3')
    (job / 'payload').write_bytes(b'1234')
    write_manifest(job, {'state': 'failed'})

    manifest = read_manifest(job)
    assert manifest is not None
    assert manifest['state'] == 'failed'
    assert recoverable_jobs() == [job]
    assert job_size(job) >= 4
    with lock_job(job):
        assert recoverable_jobs() == []
        with pytest.raises(JobBusyError), lock_job(job):
            pytest.fail('An active job cannot be claimed twice')
    assert matches_job(job, settings)
    assert not matches_job(job, ConversionSettings(source=source, output=settings.output, quality=41))
    source.write_bytes(b'changed source')
    assert not matches_job(job, settings)

    remove_job(job)
    assert not job.exists()
