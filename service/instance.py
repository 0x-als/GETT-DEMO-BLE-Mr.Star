import socket
import threading

from config import Config


class InstanceGuard:
    def __init__(self):
        self.sock = None

    def acquire(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", Config.INSTANCE_PORT))
            sock.listen(5)
        except OSError:
            sock.close()
            return False
        self.sock = sock
        return True

    def ping(self):
        try:
            with socket.create_connection(("127.0.0.1", Config.INSTANCE_PORT), timeout=1):
                pass
        except OSError:
            pass

    def serve(self, on_ping):
        threading.Thread(target=self._loop, args=(on_ping,), daemon=True).start()

    def _loop(self, on_ping):
        while True:
            try:
                conn, _ = self.sock.accept()
                conn.close()
                on_ping()
            except OSError:
                break
