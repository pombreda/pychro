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

import time
import datetime
import collections
import mmap
import struct
import re
from ._pychro import *


class VanillaChronicleReader:
    # polling_interval of None means non-blocking and an exception of NoData will be raised
    # polling_interval of 0 means blocking spin (cpu intensive)
    #
    # provide date (for start of day) or index (which includes date)
    #
    # max mapped memory only relevant on windows due to the way memory mapped files are handled
    #
    # close() resets to chronicle, releasing all resources. Reading will begin again from the start.
    #

    def __init__(self, base_dir, polling_interval=None, date=None, full_index=None,
                 max_mapped_memory=pychro.DEFAULT_MAX_MAPPED_MEMORY_PER_READER,
                 thread_id_bits=None, utcnow=datetime.datetime.utcnow):
        self._index_file_size = pychro.INDEX_FILE_SIZE
        self._utcnow = utcnow
        self._thread_id_bits = thread_id_bits
        if self._thread_id_bits is None:
            if pychro.PLATFORM_WINDOWS:
                self._thread_id_bits = 16
            else:
                with open('/proc/sys/kernel/pid_max') as fh:
                    binstr = str(bin(int(fh.read().strip())))
                    self._thread_id_bits = len(binstr) - binstr.find('1') - 1

        self._base_dir = base_dir
        self._index_data_offset_bits = 64 - self._thread_id_bits
        self._thread_id_idx_mask = eval('0b'+'1'*self._thread_id_bits+'0'*self._index_data_offset_bits)
        self._thread_id_mask = eval('0b'+'1'*self._thread_id_bits)
        self._index_data_offset_mask = eval('0b'+'0'*self._thread_id_bits+'1'*self._index_data_offset_bits)
        self._max_maps = (max_mapped_memory//(pychro.DATA_FILE_SIZE)) if max_mapped_memory else None
        if self._max_maps is not None and self._max_maps < 1:
            raise pychro.ConfigError('max_mapped_memory must be >= 64MB')
        self._polling_interval = polling_interval
        self._base_dir = base_dir

        self._max_index = 0
        self._index = 0
        self._date = None
        self._cycle_dir = None
        self._full_index_base = None
        self._index_fh = []
        self._index_mm = []
        self._data_fhs = dict()
        self._data_mms = collections.OrderedDict()
        index = None

        if full_index:
            if date:
                raise pychro.InvalidArgumentError('Providing index and date are mutually exclusive')
            date, index = VanillaChronicleReader.from_full_index(full_index)

        if date is None:
            try:
                self._try_set_cycle_dir()
            except pychro.NoData:
                return
        else:
            self._update_cycle_dir(os.path.join(base_dir, '%4d%02d%02d' % (date.year, date.month, date.day)))
        if index:
            self._index = index

    def __str__(self):
        return '<VanillaChronicleReader dir:%s idx:%s>' % (self._cycle_dir, self._index)

    def __exit__(self):
        self.close()

    @staticmethod
    def to_full_index(date, index):
        return index + ((int(datetime.datetime(date.year, date.month, date.day,
                                      tzinfo=datetime.timezone.utc).timestamp())//86400) << pychro.CYCLE_INDEX_POS)

    @staticmethod
    def from_full_index(full_index):
        index = full_index & pychro.INDEX_OFFSET_MASK
        date = datetime.datetime.fromtimestamp((full_index >> pychro.CYCLE_INDEX_POS)*86400,
                                               tz=datetime.timezone.utc).date()
        return date, index

    def _update_cycle_dir(self, fp):
        self.close()
        self._cycle_dir = fp
        dstr = os.path.split(self._cycle_dir)[1]
        self._update_date_and_index_base(datetime.date(int(dstr[:4]), int(dstr[4:6]), int(dstr[6:8])))

    def _update_date_and_index_base(self, date):
        self._date = date
        self._full_index_base = VanillaChronicleReader.to_full_index(date, 0)

    def _open_next_index(self):
        file_num = len(self._index_fh)
        if not self._cycle_dir:
            self._try_set_cycle_dir()
        try:
            self._index_fh += [open(os.path.join(self._cycle_dir, 'index-%s' % file_num), 'rb')]
        except FileNotFoundError:
            raise pychro.NoChronicleForDate
        self._index_mm += [open_read_mmap(self._index_fh[-1], pychro.INDEX_FILE_SIZE)]

    def _open_data_file(self, filenum, thread):
        if self._cycle_dir is None:
            if not self._try_next_date():
                raise pychro.NoData
        try:
            return open(os.path.join(self._cycle_dir, 'data-%s-%s' % (thread, filenum)), 'rb')
        except FileNotFoundError:
            raise pychro.CorruptData

    def _open_data_memory_map(self, filenum, thread):
        fh = self._data_fhs.get((filenum, thread))
        if not fh:
            fh = self._open_data_file(filenum, thread)
            self._data_fhs[(filenum, thread)] = fh
        if pychro.PLATFORM_WINDOWS:
            return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        return mmap.mmap(fh.fileno(), 0, prot=mmap.PROT_READ)

    def _try_set_cycle_dir(self, date=None):
        date_str = '%4d%02d%02d' % (date.year, date.month, date.day) if date else None
        for f in sorted(os.listdir(self._base_dir)):
            if date_str and date_str > f:
                continue
            fp = os.path.join(self._base_dir, f)
            if not re.match('^[0-9]{8}$', f):
                continue
            if not os.path.isdir(fp):
                continue
            self._update_cycle_dir(fp)
            return
        raise pychro.NoData

    def _try_next_date(self):
        _next = False
        if not self._cycle_dir:
            self._try_set_cycle_dir()
        cur_date = os.path.split(self._cycle_dir)[1]
        for f in sorted(os.listdir(self._base_dir)):
            if f == cur_date:
                _next = True
            elif _next:
                self._update_cycle_dir(os.path.join(self._base_dir, f))
                return True
        return False

    def _get_index_value(self, index_offset):
        index_offset *= 8
        index_filenum = index_offset >> pychro.FILENUM_FROM_INDEX_SHIFT
        index_offset &= pychro.INDEX_OFFSET_MASK
        if index_filenum >= len(self._index_mm):
            self._open_next_index()
        return read_mmap(self._index_mm[index_filenum], index_offset)

    def _get_data_memory_map(self, filenum, thread):
        if (filenum, thread) in self._data_mms:
            return self._data_mms[(filenum, thread)]

        fm = self._open_data_memory_map(filenum, thread)
        self._data_mms[(filenum, thread)] = fm

        if self._max_maps and len(self._data_mms) > self._max_maps:
            try:
                self._data_mms.popitem(last=False)[1].close()
            except ReferenceError:
                pass
        return fm

    def _next_position(self):
        while True:
            val = self._get_index_value(self._index)
            pos = val & self._index_data_offset_mask

            if not pos:
                if self._date != self._utcnow().date() and self._try_next_date():
                    continue
                if self._polling_interval is None:
                    raise pychro.NoData
                if self._polling_interval != 0:
                    time.sleep(self._polling_interval)
                continue
            break

        filenum = (pos >> pychro.FILENUM_FROM_POS_SHIFT)
        pos = pos & pychro.POS_MASK

        self._index += 1
        thread = (val & self._thread_id_idx_mask) >> self._index_data_offset_bits

        return filenum, pos, thread

    def close(self):
        while True:
            try:
                self._data_mms.popitem()[1].close()
            except ReferenceError:
                pass
            except KeyError:
                break

        while True:
            try:
                self._data_fhs.popitem()[1].close()
            except ReferenceError:
                pass
            except KeyError:
                break

        [close_mmap(mm, self._index_file_size) for mm in self._index_mm if mm]
        self._index_mm = []

        [fh.close() for fh in self._index_fh if fh]
        self._index_fh = []

        self._max_index = 0
        self._index = 0
        self._date = None
        self._cycle_dir = None
        self._full_index_base = None

    def get_index(self):
        return self._index + self._full_index_base

    def next_index(self):
        self._next_position()
        return self._index + self._full_index_base

    def get_date(self):
        return self._date

    def get_raw_bytes(self, filenum, pos, thread):
        mm = self._get_data_memory_map(filenum, thread)
        return pos, mm

    def next_raw_bytes(self):
        return self.get_raw_bytes(*self._next_position())

    def set_index(self, full_index):
        date, index = VanillaChronicleReader.from_full_index(full_index)
        if self._date != date:
            self._try_set_cycle_dir(date)
        self._index = index

    def set_date(self, date):
        self._try_set_cycle_dir(date)

    def set_end(self):
        while self._try_next_date():
            pass
        self.set_end_index_today()

    def set_start_index_today(self):
        self._index = 0

    def set_end_index_today(self):
        self.set_index(self.get_end_index_today())

    def get_end_index_today(self):
        index = max(self._max_index, self._index)
        while True:
            if not self._get_index_value(index) & self._index_data_offset_mask:
                self._max_index = index
                return self._max_index + self._full_index_base
            index += 1

    def next_reader(self):
        return RawByteReader(*self.next_raw_bytes())


class RawByteReader():
    def __init__(self, offset, bytes):
        self._offset = offset
        self._bytes = bytes

    def get_offset(self):
        return self._offset

    def set_offset(self, offset):
        self._offset = offset

    def advance(self, num_bytes):
        self._offset += num_bytes

    def read_int(self):
        ret = struct.unpack('i', self._bytes[self._offset:self._offset+4])[0]
        self._offset += 4
        return ret

    def read_short(self):
        ret = struct.unpack('h', self._bytes[self._offset:self._offset+2])[0]
        self._offset += 2
        return ret

    def read_long(self):
        ret = struct.unpack('q', self._bytes[self._offset:self._offset+8])[0]
        self._offset += 8
        return ret

    def read_double(self):
        ret = struct.unpack('d', self._bytes[self._offset:self._offset+8])[0]
        self._offset += 8
        return ret

    # todo: remove. works for test data but not correct and no corresponding write
    def read_char(self): # utf16
        ret = self._bytes[self._offset:self._offset+2].decode('utf16')
        self._offset += 2
        return ret

    def read_byte(self):
        ret = self._bytes[self._offset]
        self._offset += 1
        return ret

    def read_boolean(self):
        ret = self._bytes[self._offset]
        self._offset += 1
        return ret != 0

    def read_stopbit(self):
        shift = 0
        value = 0
        while True:
            b = self.read_byte()
            value += (b & 0x7f) << shift
            shift += 7
            if (b & 0x80) == 0:
                return value

    def read_string(self):
        l = self.read_stopbit()
        ret = self._bytes[self._offset: self._offset + l].decode()
        self._offset += l
        # potentially there is some packing which we do not advance through
        # the reader must use the get_offset/set_offset/advance methods in this case,
        # with knowledge of the maximum size.
        return ret

    def peek_int(self):
        return struct.unpack('i', self._bytes[self._offset:self._offset+4])[0]

    def peek_short(self):
        return struct.unpack('h', self._bytes[self._offset:self._offset+2])[0]

    def peek_long(self):
        return struct.unpack('q', self._bytes[self._offset:self._offset+8])[0]

    def peek_double(self):
        return struct.unpack('d', self._bytes[self._offset:self._offset+8])[0]

    def peek_char(self): # utf16
        return self._bytes[self._offset:self._offset+2].decode('utf16')

    def peek_byte(self):
        return self._bytes[self._offset]

    def peek_boolean(self):
        return self._bytes[self._offset] != 0

    def peek_string(self):
        o = self.get_offset()
        l = self.read_stopbit()
        ret = self._bytes[self._offset: self._offset + l].decode()
        self.set_offset(o)
        return ret

    # returns the string at the current offset,
    # and makes no guarantees about where the offset is left. The
    # user must set it before performing a future read.
    # This is the most efficient way to read a string.
    def peek_string_undef_offset(self):
        l = self.read_stopbit()
        return self._bytes[self._offset: self._offset + l].decode()





