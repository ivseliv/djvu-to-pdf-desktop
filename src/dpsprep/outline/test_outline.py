import shutil
import subprocess
import sys
from pathlib import Path

import pikepdf
import pytest
from pdfrw import IndirectPdfDict

from dpsprep import djvu_sexpr as sexpr

from .outline import OutlineTransformVisitor


def test_basic_outline() -> None:
    src = sexpr.ListExpression([
        sexpr.SymbolExpression(sexpr.Symbol('bookmarks')),
        sexpr.ListExpression([
            sexpr.StringExpression(b'Chapter 2'),
            sexpr.StringExpression(b'#100'),
        ]),
    ])

    visitor = OutlineTransformVisitor()
    bookmarks = visitor.visit(src)
    assert bookmarks is not None
    assert bookmarks.Count == 1
    assert bookmarks.First.Title == 'Chapter 2'
    assert bookmarks.First.A.D[0] == 99  # The page number


def test_nested_outline() -> None:
    src = sexpr.ListExpression([
        sexpr.SymbolExpression(sexpr.Symbol('bookmarks')),
        sexpr.ListExpression([
            sexpr.StringExpression(b'Chapter 2'),
            sexpr.StringExpression(b'#100'),
            sexpr.ListExpression([
                sexpr.StringExpression(b'Chapter 2.1'),
                sexpr.StringExpression(b'#200'),
            ]),
        ]),
    ])

    visitor = OutlineTransformVisitor()
    bookmarks = visitor.visit(src)
    assert bookmarks is not None
    assert bookmarks.Count == 2
    assert bookmarks.First.Count == 1
    assert bookmarks.First.A.D[0] == 99  # The page number of chapter 2
    assert bookmarks.First.First.A.D[0] == 199  # The page number of chapter 2.1


def test_outline_with_unresolved_ids() -> None:
    src = sexpr.ListExpression([
        sexpr.SymbolExpression(sexpr.Symbol('bookmarks')),
        sexpr.ListExpression([
            sexpr.StringExpression(b'Preface'),
            sexpr.StringExpression(b'#f007.djvu'),
        ]),
        sexpr.ListExpression([
            sexpr.StringExpression(b'Contents'),
            sexpr.StringExpression(b'#f011.djvu'),
        ]),
        sexpr.ListExpression([
            sexpr.StringExpression(b'0 Prologue'),
            sexpr.StringExpression(b'#p001.djvu'),
        ]),
    ])

    visitor = OutlineTransformVisitor()
    bookmarks = visitor.visit(src)
    empty_pdf_dict = IndirectPdfDict()
    assert bookmarks == empty_pdf_dict


def test_outline_with_invalid_unicode() -> None:
    src = sexpr.ListExpression([
        sexpr.SymbolExpression(sexpr.Symbol('bookmarks')),
        sexpr.ListExpression([
            sexpr.StringExpression(b'\2470'),
            sexpr.StringExpression(b'#1'),
        ]),
    ])

    visitor = OutlineTransformVisitor()
    bookmarks = visitor.visit(src)
    assert bookmarks is not None
    assert bookmarks.Count == 1
    assert bookmarks.First.Title == '"\\2470"'


@pytest.mark.parametrize('stream', [True, False])
def test_named_destinations_resolve_to_actual_pdf_pages(tmp_path: Path, stream: bool) -> None:
    source = tmp_path / 'book.djvu'
    out = tmp_path / 'book.pdf'
    shutil.copyfile('fixtures/lipsum_words.djvu', source)
    subprocess.run(
        ['djvused', '-s', str(source)],
        input=('select 1; set-page-title "Wstęp I"\nset-outline\n'
               '(bookmarks ("Książka" "#missing" ("Druga" "#p0002.djvu") ("Wstęp" "#Wst%C4%99p%20I")))\n.\n').encode(),
        check=True,
    )
    command = [sys.executable, '-m', 'dpsprep', '--pool-size', '1', '--max-dpi', '72', '--tmp', str(tmp_path)]
    if stream:
        command.append('--stream-to-dest')
    subprocess.run([*command, str(source), str(out)], check=True, capture_output=True)
    with pikepdf.open(out) as pdf:
        group = pdf.Root.Outlines.First
        assert str(group.Title) == 'Książka'
        assert '/A' not in group
        assert '/Dest' not in group
        for child, page in [(group.First, pdf.pages[1]), (group.First.Next, pdf.pages[0])]:
            dest = child.Dest if '/Dest' in child else child.A.D
            assert dest[0].objgen == page.obj.objgen
        assert str(group.First.Next.Title) == 'Wstęp'
