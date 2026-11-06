"""Both reference entrypoints must admit the verifier's eight-client burst."""
import socket
from pathlib import Path
import tempfile
import unittest

from phoenix.records import ROOT
from phoenix.verify import Subject


class ReferenceAdmissionTests(unittest.TestCase):
    def test_eight_connections_are_admitted_while_accept_is_temporarily_delayed(self):
        for name in ("reference", "retention"):
            with self.subTest(reference=name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                wrapper = directory / "delayed-accept.py"
                source = ROOT / "examples" / name / "server.py"
                wrapper.write_text(
                    "import runpy,time\nfrom http.server import ThreadingHTTPServer\n"
                    "original=ThreadingHTTPServer.get_request\n"
                    "def delayed(server):\n time.sleep(1)\n return original(server)\n"
                    "ThreadingHTTPServer.get_request=delayed\n"
                    "runpy.run_path(%r,run_name='__main__')\n" % str(source))
                subject = Subject(wrapper, directory)
                clients = []
                try:
                    subject.start()
                    for _ in range(8):
                        clients.append(socket.create_connection(("127.0.0.1", subject.port), timeout=0.15))
                finally:
                    for client in clients:
                        client.close()
                    subject.stop()
