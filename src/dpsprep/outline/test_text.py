import pathlib
import shutil
import string
import subprocess
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

import pytest

from dpsprep import djvu_cli as decode
from dpsprep import djvu_sexpr as sexpr

from .text import TextDrawVisitor, TextExtractVisitor, extract_text_as_fpdf


def remove_whitespace(src: str) -> str:
    return src.translate({ord(c): None for c in string.whitespace})


def test_coarse_text_and_character_zones() -> None:
    visitor = TextDrawVisitor(Mock(), dpi=300, raw_height=10)
    with patch.object(visitor, 'draw_text') as draw_text:
        visitor.visit(sexpr.Expression.from_string('(page 0 0 10 10 "Latin")'))
        draw_text.assert_called_once_with(0, 10, 0, 10, 'Latin')
    word = sexpr.Expression.from_string('(word 0 0 10 10 (char 0 0 5 10 "a") (char 5 0 10 10 "b"))')
    assert TextExtractVisitor().visit(word) == 'ab'


def test_extract_djvu_page_text_words() -> None:
    document = decode.Document('fixtures/lipsum_words.djvu')

    djvu_page = document.pages[0]
    djvu_text = TextExtractVisitor().visit(djvu_page.text)

    assert djvu_text is not None

    source_pdf_text = pathlib.Path('fixtures/lipsum_01.txt').read_text(encoding='utf-8')

    assert remove_whitespace(djvu_text) == remove_whitespace(source_pdf_text)


def test_extract_djvu_page_text_lines() -> None:
    document = decode.Document('fixtures/lipsum_lines.djvu')

    djvu_page = document.pages[0]
    djvu_text = TextExtractVisitor().visit(djvu_page.text)

    assert djvu_text is not None

    source_pdf_text = pathlib.Path('fixtures/lipsum_01.txt').read_text(encoding='utf-8')

    assert remove_whitespace(djvu_text) == remove_whitespace(source_pdf_text)


def test_invalid_utf8() -> None:
    document = decode.Document('fixtures/lipsum_words_invalid.djvu')

    djvu_page = document.pages[0]
    line = djvu_page.text[5]
    assert isinstance(line, sexpr.ListExpression)
    first_word_sexpr = line[5]

    # djvulibre cannot decode the first word
    with pytest.raises(UnicodeDecodeError):
        # ruff: ignore[useless-expression]
        first_word_sexpr.value

    first_word = TextExtractVisitor().visit(first_word_sexpr)
    assert first_word == ''


@pytest.mark.skipif(shutil.which('pdftotext') is None, reason='requires Poppler text extraction')
def test_rotated_unicode_word_stays_on_its_scan(tmp_path: pathlib.Path) -> None:
    source = tmp_path / 'rotated book.djvu'
    shutil.copyfile('fixtures/lipsum_words.djvu', source)
    zones = tmp_path / 'zones'
    words = ('Łódź', 'ἄνθρωπος', 'кириллица', 'a\u0301', '\ue000')
    entries = ' '.join(f'(word {100 + i * 600} 900 {400 + i * 600} 1000 "{s}")' for i, s in enumerate(words))
    zones.write_text(f'(page 0 0 4961 7016 (line 100 900 2800 1000 {entries}))', encoding='utf-8')
    subprocess.run(['djvused', '-s', '-e', 'select 1; set-txt zones', str(source)], cwd=tmp_path, check=True)
    options = Mock()
    options.dpi_overrides.get_value_for_zero_based_page.return_value = None
    centers = []
    for rotation in range(4):
        subprocess.run(['djvused', '-s', '-e', f'select 1; set-rotation {rotation}', str(source)], check=True)
        doc = decode.Document(source)
        pdf_path = tmp_path / f'rotation-{rotation}.pdf'
        extract_text_as_fpdf(doc, options).output(str(pdf_path))
        result = subprocess.check_output(['pdftotext', '-f', '1', '-l', '1', '-bbox', str(pdf_path), '-'])
        page = ET.fromstring(result).find('.//{*}page')
        assert page is not None
        boxes = {box.text: box for box in page.findall('{*}word')}
        assert set(words) == boxes.keys()
        box = boxes['Łódź']
        centers.append(tuple((float(box.attrib[f'{axis}Min']) + float(box.attrib[f'{axis}Max'])) / 2 for axis in 'xy'))
        if rotation == 0:
            width, height = float(page.attrib['width']), float(page.attrib['height'])
            unit = 72 / doc.pages[0].dpi
            assert tuple(float(box.attrib[k]) for k in ('xMin', 'yMin', 'xMax', 'yMax')) == pytest.approx(
                (100 * unit, height - 1000 * unit, 400 * unit, height - 900 * unit), abs=0.1,
            )
    x, y = centers[0]
    for actual, expected in zip(centers[1:], [(y, width - x), (width - x, height - y), (height - y, x)], strict=True):
        assert actual == pytest.approx(expected, abs=0.1)
