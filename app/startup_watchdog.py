"""Narrow detection of the fatal first-scene startup observed on Oracle/FEX."""

import re


FATAL_SIGNAL = re.compile(r"^Caught fatal signal - signo:(?:4|6|7|8|11)\b")


def world_ready(line):
    # Unity also prints "Loading: Done, Total time" before ZNet loads the world.
    return "Loading: Done." in line or bool(
        re.search(r"Loading: Done,? Total seconds sin[cz]e ZNet start:", line)
    )


class StartupWatchdog:
    def __init__(self):
        self.reset(None)

    def reset(self, process):
        self.process = process
        self.scene_started = False
        self.world_started = False
        self.chunks_loaded = False
        self.fatal = None

    def consume(self, line, *, ready=False, allow_fatal=True):
        if re.search(r"ZDOMan\.LoadChunks done \[\d+ms\]", line):
            self.chunks_loaded = True
            self.world_started = True
        if "Loading first scene!" in line:
            self.scene_started = True
        if (
            ready
            or any(
                marker in line
                for marker in (
                    "Loading: ZNet Start",
                    "ZNet.LoadWorld",
                    "ZDOMan.Load",
                    "World save (",
                    "World saved",
                )
            )
            or world_ready(line)
        ):
            self.world_started = True
        if allow_fatal and not self.fatal and FATAL_SIGNAL.match(line):
            self.fatal = line[:1000]
            return True
        return False

    @property
    def safe_to_terminate(self):
        # Unknown startup formats fail closed. A generic loading timeout is not
        # evidence that it is safe to kill a game process.
        return bool(self.fatal and self.scene_started and not self.world_started)
