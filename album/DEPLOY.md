# Triển khai album.doantien.love lên Dokploy

Ứng dụng **mặc định không có mật khẩu** (quyết định của chủ dự án): ai biết URL đều tải ảnh lên, bấm Xử lý và tìm ảnh được. Muốn khoá riêng việc tải lên/Xử lý: đặt `ALBUM_ADMIN_TOKEN` (mục 2). Đã có các chặn lạm dụng cơ bản (chỉ nhận ảnh, giới hạn dung lượng file, một job Xử lý một lúc, chặn khi ổ đĩa gần đầy, `noindex`) — xem README. Đừng đưa URL ra công khai khi chưa muốn.

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
| `ALBUM_MIN_FREE_MB` | `5120` | Đĩa host dùng chung với app khác: dưới mức trống này (kiểm lúc bắt đầu và mỗi 8 MB khi đang nhận), app từ chối tải ảnh lên (HTTP 507, thông báo tiếng Việt) |
| `ALBUM_MAX_TOTAL_GB` | `20` | **Hạn mức tổng** dung lượng ảnh gốc trong album (tính cả phần đã đặt chỗ cho các upload đang chạy). Hết hạn mức → 507. Đặt theo đĩa thực có. **`0` hoặc để trống = không giới hạn tổng** (`/api/stats` trả `max_total_bytes: null`); ngưỡng đĩa trống `ALBUM_MIN_FREE_MB` vẫn áp dụng |
| `ALBUM_THREADS` | `2` | Số luồng onnxruntime (image đặt sẵn 2 để không chiếm hết CPU của VPS dùng chung) |
| `ALBUM_INDEX_WINDOW` | `1` | Số ảnh giải mã cùng lúc khi Xử lý. `1` = tuần tự (RAM đỉnh thấp nhất); `2` = giải mã trước một ảnh, nhanh hơn ~10–20% nhưng đỉnh RAM thêm ~0.5–1 GB với ảnh 60 MP |
| `ALBUM_APP_HOSTS` | `album.doantien.love,localhost,127.0.0.1` | Danh sách host (cách nhau dấu phẩy) được dùng **toàn bộ app**. Host khác trỏ vào cùng container (tên miền CDN, mục 4b) chỉ lấy được ảnh + file tĩnh + `/healthz` + `/robots.txt`; mọi thứ khác 404 |
| `ALBUM_CDN_ORIGIN` | trống | Origin CDN cho ảnh WebP, vd. `https://img.rsrm.dev` (không có đường dẫn). Điền vào `cdn` của `/api/gallery` + `<link rel=preconnect>`. **Trống = ảnh cùng origin = công tắc tắt CDN** (xem 4b) |
| `ALBUM_ADMIN_TOKEN` | trống | Đặt thì: `GET /manage` không có cookie `album_token` hợp lệ → 401 + trang đăng nhập (`static/manage-login.html`); `POST /api/admin-login {"token"}` đúng → cookie HttpOnly, SameSite=Strict, 30 ngày (thêm Secure khi https/`X-Forwarded-Proto`), sai → 401, quá 10 lần sai/phút/IP → 429. `POST /api/upload` và `POST /api/process` nhận cookie đó hoặc header `X-Album-Token: <mã>`, sai → 401. `GET /api/process`, `/api/stats`, trang khách và `/api/gallery` vẫn mở. Trống = mở như trước. Đây là bí mật: đặt trong tab Environment, đừng commit; app không bao giờ in nó ra response hoặc log |
| `ALBUM_DERIVE_WORKERS` | `2` | Số ảnh sinh WebP cùng lúc khi Xử lý (mỗi ảnh JPEG chỉ giải mã thu nhỏ nên nhẹ RAM) |
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

## 4b. Tên miền CDN cho ảnh (tuỳ chọn)

Trang công khai tải ảnh WebP từ `/i/{id}-{v}-{240|480|2000}.webp` — URL đặt tên theo nội dung, trả `Cache-Control: public, max-age=31536000, immutable` và `Content-Type: image/webp`, nên Cloudflare cache được (đã quan sát MISS → HIT với `.webp` qua rsrm.dev).

1. Dokploy → app `album` → **Domains → Add Domain**: thêm một tên miền thuộc rsrm.dev (vd. `img.rsrm.dev`), Container Port `8000`, HTTPS bật. DNS qua Cloudflare (proxy bật).
2. Đặt `ALBUM_CDN_ORIGIN=https://img.rsrm.dev` và `ALBUM_APP_HOSTS` giữ mặc định (**không** đưa tên miền CDN vào đây). Redeploy.
3. Tên miền CDN đi vào cùng container nhưng bị `HostGate` chặn: chỉ GET/HEAD/OPTIONS ảnh + `/static/` + `/healthz` + `/robots.txt`; `/`, `/manage`, `/api/*`, `/originals/*` đều 404 `no-store`. Nên selfie và upload không bao giờ đi qua CDN (kể cả khi host test cũ có Cloudflare proxy). Ảnh gốc luôn tải cùng origin `https://album.doantien.love/originals/{id}`.
4. **Công tắc tắt:** xoá `ALBUM_CDN_ORIGIN` (hoặc để trống) và redeploy → manifest `cdn:""`, trang tải ảnh từ chính `album.doantien.love/i/…`; không cần dọn gì ở Cloudflare. Khi trang không tải được ảnh từ CDN nó tự thử lại cùng origin.
5. Lỗi (404, sai `v`) không bao giờ cacheable (`no-store`) nên không có gì bị Cloudflare giữ nhầm. Sau khi đổi ảnh gốc, `v` đổi → URL mới, không cần purge.

## 5. Giới hạn bộ nhớ và CPU (BẮT BUỘC)

App mở cho mọi người và chạy chung VPS với dịch vụ khác — **phải** đặt giới hạn để một truy vấn/ảnh xấu không làm cả máy bị OOM. Tab **Advanced → Cluster/Resources**:

- **Memory Limit = 3 GB** (tối thiểu 2.5 GB; đừng đặt thấp hơn)
- **CPU Limit = 2** (lõi)

Các chặn trong app tính theo mức này: ảnh tải lên ≤ 64 MP, selfie ≤ 40 MP và ≤ 2 truy vấn tính toán cùng lúc (3 cái đồng thời → HTTP 503 "đang bận"), tối đa 4 upload ghi đĩa cùng lúc (quá → 503, trình duyệt tự thử lại), Xử lý tuần tự từng ảnh.

Đo thực tế (macOS arm64, CPU, Python 3.9; bản Linux/Python 3.11 chưa đo được vì chưa có Docker — hãy kiểm lại bằng `docker stats` sau khi deploy):

| | RSS |
|---|---|
| App nhàn rỗi | ~0.4–0.5 GB |
| **Đỉnh khi Xử lý** (ảnh JPEG 45 MP 9000×5000 + 24 MP) | **~1.8 GB** (`ALBUM_LOW_MEM=1`; ~2.2 GB nếu tắt) |
| Đỉnh khi Xử lý một ảnh PNG **60 MP** (RGB/RGBA/xám 16-bit; tệ nhất) | ~1.6–1.85 GB; 3 ảnh 60 MP + 45 MP + 24 MP liên tiếp trong một lượt: **2.35 GB** (macOS giữ lại bộ nhớ đã cấp phát; Linux thường trả về sớm hơn) |
| Sau truy vấn selfie | giữ nguyên mức đỉnh (allocator không trả bộ nhớ ngay) |

Đỉnh do ảnh lớn nhất quyết định (ảnh 45 MP: ~135 MB mảng + bộ nhớ trung gian của model dò ở 1600 px; PNG giải mã nặng hơn JPEG cùng số điểm ảnh). Tìm kiếm trong lúc đang Xử lý vẫn chạy, chậm hơn. Nếu bị OOM-kill: tăng giới hạn.

Backfill WebP (giai đoạn `derive`, không dò mặt): đo trên Mac 8 lõi, 1900 ảnh (gồm 7 ảnh 24–45 MP): 2 luồng 0.107 s/ảnh (204 s tổng), 1 luồng 0.19 s/ảnh (361 s); RSS đỉnh +~0.2 GB so với lúc nhàn rỗi. Ảnh JPEG dùng giải mã thu nhỏ nên 45 MP chỉ ~0.3 s.

Tốc độ dò mặt: ~0.8 giây/ảnh ≤ 4000 px; ảnh 24 MP ~1.5 s, 45 MP ~2.5 s (CPU hiện đại, nhiều lõi). 1000 ảnh ≈ 15–25 phút.

## 6. Dung lượng đĩa mỗi ảnh

Đo bằng `measure.py` (xem README). Mỗi ảnh gồm:

| Thành phần | Dung lượng |
|---|---|
| Ảnh gốc | đúng bằng file tải lên (JPEG 24–45 MP thường 8–30 MB) |
| Bản xem 2000 px (JPEG q85) | ~0.5 MB (ảnh mẫu), có thể ~0.3–1 MB |
| Thumbnail 640 px | ~60 KB |
| Chỉ mục (embedding + bbox, `index.db`) | ~30 KB (≈ 1 KB/khuôn mặt, ~15 mặt/ảnh nhóm) |
| Bộ WebP `i/` (240 + 480 + 2000 px, q78) | ~190 KB (đo trên 1900 ảnh thử 1.2–45 MP; ảnh thật nhiều chi tiết có thể 250–400 KB) |
| **Ngoài ảnh gốc** | **~0.8 MB/ảnh** (~1.0–1.2 MB nếu giữ cả JPEG cũ + WebP của ảnh nhiều chi tiết) |

Ví dụ: 1000 ảnh × 15 MB gốc ≈ 15 GB + 0.8 GB — đặt `ALBUM_MAX_TOTAL_GB` ≥ con số này (mặc định 20) và để đĩa còn trống ≥ `ALBUM_MIN_FREE_MB`. Cần thêm chỗ trống cho tệp tạm lúc tải lên (một ảnh một lúc). Trước khi tải ảnh, kiểm tra đĩa của host:

```sh
curl -s https://album.doantien.love/healthz    # {"ok":true,"disk_free":…,"disk_total":…} (byte, của ổ chứa DATA_DIR)
```
Trang `/manage` cũng hiện dung lượng trống.

## 7. Tải ảnh lên qua Traefik

- Trình duyệt gửi **mỗi ảnh một request riêng** (tối đa 3 song song, ghi thẳng ra đĩa, không giữ cả file trong RAM). Một ảnh lỗi/đứt giữa chừng không làm hỏng cả lô — danh sách hiện ảnh nào lỗi; bấm chọn lại những ảnh đó.
- Traefik mặc định không giới hạn kích thước body. Nhưng entrypoint có `readTimeout` (~60 s cho cả request): ảnh 20 MB trên đường truyền chậm (< ~3 Mbit/s upload) có thể bị cắt → ảnh đó báo lỗi, tải lại.
- Cap mỗi ảnh 60 MB (`ALBUM_MAX_UPLOAD_MB`); ảnh quá cỡ bị chặn ngay ở trình duyệt.
- Nếu trước Traefik còn Cloudflare proxy: gói free giới hạn body 100 MB/request — vẫn đủ.

## 7b. Ảnh làm sập / ảnh lỗi

- Trước mỗi lần giải mã app ghi `DATA_DIR/decoding.json`. Nếu container bị OOM-kill/chết giữa lúc xử lý một ảnh, lần khởi động sau (hoặc lần Xử lý kế tiếp) app **chuyển ảnh đó sang `DATA_DIR/rejected/`** và báo trong trạng thái Xử lý — không lặp lại vòng crash. Ảnh có thể là ảnh vô tội đang được giải mã cùng lúc; xem trong `rejected/` rồi tải lại nếu cần.
- Ảnh đọc không được (cụt, hỏng) chỉ báo lỗi một lần; lần Xử lý sau bỏ qua (bảng `failed` trong `index.db`) cho đến khi file đổi.

## 8. Dùng

0. **Lần deploy đầu có WebP:** sau khi deploy, mở `/manage` và **bấm Xử lý một lần**. Giai đoạn `derive` sinh WebP + metadata cho mọi ảnh đã lập chỉ mục từ bản cũ (**không dò mặt lại**, kết quả tìm kiếm không đổi; lỗi một ảnh chỉ ghi log, ảnh không bị chuyển sang `rejected/`). Trong lúc đó và với ảnh chưa có WebP, trang dùng thumbnail JPEG cũ. Cơ sở dữ liệu nâng cấp tại chỗ (`CREATE TABLE IF NOT EXISTS photo_meta`, bảng cũ không đổi) nên quay về image cũ vẫn chạy được. Tiến độ: `GET /api/process` (`phase` = `index`/`derive`, `done`/`total`).
1. Mở `https://album.doantien.love/manage` → kéo thả ảnh gốc vào → đợi "Hoàn tất".
2. Bấm **Xử lý**: lập chỉ mục mọi ảnh chưa xử lý (tăng dần: tải thêm ảnh rồi bấm lại chỉ xử lý ảnh mới). Thanh tiến độ hiện số ảnh/khuôn mặt/lỗi/thời gian. Đóng trình duyệt cũng không dừng job; mở lại trang sẽ thấy tiến độ.
3. Khách vào `https://album.doantien.love/`, tải selfie, kéo thanh ngưỡng, bấm ảnh để xem bản lớn / tải ảnh gốc.

Restart/redeploy container: dữ liệu trong volume còn nguyên, tìm kiếm chạy ngay, không cần Xử lý lại. Job đang chạy mà container bị dừng: bấm Xử lý lần nữa, nó làm tiếp phần chưa xong.

## 9. Sao lưu

Mọi thứ nằm trong volume `album-data` (`/data`: `originals/`, `previews/`, `thumbs/`, `i/`, `index.db`). Ảnh gốc là phần không thể tạo lại; còn lại sinh lại được bằng cách xoá `index.db`, `previews/`, `thumbs/`, `i/` rồi bấm Xử lý.

- Dokploy → **Volume Backups** (nếu bản của bạn có) → đích S3, lịch hằng đêm; hoặc trên VPS:
  `docker run --rm -v album-data:/data -v $PWD:/backup alpine tar czf /backup/album-$(date +%F).tgz -C /data .`
- Sao `index.db` khi không có job đang chạy (hoặc chấp nhận sao lại lần sau).

## 10. Kiểm tra sau deploy

1. Logs không có `LỖI:`; `curl https://album.doantien.love/healthz` trả `ok:true`.
2. `/robots.txt` có `Disallow: /`, response có `X-Robots-Tag: noindex, nofollow`.
3. `curl -sI https://img.rsrm.dev/healthz` trả 200 và `curl -s -o /dev/null -w '%{http_code}' https://img.rsrm.dev/api/stats` trả **404** (host CDN không thấy API); `curl -sI https://<host CDN>/i/<id>-<v>-480.webp` có `content-type: image/webp` + `immutable`.
4. Tải 3–5 ảnh, bấm Xử lý, tìm bằng selfie, bấm một kết quả mở được ảnh.
5. Redeploy một lần, kiểm tra ảnh/chỉ mục còn nguyên. `docker stats` xem RSS khi Xử lý (so với mục 5).
5. Đừng redeploy khi đang Xử lý (`GET /api/process` có `state:"running"`): redeploy làm gián đoạn lượt đó (ảnh đang xử lý được thử lại, chỉ bị loại nếu chết hai lần liền).
