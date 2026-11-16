#!/usr/bin/env python3
"""Game-free checks for local setup, SELF decryption and purchase preparation."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from Crypto.Cipher import AES
from decrypt_self import Self, aes_ctr, check_elf, parse_keys
import setup_game
from prepare_entitlement import CONTENT_ID, derive_rif, platform_tables, prepare
from synthetic_content import write_sfo


def encrypted_fixture():
    """An original one-segment NPDRM SELF encrypted with made-up keys."""
    erk, riv = bytes(range(32)), bytes(range(16))
    free, wrapping = bytes(range(16, 32)), bytes(range(32, 48))
    metadata_key, metadata_iv = bytes(range(48, 64)), bytes(range(64, 80))
    section_key, section_iv = bytes(range(80, 96)), bytes(range(96, 112))
    ident = b"\x7fELF\x02\x02\x01" + bytes(9)
    eh = struct.pack(">16sHHIQQQIHHHHHH", ident, 2, 21, 1, 0x10000, 64, 0, 0, 64, 56, 1, 64, 0, 0)
    ph = struct.pack(">II6Q", 1, 5, 0x100, 0x10000, 0x10000, 32, 32, 16)
    payload = struct.pack(">II", 0x10008, 0x10010) + bytes(range(24))
    expected = eh + ph + bytes(0x100 - len(eh) - len(ph)) + payload
    data = bytearray(0x2A0)
    data[:32] = struct.pack(">4sIHHIQQ", b"SCE\0", 2, 1, 1, 0x180, 0x280, len(expected))
    data[0x20:0x70] = struct.pack(">10Q", 3, 0x80, 0xA0, 0xE0, 0, 0, 0, 0x120, 12, 0)
    data[0x80:0x98] = struct.pack(">QIIQ", 0, 0, 8, 0)
    data[0xA0:0xE0] = eh
    data[0xE0:0x118] = ph
    data[0x120:0x12C] = b"NPD\0" + struct.pack(">II", 1, 3)
    info = metadata_key + bytes(16) + metadata_iv + bytes(16)
    info = AES.new(erk, AES.MODE_CBC, riv).encrypt(info)
    npkey = AES.new(wrapping, AES.MODE_ECB).decrypt(free)
    data[0x1A0:0x1E0] = AES.new(npkey, AES.MODE_CBC, bytes(16)).encrypt(info)
    headers = bytearray(0xA0)
    headers[:24] = struct.pack(">QIIII", 0, 0, 1, 2, 0)
    headers[0x20:0x50] = struct.pack(">QQ8I", 0x280, 32, 2, 0, 0, 0, 3, 0, 1, 1)
    headers[0x50:0x70] = section_key + section_iv
    data[0x1E0:0x280] = aes_ctr(metadata_key, metadata_iv, bytes(headers))
    data[0x280:] = aes_ctr(section_key, section_iv, payload)
    keys = ("[appldr]\ncategory=SELF\nrevision=0001\nprogram_type=NPDRM\n"
            f"erk={erk.hex()}\nriv={riv.hex()}\n"
            f"[NP_klic_free]\nkey={free.hex()}\n[NP_klic_key]\nkey={wrapping.hex()}\n")
    return bytes(data), expected, keys


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_portable_npdrm_decrypt(self):
        data, expected, _ = encrypted_fixture()
        sf = Self(data)
        result = sf.to_elf(bytes(range(32)), bytes(range(16)), log=lambda _: None,
                           klic=bytes(range(16, 32)), klic_key=bytes(range(32, 48)))
        self.assertEqual(result, expected)
        self.assertTrue(check_elf(result, sf.e_entry, log=lambda _: None))

    def test_atomic_publication_and_verified_cache(self):
        data, expected, keys = encrypted_fixture()
        (self.root / "USRDIR").mkdir()
        (self.root / "USRDIR/EBOOT.BIN").write_bytes(data)
        keyfile = self.root / "keys"
        keyfile.write_text(keys)
        with patch.object(setup_game, "validate_title"), patch.object(setup_game, "ELF_SHA256", {hashlib.sha256(expected).hexdigest()}):
            output = setup_game.decrypt_eboot(self.root, keys=keyfile)
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            self.assertEqual(setup_game.decrypt_eboot(self.root), output)
        self.assertFalse(list((self.root / "USRDIR").glob(".EBOOT-*")))

    def test_wrong_key_does_not_overwrite_output(self):
        data, _, keys = encrypted_fixture()
        (self.root / "USRDIR").mkdir()
        (self.root / "USRDIR/EBOOT.BIN").write_bytes(data)
        output = self.root / "USRDIR/EBOOT.elf"
        output.write_bytes(b"previous output")
        keyfile = self.root / "keys"
        keyfile.write_text(keys.replace(bytes(range(32)).hex(), bytes(32).hex()))
        with patch.object(setup_game, "validate_title"), self.assertRaisesRegex(ValueError, "SELF decryption failed"):
            setup_game.decrypt_eboot(self.root, keys=keyfile)
        self.assertEqual(output.read_bytes(), b"previous output")

    def test_missing_key_is_actionable(self):
        data, _, _ = encrypted_fixture()
        (self.root / "USRDIR").mkdir()
        (self.root / "USRDIR/EBOOT.BIN").write_bytes(data)
        with patch.object(setup_game, "validate_title"), self.assertRaisesRegex(ValueError, "--keys"):
            setup_game.decrypt_eboot(self.root)

    def test_sfo_identity_and_truncation(self):
        path = self.root / "PARAM.SFO"
        write_sfo(path, title_id="NPUB30154", category="HG")
        self.assertEqual(setup_game.sfo_title(path), "NPUB30154")
        path.write_bytes(path.read_bytes()[:22])
        with self.assertRaises(ValueError):
            setup_game.sfo_title(path)

    def test_wrong_package_and_title(self):
        package = self.root / "original.pkg"
        data = bytearray(128)
        data[:4] = b"\x7fPKG"
        content = b"UP2006-NPUB30154_00-TANKBATTLE00DEMO"
        data[0x30:0x30 + len(content)] = content
        package.write_bytes(data)
        setup_game.package_title(package)
        package.write_bytes(bytes(data).replace(b"NPUB30154", b"NPUB00000"))
        with self.assertRaisesRegex(ValueError, "not the supported"):
            setup_game.package_title(package)

    def test_unsupported_unlock_is_rejected(self):
        path = self.root / "unlock.edat"
        data = bytearray(352)
        data[:16] = b"NPD\0" + struct.pack(">III", 1, 1, 1)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Unsupported unlock"):
            setup_game.validate_unlock(path)

    def test_rap_sidecar_private_and_mismatch_preserved(self):
        (self.root / (CONTENT_ID + ".edat")).write_bytes(b"synthetic unlock")
        rap = bytes(range(16))
        (self.root / (CONTENT_ID + ".rap")).write_bytes(rap)
        result = prepare(self.root)
        self.assertEqual(result.read_bytes(), derive_rif(rap, platform_tables()))
        self.assertEqual(result.stat().st_mode & 0o777, 0o600)
        result.write_bytes(b"do not replace")
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare(self.root)
        self.assertEqual(result.read_bytes(), b"do not replace")


if __name__ == "__main__":
    unittest.main()
