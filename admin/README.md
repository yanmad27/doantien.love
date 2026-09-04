# quanly — quản lý khách mời

App Node độc lập, không có dependency ngoài. Chạy song song với app thiệp trên
cùng Dokploy.

## Nó phục vụ những gì

| URL | Ai vào được | Việc |
|---|---|---|
| `quanly.doantien.love/` | cần mật khẩu | UI quản lý khách + xem RSVP + sổ lời chúc |
| `quanly.doantien.love/so-loi-chuc` | cần mật khẩu | Trang in sổ lời chúc → Lưu thành PDF |
| `doantien.love/t/<mã>` | công khai | Thiệp riêng, **render tại server** từ danh sách khách |
| `doantien.love/api/rsvp` | công khai (POST) | Nhận phản hồi RSVP từ trang thiệp |
| `doantien.love/api/wishes` | công khai (GET/POST) | Đọc và ghi sổ lời chúc |
| `doantien.love/api/sepay-webhook` | SePay (POST, có chữ ký) | Nhận báo tiền mừng cưới về |
| `doantien.love/api/gift-status` | công khai (GET) | Thiệp hỏi "tiền của khách này tới chưa" |

Các URL trên do Traefik phân tới cùng một container. `doantien.love` còn lại
vẫn do app thiệp (nginx) phục vụ.

> **Khi deploy lần đầu có sổ lời chúc:** kiểm tra luật Traefik cho `doantien.love`
> đang trỏ theo **tiền tố `/api`** chứ không phải đúng đường dẫn `/api/rsvp`.
> Nếu nó ghim vào `/api/rsvp` thì `/api/wishes` sẽ rơi xuống nginx và nhận 503 —
> sổ lời chúc im lặng không hoạt động trong khi RSVP vẫn chạy bình thường, rất
> khó đoán ra. Kiểm nhanh: `curl https://doantien.love/api/wishes` phải trả
> `{"wishes":[...]}`, không phải `{"error":"Dịch vụ nhận phản hồi..."}`.

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

## Sổ lời chúc

Khách viết vài dòng ở cuối thiệp, mẩu giấy hiện lên tường **ngay**, không duyệt
trước — thấy mẩu của mình xuất hiện chính là phần thưởng khiến người ta chịu
viết. Đổi lại, chỗ gỡ bài phá nằm ở đây:

- **Ẩn** gỡ mẩu khỏi thiệp và khỏi bản in, nhưng vẫn giữ nội dung để đọc lại.
- **Xoá** mới mất hẳn.
- Server tự chặn spam ở mức **4 mẩu mỗi IP trong 10 phút** (đếm trong RAM,
  restart là reset — giống phần chặn dò mật khẩu).

**In thành PDF:** bấm *In / Lưu PDF* trong thẻ "Sổ lời chúc", hoặc mở thẳng
`/so-loi-chuc`, rồi Ctrl/Cmd+P → chọn "Lưu thành PDF". Trang đó dùng đúng nét
chữ, màu giấy và độ nghiêng của tường trên thiệp nên quyển in ra giống hệt cái
khách đã thấy. Mẩu đang ẩn không vào bản in.

Nét chữ / màu giấy / độ nghiêng suy từ một `seed` lưu kèm mỗi mẩu, tính ở
`wishStyle()` trong `server.js`. Đổi bảng nét chữ thì phải đổi **cả hai** nơi —
`PRINT_HANDS` ở đây và `.wish--f*` / `.wish--p*` trong `styles.css` — nếu không
bản in sẽ lệch với bản trên thiệp.

## Dữ liệu

Một file JSON ở `/data/guests.json`, nằm trên Docker volume `quanly-data`.
Ghi kiểu atomic (ghi file tạm rồi `rename`) nên không vỡ file khi đang ghi.

Chứa ba mảng: `guests`, `rsvps`, `wishes`.

- Lần chạy đầu, nếu `/data/guests.json` chưa có, app nạp danh sách từ
  `guests.csv` trong repo. Sau đó **`guests.csv` không còn được đọc nữa** —
  nguồn dữ liệu thật là volume.
- File cũ chưa có `wishes` vẫn nạp được: app tự thêm mảng rỗng khi đọc.
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
| `SEPAY_WEBHOOK_SECRET` | Secret Key của webhook SePay. **Thiếu thì `/api/sepay-webhook` trả 503** thay vì nhận dữ liệu chưa xác thực |
| `SEPAY_ACCOUNT` | mặc định `03009274502` |
| `SEPAY_BANK` | mặc định `TPBank` |
| `GIFT_CODE_PREFIX` | mặc định `MC`, phải khớp cấu hình trên my.sepay.vn |

### Đổi mật khẩu

```bash
node admin/hash-password.js 'mật khẩu mới'
```

Dán kết quả vào Dokploy → app `quanly` → Environment → `ADMIN_PASSWORD_HASH`,
rồi Deploy. Đổi `SESSION_SECRET` cùng lúc sẽ đá mọi phiên đang đăng nhập ra.

## Mừng cưới qua SePay

Khách quét QR trên thiệp riêng và chuyển khoản → SePay bắn webhook về đây → lần
hỏi kế tiếp của thiệp thấy đã nhận → **đúng trang của người vừa chuyển** hiện lời
cảm ơn. Số tiền chỉ hiện trong UI quản trị, không bao giờ ra ngoài trang thiệp.

Biết được ai vừa chuyển là nhờ QR của mỗi khách mang sẵn mã riêng trong nội dung
chuyển khoản: `MC` + mã khách (ví dụ `MCab3k9z`). Ảnh QR lấy từ
`qr.sepay.vn/img` nên app không cần thư viện sinh QR.

### Cần bấm gì trên my.sepay.vn

1. Kết nối tài khoản TPBank `03009274502` (PHAN NGUYEN KHAC DOAN).
2. **Cấu hình Công ty → Cấu hình chung → Cấu trúc mã thanh toán**: tiền tố `MC`,
   hậu tố **6–6**, loại **Số và chữ**. (Mã khách dài đúng 6 ký tự.)
3. **Tạo webhook**: URL `https://doantien.love/api/sepay-webhook`, xác thực
   **HMAC-SHA256**. Secret Key chỉ hiện đầy đủ một lần — chép ngay vào
   `SEPAY_WEBHOOK_SECRET` bên Dokploy.
4. Làm ở **Test mode** trước, chạy đúng rồi mới sao cấu hình sang Live.

Bước 2 mà quên thì vẫn chạy: SePay để `code` rỗng, app tự dò `MC<mã>` trong nội
dung chuyển khoản.

### Những chỗ dễ hỏng

- **Đồng hồ máy chủ lệch quá 5 phút** → mọi webhook bị từ chối 401. Phải bật NTP.
- SePay trả mã **viết hoa** (`MCAB3K9Z`), mã khách lưu **viết thường** — app đã
  hạ chữ trước khi so, đừng bỏ bước đó đi.
- Chữ ký ký trên **bytes thô**. Parse JSON rồi stringify lại là chữ ký hỏng ngay.
- Webhook phải trả **đúng** `{"success": true}` với status 200; SePay coi mọi đáp
  án khác là thất bại và gửi lại.
- Giao dịch trùng được bỏ qua theo `id` — SePay giữ nguyên `id` qua mọi lần
  retry và mọi lần bấm gửi lại tay.
- `qr.sepay.vn` chết thì thiệp tự rơi về QR tĩnh trong repo: vẫn chuyển khoản
  được, chỉ mất phần tự nhận ra ai gửi nên không có animation.

## Bảo mật đã làm

- Mật khẩu băm scrypt, so sánh bằng `timingSafeEqual`.
- Phiên đăng nhập ký HMAC, cookie `HttpOnly` + `Secure` + `SameSite=Lax`, hạn 12 giờ.
- Chặn dò mật khẩu: 8 lần sai mỗi IP trong 15 phút (đếm trong RAM, restart là reset).
- Chặn path traversal khi phục vụ file tĩnh.
- Giới hạn body 64 KB, giới hạn độ dài mọi trường nhập.
- `noindex, nofollow` toàn bộ UI quản trị và thiệp riêng.
- CORS của `/api/rsvp` và `/api/gift-status` chỉ mở cho `SITE_ORIGIN`.
- Webhook SePay xác thực HMAC-SHA256 trên bytes thô, so bằng `timingSafeEqual`,
  và từ chối request lệch giờ quá 5 phút (chống replay).
- `/api/gift-status` chỉ trả có/chưa, không kèm số tiền, và bị giới hạn 120
  lượt hỏi mỗi IP trong 5 phút để không quét được cả bảng mã khách.

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
