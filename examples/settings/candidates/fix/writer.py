"""Buffer records and write them to a device in blocks.

The device is simulated: every block write waits ``LATENCY_SECONDS``, as
a synchronous write to a slow disk or a network sink would, and uses no
CPU while it waits.
"""

import os
import time

DEFAULT_FLUSH_EVERY = 100
"""Records buffered before a block is written, when ``FLUSH_EVERY`` is
not set."""
LATENCY_SECONDS = 0.0004
"""Time one block write takes, whatever its size."""


def write_block(block: str) -> int:
    """Write one block to the simulated device.

    Parameters
    ----------
    block : str
        The text to write.

    Returns
    -------
    int
        Characters written.
    """
    time.sleep(LATENCY_SECONDS)
    return len(block)


class Writer:
    """Collect records and write them out ``flush_every`` at a time.

    Parameters
    ----------
    flush_every : int
        Records buffered before a block is written, at least 1.

    Attributes
    ----------
    written : int
        Characters written to the device so far.

    Raises
    ------
    ValueError
        If ``flush_every`` is less than 1.
    """

    def __init__(self, flush_every: int) -> None:
        if flush_every < 1:
            msg = f"flush_every must be at least 1, got {flush_every}"
            raise ValueError(msg)
        self._flush_every = flush_every
        self._buffer: list[str] = []
        self.written = 0

    @classmethod
    def from_environment(cls) -> "Writer":
        """Build a writer with the ``FLUSH_EVERY`` setting.

        Returns
        -------
        Writer
            Flushing every ``FLUSH_EVERY`` records, or
            `DEFAULT_FLUSH_EVERY` when the variable is not set.
        """
        return cls(int(os.environ.get("FLUSH_EVERY", DEFAULT_FLUSH_EVERY)))

    def add(self, record: str) -> None:
        """Buffer a record, writing a block when the buffer is full.

        Parameters
        ----------
        record : str
            One line of text.
        """
        self._buffer.append(record)
        if len(self._buffer) >= self._flush_every:
            self.flush()

    def flush(self) -> None:
        """Write the buffered records as one block."""
        if self._buffer:
            self.written += write_block("\n".join(self._buffer) + "\n")
            self._buffer.clear()
