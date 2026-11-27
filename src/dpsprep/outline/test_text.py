import pathlib
import string
from unittest.mock import Mock, patch

import djvu.decode
import djvu.sexpr
import pytest

from .text import TextDrawVisitor, TextExtractVisitor


def remove_whitespace(src: str) -> str:
    return src.translate({ord(c): None for c in string.whitespace})


def test_coarse_text_and_character_zones() -> None:
    visitor = TextDrawVisitor(Mock(), dpi=300)
    with patch.object(visitor, 'draw_text') as draw_text:
        visitor.visit(djvu.sexpr.Expression.from_string('(page 0 0 10 10 "Latin")'))
        draw_text.assert_called_once_with(0, 10, 0, 10, 'Latin')
    word = djvu.sexpr.Expression.from_string('(word 0 0 10 10 (char 0 0 5 10 "a") (char 5 0 10 10 "b"))')
    assert TextExtractVisitor().visit(word) == 'ab'


def test_extract_djvu_page_text_words() -> None:
    document = djvu.decode.Context().new_document(
        djvu.decode.FileURI('fixtures/lipsum_words.djvu'),
    )
    document.decoding_job.wait()

    djvu_page = document.pages[0]
    djvu_page.get_info()
    djvu_text = TextExtractVisitor().visit(djvu_page.text.sexpr)

    assert djvu_text is not None

    source_pdf_text = pathlib.Path('fixtures/lipsum_01.txt').read_text(encoding='utf-8')

    assert remove_whitespace(djvu_text) == remove_whitespace(source_pdf_text)


def test_extract_djvu_page_text_lines() -> None:
    document = djvu.decode.Context().new_document(
        djvu.decode.FileURI('fixtures/lipsum_lines.djvu'),
    )
    document.decoding_job.wait()

    djvu_page = document.pages[0]
    djvu_page.get_info()
    djvu_text = TextExtractVisitor().visit(djvu_page.text.sexpr)

    assert djvu_text is not None

    source_pdf_text = pathlib.Path('fixtures/lipsum_01.txt').read_text(encoding='utf-8')

    assert remove_whitespace(djvu_text) == remove_whitespace(source_pdf_text)


def test_invalid_utf8() -> None:
    document = djvu.decode.Context().new_document(
        djvu.decode.FileURI('fixtures/lipsum_words_invalid.djvu'),
    )
    document.decoding_job.wait()

    djvu_page = document.pages[0]
    djvu_page.get_info()
    first_word_sexpr = djvu_page.text.sexpr[5][5]

    # djvulibre cannot decode the first word
    with pytest.raises(UnicodeDecodeError):
        # ruff: ignore[useless-expression]
        first_word_sexpr.value

    first_word = TextExtractVisitor().visit(first_word_sexpr)
    assert first_word == ''
