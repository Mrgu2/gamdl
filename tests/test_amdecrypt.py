import asyncio
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Crypto.Cipher import AES

from gamdl.downloader import amdecrypt


class AmdecryptOptionTests(unittest.TestCase):
    def _song_info(self) -> amdecrypt.SongInfo:
        return amdecrypt.SongInfo(
            samples=[
                amdecrypt.SampleInfo(data=b"a" * 16, duration=1024, desc_index=0),
                amdecrypt.SampleInfo(data=b"b" * 16, duration=1024, desc_index=1),
            ],
            encryption_info=amdecrypt.EncryptionInfo(scheme_type="cbcs"),
        )

    def test_hex_decrypt_single_content_key_maps_every_sample_to_track_key(self):
        song_info = self._song_info()
        track_key_hex = "00" * 16

        with (
            patch("gamdl.downloader.amdecrypt.extract_song", return_value=song_info),
            patch(
                "gamdl.downloader.amdecrypt.decrypt_samples_hex",
                return_value=b"decrypted",
            ) as decrypt_samples_hex,
            patch("gamdl.downloader.amdecrypt.write_decrypted_m4a") as write_m4a,
        ):
            asyncio.run(
                amdecrypt.decrypt_file_hex(
                    "/tmp/in.m4a",
                    "/tmp/out.m4a",
                    track_key_hex,
                    use_single_content_key=True,
                )
            )

        keys = decrypt_samples_hex.call_args.args[1]
        self.assertEqual(keys, {0: bytes.fromhex(track_key_hex), 1: bytes.fromhex(track_key_hex)})
        write_m4a.assert_called_once()

    def test_hex_decrypt_can_force_cenc_scheme(self):
        song_info = self._song_info()

        with (
            patch("gamdl.downloader.amdecrypt.extract_song", return_value=song_info),
            patch(
                "gamdl.downloader.amdecrypt.decrypt_samples_hex",
                return_value=b"decrypted",
            ) as decrypt_samples_hex,
            patch("gamdl.downloader.amdecrypt.write_decrypted_m4a"),
        ):
            asyncio.run(
                amdecrypt.decrypt_file_hex(
                    "/tmp/in.m4a",
                    "/tmp/out.m4a",
                    "11" * 16,
                    use_cenc=True,
                )
            )

        encryption_info = decrypt_samples_hex.call_args.args[2]
        self.assertEqual(encryption_info.scheme_type, "cenc")

    def test_wrapper_decrypt_forwards_single_content_key_flag(self):
        song_info = self._song_info()

        with (
            patch("gamdl.downloader.amdecrypt.extract_song", return_value=song_info),
            patch(
                "gamdl.downloader.amdecrypt.decrypt_samples",
                new=AsyncMock(return_value=b"decrypted"),
            ) as decrypt_samples,
            patch("gamdl.downloader.amdecrypt.write_decrypted_m4a"),
        ):
            asyncio.run(
                amdecrypt.decrypt_file(
                    "127.0.0.1:10020",
                    "song-123",
                    "skd://track",
                    "/tmp/in.m4a",
                    "/tmp/out.m4a",
                    use_single_content_key=True,
                )
            )

        self.assertTrue(decrypt_samples.await_args.args[6])

    def test_hex_sample_decrypt_reads_file_backed_sample_payload(self):
        key = b"\x00" * 16
        iv = b"\x00" * 16
        plaintext = b"0123456789abcdef"
        encrypted = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=iv).encrypt(
            plaintext
        )

        with tempfile.TemporaryDirectory() as tempdir:
            sample_path = Path(tempdir) / "encrypted.bin"
            sample_path.write_bytes(b"prefix" + encrypted + b"suffix")
            sample = amdecrypt.SampleInfo(
                data=b"",
                duration=1024,
                desc_index=0,
                iv=iv,
                size=len(encrypted),
                data_path=str(sample_path),
                data_offset=len(b"prefix"),
            )

            result = amdecrypt.decrypt_samples_hex(
                [sample],
                {0: key},
                amdecrypt.EncryptionInfo(scheme_type="cenc"),
            )

        self.assertEqual(result, plaintext)

    def test_write_decrypted_m4a_streams_mdat_from_file(self):
        song_info = amdecrypt.SongInfo(
            samples=[amdecrypt.SampleInfo(data=b"", duration=1024, desc_index=0, size=7)]
        )

        with tempfile.TemporaryDirectory() as tempdir:
            payload_path = Path(tempdir) / "payload.bin"
            output_path = Path(tempdir) / "out.m4a"
            payload_path.write_bytes(b"payload")

            with patch("gamdl.downloader.amdecrypt._write_moov") as write_moov:
                amdecrypt.write_decrypted_m4a(
                    str(output_path),
                    song_info,
                    b"",
                    decrypted_data_path=str(payload_path),
                    decrypted_data_size=7,
                )

            result = output_path.read_bytes()

        write_moov.assert_called_once()
        self.assertIn(struct.pack(">I", 15) + b"mdatpayload", result)

    def test_wrapper_file_backed_decrypt_streams_temp_payload_to_writer(self):
        song_info = self._song_info()

        async def fake_decrypt_samples(*args, **kwargs):
            payload_path = kwargs["decrypted_data_path"]
            self.assertTrue(payload_path)
            Path(payload_path).write_bytes(b"decrypted")
            return b""

        def fake_write_m4a(*args):
            self.assertEqual(args[2], b"")
            self.assertTrue(args[4])
            self.assertEqual(args[5], len(b"decrypted"))
            self.assertTrue(os.path.exists(args[4]))

        with (
            patch("gamdl.downloader.amdecrypt.extract_song", return_value=song_info),
            patch(
                "gamdl.downloader.amdecrypt.decrypt_samples",
                new=AsyncMock(side_effect=fake_decrypt_samples),
            ) as decrypt_samples,
            patch(
                "gamdl.downloader.amdecrypt.write_decrypted_m4a",
                side_effect=fake_write_m4a,
            ),
        ):
            asyncio.run(
                amdecrypt.decrypt_file(
                    "127.0.0.1:10020",
                    "song-123",
                    "skd://track",
                    "/tmp/in.m4a",
                    "/tmp/out.m4a",
                    file_backed_samples=True,
                )
            )

        self.assertTrue(decrypt_samples.await_args.kwargs["decrypted_data_path"])

    def test_hex_file_backed_decrypt_streams_temp_payload_to_writer(self):
        song_info = self._song_info()

        def fake_decrypt_to_file(*args):
            output_path = args[3]
            Path(output_path).write_bytes(b"decrypted")
            return len(b"decrypted")

        def fake_write_m4a(*args):
            self.assertEqual(args[2], b"")
            self.assertTrue(args[4])
            self.assertEqual(args[5], len(b"decrypted"))
            self.assertTrue(os.path.exists(args[4]))

        with (
            patch("gamdl.downloader.amdecrypt.extract_song", return_value=song_info),
            patch(
                "gamdl.downloader.amdecrypt.decrypt_samples_hex_to_file",
                side_effect=fake_decrypt_to_file,
            ) as decrypt_to_file,
            patch(
                "gamdl.downloader.amdecrypt.write_decrypted_m4a",
                side_effect=fake_write_m4a,
            ),
        ):
            asyncio.run(
                amdecrypt.decrypt_file_hex(
                    "/tmp/in.m4a",
                    "/tmp/out.m4a",
                    "11" * 16,
                    file_backed_samples=True,
                )
            )

        decrypt_to_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
