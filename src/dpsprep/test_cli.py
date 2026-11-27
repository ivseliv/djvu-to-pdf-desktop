from unittest.mock import Mock

import click
import pytest

from dpsprep import cli


class TemporaryJobError(Exception):
    pass


def test_open_djvu_document_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    document = Mock()
    constructor = Mock(side_effect=(TemporaryJobError(), document))
    monkeypatch.setattr(cli.decode, 'JobFailed', TemporaryJobError)
    monkeypatch.setattr(cli.decode, 'Document', constructor)

    assert cli.open_djvu_document('book.djvu') is document
    assert constructor.call_count == 2


def test_open_djvu_document_reports_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.decode, 'JobFailed', TemporaryJobError)
    monkeypatch.setattr(cli.decode, 'Document', Mock(side_effect=TemporaryJobError()))

    with pytest.raises(click.ClickException, match='after retrying'):
        cli.open_djvu_document('book.djvu')
