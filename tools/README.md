# tools

Script sinh brand asset. Không nằm trong Docker image (`Dockerfile` chỉ COPY file cần phục vụ).

## Sinh lại logo + icon

```bash
# 1. tải font nguồn (Cormorant Garamond variable)
curl -sL "https://github.com/google/fonts/raw/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf" \
  -o /tmp/cormorant.ttf

# 2. sinh SVG (chữ được convert thành path nên không phụ thuộc webfont)
python3 -m pip install --user fonttools
python3 tools/build-brand.py /tmp/cormorant.ttf

# 3. rasterise sang PNG + favicon.ico (dùng qlmanage của macOS)
bash tools/render-icons.sh
```

Kết quả trong `assets/`:

| File | Dùng ở đâu |
|---|---|
| `logo.svg` | logo monogram vector, dùng lại cho in ấn / social |
| `favicon.svg` | favicon cho trình duyệt hiện đại (không viền để rõ ở 16px) |
| `favicon.ico` | fallback legacy, gói 16/32/48 |
| `apple-touch-icon.png` | màn hình chính iOS (180) |
| `icon-192.png`, `icon-512.png` | PWA manifest |
| `icon-maskable-512.png` | Android maskable, nội dung thu vào vùng an toàn |

## Sinh lại ảnh share (og:image)

`tools/og-source.html` là template 1200×630. Mở bằng trình duyệt ở đúng
viewport 1200×630 (deviceScaleFactor 2), chụp màn hình rồi hạ về 1200px:

```bash
sips -Z 1200 og-2x.png --out assets/og-image.png
```

Cần server tĩnh để ảnh hoa và Google Fonts load được: `python3 -m http.server 4321`.
