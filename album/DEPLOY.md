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
| `ALBUM_THREADS` | `2` | Số luồng onnxruntime của **tìm kiếm** của khách (image đặt sẵn 2 để không chiếm hết CPU của VPS dùng chung) |
| `ALBUM_PROCESS_THREADS` | lõi CPU khả dụng − 2 (tối thiểu 1; đọc hạn mức cgroup, không phải số lõi của máy chủ) | Số luồng onnxruntime của **Xử lý**, trong một model riêng (tạo khi bắt đầu lượt, bỏ khi xong; đo trên macOS đỉnh RSS cao hơn ~0.8 GB so với dùng chung model, hãy giữ Memory limit ≥ 4 GiB khi dùng model riêng). `0` = dùng chung model với tìm kiếm (tiết kiệm RAM nhưng tranh nhau CPU) |
| `ALBUM_MAX_RESULTS` | `5000` | Số kết quả tối đa mỗi lượt tìm bằng selfie (phủ cả album) |
| `ALBUM_INDEX_WINDOW` | `1` | Số ảnh giải mã cùng lúc khi Xử lý. `1` = tuần tự (RAM đỉnh thấp nhất); `2` = giải mã trước một ảnh, nhanh hơn ~10–20% nhưng đỉnh RAM thêm ~0.5–1 GB với ảnh 60 MP |
| `ALBUM_APP_HOSTS` | `album.doantien.love,localhost,127.0.0.1` | Danh sách host (cách nhau dấu phẩy) được dùng **toàn bộ app**. Host khác trỏ vào cùng container (tên miền CDN, mục 4b) chỉ lấy được ảnh + file tĩnh + `/healthz` + `/robots.txt`; mọi thứ khác 404 |
| `ALBUM_CDN_ORIGIN` | trống | Origin CDN cho ảnh WebP, vd. `https://img.rsrm.dev` (không có đường dẫn). Điền vào `cdn` của `/api/gallery` + `<link rel=preconnect>`. **Trống = ảnh cùng origin = công tắc tắt CDN** (xem 4b) |
| `ALBUM_ADMIN_TOKEN` | trống | Đặt thì: `GET /manage` không có cookie phiên `album_session` hợp lệ → 401 + trang đăng nhập (`static/manage-login.html`); `POST /api/admin-login {"token"}` đúng → cookie `album_session` HttpOnly, SameSite=Strict, 30 ngày (thêm Secure khi https/`X-Forwarded-Proto`). Cookie là HMAC-SHA256 của nhãn cố định, khoá bằng mã + một bí mật ngẫu nhiên 32 byte của server lưu ở `DATA_DIR/.session_secret` (0600, tạo một lần; không route nào phát ra `DATA_DIR`) — **không chứa mã**, không dò ngược offline được, vẫn hợp lệ sau restart (đổi mã hoặc xoá file bí mật thì cookie cũ hết hiệu lực; chủ nhà chỉ cần đăng nhập lại). **Cookie phiên đúng được miễn mọi ngưỡng** (nó là HMAC 256-bit, không phải đoán mò) nên kẻ dò mã không thể khoá chủ nhà đang đăng nhập; cookie sai vẫn bị tính. `POST /api/upload` và `POST /api/process` nhận cookie phiên hoặc header `X-Album-Token: <mã>`, sai → 401. **Mọi thông tin xác thực chưa chứng minh được** (đăng nhập, header, cookie phiên sai) dùng chung một bộ giới hạn: tối đa 10 lượt/phút/IP và 30 lượt/phút toàn hệ thống (vượt → 429, toàn cục thì khoá cả hệ thống 60 s); chỉ tính request ĐƯA thông tin xác thực (đúng thì hoàn lại), nên khách vào `/manage` không cookie không tính. `GET /api/process` và `/api/stats` giờ cũng chỉ chủ nhà (401); trang khách và `/api/gallery` vẫn mở. Trống = mở như trước. Đây là bí mật: đặt trong tab Environment, đừng commit; app không bao giờ in nó ra response, log hay cookie |
| `ALBUM_DERIVE_WORKERS` | `2` | Số ảnh sinh WebP cùng lúc khi Xử lý (mỗi ảnh JPEG chỉ giải mã thu nhỏ nên nhẹ RAM) |
| `ALBUM_LOW_MEM` | `1` | Tắt arena cấp phát của onnxruntime (RSS đỉnh thấp hơn ~20%, tốc độ như nhau) |

### Cấu hình khuyến nghị cho server thật (CPU limit 6, Memory limit 4 GiB)

```
ALBUM_THREADS=2            # tìm kiếm của khách: luôn còn ~2 lõi
ALBUM_PROCESS_THREADS=4    # Xử lý (mặc định đã là 6 − 2); tổng luồng ORT = 6 = hạn mức, chừa chỗ cho giải mã/WebP
ALBUM_DERIVE_WORKERS=2
ALBUM_INDEX_WINDOW=1
```
**Đừng** đặt `ALBUM_THREADS=6` với CPU limit 6: các luồng onnxruntime (busy-wait) của tìm kiếm + Xử lý cùng giải mã/WebP vượt hạn mức → cgroup throttle cả process từng đợt (tìm kiếm chậm, health check có thể trễ). `/healthz` và `GET /api/process` chạy thẳng trên event loop với số liệu đã nhớ (không khoá, không đĩa); `/api/stats` và `/api/gallery` trả giá trị đã nhớ khi đang Xử lý. HEALTHCHECK của image: timeout 20 s.

**Chế độ journal SQLite:** `index.db` chuyển sang WAL ở lần khởi động đầu (bên cạnh có `index.db-wal`/`-shm`; bản image cũ vẫn mở được, nên quay lui an toàn). Người đọc không chờ người ghi. Sao lưu: dùng `sqlite3 index.db ".backup out.db"`, hoặc sao chép **cả ba** file `index.db`, `index.db-wal` và `index.db-shm` (chỉ chép `index.db` sẽ mất các commit gần nhất).

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
   **Cổng này dựa vào header `Host`.** Nên router của tên miền CDN trong Dokploy/Traefik chỉ định tuyến các đường `/i/`, `/static/`, `/thumbs/`, `/previews/` (thêm `/healthz` nếu cần), và **không ai được thêm quy tắc ghi đè header Host** (Cloudflare Transform/Origin Rule "Host Header Override") cho tên miền CDN: ghi đè thành `album.doantien.love` sẽ biến host CDN thành host đầy đủ. `/static/*.html` luôn 404 ở mọi host (HTML chỉ phát qua `/` và `/manage`).
4. **Công tắc tắt:** xoá `ALBUM_CDN_ORIGIN` (hoặc để trống) và redeploy → manifest `cdn:""`, trang tải ảnh từ chính `album.doantien.love/i/…`; không cần dọn gì ở Cloudflare. Khi trang không tải được ảnh từ CDN nó tự thử lại cùng origin.
5. Lỗi (404, sai `v`) không bao giờ cacheable (`no-store`) nên không có gì bị Cloudflare giữ nhầm. Sau khi đổi ảnh gốc, `v` đổi → URL mới, không cần purge.

## 5. Giới hạn bộ nhớ và CPU (BẮT BUỘC)

App mở cho mọi người và chạy chung VPS với dịch vụ khác — **phải** đặt giới hạn để một truy vấn/ảnh xấu không làm cả máy bị OOM. Tab **Advanced → Cluster/Resources**:

- **Memory Limit = 4 GiB** khi Xử lý dùng model riêng (mặc định; đỉnh cao hơn ~0.8 GB so với dùng chung, đo trên macOS). Với `ALBUM_PROCESS_THREADS=0` (dùng chung model) 3 GB là đủ (tối thiểu 2.5 GB)
- **CPU Limit = 6** (lõi) với server thật hiện nay (cấu hình luồng khuyến nghị ở mục 2: `ALBUM_THREADS=2`, `ALBUM_PROCESS_THREADS=4`). Nếu chỉ cấp 2 lõi: `ALBUM_THREADS=1`, `ALBUM_PROCESS_THREADS=1`

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
- Sao lưu `index.db` bằng `sqlite3 index.db ".backup index-backup.db"` (an toàn cả khi có job chạy), hoặc chép `index.db` + `index.db-wal` + `index.db-shm` cùng lúc; chỉ chép `index.db` là thiếu các commit chưa checkpoint.

## 9b. Ẩn/hiện ảnh: sao lưu và rollback

- Trạng thái ẩn nằm trong `index.db` (bảng `photo_hidden`, `settings`; tạo tự động khi khởi động, DB cũ nâng cấp tại chỗ). **Trước lần deploy đầu có tính năng này, sao lưu DB:** `sqlite3 /data/index.db ".backup /data/index-pre-hidden.db"` (an toàn khi có job chạy). Cũng nên sao lưu sau mỗi đợt ẩn/hiện lớn.
- **Rollback về image cũ: ảnh đã ẩn HIỆN LẠI với khách** (code cũ không biết `photo_hidden`; bảng thừa bị bỏ qua, DB vẫn chạy được). Đã ẩn ảnh riêng tư thì đừng rollback; nếu buộc phải, tạm đóng tên miền công khai trước.
- `/api/admin/*` cần `ALBUM_ADMIN_TOKEN`; không đặt thì trả 403 (đóng, không thể ẩn/hiện). `GET /api/stats` và `GET /api/process` không đặt mã thì vẫn mở như trước. Cần thêm file bí mật `DATA_DIR/.image_secret` (tự tạo, 0600, nằm trong volume): xoá nó chỉ làm token ảnh đang lưu trong trang khách hết hiệu lực.
- Đổi tên file ảnh = id mới = ảnh hiện (dòng ẩn của id cũ thành mồ côi, vô hại). Proxy phải giữ nguyên header `Host` (kiểm `Origin` của POST chủ nhà so với `Host`).

### Giới hạn của việc ẩn (đọc kỹ)

- **Ẩn không thu hồi URL `/i/{id}-{v}-{size}.webp` mà ai đó đã có**: các URL này bất biến, CDN (Cloudflare) cache tới một năm, và ảnh ẩn vẫn được phục vụ ở `/i/` (v là mã nội dung, không đoán được nhưng chỉ **24 bit** — đừng coi là bí mật mạnh). Với ảnh nhạy cảm, sau khi ẩn hãy **purge cache Cloudflare** (theo URL hoặc purge everything).
- `/thumbs` và `/previews` cache công khai `max-age=300`: ảnh vừa ẩn còn nằm ở CDN/trình duyệt tối đa khoảng 5 phút. `/originals` không qua CDN.
- Token ảnh ẩn (`?t=`) sống tối đa 24 h và bị vô hiệu khi ẩn/ẩn lại ảnh đó hoặc khi đổi cài đặt tìm kiếm.

### `--rebuild`

**Dừng app trước khi chạy `index_photos.py --rebuild`** (đang chạy thì web có thể ghi vào DB cũ giữa chừng). Lệnh sao `index.db` (+`-wal`/`-shm`) sang `index.db.bak-<ts>`, đọc ảnh đã ẩn + cài đặt từ bản sao rồi mới xoá DB cũ và chép trạng thái sang DB mới. DB hỏng/không đọc được → dừng với thông báo trỏ tới bản sao (chưa xoá gì); thêm `--discard-owner-state` để vẫn làm tiếp và bỏ trạng thái ẩn (ảnh đã ẩn sẽ HIỆN LẠI). Bản sao không tự xoá: dọn tay khi đã kiểm tra.

## 9c. Hai ngày (4/10 và 17/10): lên production

- **Không cần migrate dữ liệu, không lập chỉ mục lại, không mất trạng thái chủ nhà** (`photo_hidden`, `settings`): bảng `photo_event` tạo tự động khi khởi động (`CREATE TABLE IF NOT EXISTS`); vắng dòng = 4/10 nên mọi ảnh đang có là 4/10.
- Quy trình: (1) sao lưu DB gồm cả `-wal`/`-shm` nếu có: `sqlite3 /data/index.db ".backup /data/index-pre-events.db"` (hoặc chép cả ba file khi tắt app); (2) deploy; (3) vào /manage, ở mục "Tải ảnh gốc lên" chọn **Ngày 17/10** rồi tải cả đợt ảnh 17/10, bấm **Xử lý**. Trước khi có ảnh 17/10 công khai, trang khách không hiện tab/phạm vi tìm (trông y như cũ).
- Ảnh trùng SHA-256 với ảnh đã có sẽ **ở nguyên ngày hiện tại** (báo "giữ nguyên ngày …" trong danh sách tải lên). Nếu lỡ tải nhầm ngày, chọn ảnh ở mục Ẩn / hiện và bấm "Chuyển sang ngày …" (hàng loạt, hoàn tác được).
- Rollback về image cũ an toàn: code cũ bỏ qua bảng `photo_event` và hàng manifest cũ không có trường ngày; ảnh 17/10 sẽ lẫn vào thư viện cũ như ảnh thường (không mất ảnh nào). Mở lại image mới thì ngày vẫn còn.
- `--rebuild` chép `photo_event` (cùng `photo_hidden`, `settings`) từ bản sao `index.db.bak-<ts>`.
- Đợt 17/10 ≈ 1637 ảnh × ~8 MB ≈ 13 GB: tải lên theo từng lô (vài trăm ảnh mỗi lần chọn) và để tab mở tới khi "Hoàn tất"; sau khi Xử lý xong, lưới /manage tự làm mới (không cần tải lại trang). Diễn tập trước: `album/dev/realrun/`.
- Sau deploy: `GET /api/gallery` có `events` và mỗi hàng có 9 phần tử; chưa tải ảnh 17/10 thì `events[1].n` = 0.

## 9d. Video (chủ nhà tải lên MP4 đã xử lý sẵn)

Chỉ thêm, không đổi env/cấu hình/hạ tầng. Hằng số nằm trong code (`server.py`): `VIDEO_MAX_BYTES` 2 GiB mỗi file, `VIDEO_CHUNK_MAX` 16 MiB mỗi request (client mặc định 8 MiB, có thể giảm dần tới 1 MiB khi timeout), `POSTER_MAX_BYTES` 2 MiB và poster ≤ 4096 px mỗi cạnh, `MAX_VIDEO_SESSIONS` 2 phiên chưa xong cùng lúc, phiên hết hạn sau 24 giờ.

**Endpoint (đều cần chủ nhà + kiểm Origin/Host như các POST khác; JSON ≤ 4 KB):**
- `POST /api/admin/video/init` `{name (.mp4), size, sha256, ev (4/10, 17/10 hoặc null = không thuộc ngày nào), title ≤ 120}` → `{uid, offset, size, chunk_max, expires}`. Trùng sha256 đã công bố → 409 `{error:"duplicate", id}`; init lại cùng sha256 khi phiên chưa xong → trả lại phiên đó (nối lại); quá 2 phiên → 429; thiếu chỗ đĩa (giữ ngưỡng `ALBUM_MIN_FREE_MB`) → 507. Phần byte các phiên đang tải còn phải nhận được giữ chỗ: tải ảnh, chunk video và poster đều chừa chỗ đó khi kiểm ngưỡng.
- `GET /api/admin/video/{uid}` → `{uid, offset, size, poster, state: uploading|ready|published, id?, expires}`.
- `PUT /api/admin/video/{uid}/chunk?off=N` (thân thô ≤ 16 MiB): chỉ nhận khi `N` = offset đã ghi nhận, ngược lại 409 `{offset}`; ghi + fsync rồi mới tăng offset, thân ngắt giữa chừng không để lại byte nào.
- `PUT /api/admin/video/{uid}/poster` (JPEG thô ≤ 2 MiB, được giải mã kiểm tra; PUT lại = thay).
- `POST /api/admin/video/{uid}/finish`: kiểm sha256 toàn file + duyệt box MP4 (moov trước mdat, đúng một track H.264 avc1/avc3, tối đa một track mp4a, mọi mẫu trong `stsd` phải đúng codec, ma trận `tkhd` chỉ nhận đơn vị hoặc xoay đúng 90/180/270 độ; không đọc mdat). Sai → 422 kèm `reason` và phiên bị xoá. Đúng → công bố, idempotent: `{id, v, pv, w, h, duration, title, ev}`. Công bố được tuần tự hoá toàn cục (kiểm trùng → ghi trạng thái `publishing` bền → di chuyển → INSERT) và chịu được chết giữa chừng: khi khởi động, phiên `publishing` (hoặc sidecar cũ mà file đích đã đủ và đúng hash) được hoàn tất hoặc đánh dấu `published`; file trong `videos/` không bao giờ bị xoá lúc khởi động, rollback chỉ trả lại những file chính lần finish đó vừa di chuyển.
- `POST /api/admin/video/{id}/event` `{"ev": "2026-10-04" | "2026-10-17" | null}` (≤ 4 KB, JSON): đổi ngày của video ĐÃ công bố (chỉ cột `ev`, giữ `sort`; idempotent; id không có → 404, ev sai → 400); `/api/gallery` đổi ETag/`mv` ngay. Không sửa trường nào khác. Ví dụ (đặt token vào biến môi trường, đừng dán mã thật vào lệnh lưu lịch sử):
  ```bash
  curl -sS -X POST "https://album.doantien.love/api/admin/video/<id16hex>/event" \
    -H "X-Album-Token: $ALBUM_ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"ev": null}'
  ```
- Chưa có xoá/ẩn video trong bản này.

**Lưu trữ:** `DATA_DIR/video-staging/` (phiên dở dang: `{uid}.part/.poster/.json`; KHÔNG phải `tmp/` nên sống qua restart; phiên hết hạn bị dọn khi khởi động và ở MỖI request video của chủ nhà — bỏ qua phiên đang bận, không bao giờ dọn phiên `publishing`; poster được thay nguyên tử cùng hash (`{uid}.poster.new` + sidecar) và đối chiếu lại khi khởi động), `DATA_DIR/videos/{id}-{v}.mp4`, `DATA_DIR/videos/posters/{id}-{pv}.jpg`, bảng SQLite `videos`. Video không tính vào hạn mức ảnh `ALBUM_MAX_TOTAL_GB`; staging + video bị chặn bởi ngưỡng đĩa trống và 2 GiB × 2 phiên. `index_photos.py --rebuild` giữ nguyên các dòng `videos` (file nằm ngoài những gì rebuild xoá). `--rebuild --discard-owner-state` thì bỏ cả các dòng `videos` (file vẫn còn trên đĩa).

**Phục vụ:** `GET|HEAD /v/{id}-{v}.mp4` (video/mp4) và `/v/{id}-{pv}.jpg`, có Range tự xử lý (một khoảng `a-b`, `a-`, `-n`: 206; `-n` với n ≥ kích thước → 206 cả file; cú pháp sai, `start > end` hoặc nhiều khoảng → bỏ Range, 200 đủ, không bao giờ multipart; `start` ≥ kích thước → 416 `Content-Range: bytes */N`; `If-Range` khác ETag/Last-Modified → 200 đủ; HEAD khớp GET; đọc file từng khối 256 KiB; `Accept-Ranges: bytes`), `Cache-Control: public, max-age=31536000, immutable`, không nén. Query hoặc sai tên/phiên bản → 404 no-store. `/v/` KHÔNG nằm trong `PUBLIC_PREFIXES`: chỉ host của app (`ALBUM_APP_HOSTS`) phục vụ, host CDN trả 404 — router CDN không được thêm `/v/` (đó là thay đổi Traefik).

**Cloudflare:** video đi qua tên miền chính đã proxy qua Cloudflare. Điều khoản của gói Cloudflare về phát video qua CDN cần chủ nhà xác nhận trước khi bật cho khách; mỗi request tải lên ≤ 16 MiB nên nằm dưới giới hạn ~100 MB/request và timeout ~60 s của Traefik.

**Rollback = additive:** chỉ có bảng mới `videos` (`CREATE TABLE IF NOT EXISTS`, không ALTER bảng cũ), thư mục mới và route mới. Quay về bản trước: bản cũ bỏ qua bảng/thư mục, `/api/gallery` cũ không có khoá `videos`. Không cần xoá gì.

## 10. Kiểm tra sau deploy

1. Logs không có `LỖI:`; `curl https://album.doantien.love/healthz` trả `ok:true`.
2. `/robots.txt` có `Disallow: /`, response có `X-Robots-Tag: noindex, nofollow`.
3. `curl -sI https://img.rsrm.dev/healthz` trả 200 và `curl -s -o /dev/null -w '%{http_code}' https://img.rsrm.dev/api/stats` trả **404** (host CDN không thấy API); `curl -sI https://<host CDN>/i/<id>-<v>-480.webp` có `content-type: image/webp` + `immutable`.
4. Tải 3–5 ảnh, bấm Xử lý, tìm bằng selfie, bấm một kết quả mở được ảnh.
5. Redeploy một lần, kiểm tra ảnh/chỉ mục còn nguyên. `docker stats` xem RSS khi Xử lý (so với mục 5).
5. Đừng redeploy khi đang Xử lý (`GET /api/process` có `state:"running"`): redeploy làm gián đoạn lượt đó (ảnh đang xử lý được thử lại, chỉ bị loại nếu chết hai lần liền).
