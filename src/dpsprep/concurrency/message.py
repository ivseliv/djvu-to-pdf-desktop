import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class ExceptionWorkerMessage:
    error: BaseException


@dataclass(frozen=True)
class LogRecordWorkerMessage:
    record: logging.LogRecord


@dataclass(frozen=True)
class ImagePageProcessedMessage:
    page: int


@dataclass(frozen=True)
class TextPageProcessedMessage:
    page: int


@dataclass(frozen=True)
class TextLayerAlreadyProcessedMessage:
    pass


@dataclass(frozen=True)
class TextLayerFinishedMessage:
    pass


WorkerMessage = (
    ExceptionWorkerMessage |
    LogRecordWorkerMessage |
    ImagePageProcessedMessage |
    TextPageProcessedMessage |
    TextLayerAlreadyProcessedMessage |
    TextLayerFinishedMessage
)
