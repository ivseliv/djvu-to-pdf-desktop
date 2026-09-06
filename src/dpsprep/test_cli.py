from unittest.mock import Mock

import click

from dpsprep import cli


class TemporaryJobFailed(Exception):
    pass


def test_open_djvu_document_retries_once(monkeypatch: object) -> None:
    first_context = Mock()
    first_context.new_document.side_effect = TemporaryJobFailed()
    document = Mock()
    second_context = Mock()
    second_context.new_document.return_value = document

    monkeypatch.setattr(cli.djvu.decode, "JobFailed", TemporaryJobFailed)
    monkeypatch.setattr(cli.djvu.decode, "Context", Mock(side_effect=(first_context, second_context)))
    monkeypatch.setattr(cli.djvu.decode, "FileURI", lambda source: source)

    assert cli.open_djvu_document("book.djvu") is document
    assert first_context.new_document.call_count == 1
    assert second_context.new_document.call_count == 1
    document.decoding_job.wait.assert_called_once_with()


def test_open_djvu_document_reports_a_clear_error(monkeypatch: object) -> None:
    failing_context = Mock()
    failing_context.new_document.side_effect = TemporaryJobFailed()

    monkeypatch.setattr(cli.djvu.decode, "JobFailed", TemporaryJobFailed)
    monkeypatch.setattr(cli.djvu.decode, "Context", Mock(return_value=failing_context))
    monkeypatch.setattr(cli.djvu.decode, "FileURI", lambda source: source)

    try:
        cli.open_djvu_document("book.djvu")
    except click.ClickException as error:
        assert "after retrying" in error.message
    else:
        raise AssertionError("expected a ClickException")
