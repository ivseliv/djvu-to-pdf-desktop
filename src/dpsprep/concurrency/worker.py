import logging
import signal
import time
from typing import TYPE_CHECKING, Protocol

import djvu.decode

from dpsprep.options import DpsPrepOptions
from dpsprep.outline import TextExtractionTracker
from dpsprep.workflow import process_page_bg, process_text

from .message import (
    ImagePageProcessedMessage,
    LogRecordWorkerMessage,
    TextLayerAlreadyProcessedMessage,
    TextLayerFinishedMessage,
    TextPageProcessedMessage,
    WorkerMessage,
)


if TYPE_CHECKING:
    from multiprocessing.connection import Connection


logger = logging.getLogger(__name__)


class SharedPageLimit(Protocol):
    value: int


def first_worker_page(worker_id: int, pool_size: int, resume_page: int) -> int:
    """Return the first non-completed page belonging to one striped worker."""
    return resume_page + (worker_id - resume_page) % pool_size


class SubprocessWorkerLoggerHandler(logging.Handler):
    conn: 'Connection[WorkerMessage]'

    def __init__(self, conn: 'Connection[WorkerMessage]') -> None:
        super().__init__()
        self.conn = conn

    def emit(self, record: logging.LogRecord) -> None:
        self.conn.send(LogRecordWorkerMessage(record))


class SubprocessTextExtractionTracker(TextExtractionTracker):
    child_conn: 'Connection[WorkerMessage]'

    def __init__(self, child_conn: 'Connection[WorkerMessage]') -> None:
        self.child_conn = child_conn

    def mark_page_as_processed(self, page: int) -> None:
        self.child_conn.send(
            TextPageProcessedMessage(page),
        )

    def mark_all_pages_as_processed(self) -> None:
        self.child_conn.send(
            TextLayerAlreadyProcessedMessage(),
        )


class SubprocessWorker:
    options: DpsPrepOptions
    child_conn: 'Connection[WorkerMessage]'

    def __init__(self, options: DpsPrepOptions, child_conn: 'Connection[WorkerMessage]') -> None:
        self.options = options
        self.child_conn = child_conn

    def setup_child_process(self) -> None:
        # First, we disable the SIGINT handler altogether.
        # See https://stackoverflow.com/a/6191991/2756776
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        # Then, we setup a special logger that pipes its messages to the parent via a multiprocessing Pipe.
        base_logger = logging.getLogger('dpsprep')
        base_logger.setLevel(logging.DEBUG if self.options.verbose else logging.INFO)
        base_logger.handlers.clear()
        base_logger.addHandler(SubprocessWorkerLoggerHandler(self.child_conn))

    def process_text(self) -> None:
        self.setup_child_process()

        # The document must be read anew because the underlying structures are not properly copied.
        document = djvu.decode.Context().new_document(
            djvu.decode.FileURI(self.options.workdir.src),
        )
        document.decoding_job.wait()

        tracker = SubprocessTextExtractionTracker(self.child_conn)
        process_text(self.options, document, tracker)
        self.child_conn.send(TextLayerFinishedMessage())

    def process_stripe(self, worker_id: int, page_limit: SharedPageLimit | None, resume_page: int) -> None:
        try:
            if worker_id == 0 and not self.options.no_text:
                self.process_text()
            self.process_page_bg(worker_id, page_limit, resume_page)
        finally:
            self.child_conn.close()

    def process_page_bg(
        self,
        worker_id: int,
        page_limit: SharedPageLimit | None = None,
        resume_page: int = 0,
    ) -> None:
        self.setup_child_process()

        # The document must be read anew because the underlying structures are not properly copied.
        document = djvu.decode.Context().new_document(
            djvu.decode.FileURI(self.options.workdir.src),
        )
        document.decoding_job.wait()

        first_page = first_worker_page(worker_id, self.options.pool_size, resume_page)
        for i in range(first_page, len(document.pages), self.options.pool_size):
            while page_limit is not None and i >= page_limit.value:
                time.sleep(0.05)
            process_page_bg(self.options, document, i)
            self.child_conn.send(
                ImagePageProcessedMessage(i),
            )
