# Handoff — Thiệp cưới online Khắc Doãn & Đông Tiên

## 1. Thông tin ticket

- **Loại:** Frontend Feature / Wedding Invitation
- **Trạng thái:** Ready for Development
- **Ưu tiên:** High
- **Thiết bị chính:** Mobile web
- **Ngôn ngữ:** Tiếng Việt
- **Ngày cưới:** 04.10.2026
- **Bản tham chiếu:** Thiệp cứng do khách hàng cung cấp

## 2. Mục tiêu

Xây dựng thiệp cưới online có cùng nhận diện với thiệp cứng: nền ivory, hoa watercolor hồng đào, đường viền coral mảnh, chữ viết tay mềm và khoảng trắng rộng.

Trải nghiệm chính:

1. Khách mở link và thấy bìa thiệp.
2. Nhấn **Mở thiệp** để xem lời mời.
3. Cuộn qua thông tin lễ cưới, đếm ngược và lịch tháng 10.
4. Có thể xem bản đồ, lưu ngày cưới và gửi RSVP.

Không sử dụng lại concept xanh đậm “Midnight Garden” của bản cũ.

## 3. Nội dung chính

### Cặp đôi

- Chú rể: **Phan Nguyễn Khắc Doãn**
- Cô dâu: **Trần Lưu Đông Tiên**
- Tên hiển thị ngắn: **Khắc Doãn & Đông Tiên**

### Buổi lễ

- Ngày: **Chủ nhật, 04.10.2026**
- Giờ: **12:00** (đón khách từ **11:30**)
- Âm lịch: **Ngày 24 tháng 08 năm Bính Ngọ**
- Địa điểm đang dùng trong prototype: **Nhà hàng Long Phụng, Khánh Hòa**
- Hạn RSVP đang dùng: **24.09.2026**

> Cần xác nhận lại địa chỉ đầy đủ, Google Maps URL và thông tin hai bên gia đình trước khi public.

## 4. Cấu trúc trang

### 4.1. Cover — bìa thiệp

- Chiếm toàn bộ viewport.
- Nền ngoài màu beige nhạt.
- Card ở giữa theo tỷ lệ **12:17**, giống thiệp in.
- Viền coral 1 px, bo góc khoảng 16 px.
- Hoa watercolor đặt ở góc trái trên và phải dưới.
- Nội dung theo thứ tự:
  - Monogram `KD ♡ ĐT`
  - `Wedding invitation`
  - `Khắc Doãn & Đông Tiên`
  - `04 · 10 · 2026`
  - Nút `Mở thiệp`
- Khi cover còn mở, khóa scroll của trang.

### 4.2. Lời mời

- Khung viền kép mảnh trên nền ivory.
- Hoa lớn ở góc trái trên và nhánh hoa ở góc phải dưới.
- Nội dung căn giữa:
  - `Trân trọng kính mời`
  - Lời mời ngắn
  - Họ tên chú rể
  - Trái tim
  - Họ tên cô dâu
  - Lời cảm ơn
- Tên riêng dùng script font, coral, là điểm nhấn lớn nhất.

### 4.3. Countdown

- Nền blush peach.
- Tiêu đề `Hẹn nhau vào một ngày tháng Mười`.
- Bốn chỉ số: ngày, giờ, phút, giây.
- Target time: `2026-10-04T12:00:00+07:00`.
- Khi đã qua ngày cưới, tất cả giá trị dừng ở `0`.

### 4.4. Thông tin lễ cưới

- Desktop: card lễ cưới bên trái, lịch trình bên phải.
- Mobile: nội dung giới thiệu trước, card lễ cưới phía sau.
- Card lễ cưới giữ tỷ lệ 12:17 và ngôn ngữ hình ảnh giống thiệp in.
- Lịch trình:
  - `11:30` — Đón khách & chụp ảnh
  - `12:00` — Lễ thành hôn & khai tiệc
- CTA:
  - `Xem bản đồ`
  - `Lưu ngày cưới`

### 4.5. Calendar

- Hiển thị lịch tháng 10 năm 2026.
- Tuần bắt đầu từ Chủ nhật.
- Ngày 04 được tô coral và có trái tim phía trên.
- Có hoa watercolor trang trí ở góc trái dưới.

### 4.6. RSVP

- Tiêu đề: `Bạn sẽ đến chung vui chứ?`
- Trường nhập:
  - Họ và tên — bắt buộc
  - Trạng thái tham dự — bắt buộc
  - Số người — 1 đến 4
  - Lời nhắn — không bắt buộc
- Sau khi submit:
  - Ẩn form.
  - Hiện lời cảm ơn.
  - Chuyển focus vào thông báo thành công.

> Prototype hiện chỉ xử lý RSVP ở frontend, chưa lưu dữ liệu. Production cần API hoặc dịch vụ nhận form.

### 4.7. Closing

- Nền ivory, nhiều khoảng trắng.
- Monogram nhỏ, dòng `Thank you!` và ngày cưới.
- Hoa watercolor nằm ngoài mép phải để tạo cảm giác nối tiếp.

## 5. Visual tokens

| Token | Giá trị | Cách dùng |
|---|---:|---|
| Paper | `#fffaf3` | Nền chính và card |
| Paper warm | `#fff6ec` | Nền phụ |
| Peach | `#ef9990` | Viền và chi tiết nhẹ |
| Peach dark | `#d77570` | Tên, CTA và điểm nhấn |
| Rose pale | `#f5d3c7` | Selection và nền blush |
| Ink | `#59473f` | Nội dung chính |
| Ink soft | `#7f6d65` | Nội dung phụ |
| Gold | `#aa8356` | Nhãn section và chi tiết champagne |

### Font

- Script: **Dancing Script**, weight 500–600.
- Serif: **Cormorant Garamond**, weight 400–500.
- Sans-serif: **Be Vietnam Pro**, weight 300–600.
- Font tối thiểu cho nội dung thường: 16 px.
- Text phụ không nhỏ hơn 12 px.

## 6. Asset

| File | Mục đích |
|---|---|
| `public/assets/floral-cluster.webp` | Cụm hoa lớn ở cover, hero và calendar |
| `public/assets/floral-branch.webp` | Nhánh hoa dọc ở cover, card và closing |
| `public/assets/nhac-cuoi.mp3` | Nhạc nền, phát khi bấm "Mở thiệp". Tuỳ chọn — thiếu file thì thiệp tự bỏ qua phần nhạc |

Yêu cầu asset:

- Giữ nền trong suốt.
- Không kéo méo ảnh.
- Dùng `object-fit: contain` hoặc kích thước theo chiều rộng.
- Không thay bằng CSS art, emoji hoặc SVG vẽ lại.

## 7. Animation specification

Animation cần rõ nhưng nhẹ, không dùng hiệu ứng cánh hoa rơi dày.

### Khi tải trang

- Cover card đi lên từ 28 px, scale từ `0.96 → 1`.
- Duration: **900 ms**.
- Easing: `cubic-bezier(.2,.8,.2,1)`.
- Hoa hai góc scale từ `0.78 → 1`, opacity từ `0 → 1`.
- Duration hoa: **1.25 s**.
- Text trên cover xuất hiện theo từng dòng, cách nhau khoảng **110 ms**.

### Khi mở thiệp

- Card đi lên nhẹ, `rotateX(8deg)` và scale về `0.9`.
- Cover fade out trong khoảng **650 ms**.
- Nội dung chính fade + scale vào trong khoảng **800–1000 ms**.
- Sau transition, chuyển focus vào navigation để hỗ trợ bàn phím.

### Khi cuộn trang

- Các section dùng reveal: opacity `0 → 1`, translateY `22 px → 0`.
- Duration: **750 ms**.
- Mỗi phần chỉ chạy một lần.
- Hoa hero có parallax nhẹ, giới hạn khoảng `-32 px` đến `42 px`.
- Các mốc lịch trình xuất hiện lần lượt, delay 100 ms.

### Animation lặp

- Hoa float/breathe rất nhẹ trong 6–8 giây.
- Ngày 04 có heartbeat mỗi **2.1 giây**.
- RSVP success scale và fade vào trong **650 ms**.

### Reduced motion

Khi `prefers-reduced-motion: reduce`:

- Tắt animation lặp.
- Duration transition gần bằng 0.
- Nội dung reveal phải hiển thị ngay.

## 8. Interaction specification

### Open invitation

- Trigger: click/tap nút `Mở thiệp`.
- Xóa trạng thái khóa scroll.
- Ẩn cover khỏi accessibility tree sau transition.
- Bay tên cô dâu chú rể từ bìa về đúng vị trí trong trang (`flyCoverNamesIntoPlace`).
- Phát nhạc nền `assets/nhac-cuoi.mp3` (loop). Chỉ phát từ chính cú bấm này —
  trình duyệt chặn nhạc không do người dùng chủ động.
- Vào nhạc bằng fade âm lượng 0 → 0.55 trong ~1.4s.
- Nút bật/tắt nhạc ở góc dưới phải, chỉ hiện khi đọc được file nhạc.
- Thiếu `assets/nhac-cuoi.mp3` thì bỏ qua nhạc và ẩn nút, thiệp vẫn chạy bình thường.
- Lựa chọn tắt nhạc lưu trong `sessionStorage` (`kd-dt-music-muted`).

### Save the date

- Tạo file `.ics` trực tiếp trên browser.
- Thời gian bắt đầu UTC: `20261004T050000Z`.
- Thời gian kết thúc UTC: `20261004T080000Z`.
- Tên file: `dam-cuoi-khac-doan-dong-tien.ics`.
- Hai lời nhắc kèm trong `.ics` (`VALARM`): trước **7 ngày** (`TRIGGER:-P7D`) và
  trước **1 ngày** (`-P1D`), đều rơi vào 12:00 giờ Việt Nam.
- Lối "Google Calendar" **không** mang được lời nhắc: URL template của Google
  không có tham số cho reminder, khách sẽ dùng mức nhắc mặc định của họ. Muốn có
  đúng hai mốc trên thì phải chọn "Apple Calendar / Outlook" để tải `.ics`.

### Hỏi vui "bạn có đến không"

- Hiện sau khi mở thiệp **15–20 giây** (ngẫu nhiên), mỗi phiên **một lần**
  (`sessionStorage: kd-dt-nudge-xong`).
- Không hiện nếu khách đã gửi phản hồi (panel `#success` đang có class `.show`).
- Bấm "không" → modal nhảy sang góc khác và nài thêm; tới bước 3 thì nút "không"
  biến mất. Bước nào cũng đóng được bằng Esc hoặc bấm ra ngoài — đây là trò đùa,
  không phải cái bẫy.
- Bấm "có" → đóng modal, chọn sẵn `attendance = yes`, focus ô đó rồi cuộn tới
  `#rsvp`. Phải hoãn một nhịp (`setTimeout 0`) vì `<dialog>` khi đóng trả focus
  về phần tử trước đó và trình duyệt cuộn theo focus.
- Nội dung từng bước nằm ở `NUDGE_STEPS` trong `script.js`.

### Google Maps

- Mở tab mới.
- Dùng URL địa điểm chính xác sau khi khách hàng xác nhận.
- Luôn thêm `rel="noopener"`.

### RSVP payload đề xuất

```json
{
  "name": "Nguyễn Văn A",
  "attendance": "yes",
  "guests": 2,
  "message": "Chúc hai bạn trăm năm hạnh phúc"
}
```

Nếu tích hợp backend, cần thêm:

- Loading state khi gửi.
- Disable submit để tránh gửi nhiều lần.
- Success state.
- Error state và retry.
- Chống spam/rate limit.

## 9. Responsive behavior

### Desktop — trên 820 px

- Navigation đầy đủ.
- Hero sử dụng bố cục rộng.
- Event và calendar chia hai cột.
- RSVP chia heading/form thành hai cột.

### Tablet — 541 đến 820 px

- Navigation chỉ giữ CTA xác nhận.
- Các section chính chuyển thành một cột.
- Card giữ chiều rộng tối đa khoảng 470 px.

### Mobile — tối đa 540 px

- Cover card chiếm khoảng 88% viewport width.
- Tên cô dâu/chú rể không tràn card.
- Countdown chia lưới 2×2.
- Event CTA xếp dọc, full width.
- RSVP form một cột.
- Không xuất hiện horizontal scroll.

## 10. Accessibility

- Có skip link đến nội dung chính.
- Tất cả input có label.
- Hoa trang trí dùng `alt=""` và `aria-hidden="true"`.
- Focus visible có outline coral.
- Màu nội dung phải đủ tương phản trên nền ivory/blush.
- Form success dùng `role="status"` và nhận focus sau submit.
- Không phụ thuộc animation để truyền tải thông tin.
- Hỗ trợ `prefers-reduced-motion`.

## 11. Cấu trúc dữ liệu đề xuất

```js
const wedding = {
  couple: {
    groom: {
      fullName: 'Phan Nguyễn Khắc Doãn',
      displayName: 'Khắc Doãn',
    },
    bride: {
      fullName: 'Trần Lưu Đông Tiên',
      displayName: 'Đông Tiên',
    },
  },
  ceremony: {
    startAt: '2026-10-04T12:00:00+07:00',
    endAt: '2026-10-04T15:00:00+07:00',
    lunarDate: '24 tháng 08 năm Bính Ngọ',
    venueName: 'Nhà hàng Long Phụng',
    venueAddress: '',
    mapsUrl: '',
  },
  rsvpDeadline: '2026-09-24T23:59:59+07:00',
}
```

Không hard-code nội dung vào nhiều section. Các section phải đọc từ cùng một object/config.

## 12. Acceptance criteria

- [ ] Giao diện bám đúng palette và phong cách của thiệp cứng.
- [ ] Cover card giữ tỷ lệ 12:17 và hiển thị tốt từ 320 px trở lên.
- [ ] Nhấn `Mở thiệp` chạy animation, mở khóa scroll và hiển thị nội dung.
- [ ] Countdown chạy đúng múi giờ `Asia/Ho_Chi_Minh`.
- [ ] Tháng 10/2026 hiển thị đúng, ngày 04 nằm vào Chủ nhật.
- [ ] Nút Google Maps mở đúng địa điểm sau khi cấu hình.
- [ ] Nút lưu ngày cưới tải file `.ics` hợp lệ.
- [ ] RSVP validate trường bắt buộc.
- [ ] RSVP có loading, success và error state khi tích hợp API.
- [ ] Animation cuộn chỉ chạy một lần và không gây giật layout.
- [ ] Tắt animation khi bật reduced motion.
- [ ] Không có horizontal scroll trên mobile.
- [ ] Không có lỗi JavaScript trong console.
- [ ] Lighthouse Accessibility mục tiêu từ 90 trở lên.

## 13. Ngoài phạm vi hiện tại

- Trang quản trị danh sách khách mời.
- Đăng nhập.
- Phát nhạc tự động khi vừa vào trang (nhạc chỉ chạy sau khi bấm "Mở thiệp").
- Album ảnh cưới.
- Mừng cưới qua QR/chuyển khoản.
- Chia sẻ thiệp cá nhân hóa theo từng khách.

Các tính năng trên chỉ triển khai khi có yêu cầu riêng.

## 14. File implementation hiện tại

- `index.html` — cấu trúc nội dung.
- `styles.css` — responsive, visual token và animation.
- `script.js` — mở thiệp, countdown, parallax, `.ics` và RSVP demo.
- `design-qa.md` — kết quả đối chiếu với thiệp cứng.

## 15. Definition of done

- Nội dung và địa chỉ đã được chủ thiệp xác nhận.
- Hoàn thành responsive QA trên desktop, tablet và mobile thật.
- Kiểm tra đầy đủ animation và reduced motion.
- Nút bản đồ và tải lịch hoạt động trên iOS Safari, Android Chrome và desktop.
- RSVP ghi dữ liệu thành công nếu backend nằm trong scope.
- Không còn lỗi P0/P1/P2 trong design QA.
- Bản production được deploy và kiểm tra lại sau deploy.
