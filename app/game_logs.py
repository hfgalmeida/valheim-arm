"""Drain oversized game/plugin lines without losing the stdout reader."""

import asyncio


async def read_game_line(stream, on_discard):
    if not isinstance(stream, asyncio.StreamReader):
        return await stream.readline()
    discarding = False
    while True:
        try:
            line = await stream.readuntil(b"\n")
        except asyncio.LimitOverrunError as exc:
            if not discarding:
                on_discard()
                discarding = True
            await stream.readexactly(exc.consumed)
            continue
        except asyncio.IncompleteReadError as exc:
            return b"" if discarding else exc.partial
        if discarding:
            # The delimiter completes the discarded line; the next byte starts
            # a real game line, never a forged snapshot from the oversized tail.
            discarding = False
            continue
        return line
