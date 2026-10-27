from contextlib import contextmanager
from threading import Thread
from inventory import make_server


@contextmanager
def local_server(db):
    server = make_server(db, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
