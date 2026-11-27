import shutil
import subprocess
from pathlib import Path

import pytest

from dpsprep.djvu_cli import Document, JobFailed
from dpsprep.djvu_sexpr import ListExpression, parse


def test_cli_rotated_page_and_mixed_text(tmp_path: Path) -> None:
    source = tmp_path / 'Książka test.djvu'
    shutil.copyfile('fixtures/lipsum_words.djvu', source)
    subprocess.run(['djvused', '-s', '-e', 'select 1; set-rotation 1; select 2; remove-txt', str(source)], check=True)
    first, second = Document(source).pages
    assert first.rotation == 1
    assert first.raw_size == (4961, 7016)
    assert first.size == (7016, 4961)
    assert first.dpi == 600
    assert first.render('infer', (80, 56)).size == (80, 56)
    line = first.text[5]
    assert isinstance(line, ListExpression)
    assert line[5].value[-1] == 'Lorem'
    # djvused represents the absent layer as an empty page zone.
    assert second.text.value == ['page', 0, 0, 0, 0, '']
    assert second.size == first.raw_size


def test_quoted_metadata_preserves_bytes() -> None:
    tree = parse(rb'("quote: \" and slash: \\" ("\305\202" "\377") -12)')
    assert tree[0].value == 'quote: " and slash: \\'
    text_nodes = tree[1]
    assert isinstance(text_nodes, ListExpression)
    assert text_nodes[0].value == 'ł'
    with pytest.raises(UnicodeDecodeError):
        _ = text_nodes[1].value
    assert tree[2].value == -12
    with pytest.raises(ValueError, match='Unterminated'):
        parse(b'(word "broken)')


def test_invalid_document_fails_with_djvulibre_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / 'damaged.djvu'
    source.write_bytes(b'Not a DjVu.')
    with pytest.raises(JobFailed, match='djvused exited'):
        Document(source)
