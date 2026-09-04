# quanly — quản lý khách mời

App Node độc lập, không có dependency ngoài. Chạy song song với app thiệp trên
cùng Dokploy.

## Nó phục vụ những gì

| URL | Ai vào được | Việc |
|---|---|---|
| `quanly.doantien.love/` | cần mật khẩu | UI quản lý khách + xem RSVP |
| `doantien.love/t/<mã>` | công khai | Thiệp riêng, **render tại server** từ danh sách khách |
| `doantien.love/api/rsvp` | công khai (POST) | Nhận phản hồi RSVP từ trang thiệp |

Ba URL trên do Traefik phân tới cùng một container. `doantien.love` còn lại
vẫn do app thiệp (nginx) phục vụ.

## Vì sao render tại server

Crawler của Zalo/Messenger/Facebook không chạy JS. Muốn preview khi share hiện
đúng tên người nhận thì tên phải nằm sẵn trong HTML. Render tại server đạt được
điều đó **và** thêm khách là link sống ngay, không cần deploy lại.

## Số lượng khách mời

Mỗi khách có trường **Số lượng** (`seats`, 1–20, mặc định 1) = số chỗ mời cho
khách đó.

- Cột "Số lượng" trong bảng danh sách, sửa được qua nút **Sửa**.
- Thẻ thống kê **Tổng chỗ mời** cộng dồn cả danh sách, để ước lượng số mâm.
- Thiệp riêng nhúng số này vào `<body data-guest-seats>`; form xác nhận **tự
  chọn sẵn** đúng số người, khách chỉ việc bấm gửi.
- Ô chọn trên thiệp vốn chỉ có 1–4. Khách được mời đông hơn thì thiệp tự sinh
  thêm lựa chọn cho đủ, chứ không im lặng cắt xuống 4.
- "Thêm nhiều cùng lúc" luôn đặt 1 — sửa lại từng khách sau. Không đọc số từ
  dòng nhập vì tên có thể chứa dấu phẩy, tách nhầm là hỏng tên.

## Dữ liệu

Một file JSON ở `/data/guests.json`, nằm trên Docker volume `quanly-data`.
Ghi kiểu atomic (ghi file tạm rồi `rename`) nên không vỡ file khi đang ghi.

- Lần chạy đầu, nếu `/data/guests.json` chưa có, app nạp danh sách từ
  `guests.csv` trong repo. Sau đó **`guests.csv` không còn được đọc nữa** —
  nguồn dữ liệu thật là volume.
- Đã kiểm chứng dữ liệu sống sót qua redeploy.
- Xoá volume trong Dokploy = mất toàn bộ danh sách khách và RSVP. Nên bấm
  "Tải CSV" trong UI định kỳ để có bản sao.

## Biến môi trường (đặt trong Dokploy, không commit)

| Biến | Ghi chú |
|---|---|
| `ADMIN_PASSWORD_HASH` | dạng `salt:hash` scrypt, sinh bằng lệnh dưới |
| `SESSION_SECRET` | chuỗi ngẫu nhiên, dùng ký cookie phiên |
| `SITE_ORIGIN` | `https://doantien.love` |
| `DATA_DIR` | `/data` |
| `PORT` | `3000` |

### Đổi mật khẩu

```bash
node admin/hash-password.js 'mật khẩu mới'
```

Dán kết quả vào Dokploy → app `quanly` → Environment → `ADMIN_PASSWORD_HASH`,
rồi Deploy. Đổi `SESSION_SECRET` cùng lúc sẽ đá mọi phiên đang đăng nhập ra.

## Bảo mật đã làm

- Mật khẩu băm scrypt, so sánh bằng `timingSafeEqual`.
- Phiên đăng nhập ký HMAC, cookie `HttpOnly` + `Secure` + `SameSite=Lax`, hạn 12 giờ.
- Chặn dò mật khẩu: 8 lần sai mỗi IP trong 15 phút (đếm trong RAM, restart là reset).
- Chặn path traversal khi phục vụ file tĩnh.
- Giới hạn body 64 KB, giới hạn độ dài mọi trường nhập.
- `noindex, nofollow` toàn bộ UI quản trị và thiệp riêng.
- CORS của `/api/rsvp` chỉ mở cho `SITE_ORIGIN`.

**Chưa làm:** không có nhật ký đăng nhập, không có 2FA, rate limit mất khi
restart. Với một trang cưới thì chấp nhận được, nhưng đừng dùng lại mật khẩu
này ở chỗ khác.

## Build

Dockerfile nằm ở `admin/Dockerfile` nhưng **build context phải là gốc repo**
(Dokploy: `dockerContextPath = "."`), vì nó cần `index.html` ở gốc làm template
render thiệp. Để trống context thì Dokploy lấy thư mục chứa Dockerfile và build
sẽ hỏng.

## Chạy thử ở máy

```bash
docker build -f admin/Dockerfile -t quanly .
docker run --rm -p 3000:3000 \
  -e ADMIN_PASSWORD_HASH="$(node admin/hash-password.js 'test123')" \
  -e SESSION_SECRET="$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")" \
  -e SITE_ORIGIN="http://127.0.0.1:8080" \
  -v quanly-dev:/data quanly
```
