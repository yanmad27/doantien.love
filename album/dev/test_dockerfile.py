"""Mọi module nội bộ mà server.py / index_photos.py nạp (bắc cầu) phải được COPY vào ảnh Docker.   .venv/bin/python -I -m unittest discover -s dev -p test_dockerfile.py"""
import ast
import os
import re
import unittest

ALBUM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRYPOINTS = ("server", "index_photos")


def copied_files():
    out = set()
    with open(os.path.join(ALBUM, "Dockerfile"), encoding="utf-8") as f:
        text = f.read().replace("\\\n", " ")
    for line in text.splitlines():
        m = re.match(r"\s*COPY\s+(?:--\S+\s+)*(.+)", line, re.I)
        if m:
            out.update(m.group(1).split()[:-1])
    return out


def local_imports(mod):
    with open(os.path.join(ALBUM, mod + ".py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names.add(n.module.split(".")[0])
    return {x for x in names if os.path.isfile(os.path.join(ALBUM, x + ".py"))}


class DockerfileCopiesModules(unittest.TestCase):
    def test_all_imported_local_modules_are_copied(self):
        seen, todo = set(), list(ENTRYPOINTS)
        while todo:
            m = todo.pop()
            if m in seen:
                continue
            seen.add(m)
            todo.extend(local_imports(m))
        self.assertIn("mp4box", seen)
        copied = copied_files()
        missing = sorted(m + ".py" for m in seen if m + ".py" not in copied)
        self.assertEqual(missing, [], "Dockerfile thiếu COPY cho: %s" % ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
