"""CHỈ ĐỂ DIỄN TẬP (không phải bằng chứng): make_rehearsal.py SRC OUT_0410 OUT_1710 [N]
Chép tối đa N file .webp ĐÃ TẢI XONG (không lấy .part) của SRC sang OUT_0410, và ghi bản mã hoá lại (thu 97%, chất lượng khác, tên mới → sha256 mới
→ tính là ảnh khác) sang OUT_1710. Cùng người ở hai ngày → thử được tìm selfie 'có mặt ở cả hai ngày'."""
import os, shutil, sys
from PIL import Image
src, o4, o17 = sys.argv[1:4]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 12
for d in (o4, o17):
    os.makedirs(d, exist_ok=True)
files = sorted(f for f in os.listdir(src) if f.lower().endswith(".webp"))[:n]
for i, f in enumerate(files):
    shutil.copyfile(os.path.join(src, f), os.path.join(o4, f))
    if i % 2 == 0 or len(files) < 6:  # about half become 17/10 variants
        im = Image.open(os.path.join(src, f)).convert("RGB")
        im = im.resize((int(im.width * .97), int(im.height * .97)))
        im.save(os.path.join(o17, "r17_" + f.split(".jpg-")[0] + ".jpg"), quality=83)
print(len(os.listdir(o4)), "->", o4, "|", len(os.listdir(o17)), "->", o17)
