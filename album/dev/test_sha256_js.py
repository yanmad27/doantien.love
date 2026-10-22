"""Chạy KAT SHA-256 của static/sha256.js bằng node (bỏ qua nếu máy không có node)."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class Sha256Js(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "không có node")
    def test_kat(self):
        r = subprocess.run(["node", os.path.join(HERE, "test_sha256.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
