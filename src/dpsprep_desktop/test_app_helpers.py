import queue
from pathlib import Path

from .app import ConverterApp


def test_publish_result_moves_file_without_second_copy(tmp_path: Path) -> None:
    app = ConverterApp.__new__(ConverterApp)
    app.events = queue.Queue()
    source = tmp_path / 'job' / 'result.pdf'
    source.parent.mkdir()
    source.write_bytes(b'pdf result')
    destination = tmp_path / 'book.pdf'

    app._publish_result(source, destination)

    assert destination.read_bytes() == b'pdf result'
    assert not source.exists()
    assert app.events.get_nowait() == ('transfer', ('save', 10, 10))


def test_stage_source_replaces_an_interrupted_partial_copy(tmp_path: Path) -> None:
    app = ConverterApp.__new__(ConverterApp)
    app.events = queue.Queue()
    source = tmp_path / 'book.djvu'
    source.write_bytes(b'complete source')
    staged = tmp_path / 'job' / 'source.djvu'
    staged.parent.mkdir()
    staged.write_bytes(b'partial')
    staged.with_suffix('.djvu.partial').write_bytes(b'older partial')

    app._stage_source(source, staged)

    assert staged.read_bytes() == b'complete source'
    assert not staged.with_suffix('.djvu.partial').exists()
