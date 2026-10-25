import queue


class EventBus:
    def __init__(self):
        self._queue = queue.Queue()

    def emit(self, kind, data=""):
        self._queue.put((kind, data))

    def drain(self):
        while True:
            try:
                yield self._queue.get_nowait()
            except queue.Empty:
                return
