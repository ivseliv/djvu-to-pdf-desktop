from unittest.mock import Mock

import click
import pytest

from dpsprep import cli


class TemporaryJobError(Exception):
    pass


def test_open_djvu_document_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    first_context = Mock()
    first_context.new_document.side_effect = TemporaryJobError()
    document = Mock()
    second_context = Mock()
    second_context.new_document.return_value = document

    monkeypatch.setattr(cli.djvu.decode, 'JobFailed', TemporaryJobError)
    monkeypatch.setattr(cli.djvu.decode, 'Context', Mock(side_effect=(first_context, second_context)))
    monkeypatch.setattr(cli.djvu.decode, 'FileURI', lambda source: source)

    assert cli.open_djvu_document('book.djvu') is document
    assert first_context.new_document.call_count == 1
    assert second_context.new_document.call_count == 1
    document.decoding_job.wait.assert_called_once_with()


def test_open_djvu_document_reports_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    failing_context = Mock()
    failing_context.new_document.side_effect = TemporaryJobError()

    monkeypatch.setattr(cli.djvu.decode, 'JobFailed', TemporaryJobError)
    monkeypatch.setattr(cli.djvu.decode, 'Context', Mock(return_value=failing_context))
    monkeypatch.setattr(cli.djvu.decode, 'FileURI', lambda source: source)

    with pytest.raises(click.ClickException, match='after retrying'):
        cli.open_djvu_document('book.djvu')
