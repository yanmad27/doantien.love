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
| ~~Địa chỉ đầy đủ nhà hàng~~ — đã có: 1995 Hùng Vương, Cam Linh, Khánh Hòa | ✅ 03.09.2026 | `wedding.ceremony.venueAddress` |
| ~~URL Google Maps~~ — đã ghim đúng địa điểm | ✅ 03.09.2026 | `wedding.ceremony.mapsUrl` |
| **Tên hiển thị của địa điểm.** Google ghi là "Trung Tâm Hội Nghị Tiệc Cưới Long Phụng", thiệp đang ghi "Nhà hàng Long Phụng". Chưa đổi vì tên chính thức dài hơn nhiều, sẽ ảnh hưởng bố cục card 12:17 | Chủ thiệp | `wedding.ceremony.venueName` |
| **Có hiện địa chỉ đường trên card không.** Hiện card chỉ ghi "Nhà hàng Long Phụng / Khánh Hòa"; địa chỉ mới chỉ nằm trong file `.ics`. Khánh Hòa là tỉnh rộng, địa điểm thực ở Cam Ranh — khách dễ nhầm hướng Nha Trang | Chủ thiệp | `.venue` trong `index.html` |
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

## 6. Vòng 2 — mark lịch, brand asset, thiệp cá nhân hoá

### 6.1. Lỗi responsive phát hiện thêm

| Mức | Vấn đề | Trước | Sau |
|---|---|---|---|
| P1 | Nội dung trong card lễ cưới **bị cắt ngang**. Spacer `::before` có `aspect-ratio` nằm trong cột grid `auto` nên trình duyệt suy ngược chiều rộng từ chiều cao, đẩy cột lên 375px trong khi card chỉ 280–350px. `overflow:hidden` cắt phần thừa, `body{overflow-x:hidden}` che luôn dấu vết. | `.event-body` rộng 375px ở cả 320/360/390 | `grid-template-columns: minmax(0,1fr)` → khớp đúng bề rộng card |
| P2 | Tỉ lệ 12:17 vỡ ở dải 821–900px (card bị ép về sàn 320px ngay sau breakpoint desktop) | ratio 0.59 @834px | sàn cột 380px → 0.707 |
| P2 | Vùng chạm nhỏ hơn khuyến nghị iOS | link nav cao 20px, `.text-link` 30px, `.nav-brand` 34px | tất cả ≥ 44px |
| P3 | Mark ngày cưới là hình tròn **36px cố định** → tràn ra khỏi ô lịch trên màn nhỏ (ô chỉ ~33px ở 320px) | tràn | trái tim `min(92%, 2.4em)`, co theo ô |

Sau khi sửa: **0 issue** trên 13 bề rộng (320 → 1920px), cả trang chung lẫn trang khách. Tỉ lệ card 12:17 giữ đúng **0.707 từ 414px trở lên**; dưới mức đó card cao hơn tỉ lệ để không cắt nội dung (xem §3.2).

### 6.2. Mark ngày cưới

Đổi từ hình tròn sang trái tim, dùng `mask-image` (SVG data URI) nên đổi màu bằng `background` và co giãn theo ô. Số 4 lệch `translate: -50% -46%` để nằm đúng phần đầy của trái tim — Cormorant dùng oldstyle figures nên chữ số không cân giữa như font thường. Có `@supports` fallback về hình tròn nếu trình duyệt không hỗ trợ mask. Bỏ trái tim nhỏ phía trên vì đã thừa.

### 6.3. Brand asset

Chữ được convert thành path từ Cormorant Garamond (weight 500) nên **không phụ thuộc webfont**. Sinh lại bằng `tools/build-brand.py` + `tools/render-icons.sh` (xem `tools/README.md`).

- `logo.svg` — monogram KD ♡ ĐT vector
- `favicon.svg` / `favicon.ico` (16/32/48) — bản nhỏ **bỏ viền vàng**, vì ở 16px viền chỉ còn ~0.4px và bị khử răng cưa thành vệt xám
- `apple-touch-icon.png` (180), `icon-192/512.png`, `icon-maskable-512.png` — bản lớn giữ viền
- `og-image.png` (1200×630) — render từ `tools/og-source.html`, dùng chính font và hoa của thiệp

### 6.4. Thiệp cá nhân hoá theo khách

Trước đây nằm trong "ngoài phạm vi" (§13 HANDOFF), nay đã triển khai.

- `guests.csv` là nguồn duy nhất. `tools/guests.py assign` sinh mã ngẫu nhiên 6 ký tự, `links` in ra danh sách URL để dán vào danh sách gửi.
- Trang riêng sinh **trong Docker build stage** → repo không chứa hàng trăm file HTML gần giống nhau, và không có file nào liệt kê toàn bộ danh sách khách.
- Link: `doantien.love/t/<mã>/`. Tên khách nằm sẵn trong HTML (không cần JS) nên `og:title` / `og:description` khi share qua Zalo/Messenger hiện đúng tên người nhận.
- Tên hiện ở 3 chỗ: dòng "Thân mời" trên bìa, dưới "Trân trọng kính mời" ở phần lời mời, và điền sẵn ô RSVP. Payload RSVP kèm `guestCode` để biết chính xác ai phản hồi.
- Mã sai → rơi về thiệp chung thay vì 404.
- `noindex, nofollow` ở cả thẻ meta lẫn header, cộng `robots.txt` chặn `/t/` — không để tên khách lọt lên Google.

**Kiểm chứng:** tên dài nhất thử nghiệm ("Gia đình Ông Bà Nguyễn Hoàng Phương Thảo Anh", 45 ký tự) xuống 3 dòng ở 320px vẫn nằm gọn trong bìa 12:17 sau khi siết khoảng trắng ở breakpoint ≤360px. Dòng tên trên bìa dùng serif thường thay vì in hoa — tiếng Việt có dấu viết hoa vừa rộng vừa khó đọc.

> Lưu ý vận hành: sửa `guests.csv` xong phải deploy lại thì link mới có hiệu lực. Đừng sửa `code` của khách đã gửi link — link cũ sẽ hỏng.

## 7. Vòng 3 — animation

### 7.1. Thêm mới

| Hiệu ứng | Mô tả |
|---|---|
| Stagger dùng chung | `[data-stagger]` + `--i` do JS gắn, delay `calc(var(--i) * var(--step))`. Áp cho hero, count-grid, event-body, schedule, lịch (step 18ms), calendar-copy, rsvp-heading, closing |
| Nav | Trượt xuống khi mở thiệp, đổ bóng khi cuộn, tự ẩn khi cuộn xuống và hiện lại khi cuộn lên |
| Thanh tiến độ đọc | Vạch coral 2px dưới nav, `scaleX(--progress)` |
| Nhảy số countdown | Chữ số đổi thì trượt từ trên xuống 0.5s |
| Mark ngày cưới | Trái tim pop lên rồi mới bắt đầu nhịp đập |
| Gạch chân lịch trình | Đường coral chạy dần dưới từng mốc giờ, lệch pha theo `--i` |
| Bìa thiệp | Vệt sáng quét ngang một lần lúc tải; dòng "Chạm để mở" trôi nhẹ |
| Mũi tên "Xem thông tin" | Nhấp nhô liên tục, dừng khi hover |

### 7.2. Đo hiệu năng — kết quả không như dự đoán

Bản cũ đọc `document.documentElement.scrollHeight` **mỗi khung hình** trong lúc cuộn, ép trình duyệt layout đồng bộ. Đã bỏ (đo sẵn + `ResizeObserver`).

Nhưng khi đo A/B thật thì **cả bản cũ lẫn bản mới đều 60fps đều, 0 khung rớt**:

| | median | p95 | khung > 20ms |
|---|---:|---:|---:|
| Bản cũ (HEAD) | 16.70ms | 16.80ms | 0 / 246 |
| Bản mới | 16.70ms | 16.70ms | 0 / 244 |

Tôi cũng thử đổi parallax từ custom property sang property `translate`/`rotate` với giả thuyết custom property phá đường tắt compositor. **Sai** — số lần recalc style gần như không đổi (523 so với 529 trên 60 bước cuộn).

Kết luận trung thực: **không đo được cải thiện hiệu năng trên máy này** (headless Chrome, Mac M-series — quá nhanh để lộ khác biệt). Việc bỏ forced layout vẫn đúng về nguyên tắc và sẽ có ích trên máy yếu, nhưng chưa có số liệu chứng minh. Cảm giác "mượt hơn" đến từ việc parallax nay nội suy dần (lerp) nên hoa trôi có quán tính — đây là thay đổi *thẩm mỹ*, không phải hiệu năng.

> Cần đo lại trên điện thoại Android tầm trung thật mới kết luận được.

### 7.3. An toàn

- **Không JS**: trước đây `[data-reveal]` bị ẩn vô điều kiện → JS lỗi là trắng trang. Nay chỉ ẩn khi `html.js` (do inline script gắn). Đã kiểm chứng: gỡ class `.js` thì mọi nội dung `opacity: 1`.
- **Reduced motion**: 0 animation lặp, vệt sáng ẩn, nav không tự giấu, parallax không áp, số countdown không nhảy, toàn bộ nội dung hiện ngay.
- Không có `IntersectionObserver` thì hiện toàn bộ nội dung ngay.
- Nav không tự ẩn khi người dùng đang tab vào link bên trong (`:focus-within`).
- Sau thay đổi: responsive **0 issue** trên 8 bề rộng, axe **0 violation / 41 passes**.

## 8. Chạy lại QA

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

## 9. Vòng 4 — đoàn rước của Bé Phong Bì

- **Ngày QA:** 04.09.2026
- **Trình duyệt:** Google Chrome 152, tab xác nhận `visibilityState = visible`
- **Viewport:** 320×720, 390×844 và 1440×1000
- **Ảnh chụp:** đã chụp giữa chặng `riding` và sau khi sang `arrived` ở cả ba cỡ

### Kết quả

| Kiểm tra | Kết quả |
|---|---|
| Ba lớp xa / trung / gần cùng chạy | ✅ 21–22 animation hoạt động trong chặng `riding` |
| Ảnh nền, Bé và sprite tải thật | ✅ `naturalWidth` lần lượt 1120 / 400 / 1000 |
| Không tràn ngang | ✅ `scrollWidth - clientWidth = 0` ở cả ba viewport |
| Bé đỗ lại ở cổng | ✅ chặng cuối là `arrived`, Bé và hai cụm đoàn rước còn trong tranh |
| QR hiện sau 4,8 giây | ✅ `#gift-qr[hidden] = false`, ảnh QR rộng nguồn 360px |
| Reduced motion | ✅ bỏ thẳng chặng diễn, vào `arrived` và hiện QR trong dưới 80ms |
| Thiệp riêng `/t/<mã>/` | ✅ sprite đổi thành `/assets/...`, tải đủ 1000px |
| Ngân sách ảnh | ✅ 166.750 byte (162,8 KiB), dưới 200 KB |
| Thuộc tính động | ✅ keyframe chỉ đổi `transform`, `opacity`, `filter` |

Console localhost chỉ có lỗi CORS dự kiến từ API production của sổ lời chúc và
trạng thái mừng cưới; không có exception JavaScript hay lỗi tải asset hoạt cảnh.

## 10. Vòng 5 — pháo hoa và cao trào nhận tiền

- **Ngày QA:** 04.09.2026
- **Trình duyệt:** Google Chrome 152, tab xác nhận `visibilityState = visible`
- **Ảnh chụp:** đủ `idle` / `riding` / `arrived` / `received` ở 390×844 và 1440×1000

### Hình ảnh và luồng

| Kiểm tra | Kết quả |
|---|---|
| Idle có chuyển động mời bấm | ✅ Bé thở, người đánh trống nhún, cờ phất và ba nốt nhạc lệch pha |
| Riding có phản ứng phụ | ✅ sáu hạt tim/nhạc, bốn đám bụi, bốn nhịp nhân vật khác nhau |
| Lớp xa không còn placeholder | ✅ dùng lại sprite màu nước, làm mềm/giảm bão hòa cả lớp |
| Arrived có cao trào ngắn | ✅ Bé và đoàn bật mừng, ba pháo hoa nổ một lượt; QR vẫn hiện ở 4,8 giây |
| Received bung hết cỡ rồi dừng | ✅ 24 tia pháo hoa chạy năm lượt, hoa giấy trôi, Bé/đoàn nhảy hữu hạn |
| Pháo hoa có đè QR không | ✅ hai bounding box không giao nhau ở cả 390px và desktop |
| Reduced motion | ✅ 0 hạt hiện, 0 animation trong `#gift`, QR hiện ngay |
| 320px / thiệp riêng | ✅ không tràn ngang; hai asset mới phân giải thành `/assets/...` |
| Thuộc tính trong keyframe | ✅ chỉ có `transform`, `opacity`, `filter` |
| Ngân sách ảnh | ✅ không thêm raster; giữ 166.750 byte (162,8 KiB) |

### DevTools Performance — chặng `received`

Đo năm giây ở viewport 390×844 sau khi decode hết ảnh, bật CPU throttle 4×:

- 302 callback `requestAnimationFrame`; median 16,7ms, p95 16,7ms, max 16,8ms.
- 0 khoảng >20ms và 0 khoảng >34ms.
- Trace compositor ghi 301 `DrawFrame` và 301 `Display::FrameDisplayed`.
- Có 1 `Scheduler::BeginFrameDropped` ở giây 3,93, nhưng không tạo ra khoảng rAF
  dài hay khung hụt nhìn thấy. Không ghi kết quả thành “0 dropped frame”.

Đây là Chrome headless trên máy M3 có CPU throttle, chưa phải phép đo GPU trên
điện thoại Android tầm trung thật. Lỗi console localhost vẫn chỉ là CORS dự kiến
từ API production; không có exception hay lỗi tải asset của phần mừng cưới.

## 11. Vòng 6 — phối cảnh người trên mặt đường

- **Ngày QA:** 04.09.2026
- **Ảnh nguồn dò đường:** `assets/gift-journey-scene.webp`
- **Tâm đường theo % khung:** `(43,86) → (58,76) → (76,64) → (75,53) → (82,47)`
- **Công thức chiều cao:** `h = 0,49 × (footY − 34)`

Đo giữa chặng `riding` bằng Chrome 152, tab `visible`. Ba viewport 320 / 390 /
1280 cho cùng kết quả trong sai số làm tròn 0,3 điểm phần trăm và đều không tràn
ngang. Bảng dưới là số tại 1280px:

| Lớp | Chân y (%) | Cao đo (%) | Cao theo công thức (%) |
|---|---:|---:|---:|
| Bé | 87,6 | 26,3 | 26,3 |
| Trước 1 / 2 / 3 | 82,5 / 78,2 / 74,9 | 23,3 / 21,3 / 19,9 | 23,8 / 21,7 / 20,0 |
| Sau 1 / 2 / 3 | 61,4 / 59,9 / 57,2 | 14,0 / 12,6 / 11,8 | 13,4 / 12,7 / 11,4 |
| Xa, đầu → cuối | 54,5 → 49,2 | 10,1 → 7,7 | 10,0 → 7,4 |

Sai số tối đa khoảng 4% đến từ bounding box của sprite đang xoay/nhún. Ảnh chụp
ở cả ba cỡ xác nhận hàng trước bám đoạn đường rộng phía dưới–trái, hàng sau nằm
trong khúc cua x≈69–82%, và đám xa thu gọn quanh tâm đường lên cổng; không còn
nhân vật đứng trong khoảng giấy trắng. Các ảnh `.tmp-*` đã được xoá sau khi soi.

## 12. Vòng 7 — chữ trên hai tấm biển

- **Ngày QA:** 04.09.2026
- **Ca chụp:** trang chung và `/t/w8x3vb/` (tên 22 ký tự) ở 320 / 390 / 1280px
- **Trạng thái:** giữa chặng `riding`, tab Chrome 152 xác nhận `visible`

### Cách xử lý cỡ chữ

- Dưới 600px, hai chữ trong tranh bị ẩn vì biển thật chỉ rộng 30–45px. Dòng
  `Tên khách → Đám cưới Doãn & Tiên` hiện dưới khung ở 16px.
- Từ 600px, bảng cổng dùng hai dòng 12px trong vùng viền 72,5×48,3px.
- `Nhà bạn` dùng 12px trong biển chỉ đường 46,9×19,7px. Tên khách dài dùng mảnh
  giấy nối biển 133,9×31,6px; tên dài nhất nằm trọn một dòng và không overflow.
- Tên lấy từ `data-guest-name` và chỉ gán qua `textContent`.

### Tương phản và trợ năng

| Vùng | Màu chữ | Tỉ số thấp nhất |
|---|---|---:|
| Bảng cổng, nền ảnh gốc | `--ink #59473f` | 6,23:1 |
| Biển chỉ đường + giấy 78% | `--ink #59473f` | 6,25:1 |
| Mảnh giấy tên dài 84% | `--ink #59473f` | 6,81:1 |

Hai nhãn trong `.gift-scene[aria-hidden=true]` chỉ là bản trang trí. Dòng dưới
khung có một câu dành riêng cho cây trợ năng: `Hành trình từ <tên> đến Đám cưới
Doãn và Tiên.`; các chữ nhìn thấy và mũi tên nằm trong nhánh `aria-hidden` nên
không bị đọc lại. Cả sáu ca có `horizontalOverflow = 0`; trang chung ra `Nhà
bạn`, trang riêng ra nguyên văn `Gia đình cô Lê Thị Lan`.

## 13. Vòng 8 — thay chip tên bằng biển màu nước thật

Thiết kế mảnh giấy bo góc ở vòng 7 bị loại: nó rời cột biển và mang hình thức UI,
đặc biệt mọi tên khách thật đều rơi vào nhánh đó. Bản mới cắt đúng vùng biển từ
chính `gift-journey-scene.webp`, kéo rộng từ điểm gắn vào cột rồi đặt chữ lên
trên. Ảnh nền và ảnh cắt dùng cùng URL nên không tăng byte tải; không còn
`border`, `border-radius`, nền `rgba` hay nhánh theo độ dài tên.

### QA

- **Ca chụp:** `Nhà bạn` và cả ba khách 15 / 18 / 22 ký tự ở 600 và 1280px.
- **Kích thước biển:** 133,9×31,6px ở cả hai viewport do sân khấu đã chạm trần
  560px; mọi tên dùng font 12px, một dòng, không clip.
- **Vị trí:** nhãn bắt đầu x=6,2%, y=54,8%; mặt biển kéo rộng vẫn chồng lên biển
  gốc và giữ nguyên điểm nối với cột.
- **Responsive:** tám ca đều `horizontalOverflow = 0`; dưới 600px tiếp tục dùng
  dòng hành trình 16px đã duyệt ở vòng 7.

Tương phản `--ink #59473f` được đo trên đúng dải pixel nằm sau bounding box glyph
sau phép biến đổi ảnh, không lấy cả vùng nền sáng xung quanh:

| Nội dung | Tỉ số thấp nhất |
|---|---:|
| `Nhà bạn` | 6,38:1 |
| Tên 15 ký tự | 6,25:1 |
| Tên 18 ký tự | 5,55:1 |
| `Gia đình cô Lê Thị Lan` | 5,38:1 |

Toàn bộ tám ảnh `.tmp-*` đã được xoá sau khi soi.

## 14. Vòng 9 — lật phối cảnh, đưa mã QR vào trong tranh

- **Ngày QA:** 05.09.2026
- **Trình duyệt:** Google Chrome headless (CDP), tab `visible`
- **Viewport:** 320 / 390 / 1280px, thêm một lượt `--force-prefers-reduced-motion`

### Đổi gì

Yêu cầu của chủ thiệp: *"thay vì chạy tới Doãn Tiên ở xa thì flip cho Doãn Tiên ở
gần, xong thì mở cái QR trong hình luôn chứ không phải ngoài hình."*

Thêm lớp `.gift-camera` bọc mọi thứ vẽ trong tranh và cho nó dolly về tâm tấm bảng
trên cổng cưới. Bé vẫn đi hết con đường như cũ; cái đổi là **máy quay tiến tới**,
nên kết chuyến là cận cảnh cổng thay vì một cái cổng bé tí ở cuối đường. Mã QR
chuyển từ dưới khung lên treo trên chính tấm bảng đó.

`container-type` vẫn nằm ở `.gift-scene` chứ không phải lớp mới, nên `cqw/cqh` giữ
nguyên hệ quy chiếu và **không phải tính lại một toạ độ nào** của Bé hay đoàn rước.

### Ba thứ hỏng khi dựng, đã sửa

| Vấn đề | Đo được | Sửa |
|---|---|---|
| Dolly đều từ giây đầu kéo Bé văng khỏi khung giữa chuyến | ở nửa chuyến Bé ra tới **−29,3%** | dồn dolly về 1/3 cuối (`gift-dolly` 45/65/80/90/100%) |
| Pháo hoa nằm trong máy quay nên bị kéo đi | chùm 1 và 2 ra **−203%** và **−82,6%** | đưa `.gift-fireworks` + `.gift-celebration` ra ngoài `.gift-camera` |
| Kéo ngang đủ −33% làm hở mép phải tranh | mép phải chỉ tới **95,9%** ở dolly 2,7 | dùng **−27%** (ngưỡng phủ kín là 17×(1−dolly) = −28,9%) |

### Kết quả

| Kiểm | 320px | 390px | 1280px |
|---|---|---|---|
| Chặng cuối | `arrived` | `arrived` | `arrived` |
| Mã QR nằm trọn trong khung tranh | ✅ | ✅ | ✅ |
| Cỡ mã QR | 151px | 184px | 231px |
| Khung tranh | 280×187 | 350×233 | 560×373 |
| Tràn ngang | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 |
| `received`: QR tắt, lời cảm ơn hiện, pháo hoa trong khung | ✅ | ✅ | ✅ |

**Reduced motion:** vào thẳng `arrived`, `getAnimations()` trong `#gift` đếm được
**0 animation đang chạy**, mã QR `opacity: 1` ngay, không tràn ngang, 0 lỗi JS.
Dolly ở `arrived` là transform tĩnh nên người bật giảm chuyển động vẫn thấy đúng
cận cảnh cổng mà không có một khung hình chuyển động nào.

**Trợ năng:** mã QR **không** nằm trong nhánh `aria-hidden` (`.gift-scene` vẫn
`aria-hidden="true"`), có `alt` và `aria-describedby` trỏ đúng lời chú, xuất hiện
trong cây AX với `ignored=false`. `#gift` vẫn chỉ có **đúng một** vùng `aria-live`
và nó nằm ngoài `#gift-stage`.

**Hợp đồng chức năng:** 14 điểm grep được đều còn nguyên (`visibilitychange`, so
mốc `at`, `commitThanks`, fallback QR, ẩn section khi ảnh hỏng, poll 5s/15 phút,
`preventScroll`, guard reduced-motion và container query, mã QR không `loading="lazy"`).
`script.js` chỉ đổi **2 dòng**: bỏ `.gift-envelope-art` khỏi `GIFT_ART` và sửa một
chữ trong chú thích. Toàn bộ logic mừng cưới không bị đụng.

**Ngân sách ảnh:** 138.985 byte (135,7 KiB), giảm từ 166.750 — đã xoá
`gift-envelope-open.webp` vì mã QR không còn khung phong bì bọc ngoài nữa.

### Chưa kiểm được

- **Thiết bị thật:** chưa chạy trên iOS Safari và Android Chrome thật. Đặc biệt cần
  thử **quét mã bằng camera thật ở 320px**, nơi mã chỉ còn 151px — tính ra khoảng
  27mm trên màn hình thật, đủ nhưng sát ngưỡng.
- **Thiệp riêng `/t/<mã>`:** chưa dựng app quanly ở máy nên chưa xác minh mã QR từ
  `qr.sepay.vn` hiện đúng chỗ trên tấm bảng. Đường dẫn ảnh không đổi nên rủi ro thấp,
  nhưng chưa phải là đã kiểm.
- **Trình duyệt không hỗ trợ container query:** suy luận là rơi vào `arrived` với
  camera phóng sẵn (dolly chỉ dùng `%` và số không đơn vị, không dùng `cq`), Bé bị
  `@supports` giấu đi. Chưa dựng được môi trường để đo thật.
- Chưa chạy Lighthouse, chưa chạy Firefox/Safari, chưa đo lại fps sau thay đổi.

## 15. Vòng 10 — bỏ cận cảnh, pháo hoa nổ ra mã QR ở khoảng trời trống

- **Ngày QA:** 05.09.2026
- **Trình duyệt:** Google Chrome headless (CDP)
- **Viewport:** 320 / 390 / 1280px, thêm một lượt `prefers-reduced-motion: reduce`

### Vì sao bỏ cú phóng cận cảnh của §14

Chủ thiệp xem bản đang chạy thật và chỉ ra: *"góc trên trong hình bị trống, làm
pháo hoa hiện QR đi; còn người di chuyển thì bỏ hiệu ứng mờ mờ đi, 9 người di
chuyển nhỏ dần là được."*

Cú dolly ở §14 giải quyết đúng "QR trong hình" nhưng bằng cách **bỏ toàn cảnh** —
kết chuyến chỉ còn thấy cổng, mất con đường và cả đoàn người. Hướng mới giữ nguyên
toàn cảnh và dùng chính khoảng trời trống góc trên–trái làm chỗ đặt mã, lấy pháo
hoa làm cái cớ để mắt nhìn sang. Đổi lại tranh không bị phóng to nên **không còn
vấn đề nở ảnh** (§14 phải cân `--dolly` theo bề rộng khung để giữ nét).

Toàn bộ lớp `.gift-camera`, `@keyframes gift-dolly` và `gift-actor-exit` đã gỡ.

### Đổi gì

| | Trước (§14) | Sau |
|---|---|---|
| Khung cuối | cận cảnh cổng, dolly 2,7–3,9× | giữ toàn cảnh, không phóng |
| Chỗ đặt QR | trên tấm bảng của cổng | khoảng trời trống góc trên–trái |
| Cách QR hiện | mờ dần sau 0,12s | **pháo hoa nổ trước, mã hiện sau 0,34s** |
| Đoàn người | 12 người, 3 cụm, lớp xa bị `saturate(.78)` | **9 người một hàng, không lọc gì** |
| Chiều sâu | một phần do làm mềm lớp xa | hoàn toàn do cỡ người (1,00 → 0,31) |

Vị trí và cỡ 9 người suy từ tâm đường đo ở §11 và `h = 0,49 × (chân y − 34)`, nên
ai cũng đứng đúng mặt đường ở mọi bề rộng — không phải media query nào.

### Kết quả

| Kiểm | 320px | 390px | 1280px |
|---|---|---|---|
| Chặng cuối | `arrived` | `arrived` | `arrived` |
| Cỡ mã QR | 141px | 161px | 230px |
| Mã nằm trọn trong khung tranh | ✅ | ✅ | ✅ |
| Số người | 9 | 9 | 9 |
| Cao người, gần → xa (px) | — | 51→16 | 89→28 |
| `filter` trên người | `none` | `none` | `none` |
| Tràn ngang | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 |
| `received`: mã tắt, lời cảm ơn hiện | ✅ | ✅ | ✅ |

**Reduced motion:** vào thẳng `arrived`, `getAnimations()` trong `#gift` đếm **0
animation đang chạy**, mã `opacity: 1` ngay, 0 tràn ngang, 0 lỗi JS.

**Trợ năng:** mã QR **không** nằm trong nhánh `aria-hidden`, `#gift` vẫn đúng
**một** vùng `aria-live`.

**Hai cái bẫy gặp khi dựng:**

1. `gift-arrival-cheer` / `gift-victory-cheer` chuyển từ `.gift-marcher` sang
   `.gift-crowd i` mà quên mở đầu bằng `translateX(-50%) scaleX(var(--flip))`.
   `transform` ghi đè cả chuỗi nên cả chín người nhảy sang phải nửa thân đúng lúc
   reo mừng. Đã thêm lại vào cả hai keyframes.
2. Ba chùm pháo hoa đặt ở tâm mã thì bị chính mã che (mã có `z-index` cao hơn).
   Đã dời ra **mép** mã để tia toả ra ngoài. Giữ nguyên thứ tự chồng: hạt không
   bao giờ được nằm trên mặt mã, camera điện thoại sẽ đọc hụt.

**Cỡ mã ở 320px:** `41vw` thuần cho ra 132px (~23mm máy thật), quét phập phù. Đã
thêm sàn `max(140px, …)` → 141px. Trong khung 280×187 là 75% chiều cao, chật
nhưng vẫn hở mép trên.

### Chưa kiểm được

- **Chưa quét bằng camera thật.** Đây là việc cần làm trước tiên, nhất là ở 320px.
- **Thiệp riêng `/t/<mã>`:** chưa dựng app quanly ở máy.
- Chưa iOS Safari / Android thật, chưa Firefox/Safari, chưa Lighthouse, chưa đo lại fps.

## 16. Vòng 11 — zoom lúc Bé chạy vào, và mã QR bớt rời rạc

- **Ngày QA:** 05.09.2026 · Chrome headless (CDP) · 320 / 390 / 1280px + reduced motion

### a. Mã QR không còn "rời rạc"

Chủ thiệp: *"cái QR bị rời rạc quá."* Soi ở cỡ lớn thì thấy hai lỗi chồng nhau:

1. Khối trắng tinh, bo 10px, đổ bóng nổi — đúng chất thẻ giao diện dán đè lên
   tranh màu nước.
2. **Ảnh `qr-mung-cuoi.png` mang sẵn nền TRẮNG của riêng nó**, nên bọc thêm một
   lớp giấy kem quanh nó thành **hai hình chữ nhật lồng nhau**, thấy rõ đường
   viền trắng bên trong kem. Đây mới là nguồn gốc chính của cảm giác rời rạc.

Đã sửa: mượn nguyên ngôn ngữ mẩu giấy của sổ lời chúc (`.wish`) — giấy kem
`--paper`, viền mực rất nhạt, bo 3px, nghiêng 1,8°, bóng hai lớp (quầng kem toả
rộng cho mép tan vào nền trời + bóng tiếp đất bám sát). Ảnh mã đặt
`mix-blend-mode: multiply` nên nền trắng của nó nhân với giấy kem thành kem, ô đen
giữ nguyên — hết hẳn hình chữ nhật lồng nhau. Tương phản còn ~19:1.

Thêm **mẩu băng dính** ở mép trên (dùng lại `.wish::before`): không có nó thì tờ
giấy trôi lơ lửng, không có gì giữ.

### b. Zoom khi Bé chạy vào

Chủ thiệp: *"zoom in khi bé phong bì chạy vào đi."*

Dựng lại `.gift-camera` bọc các lớp vẽ và `@keyframes gift-zoom-in`:
1 → 1,22 → **1,35** → 1,28 → **1**.

`transform-origin` đặt đúng **tâm tấm bảng "Đám cưới Doãn & Tiên"** — `83% 28,5%`,
đo trên chính `gift-journey-scene.webp` (mặt giấy nằm trong x 76,5–89%, y 21–36%).
Phóng quanh điểm đó thì tấm bảng đứng yên còn mọi thứ khác nở ra quanh nó, nên
mắt bị kéo về đúng cái tên. Bản đầu đặt tâm ở giữa khúc đường (48% 62%) — chủ
thiệp yêu cầu dời vào tấm bảng.

Hệ quả đã kiểm và chấp nhận: biển "Nhà bạn" trôi ra ngoài khung (−15,6%) lúc đỉnh
zoom rồi quay lại khi nhả — đúng ý, camera đang nhìn về phía cổng. Bé vào khung
muộn hơn một nhịp (x = 0,5% ở mốc 22% thay vì 15,4%) vì tâm phóng nằm xa bên phải;
đọc ra thành "chạy vào từ mép trái". Cả chín người vẫn trong khung.

**Mốc 100% bắt buộc là scale(1)**: chỗ đặt mã QR và dải đường của chín người đều
canh theo khung KHÔNG zoom. Giữ zoom lại lúc tới nơi là mã lệch khỏi khoảng trời
trống và người bị tờ giấy che.

Đỉnh 1,35 chọn theo độ nét: ảnh gốc rộng 1120px nên khung desktop 560px còn thu
nhỏ được tới 2,0× — 1,35 **không mất một chút nét nào**.

Về mép: ở 1,35 bốn mép rơi vào −29,1% / 106,0% / −10,0% / 125,0%, phủ kín. Thật ra
**chỉ cần scale ≥ 1 là không thể hở**, vì đây là phép phóng thuần quanh một điểm
nằm trong ảnh, không kèm translate. Vệt hở mép phải từng gặp ở bản dolly §14 là do
translate kéo ảnh đi chứ không phải do scale — hai chuyện khác nhau, đừng nhầm.

Đo thật lúc chạy: 1,224 (vào) → 1,346 (đỉnh) → `none` (tới nơi). Đúng đường cong.

### c. Lỗi bắt được: chín người bị tờ giấy che

Lần đặt dải đường đầu tiên (`t = 0,46 → 0,96`) tôi kiểm sai **hai chỗ**, mỗi chỗ
đều cho kết quả "0 người bị che" trong khi thực tế có:

1. Kiểm trên toạ độ gốc thay vì toạ độ **sau** `gift-crowd-walk`.
2. Kiểm **tâm** người thay vì **mép trái** (tâm trừ nửa bề rộng).

Đo lại bằng hộp thật của tờ giấy (trừ phần phồng do `rotate`, đo được 4–5px):
ở 320px người #1 bị che **7/16px — gần nửa thân**. Ca xấu nhất là 320px vì sàn
140px làm tờ giấy chiếm 50% bề ngang khung.

Đã dời sang `t = 0,56 → 0,98`. Đo lại: **chồng lấn = 0** ở cả 320 và 390px. Giá
phải trả: người gần nhất nhỏ đi từ 15,5% xuống 14,1% chiều cao khung.

### Kết quả

| Kiểm | 320px | 390px | 1280px | +reduced motion |
|---|---|---|---|---|
| Chặng cuối | `arrived` | `arrived` | `arrived` | `arrived` |
| Cỡ mã QR | 144px | 165px | 237px | 165px |
| Mã trong khung tranh | ✅ | ✅ | ✅ | ✅ |
| `mix-blend-mode` | multiply | multiply | multiply | multiply |
| Người bị giấy che | **0** | **0** | **0** | **0** |
| `filter` trên người | none | none | none | none |
| Animation đang chạy | 12 | 12 | 12 | **0** |
| Mã trong nhánh aria-hidden | không | không | không | không |
| Vùng `aria-live` trong `#gift` | 1 | 1 | 1 | 1 |
| Tràn ngang | 0 | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 | 0 |

CSS mồ côi: 0. `script.js` vẫn chỉ đổi 2 dòng so với `main`.

### Chưa kiểm được

- **Chưa quét mã bằng camera thật** — vẫn là việc cần làm trước tiên. `multiply`
  làm nền mã thành kem `#fffaf3` thay vì trắng; tương phản tính ra ~19:1 nhưng
  chưa ai đưa điện thoại lên quét thử.
- Thiệp riêng `/t/<mã>`, iOS/Android thật, Firefox/Safari, Lighthouse, fps: chưa.

## 17. Vòng 12 — zoom vào bảng cổng, và cột biển QR mọc lên

- **Ngày QA:** 05.09.2026 · Chrome headless · 320 / 390 / 1280px + reduced motion

### Đổi gì

Chủ thiệp: *"zoom in vào bảng đám cưới Doãn Tiên, xong có 1 cái cột bảng QR mọc
lên, làm animation đẹp tí nhé."*

1. **Tâm zoom** dời vào tấm bảng cổng: `transform-origin: 83% 28.5%`. Phóng quanh
   điểm đó thì tấm bảng đứng yên còn mọi thứ khác nở ra quanh nó.
2. **Cột biển QR mọc lên khỏi mặt đất** thay cho tờ giấy dán lơ lửng. Bức tranh
   vốn đã có hai biển gỗ cắm đất (biển chỉ đường và cổng cưới) nên cái thứ ba là
   thứ thuộc về thế giới đó. Bỏ mẩu băng dính — đã có cột giữ, không cần cái cớ.
3. Nhịp: cột mọc (`gift-sign-rise`, 0,82s, vọt 1,04 rồi lún về 1) → bảng lắc một
   nhịp rồi đứng (`gift-board-settle`, kết thúc đúng −1,8° trùng rotate nghỉ) →
   pháo hoa nổ (dời sang 780/950/1110ms để mừng lúc biển vừa dựng xong).
4. Thêm **vệt bóng tiếp đất** ở chân cột — thiếu nó thì cột đứng lơ lửng trên mặt
   đường.

### Ba lỗi bắt được trong lúc dựng

| Lỗi | Biểu hiện | Nguyên nhân |
|---|---|---|
| Mã QR nở thành 381px, tràn ngang 94px | ảnh về cỡ gốc | thay khối CSS đã **xoá nhầm** luật `.gift-qr-code` |
| Cột cao 0px, biến mất | `height: 11%` | cha `.gift-qr-sign` cao `auto` nên `%` quy chiếu về 0. Đổi sang `calc(var(--board) * .16)` |
| **Số cỡ mã QR các vòng trước đều SAI** | báo 144/165/237px | đó là bề rộng **tờ giấy**, không phải vùng mã. Đệm `.55rem` ăn mất ~20px mỗi chiều |

Lỗi thứ ba đáng nhớ nhất: suốt §15–§16 tôi báo cáo cỡ mã bằng cách đo
`getBoundingClientRect()` của tờ giấy. Mã thật ở 320px chỉ ~120px chứ không phải
144px. Đã giảm đệm xuống `.35rem` (ảnh QR mang sẵn vùng lặng của nó, viền giấy
không phải vùng lặng) và từ nay đo bằng `img.offsetWidth`.

### Kết quả

| Kiểm | 320px | 390px | 1280px | +reduced motion |
|---|---|---|---|---|
| **Mã QR thật** (`img.offsetWidth`) | 127px | 147px | 217px | 147px |
| Bề rộng tờ giấy | 140px | 160px | 230px | 160px |
| Cột (rộng × cao) | 8×22 | 10×26 | 15×37 | 10×26 |
| Chân cột | 88,0% | 88,0% | 88,0% | 88,0% |
| Mép trên biển | 2,1% | 9,4% | 17,1% | 9,4% |
| Biển nằm trọn trong khung | ✅ | ✅ | ✅ | ✅ |
| Người bị biển che | **0/9** | **0/9** | **0/9** | **0/9** |
| `camTransform` lúc `arrived` | none | none | none | none |
| Biến hình của biển lúc nghỉ | identity | identity | identity | **none** |
| Animation đang chạy | 0 | 0 | 0 | **0** |
| Mã trong nhánh aria-hidden | không | không | không | không |
| Vùng `aria-live` trong `#gift` | 1 | 1 | 1 | 1 |
| Tràn ngang | 0 | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 | 0 |

Reduced motion cho `transform: none` — biển hiện thẳng ở trạng thái cuối, cột
không mọc, đúng như thiết kế (trạng thái nghỉ là "đã hiện đủ", phần ẩn nằm ở mốc
0% của keyframes).

### Chưa kiểm được

- **Chưa quét mã bằng camera thật.** Ở 320px mã thật chỉ 127px (~22mm) và nền là
  kem `#fffaf3` do `mix-blend-mode: multiply`. Vẫn là việc cần làm trước tiên.
- Thiệp riêng `/t/<mã>`, iOS/Android thật, Firefox/Safari, Lighthouse, fps: chưa.

## 18. Vòng 13 — Bé Phong Bì đứng chờ trong hình

- **Ngày QA:** 05.09.2026 · Chrome headless · 320 / 390 / 1280px + reduced motion

Chủ thiệp: *"bé phong bì hiện trong hình chứ ko phải ngoài hình."*

Ở chặng chờ, Bé cùng người đánh trống, lá cờ và ba nốt nhạc nổi trên nền giấy
phẳng phía trên nút — `#gift-stage` lúc đó còn `hidden`. Nay bỏ hẳn cụm ngoài
hình đó, bày sẵn khung tranh, Bé đứng chờ bên biển "Nhà bạn" trong tranh.

Đảo thứ tự: tranh trước, nút sau — thấy Bé rồi mới tới lời mời gọi.

### Ba chỗ phải cẩn thận

| Chỗ | Vì sao |
|---|---|
| Vị trí đứng chờ = mốc `0%` của `gift-ride` | lệch là vừa bấm Bé giật sang chỗ khác. Đo: **nhảy 0,5%** |
| Nhịp thở đặt ở ảnh, không ở track | `gift-idle-breathe` ghi đè cả `transform`, đặt lên track là mất vị trí |
| `.gift-action` phải tự khai `margin-top` | `.gift-route` trên nó mang margin **âm**; không khai thì nút đè lên đúng dòng chữ. Đã thấy bằng ảnh chụp, không phải suy đoán |

### Kết quả

| Kiểm | 320px | 390px | 1280px |
|---|---|---|---|
| Bé nằm trong khung tranh ở chặng chờ | ✅ | ✅ | ✅ |
| Bé hiện (opacity) | 1 | 1 | 1 |
| Chiều cao Bé | 59px | 74px | 116px |
| Nút đè lên dòng hành trình | không | không | không |
| Đoàn rước ở chặng chờ | ẩn (0) | ẩn (0) | ẩn (0) |
| Tràn ngang | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 |

Chuyến đi sau khi bấm không đổi: tới `arrived`, cột biển QR mọc lên, mã thật
147px, biển nằm trọn trong khung, 9 người, 1 vùng `aria-live`, 0 tràn ngang.

**Reduced motion:** vẫn 0 animation đang chạy. Lưu ý cách đo: lấy mẫu ở 400ms sau
khi bấm sẽ thấy 27 animation "đang chạy" — đó là ba chùm pháo hoa còn đợi hết
`animation-delay` (780–1110ms). Khối reduced-motion ép `transition-delay: 0` chứ
KHÔNG ép `animation-delay`, nhưng bản thân animation đã rút còn 0,01ms nên không
có chuyển động nào. Đo ở 1500ms cho đúng 0.

### Chưa kiểm được

- Chưa quét mã bằng camera thật (vẫn là việc cần làm trước tiên).
- Thiệp riêng `/t/<mã>`, iOS/Android thật, Firefox/Safari, Lighthouse: chưa.

## 19. Vòng 14 — bảng QR thành biển gỗ

- **Ngày QA:** 05.09.2026 · Chrome headless · 320 / 390 / 1280px

Chủ thiệp: *"QR có viền gỗ background màu gỗ luôn được không."*

Đổi mặt giấy kem thành **mặt gỗ nhạt + khung gỗ**, cho khớp với hai cái biển gỗ
vốn có trong tranh.

### Nền gỗ không đe doạ khả năng quét

Nghe thì ngược, nhưng ô mã là **đen tuyền** nên tương phản với gỗ vẫn rất cao:

| Mặt bảng | Tương phản với ô đen |
|---|---:|
| `#f2e2c8` | 16,5:1 |
| **`#ecd9b9`** (đang dùng, giữa dải gradient) | **15,2:1** |
| `#e6d0ac` | 14,0:1 |

Khác hẳn nền màu thường: nếu ô mã là màu thì nền gỗ mới thành vấn đề.

### Tông gỗ lấy mẫu từ chính bức tranh, không tự chọn

Bản đầu tôi chọn khung `#a9855a → #8e6d40` theo cảm nhận "màu gỗ". Dựng ra thì nó
đọc thành **vật liệu khác hẳn**, dán lên tranh chứ không thuộc về tranh.

Đọc pixel thật từ `gift-journey-scene.webp`:

| Chỗ lấy mẫu | Màu |
|---|---|
| Mặt biển "Nhà bạn" | `#fbf4e8` |
| Cột biển | `#ffe9d1` |
| Cột trái cổng cưới | `#f4ddc0` |
| Hàng rào gỗ (chỗ sẫm nhất tìm được) | `#d9b58f` |

Gỗ trong tranh **nhạt hơn nhiều** so với "màu gỗ" mặc định trong đầu. Đã đổi khung
sang `#d9b58f → #c69c6d` và mặt bảng `#f0dfc2 → #e6d0ac`.

### Khung ăn vào trong, không nở ra ngoài

Nở ra ngoài thì đụng đoàn người: ở 320px mép phải bảng ở 70%, mép trái người gần
nhất ở 71,6% — chỉ hở **4,5px**. Nên đệm khung ăn vào trong, mã hẹp lại ~7px mỗi
bên.

### Kết quả

| | 320px | 390px | 1280px |
|---|---|---|---|
| Mã QR thật | 124px | 144px | 214px |
| Bề rộng bảng | 140px | 160px | 230px |
| Người bị biển che | **0** | **0** | **0** |
| `mix-blend-mode` | multiply | multiply | multiply |
| Biển trọn trong khung | ✅ | ✅ | ✅ |
| Tràn ngang | 0 | 0 | 0 |
| Lỗi JS | 0 | 0 | 0 |

Mã ở 320px xuống còn **124px** (trước khi có khung gỗ là 127px). Đây là lần thứ
hai cỡ mã bị bào mỏng vì lý do thẩm mỹ — nếu lần quét thử bằng camera thật không
ăn, chỗ cần nới đầu tiên là `--board`, và nới thì phải tính lại dải đường của
chín người.

### Chưa kiểm được

- **Chưa quét mã bằng camera thật.** Giờ mã còn nằm trên nền GỖ chứ không phải
  trắng — tương phản tính ra 15,2:1 nhưng đó vẫn là phép tính, không phải phép thử.
- Thiệp riêng `/t/<mã>`, iOS/Android thật, Firefox/Safari: chưa.

## 20. Vòng 15 — biển "Nhà bạn" lệch khỏi cột

- **Ngày QA:** 05.09.2026 · Chrome headless · 600 / 900 / 1280px (biển chỉ hiện từ 600px)

Chủ thiệp: *"cái cột nhà bạn bị lệch."*

### Nguyên nhân

Mặt biển được cắt từ chính `gift-journey-scene.webp` rồi kéo ngang để chứa tên
khách. Nhưng **cái cột nằm trong ảnh nền, không giãn theo**:

| | Cũ | Mới |
|---|---|---|
| Gốc phóng | `4.8% 59%` (mép trái biển) | **`10% 59%` (chính cái cột)** |
| Scale | `2.2, 1.35` | `1.68, 1.5` |
| Biển trải | 4,1% → 32,7% | 0,8% → 22,6% |
| Tâm biển | 18,4% | 11,7% |
| **Lệch khỏi cột** | **8,4%** | **1,7%** |

Phóng quanh mép trái thì biển chỉ nở về một phía, chạy tới 32,7% trong khi cột
đứng yên ở ~10% — nửa phải tấm biển treo lơ lửng không có gì đỡ. Phóng quanh
chính cái cột thì biển nở đều hai bên. Chút lệch còn lại (1,7%) là đúng với bức
tranh gốc: đây là biển mũi tên, cột vốn nằm hơi lệch trái.

### Sửa kèm một lỗi CÓ SẴN trên main

Tên khách dài nhất trong danh sách thử (44 ký tự, "Gia đình Ông Bà Nguyễn Hoàng
Phương Thảo Anh") **tràn khỏi mặt gỗ** — dòng cuối rơi xuống mặt đường.

| | main | sau khi thu biển | sau khi sửa |
|---|---:|---:|---:|
| Chữ cần | 39px | 52px | 43px |
| Hộp có | 32px | 39px | **43px** |
| Thiếu | **7px** | **13px** | **0** |

Lỗi này **đã có trên main**, không phải do vòng này gây ra — thu bề ngang biển chỉ
làm nó nặng thêm. Đã nới cả bề ngang (`18.2cqw`) lẫn chiều cao (`scaleY 1.5`, hộp
`11.6cqh`) nên giờ cả ba tên thử đều vừa khít, `thiếu = 0`.

`guests.csv` chỉ là dữ liệu mồi (3 khách, dài nhất 14 ký tự) — danh sách thật nằm
trong Docker volume nên vẫn phải chịu được tên dài.

### Kết quả

| Tên thử | 600px | 900px | 1280px |
|---|---|---|---|
| "Nhà bạn" | vừa | vừa | vừa |
| "Gia đình cô Lê Thị Lan" (22) | vừa | vừa | vừa |
| 44 ký tự | vừa | vừa | vừa |

Chữ nằm trọn trong mặt biển ở mọi ca; 0 tràn ngang trang; 0 lỗi JS.

### Chưa kiểm được

- Chưa xem trên thiệp riêng `/t/<mã>` thật (chưa dựng app quanly ở máy) — tên ở đó
  do server đổ vào `#gift-origin-sign`, cùng phần tử đã thử ở đây nên rủi ro thấp.
- Chưa quét mã QR bằng camera thật.

## 21. Vòng 16 — thử cắt thớ gỗ thật từ tranh, và vì sao PHẢI BỎ

- **Ngày:** 05.09.2026

Chủ thiệp: *"bảng gỗ phải chung loại gỗ vs hình nha."*

Vòng 14 lấy đúng **màu** gỗ (đọc pixel từ tranh) nhưng vẽ bằng gradient CSS phẳng
— đúng màu mà sai chất liệu. Vòng này thử cắt thẳng thớ gỗ từ
`gift-journey-scene.webp`, dùng kỹ thuật của `.gift-origin-board-art`:

```css
background: url("./assets/gift-journey-scene.webp") 5.6% 60% / 1000% 1333% no-repeat;
```

**Kết quả: hỏng, đã revert.** Chủ thiệp xem xong nói gọn "xấu quá".

### Vì sao hỏng — con số

Vùng ván biển "Nhà bạn" trong ảnh gốc chỉ khoảng **112 × 56 pixel**. Kéo cho đầy
tấm bảng phải phóng **10× ngang, 13,3× dọc**. Ở mức đó thớ gỗ tan thành mảng loang
mờ, không còn ra vân gỗ.

Đây là giới hạn của chính bức tranh, không phải lỗi tham số: **trong
`gift-journey-scene.webp` không có mảng gỗ nào đủ lớn.** Đã thử năm vùng:

| Vùng | Vì sao không dùng được |
|---|---|
| Cột cổng (500%, 800%) | bắt cả hoa và cây |
| Nguyên tấm bảng cổng | khung + mặt đẹp, nhưng **mang theo hoa và rèm** — mà mã QR đứng ngay cạnh chính cái cổng đó, thành hai tấm bảng giống hệt nhau |
| Đáy bảng cổng | trên/dưới cắt cụt |
| Cột dọc cổng | lẫn cây cối |
| Ván biển "Nhà bạn" | mảng gỗ sạch duy nhất, nhưng chỉ 112×56px → nhoè |

Điều đáng ghi: tương phản thì **không** phải vấn đề. Ô mã là đen tuyền nên trên gỗ
thật vẫn đạt 14,9–19,2:1, còn tốt hơn gradient (15,2:1). Vấn đề thuần tuý là **độ
phân giải nguồn**.

### Cách đúng, chưa làm được

Phải **vẽ riêng một asset tấm biển gỗ** (`assets/gift-qr-board.webp`, nền trong
suốt, ~700×700, ≤25KB) cùng bút pháp màu nước với tranh. Đã giao cho một agent
sinh ảnh, nhưng **bị chặn**: máy chưa có `OPENAI_API_KEY`, cũng không có CLI sinh
ảnh nào. Đây là quyết định của chủ thiệp (dịch vụ ngoài, có tính phí), không phải
việc kỹ thuật.

Trong lúc chờ, giữ bản gỗ gradient của vòng 14: sạch, không nhoè, nhưng đúng là
chưa cùng chất liệu với tranh.

**Đừng thử lại hướng cắt-phóng-to.** Đã đo, đã dựng, đã nhìn — nguồn không đủ pixel.
