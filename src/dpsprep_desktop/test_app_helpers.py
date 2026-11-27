import queue
from pathlib import Path

import pytest

from .core import ConversionSettings
from .jobs import create_job, write_manifest
from .runner import ConversionRunner


def test_publish_result_moves_file_without_second_copy(tmp_path: Path) -> None:
    app = ConversionRunner(queue.Queue())
    source = tmp_path / 'job' / 'result.pdf'
    source.parent.mkdir()
    source.write_bytes(b'pdf result')
    destination = tmp_path / 'book.pdf'

    app._publish_result(source, destination)

    assert destination.read_bytes() == b'pdf result'
    assert not source.exists()
    assert app.events.get_nowait() == ('transfer', ('save', 10, 10))


def test_stage_source_replaces_an_interrupted_partial_copy(tmp_path: Path) -> None:
    app = ConversionRunner(queue.Queue())
    source = tmp_path / 'book.djvu'
    source.write_bytes(b'complete source')
    staged = tmp_path / 'job' / 'source.djvu'
    staged.parent.mkdir()
    staged.write_bytes(b'partial')
    staged.with_suffix('.djvu.partial').write_bytes(b'older partial')

    app._stage_source(source, staged)

    assert staged.read_bytes() == b'complete source'
    assert not staged.with_suffix('.djvu.partial').exists()


def test_no_overwrite_keeps_a_file_created_during_conversion(tmp_path: Path) -> None:
    app = ConversionRunner(queue.Queue())
    output = tmp_path / 'book.pdf'
    staged = tmp_path / 'result.pdf'
    staged.write_bytes(b'conversion')
    output.write_bytes(b'another result')
    with pytest.raises(FileExistsError):
        app._publish_result(staged, output, overwrite=False)
    assert staged.read_bytes() == b'conversion'
    assert output.read_bytes() == b'another result'


def test_cancel_before_worker_exits_staging_without_leaving_partial(tmp_path: Path) -> None:
    app = ConversionRunner(queue.Queue())
    source = tmp_path / 'book.djvu'
    source.write_bytes(b'original')
    destination = tmp_path / 'source.djvu'
    app.cancel()
    with pytest.raises(InterruptedError):
        app._stage_source(source, destination)
    assert not destination.exists()
    assert not destination.with_suffix('.djvu.partial').exists()


def test_resuming_a_saved_pdf_preserves_destination_and_allows_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv('DPSPREP_JOBS_ROOT', str(tmp_path / 'jobs'))
    source = tmp_path / 'book.djvu'
    source.write_bytes(b'original')
    output = tmp_path / 'book.pdf'
    settings = ConversionSettings(source=source, output=output, overwrite=False)
    job = create_job(settings, 'test')
    staged = job / 'result.pdf'
    staged.write_bytes(b'ready to publish')
    write_manifest(job, {'state': 'save_failed'})
    output.write_bytes(b'keep this')
    runner = ConversionRunner(queue.Queue())
    runner.resume_job = job
    runner.run(settings)
    assert ('complete', 1) in runner.events.queue
    assert output.read_bytes() == b'keep this'
    assert staged.is_file()

    alternate = tmp_path / 'another.pdf'
    runner.run(ConversionSettings(source=source, output=alternate, overwrite=False))
    assert ('complete', 0) in runner.events.queue
    assert alternate.read_bytes() == b'ready to publish'
    assert output.read_bytes() == b'keep this'
    assert not job.exists()


def test_failure_before_worker_launch_reports_completion(tmp_path: Path) -> None:
    runner = ConversionRunner(queue.Queue())
    runner.run(ConversionSettings(source=tmp_path / 'missing.djvu', output=tmp_path / 'out.pdf'))
    assert list(runner.events.queue)[-1] == ('complete', 1)
