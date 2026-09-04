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
