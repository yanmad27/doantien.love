# Triển khai album.doantien.love lên Dokploy

Ứng dụng **không có mật khẩu** (quyết định của chủ dự án): ai biết URL đều tải ảnh lên, bấm Xử lý và tìm ảnh được. Đã có các chặn lạm dụng cơ bản (chỉ nhận ảnh, giới hạn dung lượng file, một job Xử lý một lúc, chặn khi ổ đĩa gần đầy, `noindex`) — xem README. Đừng đưa URL ra công khai khi chưa muốn.

> Model InsightFace `buffalo_l` chỉ cho dùng **phi thương mại**.

## 1. Tạo ứng dụng

1. Dokploy → Project → **Create Service → Application**, tên `album`.
2. **Provider: Git** — repo `doantien.love`, branch `feat/album-face-search` (hoặc `main` sau khi merge).
3. **Build Type: Dockerfile**
   - **Docker Context Path:** `album`
   - **Dockerfile Path:** `Dockerfile` (tính từ context, tức `album/Dockerfile`)
   - Không dùng Nixpacks/Buildpacks. Không cần build args.
4. Image build đã **tải model và kiểm SHA-256 ngay lúc build** (~290 MB tải từ GitHub; build lần đầu vài phút). Lúc chạy container không cần ra mạng.

## 2. Biến môi trường (tab Environment)

Không có bí mật nào. Tất cả đều có mặc định trong image; chỉ đặt khi muốn đổi:

| Biến | Mặc định trong image | Ý nghĩa |
|---|---|---|
| `PORT` | `8000` | Cổng app lắng nghe (host `0.0.0.0`) |
| `DATA_DIR` | `/data` | Nơi lưu mọi thứ bền vững (volume) |
| `ALBUM_MAX_UPLOAD_MB` | `60` | Giới hạn dung lượng mỗi ảnh (ảnh 45 MP JPEG thường 10–30 MB) |
| `ALBUM_MIN_FREE_MB` | `2048` | Dưới mức đĩa trống này, app từ chối tải ảnh lên (thông báo tiếng Việt) |
| `ALBUM_THREADS` | `0` (= số lõi) | Số luồng onnxruntime; đặt `2` nếu muốn app chiếm ít CPU hơn |
| `ALBUM_LOW_MEM` | `1` | Tắt arena cấp phát của onnxruntime (RSS đỉnh thấp hơn ~20%, tốc độ như nhau) |

## 3. Volume (bắt buộc — nếu không, mất hết ảnh khi redeploy)

Tab **Advanced → Volumes/Mounts** → thêm **Volume Mount** (named volume, *không phải* bind mount):

- Volume name: `album-data`
- Mount path: `/data` (khớp `DATA_DIR`)

Container chạy bằng user không phải root (uid 10001). Named volume mới tạo kế thừa quyền của `/data` trong image nên ghi được. Nếu dùng **bind mount** thư mục host, phải `chown 10001:10001` thư mục đó trước; nếu sai, app dừng ngay khi khởi động với dòng `LỖI: không ghi được vào DATA_DIR=…`.

## 4. Tên miền + HTTPS

Tab **Domains → Add Domain**:
- Host `album.doantien.love`, **Container Port `8000`** (= `PORT`), Path `/`, HTTPS bật, Certificate = Let's Encrypt.
- DNS: bản ghi A/AAAA của `album.doantien.love` trỏ về VPS (việc ở DNS do chủ dự án làm).

## 5. Giới hạn bộ nhớ và CPU

Tab **Advanced → Cluster/Resources**: **Memory Limit = 3 GB** (tối thiểu 2.5 GB), CPU 2 lõi trở lên.

Đo thực tế (macOS arm64, CPU, Python 3.9; bản Linux/Python 3.11 chưa đo được vì chưa có Docker — hãy kiểm lại bằng `docker stats` sau khi deploy):

| | RSS |
|---|---|
| App nhàn rỗi | ~0.4–0.5 GB |
| **Đỉnh khi Xử lý** (có ảnh JPEG 45 MP 9000×5000) | **~1.8 GB** (`ALBUM_LOW_MEM=1`; ~2.2 GB nếu tắt) |
| Sau truy vấn selfie | giữ nguyên mức đỉnh (allocator không trả bộ nhớ ngay) |

Đỉnh do ảnh lớn nhất quyết định (ảnh 45 MP: ~135 MB mảng + bộ nhớ trung gian của model dò ở 1600 px). Tìm kiếm trong lúc đang Xử lý vẫn chạy, chậm hơn. Nếu bị OOM-kill: tăng giới hạn, hoặc đặt `ALBUM_THREADS=2`.

Tốc độ: ~0.8 giây/ảnh ≤ 4000 px; ảnh 24 MP ~1.5 s, 45 MP ~2.5 s (CPU hiện đại, nhiều lõi). 1000 ảnh ≈ 15–25 phút.

## 6. Dung lượng đĩa mỗi ảnh

Đo bằng `measure.py` (xem README). Mỗi ảnh gồm:

| Thành phần | Dung lượng |
|---|---|
| Ảnh gốc | đúng bằng file tải lên (JPEG 24–45 MP thường 8–30 MB) |
| Bản xem 2000 px (JPEG q85) | ~0.5 MB (ảnh mẫu), có thể ~0.3–1 MB |
| Thumbnail 640 px | ~60 KB |
| Chỉ mục (embedding + bbox, `index.db`) | ~30 KB (≈ 1 KB/khuôn mặt, ~15 mặt/ảnh nhóm) |
| **Ngoài ảnh gốc** | **~0.6 MB/ảnh** |

Ví dụ: 1000 ảnh × 15 MB gốc ≈ 15 GB + 0.6 GB. Cần thêm chỗ trống cho tệp tạm lúc tải lên (một ảnh một lúc). Trước khi tải ảnh, kiểm tra đĩa của host:

```sh
curl -s https://album.doantien.love/healthz    # {"ok":true,"disk_free":…,"disk_total":…} (byte, của ổ chứa DATA_DIR)
```
Trang `/manage` cũng hiện dung lượng trống.

## 7. Tải ảnh lên qua Traefik

- Trình duyệt gửi **mỗi ảnh một request riêng** (tối đa 3 song song, ghi thẳng ra đĩa, không giữ cả file trong RAM). Một ảnh lỗi/đứt giữa chừng không làm hỏng cả lô — danh sách hiện ảnh nào lỗi; bấm chọn lại những ảnh đó.
- Traefik mặc định không giới hạn kích thước body. Nhưng entrypoint có `readTimeout` (~60 s cho cả request): ảnh 20 MB trên đường truyền chậm (< ~3 Mbit/s upload) có thể bị cắt → ảnh đó báo lỗi, tải lại.
- Cap mỗi ảnh 60 MB (`ALBUM_MAX_UPLOAD_MB`); ảnh quá cỡ bị chặn ngay ở trình duyệt.
- Nếu trước Traefik còn Cloudflare proxy: gói free giới hạn body 100 MB/request — vẫn đủ.

## 8. Dùng

1. Mở `https://album.doantien.love/manage` → kéo thả ảnh gốc vào → đợi "Hoàn tất".
2. Bấm **Xử lý**: lập chỉ mục mọi ảnh chưa xử lý (tăng dần: tải thêm ảnh rồi bấm lại chỉ xử lý ảnh mới). Thanh tiến độ hiện số ảnh/khuôn mặt/lỗi/thời gian. Đóng trình duyệt cũng không dừng job; mở lại trang sẽ thấy tiến độ.
3. Khách vào `https://album.doantien.love/`, tải selfie, kéo thanh ngưỡng, bấm ảnh để xem bản lớn / tải ảnh gốc.

Restart/redeploy container: dữ liệu trong volume còn nguyên, tìm kiếm chạy ngay, không cần Xử lý lại. Job đang chạy mà container bị dừng: bấm Xử lý lần nữa, nó làm tiếp phần chưa xong.

## 9. Sao lưu

Mọi thứ nằm trong volume `album-data` (`/data`: `originals/`, `previews/`, `thumbs/`, `index.db`). Ảnh gốc là phần không thể tạo lại; còn lại sinh lại được bằng cách xoá `index.db`, `previews/`, `thumbs/` rồi bấm Xử lý.

- Dokploy → **Volume Backups** (nếu bản của bạn có) → đích S3, lịch hằng đêm; hoặc trên VPS:
  `docker run --rm -v album-data:/data -v $PWD:/backup alpine tar czf /backup/album-$(date +%F).tgz -C /data .`
- Sao `index.db` khi không có job đang chạy (hoặc chấp nhận sao lại lần sau).

## 10. Kiểm tra sau deploy

1. Logs không có `LỖI:`; `curl https://album.doantien.love/healthz` trả `ok:true`.
2. `/robots.txt` có `Disallow: /`, response có `X-Robots-Tag: noindex, nofollow`.
3. Tải 3–5 ảnh, bấm Xử lý, tìm bằng selfie, bấm một kết quả mở được ảnh.
4. Redeploy một lần, kiểm tra ảnh/chỉ mục còn nguyên. `docker stats` xem RSS khi Xử lý (so với mục 5).
