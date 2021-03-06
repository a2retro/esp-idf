#!/usr/bin/env python
import unittest
import struct
import csv
import sys
import subprocess
import tempfile
import os
sys.path.append("..")
from gen_esp32part import *

SIMPLE_CSV = """
# Name,Type,SubType,Offset,Size
factory,0,2,65536,1048576
"""

LONGER_BINARY_TABLE = ""
# type 0x00, subtype 0x00,
# offset 64KB, size 1MB
LONGER_BINARY_TABLE += "\xAA\x50\x00\x00" + \
                       "\x00\x00\x01\x00" + \
                       "\x00\x00\x10\x00" + \
                       "factory\0" + ("\0"*8) + \
                       "\x00\x00\x00\x00"
# type 0x01, subtype 0x20,
# offset 0x110000, size 128KB
LONGER_BINARY_TABLE += "\xAA\x50\x01\x20" + \
                       "\x00\x00\x11\x00" + \
                       "\x00\x02\x00\x00" + \
                       "data" + ("\0"*12) + \
                       "\x00\x00\x00\x00"
# type 0x10, subtype 0x00,
# offset 0x150000, size 1MB
LONGER_BINARY_TABLE += "\xAA\x50\x10\x00" + \
                       "\x00\x00\x15\x00" + \
                       "\x00\x10\x00\x00" + \
                       "second" + ("\0"*10) + \
                       "\x00\x00\x00\x00"


class CSVParserTests(unittest.TestCase):

    def test_simple_partition(self):
        table = PartitionTable.from_csv(SIMPLE_CSV)
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0].name, "factory")
        self.assertEqual(table[0].type, 0)
        self.assertEqual(table[0].subtype, 2)
        self.assertEqual(table[0].offset, 65536)
        self.assertEqual(table[0].size, 1048576)


    def test_require_type(self):
        csv = """
# Name,Type, SubType,Offset,Size
ihavenotype,
"""
        with self.assertRaisesRegexp(InputError, "type"):
            PartitionTable.from_csv(csv)


    def test_type_subtype_names(self):
        csv_magicnumbers = """
# Name, Type, SubType, Offset, Size
myapp, 0, 0,,  0x100000
myota_0, 0, 0x10,, 0x100000
myota_1, 0, 0x11,, 0x100000
myota_15, 0, 0x1f,, 0x100000
mytest, 0, 0x20,, 0x100000
myota_status, 1, 0,, 0x100000
        """
        csv_nomagicnumbers = """
# Name, Type, SubType, Offset, Size
myapp, app, factory,, 0x100000
myota_0, app, ota_0,, 0x100000
myota_1, app, ota_1,, 0x100000
myota_15, app, ota_15,, 0x100000
mytest, app, test,, 0x100000
myota_status, data, ota,, 0x100000
"""
        # make two equivalent partition tables, one using
        # magic numbers and one using shortcuts. Ensure they match
        magic = PartitionTable.from_csv(csv_magicnumbers)
        magic.verify()
        nomagic = PartitionTable.from_csv(csv_nomagicnumbers)
        nomagic.verify()

        self.assertEqual(nomagic["myapp"].type, 0)
        self.assertEqual(nomagic["myapp"].subtype, 0)
        self.assertEqual(nomagic["myapp"], magic["myapp"])
        self.assertEqual(nomagic["myota_0"].type, 0)
        self.assertEqual(nomagic["myota_0"].subtype, 0x10)
        self.assertEqual(nomagic["myota_0"], magic["myota_0"])
        self.assertEqual(nomagic["myota_15"], magic["myota_15"])
        self.assertEqual(nomagic["mytest"], magic["mytest"])
        self.assertEqual(nomagic["myota_status"], magic["myota_status"])

        #self.assertEqual(nomagic.to_binary(), magic.to_binary())

    def test_unit_suffixes(self):
        csv = """
# Name, Type, Subtype, Offset, Size
one_megabyte, app, factory, 32k, 1M
"""
        t = PartitionTable.from_csv(csv)
        t.verify()
        self.assertEqual(t[0].offset, 32*1024)
        self.assertEqual(t[0].size, 1*1024*1024)

    def test_default_offsets(self):
        csv = """
# Name, Type, Subtype, Offset, Size
first, app, factory,, 1M
second, data, 0x15,, 1M
minidata, data, 0x40,, 32K
otherapp, app, factory,, 1M
        """
        t = PartitionTable.from_csv(csv)
        # 'first'
        self.assertEqual(t[0].offset, 0x010000) # 64KB boundary as it's an app image
        self.assertEqual(t[0].size,   0x100000) # Size specified in CSV
        # 'second'
        self.assertEqual(t[1].offset, 0x110000) # prev offset+size
        self.assertEqual(t[1].size,   0x100000) # Size specified in CSV
        # 'minidata'
        self.assertEqual(t[2].offset, 0x210000)
        # 'otherapp'
        self.assertEqual(t[3].offset, 0x220000) # 64KB boundary as it's an app image

    def test_negative_size_to_offset(self):
        csv = """
# Name, Type, Subtype, Offset, Size
first, app, factory, 0x10000, -2M
second, data, 0x15,         ,  1M
        """
        t = PartitionTable.from_csv(csv)
        t.verify()
        # 'first'
        self.assertEqual(t[0].offset, 0x10000) # in CSV
        self.assertEqual(t[0].size,   0x200000 - t[0].offset) # Up to 2M
        # 'second'
        self.assertEqual(t[1].offset, 0x200000) # prev offset+size

    def test_overlapping_offsets_fail(self):
        csv = """
first, app, factory, 0x100000, 2M
second, app, ota_0,  0x200000, 1M
"""
        t = PartitionTable.from_csv(csv)
        with self.assertRaisesRegexp(InputError, "overlap"):
            t.verify()

class BinaryOutputTests(unittest.TestCase):
    def test_binary_entry(self):
        csv = """
first, 0x30, 0xEE, 0x100400, 0x300000
"""
        t = PartitionTable.from_csv(csv)
        tb = t.to_binary()
        self.assertEqual(len(tb), 32)
        self.assertEqual('\xAA\x50', tb[0:2]) # magic
        self.assertEqual('\x30\xee', tb[2:4]) # type, subtype
        eo, es = struct.unpack("<LL", tb[4:12])
        self.assertEqual(eo, 0x100400) # offset
        self.assertEqual(es, 0x300000) # size

    def test_multiple_entries(self):
        csv = """
first, 0x30, 0xEE, 0x100400, 0x300000
second,0x31, 0xEF,         , 0x100000
"""
        t = PartitionTable.from_csv(csv)
        tb = t.to_binary()
        self.assertEqual(len(tb), 64)
        self.assertEqual('\xAA\x50', tb[0:2])
        self.assertEqual('\xAA\x50', tb[32:34])


class BinaryParserTests(unittest.TestCase):
    def test_parse_one_entry(self):
        # type 0x30, subtype 0xee,
        # offset 1MB, size 2MB
        entry = "\xAA\x50\x30\xee" + \
                "\x00\x00\x10\x00" + \
                "\x00\x00\x20\x00" + \
                "0123456789abc\0\0\0" + \
                "\x00\x00\x00\x00"
        # verify that parsing 32 bytes as a table
        # or as a single Definition are the same thing
        t = PartitionTable.from_binary(entry)
        self.assertEqual(len(t), 1)
        t[0].verify()

        e = PartitionDefinition.from_binary(entry)
        self.assertEqual(t[0], e)
        e.verify()

        self.assertEqual(e.type, 0x30)
        self.assertEqual(e.subtype, 0xEE)
        self.assertEqual(e.offset, 0x100000)
        self.assertEqual(e.size,   0x200000)
        self.assertEqual(e.name, "0123456789abc")

    def test_multiple_entries(self):
        t = PartitionTable.from_binary(LONGER_BINARY_TABLE)
        t.verify()

        self.assertEqual(3, len(t))
        self.assertEqual(t[0].type, PartitionDefinition.APP_TYPE)
        self.assertEqual(t[0].name, "factory")

        self.assertEqual(t[1].type, PartitionDefinition.DATA_TYPE)
        self.assertEqual(t[1].name, "data")

        self.assertEqual(t[2].type, 0x10)
        self.assertEqual(t[2].name, "second")

        round_trip = t.to_binary()
        self.assertEqual(round_trip, LONGER_BINARY_TABLE)

    def test_bad_magic(self):
        bad_magic = "OHAI" + \
                    "\x00\x00\x10\x00" + \
                    "\x00\x00\x20\x00" + \
                    "0123456789abc\0\0\0" + \
                    "\x00\x00\x00\x00"
        with self.assertRaisesRegexp(InputError, "Invalid magic bytes"):
            PartitionTable.from_binary(bad_magic)

    def test_bad_length(self):
        bad_length = "OHAI" + \
                    "\x00\x00\x10\x00" + \
                    "\x00\x00\x20\x00" + \
                    "0123456789"
        with self.assertRaisesRegexp(InputError, "32 bytes"):
            PartitionTable.from_binary(bad_length)


class CSVOutputTests(unittest.TestCase):

    def test_output_simple_formatting(self):
        table = PartitionTable.from_csv(SIMPLE_CSV)
        as_csv = table.to_csv(True)
        c = csv.reader(as_csv.split("\n"))
        # first two lines should start with comments
        self.assertEqual(c.next()[0][0], "#")
        self.assertEqual(c.next()[0][0], "#")
        row = c.next()
        self.assertEqual(row[0], "factory")
        self.assertEqual(row[1], "0")
        self.assertEqual(row[2], "2")
        self.assertEqual(row[3], "0x10000") # reformatted as hex
        self.assertEqual(row[4], "0x100000") # also hex

        # round trip back to a PartitionTable and check is identical
        roundtrip = PartitionTable.from_csv(as_csv)
        self.assertEqual(roundtrip, table)

    def test_output_smart_formatting(self):
        table = PartitionTable.from_csv(SIMPLE_CSV)
        as_csv = table.to_csv(False)
        c = csv.reader(as_csv.split("\n"))
        # first two lines should start with comments
        self.assertEqual(c.next()[0][0], "#")
        self.assertEqual(c.next()[0][0], "#")
        row = c.next()
        self.assertEqual(row[0], "factory")
        self.assertEqual(row[1], "app")
        self.assertEqual(row[2], "2")
        self.assertEqual(row[3], "64K")
        self.assertEqual(row[4], "1M")

        # round trip back to a PartitionTable and check is identical
        roundtrip = PartitionTable.from_csv(as_csv)
        self.assertEqual(roundtrip, table)

class CommandLineTests(unittest.TestCase):

    def test_basic_cmdline(self):
        try:
            binpath = tempfile.mktemp()
            csvpath = tempfile.mktemp()

            # copy binary contents to temp file
            with open(binpath, 'w') as f:
                f.write(LONGER_BINARY_TABLE)

            # run gen_esp32part.py to convert binary file to CSV
            subprocess.check_call([sys.executable, "../gen_esp32part.py",
                                   binpath, csvpath])
            # reopen the CSV and check the generated binary is identical
            with open(csvpath, 'r') as f:
                from_csv = PartitionTable.from_csv(f.read())
            self.assertEqual(from_csv.to_binary(), LONGER_BINARY_TABLE)

            # run gen_esp32part.py to conver the CSV to binary again
            subprocess.check_call([sys.executable, "../gen_esp32part.py",
                                   csvpath, binpath])
            # assert that file reads back as identical
            with open(binpath, 'rb') as f:
                binary_readback = f.read()
            self.assertEqual(binary_readback, LONGER_BINARY_TABLE)

        finally:
            for path in binpath, csvpath:
                try:
                    os.remove(path)
                except OSError:
                    pass


if __name__ =="__main__":
    unittest.main()
