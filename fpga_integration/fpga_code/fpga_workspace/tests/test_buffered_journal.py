import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from board.fpg1_v3_server import BufferedJournal


class BlockingFile(io.StringIO):
    def __init__(self):
        super().__init__();self.entered=threading.Event();self.release=threading.Event()
    def write(self,text):
        self.entered.set()
        if not self.release.wait(3):raise TimeoutError('Test writer was not released')
        return super().write(text)


class BufferedJournalTests(unittest.TestCase):
    def test_stalled_disk_does_not_block_the_protocol_producer(self):
        output=BlockingFile();journal=BufferedJournal(None,output=output)
        try:
            journal.write('first')
            self.assertTrue(output.entered.wait(1))
            done=threading.Event()
            def publish():journal.write('second');done.set()
            producer=threading.Thread(target=publish);producer.start()
            self.assertTrue(done.wait(1),'Protocol producer waited for the disk')
            self.assertFalse(output.release.is_set())
            producer.join()
        finally:output.release.set();journal.close()
        self.assertEqual(journal.written,2)

    def test_shutdown_drains_all_rows_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'journal.jsonl';journal=BufferedJournal(path)
            for sequence in range(100):journal.write('reply',sequence=sequence)
            journal.close()
            rows=[json.loads(row) for row in path.read_text().splitlines()]
            self.assertEqual([row['sequence'] for row in rows[:-1]],list(range(100)))
            self.assertEqual(rows[-1]['event'],'journal_closed')
            self.assertIsNone(rows[-1]['error'])
            self.assertFalse(journal.worker.is_alive())

    def test_backlog_exhaustion_is_reported_and_stops_service(self):
        output=BlockingFile();journal=BufferedJournal(None,capacity=1,output=output)
        try:
            journal.write('first');self.assertTrue(output.entered.wait(1));journal.write('second')
            with self.assertRaisesRegex(RuntimeError,'backlog'):journal.write('overflow')
            self.assertFalse(journal.running)
        finally:
            output.release.set()
            with self.assertRaisesRegex(RuntimeError,'backlog'):journal.close()

    def test_disk_error_is_reported_on_shutdown(self):
        class BadFile(io.StringIO):
            def flush(self):raise OSError('disk failed')
        journal=BufferedJournal(None,output=BadFile());journal.write('first');journal.worker.join(1)
        self.assertFalse(journal.running)
        with self.assertRaisesRegex(RuntimeError,'disk failed'):journal.close()


if __name__=='__main__':unittest.main()
