from __future__ import annotations

import asyncio
import contextlib


class ProcessOutputLimitError(RuntimeError):
    pass


class ProcessExecutionTimeout(RuntimeError):
    pass


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()

    async def discard(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        with contextlib.suppress(Exception):
            while await stream.read(64 * 1024):
                pass

    # On Windows, wait() can remain blocked while unread pipe data is buffered.
    # Drain and discard after killing; no bytes are retained in memory.
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(
                process.wait(),
                discard(process.stdout),
                discard(process.stderr),
                return_exceptions=True,
            ),
            timeout=5,
        )


async def communicate_bounded(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
    max_output_bytes: int,
) -> tuple[bytes, bytes]:
    """Read stdout/stderr concurrently with one shared pre-decode byte budget."""
    if process.stdout is None or process.stderr is None:
        raise ValueError("process pipes are required")
    budget = max(1, int(max_output_bytes))
    stdout = bytearray()
    stderr = bytearray()
    total = 0
    lock = asyncio.Lock()

    async def consume(
        stream: asyncio.StreamReader, destination: bytearray,
    ) -> None:
        nonlocal total
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return
            async with lock:
                remaining = budget - total
                if remaining <= 0:
                    raise ProcessOutputLimitError("process output exceeded byte limit")
                destination.extend(chunk[:remaining])
                total += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    raise ProcessOutputLimitError("process output exceeded byte limit")

    readers = [
        asyncio.create_task(consume(process.stdout, stdout)),
        asyncio.create_task(consume(process.stderr, stderr)),
    ]

    async def read_and_wait() -> None:
        await asyncio.gather(*readers)
        await process.wait()

    operation = asyncio.create_task(read_and_wait())
    try:
        await asyncio.wait_for(operation, timeout=max(0.1, float(timeout)))
    except asyncio.TimeoutError as exc:
        operation.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await operation
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await _stop_process(process)
        raise ProcessExecutionTimeout("process execution timed out") from exc
    except ProcessOutputLimitError:
        operation.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await operation
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await _stop_process(process)
        raise
    except asyncio.CancelledError:
        operation.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await operation
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        await _stop_process(process)
        raise
    return bytes(stdout), bytes(stderr)
