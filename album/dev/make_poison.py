"""Sinh ảnh "bom" cho dev/check_limits.py --poison (PNG nhỏ khai báo rất nhiều điểm ảnh). Chỉ dev.

  python dev/make_poison.py /tmp/album-poison
"""
import os
import sys

from PIL import Image

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
for name, mode, size, fill in [("bomb150_rgb.png", "RGB", 12247, (90, 90, 90)), ("a60_rgb.png", "RGB", 7746, (90, 90, 90)),
                               ("b60_rgba.png", "RGBA", 7746, (90, 90, 90, 255)), ("c60_gray16.png", "I;16", 7746, 30000),
                               ("selfie50.png", "RGB", 7071, (120, 100, 90)), ("selfie40.png", "RGB", 6324, (120, 100, 90))]:
    Image.new(mode, (size, size), fill).save(os.path.join(out, name))
