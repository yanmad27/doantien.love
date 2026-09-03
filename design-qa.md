# Design QA — Thiệp cưới Khắc Doãn & Đông Tiên

Đối chiếu bản web với `HANDOFF.md` và thiệp cứng.

- **Ngày QA:** 03.09.2026
- **Build:** `index.html` + `styles.css` + `script.js` + `assets/floral-*.webp`
- **Công cụ:** Chrome 152 headless (CDP), axe-core 4.10.2
- **Viewport đã chạy:** 320 / 390 / 430 / 768 / 1024 / 1440 px
- **Trạng thái:** đã đóng (cover) và đã mở thiệp

---

## 1. Kết quả acceptance criteria (HANDOFF §12)

| # | Tiêu chí | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Bám palette & phong cách thiệp cứng | ✅ | Ivory `#fffaf3`, viền coral, hoa watercolor giữ nguyên asset gốc |
| 2 | Cover card tỷ lệ 12:17, tốt từ 320px | ✅ | Đo được `343×486` @390px → ratio `0.7059` (đích `0.7059`) |
| 3 | `Mở thiệp` chạy animation, mở khóa scroll, hiện nội dung | ✅ | `body.is-closed` → `opened`, `html.is-locked` được gỡ |
| 4 | Countdown đúng `Asia/Ho_Chi_Minh` | ✅ | Target parse từ `2026-10-04T11:30:00+07:00`, không phụ thuộc TZ máy khách |
| 5 | Tháng 10/2026 đúng, ngày 04 là Chủ nhật | ✅ | 4 ô trống đầu → 01/10 rơi vào thứ Năm; ngày 4 nằm cột CN. `Intl` cũng trả `Chủ nhật` |
| 6 | Nút Google Maps mở đúng địa điểm | ⚠️ | Cơ chế chạy đúng (`target=_blank`, `rel="noopener noreferrer"`), **URL còn là query tạm** — xem §4 |
| 7 | Nút lưu ngày cưới tải `.ics` hợp lệ | ✅ | Xem §3.9 |
| 8 | RSVP validate trường bắt buộc | ✅ | Submit rỗng → chặn, hiện 2 thông báo lỗi, focus về ô đầu tiên |
| 9 | RSVP có loading / success / error | ✅ | Đã có đủ 3 state; chạy demo khi `rsvp.endpoint = null` |
| 10 | Animation cuộn chạy 1 lần, không giật layout | ✅ | `IntersectionObserver` + `unobserve`; reveal chỉ đổi `opacity`/`transform` |
| 11 | Tắt animation khi reduced motion | ✅ | `animation-duration` → `1e-05s`, `iteration-count` → `1`, parallax reset `0px` |
| 12 | Không horizontal scroll trên mobile | ✅ | `scrollWidth == clientWidth` ở cả 6 viewport, cả đầu và cuối trang |
| 13 | Không lỗi JavaScript trong console | ✅ | 0 lỗi ở cả 3 trạng thái kiểm thử |
| 14 | Lighthouse Accessibility ≥ 90 | ✅ | axe-core: **0 violation / 40 passes** (trước fix: 27 violation) |

---

## 2. Tổng hợp lỗi đã xử lý

| ID | Mức | Vấn đề | Trước | Sau |
|---|---|---|---|---|
| 1 | P1 | Tab từ cover lọt thẳng vào vùng `aria-hidden="true"` | 12 phần tử focus được | 0 |
| 2 | P1 | CTA trong card lễ cưới bị `overflow:hidden` cắt mất | cắt 106px @320, 28px @390, 10px @1440 | 0 ở mọi viewport |
| 3 | P1 | Tương phản màu không đạt WCAG AA | 26 lỗi axe (serious) | 0 |
| 4 | P2 | Không còn `<h1>` sau khi mở thiệp | 0 `h1` | đúng 1 `h1` |
| 5 | P2 | Chữ phụ nhỏ hơn 12px (HANDOFF §5) | 14 nhóm, nhỏ nhất 10.6px | min toàn trang = 12px |
| 6 | P2 | Anchor nhảy vào section bị nav fixed che | `scroll-margin-top: 0` | section dừng ở 88px, nav cao 64px |
| 7 | P2 | `aria-live="polite"` đọc lại countdown mỗi giây | screen reader spam | đổi sang `role="group"` |
| 8 | P2 | `aria-label` trên `div` không role (lịch, monogram) | axe `aria-prohibited-attr` | thêm `role="group"` / `role="img"` |
| 9 | P2 | Skip link trỏ vào đích đang ẩn | không dùng được khi cover mở | click sẽ mở thiệp rồi mới nhảy |
| 10 | P3 | Nội dung lặp cứng ở nhiều section (HANDOFF §11) | hard-code | 1 object `wedding` + binding `data-w` |
| 11 | P3 | `.ics` thiếu chuẩn RFC 5545 | `DTSTAMP` cố định, không fold dòng, thiếu `CALSCALE` | đã sửa đủ |
| 12 | P3 | RSVP payload sai kiểu | `guests: "2 người"` | `guests: 2` đúng schema §8 |
| 13 | P3 | Input 15.2px → iOS Safari tự zoom khi focus | zoom | 16px, không zoom |
| 14 | P3 | `.hero-copy` bị bó 270px, gãy dòng lệch trên mobile | 3 dòng lệch | 305px, 2 dòng theo `<br>` |

### Chi tiết đáng lưu ý

**#2 — Card lễ cưới bị cắt.** `aspect-ratio: 12/17` + `overflow: hidden` khiến automatic minimum size về 0, nên nội dung dài hơn khung bị cắt thay vì đẩy card cao ra. Đã đổi sang grid có spacer giữ tỉ lệ làm **chiều cao tối thiểu**:

```css
.printed-card { display: grid; }
.printed-card::before { content: ""; grid-area: 1 / 1; aspect-ratio: 12 / 17; }
.event-body { grid-area: 1 / 1; align-self: center; }
```

Đồng thời siết nhịp dọc trong card (padding, `script-title`, `date-row`, `card-actions`) để tỉ lệ thật sự giữ được:

| Viewport | Ratio đo được | Ghi chú |
|---|---:|---|
| 320px | 0.525 | card chỉ rộng 280px, nội dung không thể vừa 12:17 |
| 390px | 0.656 | gần đúng |
| 430px | **0.707** | đúng 12:17 |
| 768px | **0.707** | đúng 12:17 |
| 1024px | **0.707** | đúng 12:17 |
| 1440px | **0.707** | đúng 12:17 |

**#9 — `.ics` đã kiểm chứng:** `DTSTART:20261004T043000Z`, `DTEND:20261004T080000Z` (khớp HANDOFF §8), CRLF, kết thúc bằng CRLF, có `CALSCALE:GREGORIAN`, `DTSTAMP` sinh động, mọi dòng ≤ 75 octet (fold theo byte để không vỡ dấu tiếng Việt), tên file `dam-cuoi-khac-doan-dong-tien.ics`.

---

## 3. Sai lệch có chủ đích so với handoff — **cần duyệt**

### 3.1. Thêm 2 biến màu cho chữ nhỏ

Palette gốc ở HANDOFF §5 không đạt WCAG AA khi dùng cho chữ nhỏ:

| Token gốc | Nền | Tỉ lệ | Yêu cầu |
|---|---|---:|---:|
| `--gold #aa8356` | `#f2ddd5` | 2.64 | 4.5 |
| `--peach-dark #d77570` | `#fffaf3` | 3.03 | 4.5 |
| `--peach-dark` (chữ lớn) | `#f4dfd5` | 2.45 | 3.0 |
| `--ink-soft #7f6d65` | `#efe7de` | 4.01 | 4.5 |

Đã bổ sung / chỉnh:

- `--peach-ink: #bf504b` — dùng cho **chữ** và nền nút. `--peach-dark #d77570` giữ nguyên cho **viền, trái tim, nét trang trí**.
- `--gold-ink: #7d5b2c` — dùng cho nhãn section. `--gold` giữ nguyên cho chi tiết trang trí.
- `--ink-soft`: `#7f6d65` → `#6f5d55` (chênh lệch thị giác rất nhỏ, đủ AA trên cả 3 nền blush).

> Kết quả: coral đậm hơn bản gốc một chút. Nếu chủ thiệp muốn giữ đúng sắc coral nhạt, phương án thay thế là làm sáng nền blush (`#f4dfd5`, `#f2ddd5`, `#efe7de`) về gần `--paper-warm` — cần quyết định trước khi public.

### 3.2. Card lễ cưới ở ≤ 390px không giữ đúng 12:17

Ưu tiên **không cắt nội dung** hơn giữ tỉ lệ. Nếu bắt buộc giữ 12:17 ở mọi màn hình thì phải rút gọn copy trong card (bỏ `event-lead` hoặc `lunar-date`). Chờ quyết định.

### 3.3. Cấu trúc heading

`h1` chuyển từ cover sang khối tên cô dâu/chú rể ở phần lời mời (cover chỉ là splash và bị ẩn khỏi accessibility tree sau khi mở). Cover title đổi thành `<p class="cover-title">`, giao diện không đổi.

---

## 4. Còn tồn đọng — chờ chủ thiệp / backend

| Việc | Người quyết | Vị trí sửa |
|---|---|---|
| Địa chỉ đầy đủ nhà hàng | Chủ thiệp | `wedding.ceremony.venueAddress` |
| URL Google Maps chính xác | Chủ thiệp | `wedding.ceremony.mapsUrl` |
| Thông tin hai bên gia đình | Chủ thiệp | chưa có trong bản hiện tại |
| Xác nhận giờ kết thúc tiệc (đang để 15:00) | Chủ thiệp | `wedding.ceremony.endAt` |
| Endpoint nhận RSVP + chống spam / rate limit | Backend | `wedding.rsvp.endpoint` |

Khi có endpoint, chỉ cần gán `wedding.rsvp.endpoint = 'https://...'` — phần loading / error / retry đã sẵn sàng, payload đúng schema HANDOFF §8:

```json
{ "name": "Nguyễn Văn A", "attendance": "yes", "guests": 2, "message": "Chúc hai bạn trăm năm hạnh phúc" }
```

---

## 5. Chưa kiểm thử được

- **Thiết bị thật:** toàn bộ QA chạy trên Chrome headless macOS. Chưa chạy trên iOS Safari và Android Chrome thật (HANDOFF §15 yêu cầu).
- **Tải file `.ics` trên iOS Safari:** nội dung file đã đúng chuẩn, nhưng hành vi tải/mở app Lịch trên iOS chưa xác minh.
- **Lighthouse:** dùng axe-core (0 violation) làm proxy, chưa chạy Lighthouse CLI để lấy điểm số chính thức.
- **Đối chiếu pixel với thiệp cứng:** chưa có file thiết kế gốc để so trực tiếp, mới đối chiếu theo mô tả trong handoff.

---

## 6. Chạy lại QA

```bash
python3 -m http.server 4321      # phục vụ tĩnh từ thư mục dự án
open http://127.0.0.1:4321/index.html
```

Kiểm tra nhanh:

1. Tab từ đầu trang khi cover còn đóng → chỉ đi qua skip link và nút `Mở thiệp`.
2. Mở thiệp → focus phải nhảy vào `KD ♡ ĐT` trên nav.
3. Bật Reduced Motion trong OS → mọi hoa ngừng chuyển động, nội dung hiện ngay.
4. Thu cửa sổ về 320px → không có thanh cuộn ngang, 2 CTA trong card vẫn thấy đủ.
5. Bấm `Lưu ngày cưới` → mở file `.ics`, xác nhận `DTSTART:20261004T043000Z`.
