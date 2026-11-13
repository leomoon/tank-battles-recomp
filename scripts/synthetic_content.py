"""Create original PARAM.SFO fixtures for the game-free tests."""
from pathlib import Path
import struct

def write_sfo(path, title_id="TEST00000", category="DG"):
    values = {"TITLE_ID": title_id, "CATEGORY": category, "TITLE": "Synthetic test title"}
    keys, data, entries = bytearray(), bytearray(), bytearray()
    for name, value in values.items():
        key_offset = len(keys)
        keys += name.encode() + b"\0"
        value_bytes = value.encode() + b"\0"
        entries += struct.pack("<HHIII", key_offset, 0x0204, len(value_bytes), len(value_bytes), len(data))
        data += value_bytes
    key_offset = 20 + len(entries)
    data_offset = key_offset + len(keys)
    Path(path).write_bytes(struct.pack("<5I", 0x46535000, 0x101, key_offset, data_offset, len(values))
                           + entries + keys + data)
