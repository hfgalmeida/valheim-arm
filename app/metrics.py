import asyncio
import time
from collections import deque
from pathlib import Path

import psutil


class Metrics:
    def __init__(self, data):
        self.data = data
        self.history = deque(maxlen=720)
        self.previous = None

    def sample(self):
        now = time.monotonic()
        cgroup = Path("/sys/fs/cgroup")
        try:
            stat = dict(line.split() for line in (cgroup / "cpu.stat").read_text().splitlines())
            cpu_seconds = int(stat["usage_usec"]) / 1e6
            memory = int((cgroup / "memory.current").read_text())
            raw = (cgroup / "memory.max").read_text().strip()
            memory_limit = int(raw) if raw != "max" else psutil.virtual_memory().total
            scope = "contêiner (cgroup v2)"
        except (OSError, ValueError, KeyError):
            proc = psutil.Process()
            cpu_seconds = sum(proc.cpu_times()[:2])
            memory = proc.memory_info().rss
            memory_limit = psutil.virtual_memory().total
            scope = "supervisor somente (cgroup v2 indisponível)"
        net = psutil.net_io_counters()
        disk = psutil.disk_usage(self.data)
        sample = {"time": time.time(), "cpu_percent": 0.0, "memory_bytes": memory,
                  "memory_limit": memory_limit, "network_in_bps": 0.0, "network_out_bps": 0.0,
                  "disk_used": disk.used, "disk_total": disk.total, "scope": scope}
        if self.previous:
            before, cpu, received, sent = self.previous
            elapsed = max(now - before, 0.001)
            sample.update(cpu_percent=max(0, (cpu_seconds - cpu) / elapsed * 100),
                          network_in_bps=max(0, (net.bytes_recv - received) / elapsed),
                          network_out_bps=max(0, (net.bytes_sent - sent) / elapsed))
        self.previous = now, cpu_seconds, net.bytes_recv, net.bytes_sent
        self.history.append(sample)
        return sample

    async def run(self):
        while True:
            self.sample()
            await asyncio.sleep(5)
