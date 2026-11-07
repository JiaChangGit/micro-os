"""固定 ELF loader 的輸入邊界，使用最小 ELF64 fixture。"""
import struct
import unittest

from tools.pack_users import ELF_HEADER, PROGRAM_HEADER, read_image, render


def fixture() -> bytearray:
    data = bytearray(8192)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    ELF_HEADER.pack_into(data, 0, ident, 2, 183, 1, 0x400000,
                         64, 0, 0, 64, 56, 1, 0, 0, 0)
    PROGRAM_HEADER.pack_into(data, 64, 1, 5, 4096, 0x400000,
                             0x400000, 4, 4, 4096)
    data[4096:4100] = bytes.fromhex("c0035fd6")
    return data


class PackUsersTests(unittest.TestCase):
    def test_fixed_image_roundtrip(self):
        image = read_image(fixture())
        self.assertEqual(image.entry, 0x400000)
        self.assertEqual(image.segments[0].data, bytes.fromhex("c0035fd6"))
        header = render([image] * 3)
        self.assertIn("user_images[3]", header)
        self.assertIn("{0x400000, 4, 4, 5, user_0_data_0}", header)

    def test_header_boundary(self):
        for size in (0, 1, 63):
            with self.subTest(size=size), self.assertRaises(ValueError):
                read_image(fixture()[:size])
        for offset, value in ((4, 1), (5, 2), (6, 2)):
            data = fixture()
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                read_image(data)

    def test_bad_program_headers(self):
        for offset, fmt, value in ((32, "Q", 8192), (54, "H", 55),
                                   (56, "H", 0), (56, "H", 17)):
            data = fixture()
            struct.pack_into("<" + fmt, data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                read_image(data)

    def test_segment_contract(self):
        cases = [
            (0, "I", 2), (0, "I", 3), (0, "I", 7),
            (4, "I", 7), (4, "I", 1), (4, "I", 13),
            (8, "Q", 4097), (16, "Q", 0x400001),
            (16, "Q", 0x3ff000), (16, "Q", 0x600000),
            (24, "Q", 0x401000), (32, "Q", 8193),
            (40, "Q", 3), (40, "Q", 0x200001), (48, "Q", 8192),
        ]
        for offset, fmt, value in cases:
            data = fixture()
            struct.pack_into("<" + fmt, data, 64 + offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                read_image(data)

    def test_overlapping_page_rejected(self):
        data = fixture()
        struct.pack_into("<H", data, 56, 2)
        PROGRAM_HEADER.pack_into(data, 120, 1, 6, 4096, 0x400000,
                                 0x400000, 4, 4, 4096)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            read_image(data)

    def test_entry_in_bss_or_data_rejected(self):
        for entry in (0x400001, 0x400004, 0x401000):
            data = fixture()
            struct.pack_into("<Q", data, 24, entry)
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                read_image(data)

    def test_entry_requires_complete_instruction_in_file(self):
        for length in (1, 2, 3):
            data = fixture()
            struct.pack_into("<Q", data, 64 + 32, length)
            with self.subTest(length=length), self.assertRaisesRegex(ValueError, "entry"):
                read_image(data)
        self.assertEqual(read_image(fixture()).entry, 0x400000)

    def test_memory_tail_does_not_expand_payload(self):
        data = fixture()
        struct.pack_into("<Q", data, 64 + 40, 8192)
        segment = read_image(data).segments[0]
        self.assertEqual(segment.mem_size, 8192)
        self.assertEqual(len(segment.data), 4)

    def test_physical_reservation_is_smaller_than_virtual_slot(self):
        data = fixture()
        struct.pack_into("<Q", data, 64 + 40, 0x1b000)
        self.assertEqual(read_image(data).segments[0].mem_size, 0x1b000)
        struct.pack_into("<Q", data, 64 + 40, 0x1b001)
        with self.assertRaisesRegex(ValueError, "physical frame"):
            read_image(data)


if __name__ == "__main__":
    unittest.main()
