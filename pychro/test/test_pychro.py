#!/usr/bin/python3

#
#  Copyright 2015 Jon Turner 
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import unittest
import zipfile
import datetime
import os
import sys
import mmap
import struct
import time
import threading
import multiprocessing
import random
import shutil
import tempfile


sys.path.append(os.path.split(os.path.dirname(__file__))[0])
import pychro

ONLY_QUICK_TESTS = os.environ.get('PYCHRO_QUICK_TESTS', '0') == '1'

NUM_WORDS = 1024 if ONLY_QUICK_TESTS else 1024*1024


class TempDir:
    def __init__(self):
        self.path = tempfile.mkdtemp()

    def __del__(self):
        if not pychro.PLATFORM_WINDOWS:
            shutil.rmtree(self.path)
            
            
class TempFile:
    def __init__(self):
        self.path = tempfile.mktemp()

    def __del__(self):
        if not pychro.PLATFORM_WINDOWS:
            os.remove(self.path)


class WriteOMThread(threading.Thread):
    def __init__(self, path, id, num_msgs, initial_sleep, write_sleep):
        super().__init__()
        self._path = path
        self._num_msgs = num_msgs
        self._id = id
        self._initial_sleep = initial_sleep
        self._write_sleep = write_sleep
        self.partial_write_exception = False

    def run(self):
        self.write_chron = pychro.VanillaChronicleWriter(self._path, utcnow=TestWriteOverMidnight.mock_utcnow)
        appender = self.write_chron.get_appender()
        time.sleep(self._initial_sleep)
        for i in range(self._num_msgs):
            time.sleep(1)
            val = self._id*1000+i
            appender.write_int(val)
            time.sleep(self._write_sleep)
            try:
                appender.finish()
            except pychro.PartialWriteLostOnRollover:
                self.partial_write_exception = True
                break
        self.write_chron.close()


class TestThreadIdBits(unittest.TestCase):
    def test_14(self):
        self.with_bits(14)

    def test_15(self):
        self.with_bits(15)

    def test_16(self):
        self.with_bits(16)

    def test_17(self):
        self.with_bits(17)

    def test_18(self):
        self.with_bits(18)

    def with_bits(self, thread_id_bits):
        self.tempdir = TempDir()

        self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path, thread_id_bits=thread_id_bits)
        appender = self.write_chron.get_appender()
        appender.write_double(1.2345)
        appender.finish()

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path, thread_id_bits=thread_id_bits)
        self.assertEqual(1.2345, self.read_chron.next_reader().read_double())



class TestWriteOverMidnight(unittest.TestCase):
    @staticmethod
    def mock_utcnow():
        dt = TestWriteOverMidnight.DT_START + (datetime.datetime.utcnow() - TestWriteOverMidnight.DT_BASE)
        return dt

    def setUp(self):
        self.tempdir = TempDir()
        TestWriteOverMidnight.DT_START = datetime.datetime(2015, 1, 1, 23, 59, 56)
        TestWriteOverMidnight.DT_BASE = datetime.datetime.utcnow()

    def test_new_appenders(self):
        self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path,
                                                         utcnow=TestWriteOverMidnight.mock_utcnow)
        appender = self.write_chron.get_appender()
        appender.write_int(1)
        appender.finish()
        appender = self.write_chron.get_appender()
        appender.write_int(2)
        appender.finish()

        time.sleep(5)

        appender = self.write_chron.get_appender()
        appender.write_int(3)
        appender.finish()
        appender = self.write_chron.get_appender()
        appender.write_int(4)
        appender.finish()

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow,
                                                        date=datetime.date(2015, 1, 1))

        self.assertEqual(1, self.read_chron.next_reader().read_int())
        self.assertEqual(18071573114126337, self.read_chron.get_index())
        self.assertEqual((datetime.date(2015, 1, 1), 1),
                         (pychro.VanillaChronicleReader.from_full_index(self.read_chron.get_index())))
        self.assertEqual(2, self.read_chron.next_reader().read_int())
        self.assertEqual(18071573114126338, self.read_chron.get_index())
        self.assertEqual((datetime.date(2015, 1, 1), 2),
                         (pychro.VanillaChronicleReader.from_full_index(self.read_chron.get_index())))

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow,
                                                        date=datetime.date(2015, 1, 2))

        self.assertEqual(3, self.read_chron.next_reader().read_int())
        self.assertEqual(18072672625754113, self.read_chron.get_index())
        self.assertEqual((datetime.date(2015, 1, 2), 1),
                         (pychro.VanillaChronicleReader.from_full_index(self.read_chron.get_index())))
        self.assertEqual(4, self.read_chron.next_reader().read_int())
        self.assertEqual(18072672625754114, self.read_chron.get_index())
        self.assertEqual((datetime.date(2015, 1, 2), 2),
                         (pychro.VanillaChronicleReader.from_full_index(self.read_chron.get_index())))


        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow)

        self.assertEqual(1, self.read_chron.next_reader().read_int())
        self.assertEqual(18071573114126337, self.read_chron.get_index())
        self.assertEqual(2, self.read_chron.next_reader().read_int())
        self.assertEqual(18071573114126338, self.read_chron.get_index())
        self.assertEqual(3, self.read_chron.next_reader().read_int())
        self.assertEqual(18072672625754113, self.read_chron.get_index())
        self.assertEqual(4, self.read_chron.next_reader().read_int())
        self.assertEqual(18072672625754114, self.read_chron.get_index())

        self.read_chron.set_index(18072672625754113)
        self.assertEqual(4, self.read_chron.next_reader().read_int())
        self.read_chron.set_index(18071573114126337)
        self.assertEqual(2, self.read_chron.next_reader().read_int())

    def test_same_appender(self):
        self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path,
                                                         utcnow=TestWriteOverMidnight.mock_utcnow)
        appender = self.write_chron.get_appender()
        appender.write_int(1)
        appender.finish()
        appender.write_int(2)
        appender.finish()

        time.sleep(5)

        appender.write_int(3)
        appender.finish()
        appender.write_int(4)
        appender.finish()

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow,
                                                        date=datetime.date(2015, 1, 1))

        self.assertEqual(1, self.read_chron.next_reader().read_int())
        self.assertEqual(2, self.read_chron.next_reader().read_int())

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow,
                                                        date=datetime.date(2015, 1, 2))

        self.assertEqual(3, self.read_chron.next_reader().read_int())
        self.assertEqual(4, self.read_chron.next_reader().read_int())

        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow)

        self.assertEqual(1, self.read_chron.next_reader().read_int())
        self.assertEqual(2, self.read_chron.next_reader().read_int())
        self.assertEqual(3, self.read_chron.next_reader().read_int())
        self.assertEqual(4, self.read_chron.next_reader().read_int())


    def test_multi_thread(self):
        self.num_threads = 2 # 3 could be racey...
        self.num_msgs = 6
        self.threads = []

        for i in range(self.num_threads):
            self.threads.append(WriteOMThread(self.tempdir.path, i+1, self.num_msgs, i*0.5, 0))

        for i in range(self.num_threads):
            self.threads[i].start()

        for i, t in enumerate(self.threads):
            t.join()
            self.assertEqual(False, t.partial_write_exception)
        print('Writing done, reading..')
        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path,
                                                        utcnow=TestWriteOverMidnight.mock_utcnow,
                                                        date=datetime.date(2015, 1, 1))

        ints = set()
        while True:
            try:
                reader = self.read_chron.next_reader()
                this_int = reader.read_int()
                ints.add(this_int)
            except pychro.NoData:
                break

        self.assertEqual(len(ints), self.num_threads*self.num_msgs)
        for i in range(self.num_threads):
            for j in range(self.num_msgs):
                self.assertEqual(ints.__contains__((i+1)*1000+j), True)


class MultiWriteChronThread(threading.Thread):
    def __init__(self, n, _id, _chron):
        super().__init__()
        self.n = n
        self.id = _id
        self.write_chron = _chron

    def run(self):
        for i in range(self.n):
            a = self.write_chron.get_appender()
            s = '%s=%s(%s)' % (self.id, i, str(random.random())*random.randint(0, 30))
            a.write_string(s)
            a.finish()


class MultiReadChronThread(threading.Thread):
    def __init__(self, _chron):
        super().__init__()
        self.read_chron = _chron
        self.n = 0
        self.e = 0
        self.n_for_id = dict()

    def run(self):
        while True:
            try:
                s = self.read_chron.next_reader().read_string()
                if s[-1] != ')':
                    self.e += 1
                id, i = s.split('=')
                self.n += 1
                self.n_for_id[id] = 1 + self.n_for_id.get(id, 0)
            except pychro.NoData:
                break


class TestMultiWriteChron(unittest.TestCase):
    def setUp(self):
        self.tempdir = TempDir()
        self.n = NUM_WORDS # exactly / 2*1024*1024 is a rather specific case as exactly fills up an index file
        self._write_chron1 = pychro.VanillaChronicleWriter(self.tempdir.path)
        self._write_chron2 = pychro.VanillaChronicleWriter(self.tempdir.path)
        self._read_chron1 = pychro.VanillaChronicleReader(self.tempdir.path)
        self._read_chron2 = pychro.VanillaChronicleReader(self.tempdir.path)

    def test_one(self):
        tws = []
        tws += [MultiWriteChronThread(self.n, 1, self._write_chron1)]
        tws += [MultiWriteChronThread(self.n, 2, self._write_chron1)]
        tws += [MultiWriteChronThread(self.n, 3, self._write_chron2)]
        tws += [MultiWriteChronThread(self.n, 4, self._write_chron2)]

        [t.start() for t in tws]
        [t.join() for t in tws]

        trs = [MultiReadChronThread(self._read_chron1), MultiReadChronThread(self._read_chron2)]

        [t.start() for t in trs]
        for t in trs:
            t.join()
            self.assertEqual(0, t.e)
            self.assertEqual(self.n*len(tws), t.n)
            self.assertEqual([self.n for _ in range(len(tws))], list(t.n_for_id.values()))

    def tearDown(self):
        self._write_chron1.close()
        self._write_chron2.close()
        self._read_chron1.close()
        self._read_chron2.close()


class TestGetThreadId(unittest.TestCase):
    def test_get_thread_id(self):
        tid = pychro._pychro.get_thread_id()
        self.assertIsInstance(tid, int)


class TestWriteChron(unittest.TestCase):
    def setUp(self):
        self.tempdir = TempDir()
        self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path)
        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path)

    def test_perf_str(self):
        n = NUM_WORDS
        strings = [str(random.random())*10 for _ in range(n)]
        print('Average length %s' % (sum(map(len, strings))/n))
        appender = self.write_chron.get_appender()
        t = time.time()
        for i in range(n):
            appender.write_string(strings[i])
            appender.finish()
        t = time.time() - t
        print('Write %.2f strings/s' % (n/t))
        t = time.time()
        for i in range(n):
            reader = self.read_chron.next_reader()
            reader.read_string()
        t = time.time() - t
        print('Read %.2f strings/s' % (n/t))

    def test_perf_int(self):
        n = NUM_WORDS
        appender = self.write_chron.get_appender()
        t = time.time()
        for i in range(n):
            appender.write_int(i)
            appender.finish()
        t = time.time() - t
        print('Write %.2f ints/s' % (n/t))
        t = time.time()
        for i in range(n):
            reader = self.read_chron.next_reader()
            reader.read_int()
        t = time.time() - t
        print('Read %.2f ints/s' % (n/t))

    def test_write_stop_start(self):
        n = 3
        for i in range(n):
            appender = self.write_chron.get_appender()
            appender.write_int(i+10)
            appender.finish()
            self.write_chron.close()
            self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path)
        print('Writing done, reading..')
        for i in range(n):
            reader = self.read_chron.next_reader()
            self.assertEqual(i+10, reader.read_int())

    def test_write_multi_index(self):
        n = NUM_WORDS*3
        for i in range(n):
            appender = self.write_chron.get_appender()
            appender.write_int(i)
            appender.finish()
        print('Writing done, reading..')
        for i in range(n):
            reader = self.read_chron.next_reader()
            self.assertEqual(i, reader.read_int())

    def test_write_multi_data(self):
        for i in range(NUM_WORDS):
            appender = self.write_chron.get_appender()
            appender.write_int(i+i)
            appender.write_string(str(self))
            appender.write_int(-i)
            appender.finish()
            appender.write_int(0x7fffffff & i*i)
            appender.write_string('Hello'*10)
            appender.write_int(-i)
            appender.finish()
            appender.write_int(i+1)
        print('Writing done, reading..')
        for i in range(NUM_WORDS):
            reader = self.read_chron.next_reader()
            self.assertEqual(i+i, reader.read_int())
            self.assertEqual(str(self), reader.read_string())
            self.assertEqual(-i, reader.read_int())
            reader = self.read_chron.next_reader()
            self.assertEqual(0x7fffffff & i*i, reader.read_int())
            self.assertEqual('Hello'*10, reader.read_string())
            self.assertEqual(-i, reader.read_int())

    def tearDown(self):
        self.write_chron.close()
        self.read_chron.close()


class CMapThread(threading.Thread):
    def __init__(self, _id, data):
        super().__init__()
        self.id = _id
        self.data = data
        self.writes = 0

    def run(self):
        offset = 0
        while offset < NUM_WORDS*8:
            if pychro.try_atomic_write_mmap(self.data, offset, 0, self.id) == 0:
                self.writes += 1
            offset += 8


class TestCMapWrite(unittest.TestCase):
    def setUp(self):
        self.tempfile = TempFile()
        with open(self.tempfile.path, 'wb') as fh:
            fh.write(b'\000'*8*NUM_WORDS)
            fh.close()
        self.open()

    def tearDown(self):
        self.close()

    def open(self):
        self.fh = open(self.tempfile.path, 'r+b')
        self.data = pychro.open_write_mmap(self.fh, NUM_WORDS*8)

    def close(self):
        pychro.close_mmap(self.data, NUM_WORDS*8)
        self.data = None
        self.fh.close()
        self.fh = None

    def test_read_words(self):
        for i in range(NUM_WORDS):
            self.assertEqual(pychro.read_mmap(self.data, i*8), 0)

    def test_write_words(self):
        for i in range(NUM_WORDS):
            pychro.try_atomic_write_mmap(self.data, i*8, pychro.read_mmap(self.data, i*8), i)
        self.close()
        self.open()
        for i in range(NUM_WORDS):
            self.assertEqual(pychro.read_mmap(self.data, i*8), i)

    def test_processes(self):
        pass

    def test_threads(self):
        for i in range(2):
            for j in range(NUM_WORDS):
                pychro.try_atomic_write_mmap(self.data, j*8, pychro.read_mmap(self.data, j*8), 0)
            ts = []
            num_threads = 2
            for i in range(1, num_threads+1):
                t = CMapThread(i, self.data)
                t.start()
                ts += [t]
            tcount = dict([(j, 0) for j in range(1, num_threads+1)])
            acount = dict([(j, 0) for j in range(1, num_threads+1)])
            for t in ts:
                t.join()
                tcount[t.id] = t.writes
            for w in range(NUM_WORDS):
                v = pychro.read_mmap(self.data, w*8)
                acount[v] = acount.get(v, 0)+1

            self.assertEqual(len(tcount), len(acount))
            self.assertEqual(NUM_WORDS, sum(acount.values()))
            for j in range(1, 1+num_threads):
                self.assertEqual(acount.get(j), tcount.get(j))
            self.assertEqual(NUM_WORDS, sum(tcount.values()))


class MMapThread(threading.Thread):
    def __init__(self, _id, mm, lock):
        super().__init__()
        self.id = _id
        self.mm = mm
        self.writes = 0
        self.lock = lock

    def run(self):
        packedid = struct.pack('Q', self.id)
        offset = 0
        while offset < NUM_WORDS*8:
            self.lock.acquire()
            if self.mm[offset:offset+8] == b'\x00\x00\x00\x00\x00\x00\x00\x00':
                self.mm[offset:offset+8] = packedid
                self.writes += 1
            self.lock.release()
            offset += 8


class TestMMapWrite(unittest.TestCase):
    def setUp(self):
        self.tempfile = TempFile()
        with open(self.tempfile.path, 'wb') as fh:
            fh.write(b'\000'*8*NUM_WORDS)
            fh.close()
        self.open()

    def tearDown(self):
        self.close()

    def open(self):
        self.fh = open(self.tempfile.path, 'r+b')
        if pychro.PLATFORM_WINDOWS:
            self.mm = mmap.mmap(self.fh.fileno(), 0)
        else:
            self.mm = mmap.mmap(self.fh.fileno(), 0, prot=mmap.PROT_READ | mmap.PROT_WRITE)

    def close(self):
        self.mm.close()
        self.mm = None
        self.fh.close()
        self.fh = None

    def test_write_bytes(self):
        for i in range(len(self.mm)):
            self.mm[i] = i % 256
        self.close()
        self.open()
        for i in range(len(self.mm)):
            self.assertEqual(self.mm[i], i % 256)

    def test_write_words(self):
        for i in range(NUM_WORDS):
            self.mm[i*8:i*8+8] = struct.pack('Q', i)
        self.close()
        self.open()
        for i in range(NUM_WORDS):
            self.assertEqual(struct.unpack('Q', self.mm[i*8:i*8+8])[0], i)

    def test_processes(self):
        pass

    def test_threads(self):
        lock = multiprocessing.Lock()
        for i in range(2):
            ts = []
            self.mm[0:NUM_WORDS*8] = b'\x00' * NUM_WORDS * 8
            num_threads = 2
            for i in range(1, num_threads+1):
                t = MMapThread(i, self.mm, lock)
                t.start()
                ts += [t]
            tcount = dict([(i, 0) for i in range(1, num_threads+1)])
            acount = dict([(i, 0) for i in range(1, num_threads+1)])
            for t in ts:
                t.join()
                tcount[t.id] = t.writes
            for w in range(NUM_WORDS):
                v = struct.unpack('Q', self.mm[w*8:w*8+8])[0]
                acount[v] = acount.get(v, 0)+1

            self.assertEqual(len(tcount), len(acount))
            self.assertEqual(NUM_WORDS, sum(acount.values()))
            for i in range(1, 1+num_threads):
                self.assertEqual(acount.get(i), tcount.get(i))
            self.assertEqual(NUM_WORDS, sum(tcount.values()))


class TestMMap(unittest.TestCase):
    def setUp(self):
        self.size = 4096
        self.tempfile = TempFile()
        with open(self.tempfile.path, 'wb') as fh:
            for i in range(self.size//8):
                fh.write(struct.pack('Q', i))
        self.fh = open(self.tempfile.path, 'r+b')
        self.read_data = pychro.open_read_mmap(self.fh, self.size)
        self.write_data = pychro.open_write_mmap(self.fh, self.size)

    def tearDown(self):
        pychro.close_mmap(self.write_data, self.size)
        pychro.close_mmap(self.read_data, self.size)
        self.fh.close()

    def test_read(self):
        for i, offset in enumerate(range(0, self.size, 8)):
            self.assertEqual(i, pychro.read_mmap(self.read_data, offset))

    def test_write(self):
        for i, offset in enumerate(range(0, self.size, 8)):
            self.assertEqual(i, pychro.read_mmap(self.write_data, offset))
        for i, offset in enumerate(range(0, self.size, 8)):
            self.assertEqual(i, pychro.read_mmap(self.read_data, offset))

        for i, offset in enumerate(range(0, self.size, 8)):
            pychro.unsafe_write_mmap(self.write_data, offset, i*i)

        for i, offset in enumerate(range(0, self.size, 8)):
            self.assertEqual(i*i, pychro.read_mmap(self.write_data, offset))
        for i, offset in enumerate(range(0, self.size, 8)):
            self.assertEqual(i*i, pychro.read_mmap(self.read_data, offset))


class TestReadWriteTypes(unittest.TestCase):
    def setUp(self):
        self.tempdir = TempDir()
        self.write_chron = pychro.VanillaChronicleWriter(self.tempdir.path)
        self.read_chron = pychro.VanillaChronicleReader(self.tempdir.path)

    def test1(self):
        appender = self.write_chron.get_appender()
        for i in range(256):
            appender.write_byte(i)
        for i in (0, 1, 10, 100, 1000, 10000):
            appender.write_stopbit(i)
        appender.write_boolean(True)
        appender.write_boolean(False)
        appender.write_string("")
        appender.write_string("AAAA")
        appender.write_string("ZZZZZZZZZZZZZZZZZ")
        appender.write_double(-5.4321)
        appender.write_int(0)
        appender.write_int(-2**31)
        appender.write_int(2**31-1)
        appender.write_long(-2**63)
        appender.write_long(2**63-1)
        appender.write_long(0)
        appender.write_string('\u1234')
        appender.finish()
        self.write_chron.close()

        reader = self.read_chron.next_reader()
        for i in range(256):
            self.assertEqual(i, reader.read_byte())
        for i in (0, 1, 10, 100, 1000, 10000):
            self.assertEqual(i, reader.read_stopbit())
        self.assertEqual(True, reader.read_boolean())
        self.assertEqual(False, reader.read_boolean())
        self.assertEqual("", reader.read_string())
        self.assertEqual("AAAA", reader.read_string())
        self.assertEqual("ZZZZZZZZZZZZZZZZZ", reader.read_string())
        self.assertEqual(-5.4321, reader.read_double())
        self.assertEqual(0, reader.read_int())
        self.assertEqual(-2**31, reader.read_int())
        self.assertEqual(2**31-1, reader.read_int())
        self.assertEqual(-2**63, reader.read_long())
        self.assertEqual(2**63-1, reader.read_long())
        self.assertEqual(0, reader.read_long())
        self.assertEqual('\u1234', reader.read_string())


    def tearDown(self):
        self.read_chron.close()


class TestDateIndex(unittest.TestCase):
    def test_date_index(self):
        self.assertEqual(18187021835042826, pychro.VanillaChronicleWriter.to_full_index(datetime.date(2015, 4, 16), 10))
        self.assertEqual((datetime.date(2015, 4, 16), 10),
                         pychro.VanillaChronicleWriter.from_full_index(18187021835042826))


class TestPychroReader(unittest.TestCase):
    FILE_ZIPS = [
        ('a', os.path.abspath(os.path.join(os.path.dirname(__file__), '../../test-files-a.zip'))),
        ('b', os.path.abspath(os.path.join(os.path.dirname(__file__), '../../test-files-b.zip'))),
        ('c', os.path.abspath(os.path.join(os.path.dirname(__file__), '../../test-files-c.zip')))
    ]

    def setUp(self):
        for suffix, zf in self.FILE_ZIPS:
            base = 'test-files-' + suffix
            if not os.path.isdir(base):
                with zipfile.ZipFile(zf) as zfh:
                    zfh.extractall(base)

        self._indexes = [(r'test-files-a/PychroTestChron1.Small', 10, datetime.date(2015, 2, 21))]
        if not ONLY_QUICK_TESTS:
            self._indexes += [(r'test-files-c/PychroTestIndex', 3000000, datetime.date(2015, 2, 23))]

        self._files = [
            (r'test-files-a/PychroTestChron1.Small', 10, 10, datetime.date(2015, 2, 21)),
            (r'test-files-a/PychroTestChron1.Small', 10, 10, None),
            (r'test-files-a/PychroTestChron2.Small', 10, 10, None),
            (r'test-files-a/PychroTestChron3.Small', 10, 10, None),
            (r'test-files-a/PychroTestChron1.Large', 100000, 100000, None),
            (r'test-files-a/PychroTestChron2.Large', 100000, 100000, None),
            (r'test-files-a/PychroTestChron3.Large', 100000, 100000, None),

            (r'test-files-b/PychroTestChron3.Small2day', 10, 10, datetime.date(2015, 2, 22)),
            (r'test-files-b/PychroTestChron3.Small2day', 20, 10, datetime.date(2015, 2, 21)),
            (r'test-files-b/PychroTestChron3.Small2day', 20, 10, None)
        ]

    def test_indexes(self):
        for fn, msgs_total, _date in self._indexes:
            t = time.time()
            self.do_index_test(fn, msgs_total, _date)
            t = time.time() - t
            print('test_indexes: %s with msgs=%s, took %ss' % (fn, msgs_total, t))

    def do_index_test(self, path, num_msgs, _date):
        chronicle = pychro.VanillaChronicleReader(path, thread_id_bits=16, date=_date)
        if _date:
            self.assertEqual(_date, chronicle.get_date())
        self.assertEqual(pychro.VanillaChronicleReader.to_full_index(chronicle.get_date(), num_msgs),
                         chronicle.get_end_index_today())
        msg_num = 0
        msg_nums = []
        for i in range(2):
            while True:
                try:
                    chronicle._next_position()
                    msg_num += 1
                except pychro.NoData:
                    break
            msg_nums += [msg_num]
            msg_num = 0
            chronicle.set_start_index_today()
        self.assertEqual(msg_nums[1], num_msgs)
        self.assertEqual(msg_nums[0], num_msgs)

    def test_files(self):
        for max_mem in [None, pychro.DATA_FILE_SIZE, 1024*1024*1024]:
            for fn, msgs_total, msgs_today, _date in self._files:
                if not ONLY_QUICK_TESTS or msgs_total < 100:
                    t = time.time()
                    self.do_file_test(fn, msgs_total, msgs_today, max_mem, _date)
                    t = time.time() - t
                    print('test_files: %s with msgs=%s, max_mem=%s took %ss' % (fn, msgs_total, max_mem, t))

    def do_file_test(self, path, num_msgs_total, num_msgs_today, max_mem, _date):
        chronicle = pychro.VanillaChronicleReader(path,
                                                  thread_id_bits=16,
                                                  polling_interval=None,
                                                  max_mapped_memory=max_mem,
                                                  date=_date)
        if _date:
            self.assertEqual(_date, chronicle.get_date())
        self.assertEqual(pychro.VanillaChronicleReader.to_full_index(chronicle.get_date(), num_msgs_today),
                         chronicle.get_end_index_today())
        msg_num = 0
        msg_nums = []
        for i in range(2):
            while True:
                try:
                    reader = chronicle.next_reader()
                    cmd = reader.read_int()

                    for j in range(cmd % 10):
                        s = j % 5
                        if s == 0:
                            reader.read_double()
                        elif s == 1:
                            reader.read_string()
                        elif s == 2:
                            reader.read_byte()
                        elif s == 3:
                            reader.read_long()
                        elif s == 4:
                            reader.read_char()
                        else:
                            pass
                    msg_num += 1
                except pychro.NoData:
                    break
            msg_nums += [msg_num]
            msg_num = 0
            chronicle.set_start_index_today()
        self.assertEqual(msg_nums[1], num_msgs_today)
        self.assertEqual(msg_nums[0], num_msgs_total)

if __name__ == '__main__':
    unittest.main()
