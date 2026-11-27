import os
import stat
from pathlib import Path

import click
import pytest

from dpsprep.workflow.workdir import get_file_hash, initialize_workdir


def test_cli_cache_identity_and_lock(tmp_path: Path) -> None:
    src = tmp_path / 'book.djvu'
    src.write_bytes(b'test')
    with initialize_workdir(src, None, tmp_path, settings_key=b'72dpi') as first:
        sentinel = first.working / 'cache'
        sentinel.touch()
        if os.name == 'posix':
            assert stat.S_IMODE(first.working.parent.stat().st_mode) == 0o700
        with pytest.raises(click.ClickException, match='Another conversion'), initialize_workdir(
            src, None, tmp_path, delete_existing=True, settings_key=b'72dpi',
        ):
            pass
        with initialize_workdir(src, None, tmp_path, settings_key=b'300dpi') as second:
            assert second.working != first.working
    with initialize_workdir(src, None, tmp_path, settings_key=b'72dpi') as resumed:
        assert resumed.working == first.working
        assert sentinel.exists()


def test_desktop_job_preserves_old_cache_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / 'book.djvu'
    src.write_bytes(b'test')
    monkeypatch.setenv('DPSPREP_DESKTOP_JOB', str(tmp_path))
    with initialize_workdir(src, None, tmp_path) as job:
        assert job.working == tmp_path / 'dpsprep' / get_file_hash(src)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permissions')
def test_desktop_job_rejects_linked_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / 'book.djvu'
    src.write_bytes(b'test')
    monkeypatch.setenv('DPSPREP_DESKTOP_JOB', str(tmp_path))
    (tmp_path / 'dpsprep').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(click.ClickException, match='Unsafe'), initialize_workdir(src, None, tmp_path):
        pass
