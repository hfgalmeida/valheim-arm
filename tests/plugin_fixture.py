"""Synthetic PE/CLR headers; no executable game or third-party plugin required."""

import struct


def dll(marker=1):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x14C, 1)
    struct.pack_into("<HH", data, 148, 224, 0x2102)
    struct.pack_into("<H", data, 152, 0x10B)
    struct.pack_into("<I", data, 152 + 92, 16)
    struct.pack_into("<II", data, 152 + 208, 0x2000, 72)
    struct.pack_into("<IIII", data, 152 + 224 + 8, 512, 0x2000, 512, 512)
    struct.pack_into("<I", data, 512, 72)
    struct.pack_into("<II", data, 520, 0x2080, 20)
    data[640:644] = b"BSJB"
    data[900] = marker
    return bytes(data)
