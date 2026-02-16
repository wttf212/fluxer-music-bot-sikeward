from collections import deque
from dataclasses import dataclass


@dataclass
class Track:
    query: str
    title: str
    requested_by: str


class TrackQueue:
    def __init__(self):
        self._queue: deque[Track] = deque()

    def add(self, track: Track):
        self._queue.append(track)

    def next(self) -> Track | None:
        return self._queue.popleft() if self._queue else None

    def clear(self):
        self._queue.clear()

    def list(self) -> list[Track]:
        return list(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0
