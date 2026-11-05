import logging
import sys

from sql_agent.logging_config import configure_logging


def test_configure_logging_uses_stderr() -> None:
    configure_logging("DEBUG")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert root.handlers[0].stream is sys.stderr
    assert logging.getLogger("httpx").level == logging.WARNING
