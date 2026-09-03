#!/usr/bin/env python3
"""Sinh logo + favicon dạng vector cho thiệp cưới.

Chữ được convert thành path từ Cormorant Garamond (weight 500) nên logo không
phụ thuộc webfont — dùng được cả ở favicon, og:image, in ấn.

Chạy:  python3 tools/build-brand.py [đường-dẫn-CormorantGaramond-variable.ttf]
Font:  https://github.com/google/fonts/raw/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf
"""
import sys
import pathlib

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools.pens.svgPathPen import SVGPathPen

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("/tmp/logowork/cormorant.ttf")

INK = "#8d735d"      # màu monogram trên site
PEACH = "#ef9990"    # --peach
PEACH_INK = "#bf504b"  # --peach-ink
PAPER = "#fffaf3"    # --paper
GOLD = "#aa8356"     # --gold

WEIGHT = 500
TRACKING = -80       # letter-spacing -.08em ở .monogram
GAP = 210            # khoảng trắng hai bên trái tim

# Trái tim vẽ tay trong hộp 100×88 (y hướng xuống) — không phụ thuộc font.
HEART = ("M50 88 C18 64 2 46 2 28 C2 12 14 2 27 2 "
         "C36 2 45 7 50 15 C55 7 64 2 73 2 C86 2 98 12 98 28 C98 46 82 64 50 88 Z")


def glyphs():
    font = instancer.instantiateVariableFont(TTFont(FONT), {"wght": WEIGHT})
    gset = font.getGlyphSet()
    cmap = font.getBestCmap()
    upem = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight if hasattr(font["OS/2"], "sCapHeight") else 700
    out = {}
    for ch in "KDĐT":
        name = cmap[ord(ch)]
        pen = SVGPathPen(gset)
        gset[name].draw(pen)
        out[ch] = {"d": pen.getCommands(), "adv": font["hmtx"][name][0]}
    return out, upem, cap


def build_logo(g, upem, cap):
    """KD ♡ ĐT trên một dòng, chữ đã outline."""
    parts, x = [], 0

    def run(text):
        nonlocal x
        for i, ch in enumerate(text):
            parts.append(f'<path transform="translate({x} 0)" d="{g[ch]["d"]}"/>')
            x += g[ch]["adv"] + (TRACKING if i < len(text) - 1 else 0)

    run("KD")
    x += GAP
    heart_x, heart_w = x, int(cap * 0.62)
    x += heart_w + GAP
    run("ĐT")

    total_w, pad = x, 60
    hs = heart_w / 100.0
    heart_y = (cap - 88 * hs * (88 / 88)) / 2  # canh giữa theo cap height

    # Chữ nằm trong hệ toạ độ font (y hướng lên) nên lật trục y.
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w + pad * 2} {cap + pad * 2}" role="img" aria-label="Khắc Doãn và Đông Tiên">
  <title>Khắc Doãn &amp; Đông Tiên</title>
  <g transform="translate({pad} {cap + pad}) scale(1 -1)" fill="{INK}">
    {chr(10).join("    " + p for p in parts).strip()}
  </g>
  <g transform="translate({heart_x + pad} {pad + heart_y}) scale({hs})" fill="none" stroke="{PEACH}" stroke-width="7" stroke-linejoin="round">
    <path d="{HEART}"/>
  </g>
</svg>
'''


def build_icon(scale=0.56, frame=True):
    """Trái tim đặc trên nền giấy.

    frame=False cho favicon 16/32px: ở cỡ đó đường viền vàng chỉ còn ~0.4px,
    bị khử răng cưa thành vệt xám và làm mờ hình. Bản lớn (apple-touch, 192,
    512) luôn hiển thị to nên giữ viền cho sang.
    """
    w = 100 * scale
    h = 88 * scale
    x = (64 - w) / 2
    y = (64 - h) / 2 + 1  # nhích xuống 1px cho cân thị giác
    border = (
        f'\n  <rect x="2.5" y="2.5" width="59" height="59" fill="none" '
        f'stroke="{GOLD}" stroke-opacity=".55" stroke-width="1.5"/>' if frame else ''
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Thiệp cưới Khắc Doãn và Đông Tiên">
  <title>Khắc Doãn &amp; Đông Tiên</title>
  <rect width="64" height="64" fill="{PAPER}"/>{border}
  <g transform="translate({x:.2f} {y:.2f}) scale({scale})" fill="{PEACH_INK}">
    <path d="{HEART}"/>
  </g>
</svg>
'''


def main():
    g, upem, cap = glyphs()
    assets = ROOT / "assets"
    (assets / "logo.svg").write_text(build_logo(g, upem, cap), encoding="utf-8")
    # favicon.svg: trình duyệt hiện đại vẽ ở 16–32px là chính → bỏ viền.
    (assets / "favicon.svg").write_text(build_icon(scale=0.56, frame=False), encoding="utf-8")
    # nguồn cho apple-touch-icon / 192 / 512 (luôn hiển thị lớn).
    (assets / "icon-large.svg").write_text(build_icon(scale=0.46, frame=True), encoding="utf-8")
    # maskable: hệ điều hành có thể cắt tới 20% mép → thu nhỏ nội dung vào vùng an toàn.
    (assets / "icon-maskable.svg").write_text(build_icon(scale=0.34, frame=False), encoding="utf-8")
    print("wrote logo.svg, favicon.svg, icon-large.svg, icon-maskable.svg (cap:", cap, "upem:", upem, ")")


if __name__ == "__main__":
    main()
