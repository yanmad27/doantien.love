/* Nguồn dữ liệu duy nhất cho toàn trang (HANDOFF §11).
   Mọi section đọc từ đây qua data-w / data-w-href — không hard-code lặp lại. */
const wedding = {
  couple: {
    groom: { fullName: 'Phan Nguyễn Khắc Doãn', familyName: 'Phan Nguyễn', displayName: 'Khắc Doãn' },
    bride: { fullName: 'Trần Lưu Đông Tiên', familyName: 'Trần Lưu', displayName: 'Đông Tiên' },
  },
  ceremony: {
    startAt: '2026-10-04T12:00:00+07:00',
    endAt: '2026-10-04T15:00:00+07:00',
    // Khung giờ đón khách, hiển thị trên thẻ mời
    welcomeStartAt: '2026-10-04T11:15:00+07:00',
    welcomeEndAt: '2026-10-04T12:00:00+07:00',
    lunarDate: '24 tháng 08 năm Bính Ngọ',
    venueShortName: 'Long Phụng',
    venueName: 'Nhà hàng Long Phụng',
    venueRegion: 'Khánh Hòa',
    // không kèm tỉnh ở đây — venueRegion đã có, .ics ghép cả ba trường lại
    venueAddress: '1995 Hùng Vương, Cam Linh',
    // Link chủ thiệp xác nhận, ghim đúng địa điểm:
    // Trung Tâm Hội Nghị Tiệc Cưới Long Phụng, 1995 Hùng Vương, Cam Linh, Khánh Hòa
    // ftid 0x3170ed28fcc611f5:0x4ed743a805f534da
    mapsUrl: 'https://maps.app.goo.gl/YupybswuAHtPZ6ZY7',
  },
  rsvp: {
    deadline: '2026-09-24T23:59:59+07:00',
    // App quanly nhận và lưu phản hồi, nhưng đi qua chính domain thiệp
    // (Traefik trỏ /api/rsvp sang đó) nên không phụ thuộc DNS của subdomain.
    // Đặt null để quay lại chế độ demo.
    endpoint: 'https://doantien.love/api/rsvp',
  },
  gift: {
    // App quanly trả lời "khách này đã mừng cưới chưa" để thiệp chạy lời cảm ơn.
    // Chỉ có/chưa, không kèm số tiền. Đặt null để tắt hẳn phần hỏi.
    statusEndpoint: 'https://doantien.love/api/gift-status',
  },
  wishes: {
    // App quanly lưu và trả về sổ lời chúc, cũng đi qua domain thiệp như RSVP.
    // Đặt null để ẩn hẳn section — thà không có sổ còn hơn để khách viết vào
    // một cái hộp không ai nhận.
    endpoint: 'https://doantien.love/api/wishes',
  },
}

// khai báo sớm vì countdown chạy ngay lúc load, trước phần xử lý scroll bên dưới
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')

const TZ = 'Asia/Ho_Chi_Minh'
const WEEKDAYS = ['Chủ nhật', 'Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy']

const startAt = new Date(wedding.ceremony.startAt)
const endAt = new Date(wedding.ceremony.endAt)
const rsvpDeadline = new Date(wedding.rsvp.deadline)

/** Tách các thành phần ngày/giờ theo múi giờ lễ cưới, không phụ thuộc máy người xem. */
function partsInTZ(date, timeZone = TZ) {
  const fmt = new Intl.DateTimeFormat('en-GB', {
    timeZone, weekday: 'short', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const out = {}
  for (const { type, value } of fmt.formatToParts(date)) out[type] = value
  const weekdayIndex = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(out.weekday)
  return { ...out, weekdayIndex }
}

const c = partsInTZ(startAt)
const d = partsInTZ(rsvpDeadline)
const w1 = partsInTZ(new Date(wedding.ceremony.welcomeStartAt))
const w2 = partsInTZ(new Date(wedding.ceremony.welcomeEndAt))

/* Chuỗi hiển thị dẫn xuất từ config — mọi section dùng chung. */
const view = {
  'couple.groom.familyName': wedding.couple.groom.familyName,
  'couple.groom.displayName': wedding.couple.groom.displayName,
  'couple.bride.familyName': wedding.couple.bride.familyName,
  'couple.bride.displayName': wedding.couple.bride.displayName,
  'ceremony.dateDotted': `${c.day} · ${c.month} · ${c.year}`,
  'ceremony.dateDottedShort': `${c.day}.${c.month}.${c.year}`,
  'ceremony.dayMonth': `${c.day}.${c.month}`,
  'ceremony.dayMonthLong': `${c.day} tháng ${Number(c.month)}`,
  'ceremony.year': c.year,
  'ceremony.time': `${c.hour}:${c.minute}`,
  'ceremony.weekdayName': WEEKDAYS[c.weekdayIndex],
  'ceremony.welcomeWindow': `${w1.hour}:${w1.minute} – ${w2.hour}:${w2.minute}`,
  'ceremony.lunarDate': wedding.ceremony.lunarDate,
  'ceremony.venueShortName': wedding.ceremony.venueShortName,
  'ceremony.venueName': wedding.ceremony.venueName,
  'ceremony.venueRegion': wedding.ceremony.venueRegion,
  'ceremony.mapsUrl': wedding.ceremony.mapsUrl,
  'rsvp.deadlineDotted': `${d.day}.${d.month}.${d.year}`,
}

document.querySelectorAll('[data-w]').forEach((el) => {
  const value = view[el.dataset.w]
  if (value !== undefined) el.textContent = value
})
document.querySelectorAll('[data-w-href]').forEach((el) => {
  const value = view[el.dataset.wHref]
  if (value) el.href = value
})

/* ---------------------------------------------------------
   Mở thiệp (HANDOFF §8)
   --------------------------------------------------------- */

const cover = document.getElementById('cover')
const site = document.getElementById('site')
const openButton = document.getElementById('open-invitation')

document.documentElement.classList.add('is-locked')

/* Tắt khôi phục vị trí cuộn của trình duyệt: thiệp luôn phải bắt đầu từ bìa.
   Để mặc định thì F5 giữa trang xong bấm "Mở thiệp" là rơi thẳng vào giữa bài. */
if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'

/* Tên cô dâu chú rể là chữ duy nhất có mặt ở CẢ bìa lẫn trang thiệp. Để bìa tan
   rồi trang mới hiện thì mắt thấy đúng hai chữ đó biến mất rồi quay lại; cho hai
   lớp chồng nhau thì thành nhìn đôi. Nên thay vì tan, tên bay từ bìa về đúng vị
   trí của nó trong trang rồi nhường chỗ cho chữ thật — chữ có mặt liên tục suốt
   chuyển cảnh, không có khoảnh khắc nào mất.

   Bản sao phải nằm ngoài .cover vì .cover-card đang phóng to: mọi thứ bên trong
   nó, kể cả position: fixed, đều bị kéo theo transform của nó. */
const NAME_FLIGHT_MS = 780

/* Hiệu ứng bay dựa hoàn toàn vào phép đo vị trí, nên chỉ chạy khi chữ đã ổn định.
   KHÔNG dùng document.fonts.status để kiểm: ngay sau khi tải trang nó đã báo
   "loaded" chỉ vì chưa font nào được yêu cầu — font script chỉ bắt đầu tải lúc
   trình duyệt cần vẽ. Bấm mở trong khoảng đó thì đo bằng metrics của font dự
   phòng, font thật về giữa chừng là chữ đích dịch ~9px và thấy nhảy rõ (hay gặp
   trên điện thoại vì mạng và CPU chậm hơn).
   Cũng không chờ suông document.fonts.ready: nếu font script chưa được yêu cầu
   thì hàng đợi đang rỗng và nó cũng resolve ngay. Phải TỰ gọi fonts.load() cho
   đúng font dùng để vẽ tên, rồi mới chờ ready. */
let fontsSettled = false
if (document.fonts && document.fonts.load) {
  Promise.all([
    document.fonts.load('400 4rem "Great Vibes"'),
    document.fonts.load('500 1rem "Cormorant Garamond"'),
  ])
    .then(() => document.fonts.ready)
    .catch(() => null)
    .then(() => { fontsSettled = true })
} else {
  fontsSettled = true
}

function flyCoverNamesIntoPlace() {
  if (reducedMotion.matches || typeof Element.prototype.animate !== 'function') return false
  // chưa ổn định thì bỏ hiệu ứng bay, rơi về cross-fade — bản đó không đo đạc gì
  // nên không thể nhảy
  if (!fontsSettled) return false
  const sources = document.querySelectorAll('#cover-title span')
  const targets = document.querySelectorAll('.hero-frame .couple-name')
  if (!sources.length || sources.length !== targets.length) return false

  // đo đích ở trạng thái cuối: nếu khối hero còn đang chờ reveal thì vị trí đo
  // được là vị trí tạm, tên sẽ đáp trượt chỗ
  const heroFrame = document.querySelector('.hero-frame')
  if (heroFrame) heroFrame.classList.add('visible')

  const layer = document.createElement('div')
  layer.className = 'name-flight'
  layer.setAttribute('aria-hidden', 'true')
  document.body.appendChild(layer)

  const restore = []
  sources.forEach((source, i) => {
    const target = targets[i]
    // Range chứ không phải element rect: nguồn là span inline còn đích là block,
    // hai loại hộp cao thấp khác nhau. Range cho đúng hộp chữ của cả hai nên căn
    // mép trên + tỷ lệ bề rộng là baseline trùng nhau.
    const from = rangeRect(source)
    const to = rangeRect(target)
    if (!from.width || !to.width) return

    const cs = getComputedStyle(source)
    const clone = document.createElement('span')
    clone.textContent = source.textContent
    Object.assign(clone.style, {
      position: 'absolute',
      left: from.left + 'px',
      top: from.top + 'px',
      whiteSpace: 'pre',
      fontFamily: cs.fontFamily,
      fontSize: cs.fontSize,
      fontStyle: cs.fontStyle,
      fontWeight: cs.fontWeight,
      lineHeight: cs.lineHeight,
      letterSpacing: cs.letterSpacing,
      color: cs.color,
    })
    layer.appendChild(clone)

    /* Hộp element của bản sao KHÔNG trùng hộp chữ của nó: hộp element là hộp
       dòng, tính cả half-leading, mà line-height ở đây nhỏ hơn 1 nên half-leading
       âm và chữ tụt xuống cả chục px. Đặt left/top theo hộp chữ đo được ở nguồn
       mà không bù phần lệch này thì chữ nhảy một phát ngay khi bấm, rồi nhảy lần
       nữa lúc trao lại cho chữ thật. Đo hộp chữ của chính bản sao rồi bù. */
    const box = clone.getBoundingClientRect()
    const own = rangeRect(clone)
    const insetX = own.left - box.left
    const insetY = own.top - box.top
    clone.style.left = from.left - insetX + 'px'
    clone.style.top = from.top - insetY + 'px'
    // gốc scale đặt ngay tại góc hộp CHỮ, nhờ vậy translate bên dưới chỉ cần là
    // khoảng cách giữa hai hộp chữ, không phải bù thêm phần lệch nhân theo scale
    clone.style.transformOrigin = `${insetX}px ${insetY}px`

    // giấu chữ thật ở đích trong lúc bản sao đang bay, nếu không có hai chữ cùng lúc
    target.style.visibility = 'hidden'
    restore.push(() => { target.style.visibility = '' })

    clone.animate([
      { transform: 'none' },
      { transform: `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${to.width / from.width})` },
    ], { duration: NAME_FLIGHT_MS, easing: 'cubic-bezier(.62,.02,.34,1)', fill: 'both' })
  })

  if (!restore.length) {
    layer.remove()
    return false
  }
  // dọn bằng timeout chứ không chờ promise của animation: tab bị ẩn giữa chừng
  // thì promise không resolve, tên ở trang sẽ kẹt visibility: hidden vĩnh viễn
  window.setTimeout(() => {
    restore.forEach((fn) => fn())
    layer.remove()
  }, NAME_FLIGHT_MS)
  return true
}

/* ---------------------------------------------------------
   Hỏi vui "bạn có đến không"

   Hiện đúng lúc khách cuộn tới form xác nhận — hỏi ngay chỗ có thể trả lời,
   không cắt ngang lúc họ đang đọc. Bấm "không" thì modal nhảy sang góc khác và
   nài thêm ĐÚNG một câu nữa; bấm "không" lần hai là thôi. Đây là trò đùa nên
   bước nào cũng đóng được bằng Esc hoặc bấm ra ngoài.
   --------------------------------------------------------- */

/* Cờ nằm trong bộ nhớ chứ KHÔNG lưu sessionStorage: mỗi lần tải trang hỏi đúng
   một lần, tải lại thì hỏi lại (yêu cầu của chủ thiệp). Lưu vào storage là reload
   xong im luôn. */
let nudgeShown = false

/* Hai bước, mỗi bước một góc khác nhau cho có cảm giác "nó lại hiện chỗ khác".
   Bấm "không" ở bước cuối thì đóng hẳn — nài tới lần thứ ba là phiền. */
const NUDGE_STEPS = [
  {
    place: 'center',
    overline: 'Câu hỏi quan trọng',
    title: 'Bạn đến chứ?',
    copy: 'Tụi mình hỏi trước cho chắc, chứ tới ngày mà thiếu bạn thì kỳ lắm.',
    yes: 'Có chứ, đợi mình!',
    no: 'Chắc là không…',
  },
  {
    place: 'bottom-left',
    overline: 'Ơ khoan đã',
    title: 'Cưới có một lần thôi mà',
    copy: 'Tụi mình cưới đúng một lần duy nhất trong đời. Bạn không đến thì bàn tiệc dư một ghế, ảnh cưới thiếu một người, và tụi mình sẽ nhắc chuyện này tới già.',
    yes: 'Thôi được rồi, mình đến!',
    no: 'Vẫn không',
  },
]

const nudge = document.getElementById('nudge')
let nudgeReady = false

function renderNudgeStep(i) {
  const step = NUDGE_STEPS[i]
  nudge.dataset.place = step.place
  nudge.dataset.step = String(i)
  document.getElementById('nudge-overline').textContent = step.overline
  document.getElementById('nudge-title').textContent = step.title
  document.getElementById('nudge-copy').textContent = step.copy
  const yes = document.getElementById('nudge-yes')
  const no = document.getElementById('nudge-no')
  yes.textContent = step.yes
  no.textContent = step.no || ''
  no.hidden = !step.no
}

function closeNudge() {
  if (nudge.open) nudge.close()
}

if (nudge && typeof nudge.showModal === 'function') {
  let stepIndex = 0

  document.getElementById('nudge-no').addEventListener('click', () => {
    stepIndex += 1
    if (stepIndex >= NUDGE_STEPS.length) return declineForGuest()
    renderNudgeStep(stepIndex)
  })

  /* Nói "không" tới lần thứ hai thì gửi luôn phản hồi là không đến — khách đã
     trả lời rồi, bắt họ cuộn xuống điền lại y hệt là thừa. */
  function declineForGuest() {
    closeNudge()
    const attendance = document.querySelector('#rsvp-form [name="attendance"]')
    const nameField = document.querySelector('#rsvp-form [name="name"]')
    const rsvpForm = document.getElementById('rsvp-form')
    if (!attendance || !nameField || !rsvpForm) return
    attendance.value = 'no'
    // phát change để phần karaoke tự ẩn và xoá giá trị — gán .value không tự bắn
    attendance.dispatchEvent(new Event('change'))
    /* Hoãn 60ms chứ không phải 0: <dialog> đóng xong trình duyệt mới trả focus về
       phần tử trước đó, và cú trả focus đó xảy ra SAU một vòng lặp sự kiện — đặt
       focus ở nhịp 0 là bị nó ghi đè, con trỏ rơi về <body>. */
    window.setTimeout(() => {
      /* Không có tên thì không gửi thay được — trang chung không điền sẵn tên.
         Đưa khách tới ô tên thay vì bắn ra lỗi đỏ mà họ không hiểu vì sao. */
      if (!nameField.value.trim()) {
        // focus TRƯỚC rồi mới cuộn, giống nhánh nút "có" — làm ngược lại thì cú
        // cuộn nuốt mất focus và con trỏ không vào ô tên
        nameField.focus({ preventScroll: true })
        document.getElementById('rsvp').scrollIntoView({
          behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start',
        })
        return
      }
      rsvpForm.requestSubmit()
    }, 60)
  }

  document.getElementById('nudge-yes').addEventListener('click', () => {
    closeNudge()
    // Đưa thẳng tới ô xác nhận và điền sẵn "Có" — hỏi xong mà bắt khách tự đi
    // tìm form thì mất luôn cái đà vừa gật đầu.
    const attendance = document.querySelector('#rsvp-form [name="attendance"]')
    if (attendance) {
      attendance.value = 'yes'
      // phát change để phần karaoke bật/tắt theo — gán .value bằng JS không tự bắn
      attendance.dispatchEvent(new Event('change'))
    }
    const rsvp = document.getElementById('rsvp')
    if (!rsvp) return
    /* Phải hoãn sang khung hình sau: <dialog> lúc đóng trả focus về phần tử được
       focus trước đó (ở đây là logo trên nav, đầu trang) và trình duyệt cuộn theo
       focus. Cuộn ngay trong handler thì bị nó kéo ngược lên đầu ngay sau đó —
       nhìn như nút "Có" không làm gì cả. */
    window.setTimeout(() => {
      if (attendance) attendance.focus({ preventScroll: true })
      rsvp.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' })
    }, 0)
  })

  // bấm ra ngoài khung cũng đóng được: <dialog> không tự làm việc này
  nudge.addEventListener('click', (event) => {
    if (event.target === nudge) closeNudge()
  })
  nudge.addEventListener('close', closeNudge)

  nudgeReady = true
}

/* Gắn sau khi mở thiệp: canh form xác nhận lọt vào màn hình thì hỏi. Chỉ bắt đầu
   canh từ lúc mở thiệp, vì trước đó cả .site còn ẩn.

   Tự đo vị trí thay vì dùng IntersectionObserver: chỉ cần một phép đọc rect mỗi
   lần cuộn, gỡ listener ngay khi hỏi xong, và quan trọng hơn là kiểm chứng được
   bằng kịch bản tự động (callback của IO chỉ chạy khi trình duyệt thật sự vẽ). */
function armNudge() {
  if (!nudgeReady) return
  const form = document.getElementById('rsvp-form')
  if (!form) return

  const check = () => {
    if (nudgeShown) return window.removeEventListener('scroll', check)
    const rect = form.getBoundingClientRect()
    // mép trên form đã vào 80% dưới của màn hình = khách cuộn tới nơi rồi
    if (rect.top > window.innerHeight * 0.8 || rect.bottom < 0) return
    window.removeEventListener('scroll', check)
    /* Khách đã gửi phản hồi rồi thì thôi, hỏi nữa là vô duyên. Panel này ẩn/hiện
       bằng class .show (display: none trong CSS) chứ KHÔNG dùng thuộc tính hidden
       — kiểm .hidden thì lúc nào cũng false. */
    const success = document.getElementById('success')
    if (success && success.classList.contains('show')) return
    nudgeShown = true
    renderNudgeStep(0)
    nudge.showModal()
  }

  window.addEventListener('scroll', check, { passive: true })
  check() // phòng trường hợp form đã nằm sẵn trong màn hình
}

/* ---------------------------------------------------------
   Mừng cưới — QR giấu sau một nút bấm

   Bày QR ra giữa thiệp thì kỳ, nên để sau nút "Gửi lời chúc mừng từ xa": ai
   muốn mới mở. Bấm xong chạy vài dòng trạng thái cho vui rồi QR mới hiện.
   --------------------------------------------------------- */

/* Màn diễn trước khi hiện QR. Trò đùa nằm ở chỗ thanh tiến trình kẹt ở 99% —
   ai cũng từng chờ cái thanh đó. Tổng ~5.3s, đủ dài để đọc hết mà không chán. */
const GIFT_STEPS = [
  { text: 'Đang mở phong bì…', pct: 12, ms: 850 },
  { text: 'Đang phủi bụi mã QR…', pct: 41, ms: 850 },
  { text: 'Đang kiểm tra xem bạn có thật lòng không…', pct: 88, ms: 1000 },
  { text: 'Kết quả: thật lòng 100%. Đang xúc động…', pct: 99, ms: 1000 },
  { text: 'Vẫn 99%… thanh nào chẳng thế', pct: 99, ms: 1000 },
  { text: 'Thôi hiện luôn cho nhanh!', pct: 100, ms: 600 },
]

// Hỏi 5s một lần, bỏ cuộc sau 15 phút — quá lâu thì khách cũng đóng thiệp rồi.
const GIFT_POLL_MS = 5_000
const GIFT_POLL_GIVE_UP_MS = 15 * 60_000

const gift = document.getElementById('gift')
const giftQrImg = document.getElementById('gift-qr-img')

if (gift && giftQrImg) {
  /* Chỉ hiện cả khối khi trình duyệt xác nhận tải được ảnh QR. Thiếu file mà vẫn
     để nút thì khách bấm xong nhận một ô ảnh vỡ — thà không có còn hơn. */
  const showGift = () => { gift.hidden = false }
  if (giftQrImg.complete && giftQrImg.naturalWidth) showGift()
  giftQrImg.addEventListener('load', showGift)

  /* Thiệp riêng lấy QR từ qr.sepay.vn (mang mã khách). Dịch vụ đó chết mà ẩn
     luôn cả khối thì khách hết đường mừng cưới — nên thử QR tĩnh trong repo
     trước đã. Rơi về QR tĩnh thì vẫn chuyển khoản được, chỉ mất phần tự nhận ra
     ai vừa gửi. Ẩn khối là bước cuối cùng, khi đến ảnh dự phòng cũng hỏng. */
  giftQrImg.addEventListener('error', () => {
    const fallback = giftQrImg.dataset.qrFallback
    if (fallback && !giftQrImg.dataset.qrFellBack) {
      giftQrImg.dataset.qrFellBack = '1'
      giftQrImg.src = fallback
      return
    }
    gift.hidden = true
  })

  /* --- Lời cảm ơn khi tiền về ---
     Chỉ chạy trên thiệp riêng: thiệp chung không có mã khách nên không thể biết
     ai vừa chuyển. */
  const guestCode = document.body.dataset.guestCode || ''
  const guestName = document.body.dataset.guestName || ''
  const thanks = document.getElementById('gift-thanks')
  const giftQr = document.getElementById('gift-qr')
  const statusEndpoint = wedding.gift.statusEndpoint
  const THANKED_KEY = `kd-dt-gift-${guestCode}`

  /* Nhớ mốc thời gian của khoản mừng cưới mới nhất đã hiện lời cảm ơn.
     localStorage chứ không sessionStorage: khách đóng hẳn trình duyệt rồi mở
     lại thiệp thì vẫn phải nhớ, chứ không bày lại QR như chưa nhận gì.
     Máy chủ mới là nguồn thật — chỗ này chỉ để khỏi chớp một nhịp QR trong lúc
     chờ mạng trả lời, và để còn đúng khi khách mất mạng.
     Chế độ riêng tư ở vài máy chặn storage: hỏng chỗ này không được làm chết cả
     thiệp, giống cách phần nhạc nền đang làm. */
  function seenAt() {
    try { return window.localStorage.getItem(THANKED_KEY) || '' } catch { return '' }
  }
  function rememberAt(at) {
    try { window.localStorage.setItem(THANKED_KEY, at) } catch { /* chặn thì thôi */ }
  }

  let lastKnownAt = seenAt() // khoản mới nhất ĐÃ hiện lời cảm ơn
  let serverAt = ''          // khoản mới nhất máy chủ biết, cập nhật mỗi lần hỏi
  let wantMore = false       // khách vừa bấm "Mừng thêm lần nữa" ở lần xem này
  let pollTimer = 0
  let giveUpAt = 0

  function stopPolling() {
    window.clearInterval(pollTimer)
    pollTimer = 0
    document.removeEventListener('visibilitychange', onVisible)
  }

  function showThanks(at) {
    stopPolling()
    wantMore = false
    lastKnownAt = at || lastKnownAt || '1'
    rememberAt(lastKnownAt)
    if (!thanks) return
    const line = document.getElementById('gift-thanks-line')
    // Tên đã escape sẵn ở server; gán qua textContent nên không dựng HTML.
    if (line && guestName) line.textContent = `Tụi mình nhận được rồi, cảm ơn ${guestName}!`
    if (giftQr) giftQr.hidden = true
    thanks.hidden = false
  }

  async function fetchStatus() {
    try {
      const res = await fetch(`${statusEndpoint}?code=${encodeURIComponent(guestCode)}`, { cache: 'no-store' })
      if (!res.ok) return null
      return await res.json()
    } catch { return null } // mạng chập chờn thì để lần hỏi sau lo
  }

  async function checkGift() {
    if (Date.now() > giveUpAt) return stopPolling()
    const data = await fetchStatus()
    if (!data?.thanked) return
    serverAt = data.at || '1'
    /* So mốc thời gian chứ không chỉ hỏi "đã nhận chưa": sau khi bấm "Mừng thêm
       lần nữa" thì câu trả lời vẫn luôn là "đã nhận" (khoản cũ). Cứ thấy true mà
       hiện lời cảm ơn là QR bị giật đi ngay lúc khách đang quét mã để chuyển
       tiếp. Chỉ khoản MỚI mới được thay màn hình. */
    if (serverAt !== lastKnownAt) showThanks(serverAt)
  }

  function onVisible() {
    /* Quan trọng nhất của cả tính năng: khách chuyển tiền ở app ngân hàng rồi
       mới quay lại đây. Lúc tab bị ẩn, điện thoại thường treo luôn setInterval,
       nên phải hỏi lại ngay khi tab sáng lại thay vì chờ nhịp kế tiếp. */
    if (document.visibilityState === 'visible') checkGift()
  }

  function startPolling() {
    if (pollTimer || !guestCode || !statusEndpoint) return
    // QR dự phòng không mang mã khách nên có hỏi cũng không bao giờ khớp
    if (giftQrImg.dataset.qrFellBack) return
    giveUpAt = Date.now() + GIFT_POLL_GIVE_UP_MS
    pollTimer = window.setInterval(checkGift, GIFT_POLL_MS)
    document.addEventListener('visibilitychange', onVisible)
    checkGift()
  }

  /* Hỏi một lần ngay khi tải trang, không chờ khách bấm mở QR. Thiếu bước này
     thì "đã chuyển hay chưa" chỉ nằm trong storage của máy khách: đổi máy, xoá
     dữ liệu duyệt web, hay mở bằng trình duyệt khác là thiệp quên sạch và bày
     lại QR như chưa nhận gì. Máy chủ nhớ giùm nên reload kiểu gì cũng đúng. */
  if (guestCode && statusEndpoint) {
    fetchStatus().then((data) => { if (data?.thanked) serverAt = data.at || '1' })
  }

  document.getElementById('gift-open').addEventListener('click', (event) => {
    const button = event.currentTarget
    const stage = document.getElementById('gift-stage')
    const status = document.getElementById('gift-status')
    const qr = giftQr
    button.hidden = true
    stage.hidden = false

    const bar = document.getElementById('gift-bar')
    const reveal = () => {
      status.hidden = true
      bar.hidden = true
      /* Đã mừng cưới rồi thì đưa thẳng lời cảm ơn, đừng bày lại QR. Ưu tiên câu
         trả lời của máy chủ, rơi về storage khi mạng chưa kịp về. */
      const known = serverAt || lastKnownAt
      if (known && !wantMore) return showThanks(known)
      qr.hidden = false
      startPolling()
    }
    // Người bật giảm chuyển động thì bỏ màn diễn, hiện QR luôn — họ chỉ muốn
    // lấy mã chứ không muốn xem hoạt cảnh.
    if (reducedMotion.matches) return reveal()

    bar.hidden = false
    // mỗi bước một thời lượng riêng nên phải cộng dồn, không nhân đều được
    let at = 0
    GIFT_STEPS.forEach((step) => {
      window.setTimeout(() => {
        status.textContent = step.text
        bar.style.setProperty('--pct', step.pct + '%')
      }, at)
      at += step.ms
    })
    window.setTimeout(reveal, at)
  })

  /* Mừng thêm lần nữa: lấy lại QR mà KHÔNG diễn lại 5.3 giây — khách đã xem một
     lần rồi, bắt xem lại chỉ tổ phiền. Lời cảm ơn quay lại khi có khoản mới. */
  const moreButton = document.getElementById('gift-more')
  if (moreButton) {
    moreButton.addEventListener('click', () => {
      wantMore = true
      if (thanks) thanks.hidden = true
      if (giftQr) giftQr.hidden = false
      startPolling()
    })
  }
}

/* ---------------------------------------------------------
   Nhạc nền
   --------------------------------------------------------- */

const music = document.getElementById('wedding-music')
const musicToggle = document.getElementById('music-toggle')
const MUSIC_VOLUME = 0.55
const MUSIC_MUTED_KEY = 'kd-dt-music-muted'

/* Lựa chọn tắt nhạc của khách phải được tôn trọng khi họ mở lại thiệp trong
   cùng phiên. sessionStorage bị chặn ở chế độ riêng tư trên một số máy nên
   luôn bọc try/catch, hỏng chỗ này không được làm chết cả thiệp. */
function readMuted() {
  try {
    return window.sessionStorage.getItem(MUSIC_MUTED_KEY) === '1'
  } catch (error) {
    return false
  }
}

function writeMuted(value) {
  try {
    window.sessionStorage.setItem(MUSIC_MUTED_KEY, value ? '1' : '0')
  } catch (error) {
    /* không lưu được thì thôi, không ảnh hưởng phiên hiện tại */
  }
}

function syncMusicToggle() {
  if (!musicToggle) return
  const playing = Boolean(music) && !music.paused
  musicToggle.classList.toggle('is-muted', !playing)
  musicToggle.setAttribute('aria-pressed', playing ? 'false' : 'true')
  musicToggle.setAttribute('aria-label', playing ? 'Tắt nhạc' : 'Bật nhạc')
}

/* Vào nhạc bằng cách dâng dần âm lượng: đúng lúc bấm mở thiệp mà nhạc vào ngay
   ở mức đầy thì giật mình, nhất là khi khách đang để loa to. */
function fadeInMusic() {
  if (!music) return
  const steps = 24
  let step = 0
  const timer = window.setInterval(() => {
    step += 1
    music.volume = Math.min(MUSIC_VOLUME, (step / steps) * MUSIC_VOLUME)
    if (step >= steps) window.clearInterval(timer)
  }, 60)
}

function startMusic() {
  if (!music || readMuted()) return
  // Hạ volume TRƯỚC khi play() và dâng lên ngay, không chờ promise: promise của
  // play() có thể resolve rất trễ (hoặc không) tuỳ trình duyệt/thiết bị âm thanh,
  // mà chờ nó mới fade thì trong lúc chờ nhạc đã phát ở volume 1.0 — to hơn hẳn
  // mức định và đúng vào lúc khách vừa bấm mở thiệp.
  music.volume = 0
  const attempt = music.play()
  fadeInMusic()
  // Phải bắt reject của play(), nếu không console đầy unhandled rejection.
  // Nhưng KHÔNG được ẩn nút ở đây:
  //  - khách bấm tắt nhạc trong lúc play() còn đang chờ thì chính pause() đó làm
  //    promise reject bằng AbortError — ẩn nút là khách mất luôn đường bật lại;
  //  - trình duyệt chặn (NotAllowedError) thì lại càng cần nút để bấm thủ công.
  // Nút chỉ ẩn khi bản thân file nhạc hỏng/thiếu, xử lý ở sự kiện 'error' bên dưới.
  if (attempt && typeof attempt.catch === 'function') {
    attempt.catch(syncMusicToggle)
  }
}

function hideMusicToggle() {
  if (musicToggle) musicToggle.hidden = true
}

/* Gọi khi mở thiệp. Trường hợp khách đã tắt nhạc từ trước vẫn phải nạp metadata:
   không nạp thì không biết file có tồn tại, nút sẽ không hiện và khách mất luôn
   đường bật nhạc lại. */
function prepareMusic() {
  if (!music) return
  music.preload = 'metadata'
  if (readMuted()) {
    music.load()
    return
  }
  startMusic()
}

if (music && musicToggle) {
  // Chỉ hiện nút khi trình duyệt xác nhận đọc được file: thiếu assets/nhac-cuoi.mp3
  // thì thà không có nút, hơn là nút bấm mà không ra tiếng.
  music.addEventListener('loadedmetadata', () => {
    if (document.body.classList.contains('opened')) {
      musicToggle.hidden = false
      syncMusicToggle()
    }
  })
  music.addEventListener('error', hideMusicToggle)
  music.addEventListener('play', syncMusicToggle)
  music.addEventListener('pause', syncMusicToggle)

  musicToggle.addEventListener('click', () => {
    if (music.paused) {
      writeMuted(false)
      startMusic()
    } else {
      music.pause()
      writeMuted(true)
    }
    syncMusicToggle()
  })
}

function rangeRect(element) {
  const range = document.createRange()
  range.selectNodeContents(element)
  return range.getBoundingClientRect()
}

function openInvitation() {
  // Thứ tự bắt buộc: mở khoá cuộn TRƯỚC (thanh cuộn xuất hiện làm hẹp viewport,
  // đo trước khi mở khoá thì tên đáp lệch đúng bằng bề rộng thanh cuộn), rồi đo
  // và cất cánh, rồi mới gắn .opened — gắn .opened trước thì phép đo đọc phải
  // trạng thái cuối của bìa (đã phóng to) chứ không phải vị trí đang thấy.
  document.documentElement.classList.remove('is-locked')
  document.body.classList.remove('is-closed')
  /* Về đầu trang NGAY khi vừa mở khoá cuộn. Tải lại trang giữa chừng thì trình
     duyệt nhớ vị trí cuộn cũ, nhưng lúc bìa còn đóng thì overflow: hidden nên
     không thấy; vừa bỏ khoá là nó nhảy phịch xuống chỗ cũ — mở thiệp ra đã thấy
     ngay section 2, 3 thay vì phần đầu, và animation bay tên cũng đáp sai chỗ vì
     đo trước lúc nhảy. Phải đặt trước flyCoverNamesIntoPlace() để phép đo đọc
     đúng toạ độ cuối. */
  window.scrollTo(0, 0)
  if (flyCoverNamesIntoPlace()) document.body.classList.add('names-flying')
  document.body.classList.add('opened')
  site.setAttribute('aria-hidden', 'false')
  // Phải gọi ngay trong handler của click: trình duyệt chỉ cho phát nhạc khi
  // lệnh play() nằm trong cùng cử chỉ của người dùng, đẩy vào setTimeout là bị chặn.
  prepareMusic()
  // Canh form xác nhận từ lúc mở thiệp, không phải lúc tải trang: trước đó cả
  // .site còn ẩn nên phép quan sát không có nghĩa.
  armNudge()
  // 950ms = lúc bìa đã tan hẳn (xem khối chuyển cảnh trong styles.css).
  // Nhả focus sớm hơn thì màn hình giật về nav lúc bìa còn đang mở dở.
  window.setTimeout(() => {
    cover.setAttribute('aria-hidden', 'true')
    // Dời focus vào thanh nav, KHÔNG vào .nav-brand: focus thẳng vào link thì
    // trình duyệt vẽ vòng :focus-visible quanh trái tim ở mọi lần mở thiệp.
    // Thanh nav mang tabindex="-1" nên không bao giờ tab tới được, tắt vòng của
    // nó không mất gì; link trái tim vẫn giữ vòng khi có người tab tới thật.
    nav.focus({ preventScroll: true })
  }, reducedMotion.matches ? 0 : 950)
}

openButton.addEventListener('click', openInvitation)

// Skip link trỏ vào #invitation nằm trong .site — phải mở thiệp trước thì đích mới hiển thị
document.querySelector('.skip-link').addEventListener('click', () => {
  if (document.body.classList.contains('is-closed')) openInvitation()
})

/* ---------------------------------------------------------
   Countdown (HANDOFF §4.3)
   --------------------------------------------------------- */

const pad = (value, size = 2) => String(value).padStart(size, '0')
const countdownFields = {
  days: document.getElementById('days'),
  hours: document.getElementById('hours'),
  minutes: document.getElementById('minutes'),
  seconds: document.getElementById('seconds'),
}

let countdownTimer = 0

// Lớp .tick tự gỡ khi animation kết thúc → lần đổi sau add lại là chạy tiếp,
// không cần ép reflow để restart animation.
Object.values(countdownFields).forEach((el) => {
  el.addEventListener('animationend', () => el.classList.remove('tick'))
})

function setDigits(el, value) {
  if (el.textContent === value) return
  el.textContent = value
  if (!reducedMotion.matches) el.classList.add('tick')
}

function updateCountdown() {
  const distance = Math.max(0, startAt.getTime() - Date.now())
  setDigits(countdownFields.days, pad(Math.floor(distance / 86400000)))
  setDigits(countdownFields.hours, pad(Math.floor((distance % 86400000) / 3600000)))
  setDigits(countdownFields.minutes, pad(Math.floor((distance % 3600000) / 60000)))
  setDigits(countdownFields.seconds, pad(Math.floor((distance % 60000) / 1000)))
  if (distance === 0) window.clearInterval(countdownTimer)
}

updateCountdown()
countdownTimer = window.setInterval(updateCountdown, 1000)

/* ---------------------------------------------------------
   Lưu ngày cưới → .ics (HANDOFF §8)
   --------------------------------------------------------- */

const toIcsUtc = (date) => date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')
const escapeIcs = (text) => String(text).replace(/([\\;,])/g, '\\$1').replace(/\n/g, '\\n')

/** RFC 5545 giới hạn 75 octet mỗi dòng — gấp dòng theo byte để không vỡ ký tự tiếng Việt. */
function foldIcsLine(line) {
  const bytes = new TextEncoder().encode(line)
  if (bytes.length <= 75) return line
  const chunks = []
  let current = ''
  let currentBytes = 0
  let limit = 75
  for (const char of line) {
    const size = new TextEncoder().encode(char).length
    if (currentBytes + size > limit) {
      chunks.push(current)
      current = char
      currentBytes = size
      limit = 74 // các dòng tiếp theo có thêm 1 octet khoảng trắng ở đầu
    } else {
      current += char
      currentBytes += size
    }
  }
  chunks.push(current)
  return chunks.join('\r\n ')
}

/* Nhắc trước 7 ngày và 1 ngày. Mỗi VALARM phải có UID + đúng thứ tự thuộc tính
   để Apple Calendar chịu nhận; thiếu ACTION/DESCRIPTION là nó bỏ qua lời nhắc
   mà vẫn nhập sự kiện bình thường, nên lỗi rất khó phát hiện. */
const ICS_REMINDERS = [
  { trigger: '-P7D', label: 'Còn 7 ngày nữa' },
  { trigger: '-P1D', label: 'Ngày mai rồi!' },
]

function icsAlarms(title) {
  return ICS_REMINDERS.flatMap(({ trigger, label }, i) => [
    'BEGIN:VALARM',
    `UID:nhac-${i}-khac-doan-dong-tien@doantien.love`,
    `TRIGGER:${trigger}`,
    'ACTION:DISPLAY',
    `DESCRIPTION:${escapeIcs(`${label} — ${title}`)}`,
    'END:VALARM',
  ])
}

function saveDate() {
  const { venueName, venueRegion, venueAddress } = wedding.ceremony
  const location = [venueName, venueAddress, venueRegion].filter(Boolean).join(', ')
  const title = `Lễ thành hôn ${wedding.couple.groom.displayName} & ${wedding.couple.bride.displayName}`
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'CALSCALE:GREGORIAN',
    'PRODID:-//doantien.love//Wedding Invitation//VI',
    'BEGIN:VEVENT',
    'UID:khac-doan-dong-tien-04102026@doantien.love',
    `DTSTAMP:${toIcsUtc(new Date())}`,
    `DTSTART:${toIcsUtc(startAt)}`,
    `DTEND:${toIcsUtc(endAt)}`,
    `SUMMARY:${escapeIcs(title)}`,
    `LOCATION:${escapeIcs(location)}`,
    `DESCRIPTION:${escapeIcs(`Trân trọng mời bạn đến chung vui cùng ${wedding.couple.groom.displayName} và ${wedding.couple.bride.displayName}.`)}`,
    ...icsAlarms(title),
    'END:VEVENT',
    'END:VCALENDAR',
  ]
  const calendar = lines.map(foldIcsLine).join('\r\n') + '\r\n'
  const url = URL.createObjectURL(new Blob([calendar], { type: 'text/calendar;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'dam-cuoi-khac-doan-dong-tien.ics'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/** Link mở thẳng Google Calendar với sự kiện đã điền sẵn. */
function googleCalendarUrl() {
  const { venueName, venueAddress, venueRegion } = wedding.ceremony
  const location = [venueName, venueAddress, venueRegion].filter(Boolean).join(', ')
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: `Lễ thành hôn ${wedding.couple.groom.displayName} & ${wedding.couple.bride.displayName}`,
    dates: `${toIcsUtc(startAt)}/${toIcsUtc(endAt)}`,
    details: `Trân trọng mời bạn đến chung vui cùng ${wedding.couple.groom.displayName} và ${wedding.couple.bride.displayName}.`,
    location,
  })
  return `https://calendar.google.com/calendar/render?${params}`
}

/* Menu chọn loại lịch. Không tự đoán theo hệ điều hành: người dùng iPhone vẫn
   có thể xài Google Calendar và ngược lại. */
const saveButton = document.querySelector('.save-date')
const saveMenu = document.getElementById('save-menu')
document.getElementById('save-google').href = googleCalendarUrl()

function positionSaveMenu() {
  const rect = saveButton.getBoundingClientRect()
  const gap = 8
  // trên mobile nút chiếm hết chiều ngang — cho menu rộng bằng nút cho gọn mắt
  saveMenu.style.minWidth = `${Math.max(232, rect.width)}px`
  const left = Math.min(rect.left, window.innerWidth - saveMenu.offsetWidth - 12)
  saveMenu.style.left = `${Math.max(12, left)}px`
  const spaceBelow = window.innerHeight - rect.bottom
  saveMenu.style.top = spaceBelow > saveMenu.offsetHeight + gap + 8
    ? `${rect.bottom + gap}px`
    : `${Math.max(12, rect.top - saveMenu.offsetHeight - gap)}px`
}

function closeSaveMenu(returnFocus) {
  if (saveMenu.hidden) return
  saveMenu.hidden = true
  saveButton.setAttribute('aria-expanded', 'false')
  document.removeEventListener('pointerdown', onPointerOutside, true)
  document.removeEventListener('keydown', onSaveMenuKey)
  window.removeEventListener('scroll', onAnchorMoved)
  window.removeEventListener('resize', onAnchorMoved)
  if (returnFocus) saveButton.focus()
}

function onPointerOutside(event) {
  if (!saveMenu.contains(event.target) && event.target !== saveButton) closeSaveMenu(false)
}
function onSaveMenuKey(event) {
  if (event.key === 'Escape') closeSaveMenu(true)
}
/* Bám theo nút chứ không đóng menu: trên điện thoại thanh địa chỉ ẩn/hiện bắn
   scroll liên tục, đóng theo scroll thì menu tắt ngay khi vừa mở. */
let anchorRaf = 0
function onAnchorMoved() {
  if (anchorRaf) return
  anchorRaf = window.requestAnimationFrame(() => {
    anchorRaf = 0
    if (!saveMenu.hidden) positionSaveMenu()
  })
}

saveButton.addEventListener('click', () => {
  if (!saveMenu.hidden) return closeSaveMenu(true)
  saveMenu.hidden = false
  positionSaveMenu()
  saveButton.setAttribute('aria-expanded', 'true')
  saveMenu.querySelector('.save-menu-item').focus({ preventScroll: true })
  document.addEventListener('pointerdown', onPointerOutside, true)
  document.addEventListener('keydown', onSaveMenuKey)
  window.addEventListener('scroll', onAnchorMoved, { passive: true })
  window.addEventListener('resize', onAnchorMoved)
})

document.getElementById('save-google').addEventListener('click', () => closeSaveMenu(false))
document.getElementById('save-ics').addEventListener('click', () => {
  saveDate()
  closeSaveMenu(true)
})

/* ---------------------------------------------------------
   RSVP (HANDOFF §4.6 + §8)
   --------------------------------------------------------- */

const form = document.getElementById('rsvp-form')
const success = document.getElementById('success')
const formError = document.getElementById('rsvp-error')
const submitButton = form.querySelector('[type="submit"]')
const submitLabel = submitButton.textContent

const RULES = [
  { name: 'name', errorId: 'err-name', message: 'Vui lòng nhập họ và tên.' },
  { name: 'attendance', errorId: 'err-attendance', message: 'Vui lòng chọn phản hồi của bạn.' },
]

function validate() {
  let firstInvalid = null
  for (const rule of RULES) {
    const field = form.elements[rule.name]
    const isEmpty = !field.value.trim()
    document.getElementById(rule.errorId).textContent = isEmpty ? rule.message : ''
    field.setAttribute('aria-invalid', isEmpty ? 'true' : 'false')
    if (isEmpty && !firstInvalid) firstInvalid = field
  }
  if (firstInvalid) firstInvalid.focus()
  return !firstInvalid
}

/* Trang riêng của từng khách (/t/<mã>) gắn sẵn data-guest-* lên <body>.
   Trang chung không có thuộc tính này nên guest.name rỗng. */
const guest = {
  name: document.body.dataset.guestName || '',
  code: document.body.dataset.guestCode || '',
  seats: Number(document.body.dataset.guestSeats) || 0,
}

if (guest.name && !form.elements.name.value) {
  form.elements.name.value = guest.name
}

/* Điền sẵn số người theo số chỗ chủ thiệp đã mời cho khách này.
   Ô chọn chỉ có sẵn 1–4 vì đa số khách đi lẻ; mời cả nhà đông hơn thì sinh thêm
   lựa chọn cho vừa, chứ không im lặng cắt xuống 4 rồi để chủ thiệp thiếu mâm. */
if (guest.seats > 0) {
  const select = form.elements.guests
  const max = Math.max(guest.seats, select.options.length)
  for (let n = select.options.length + 1; n <= max; n += 1) {
    select.add(new Option(`${n} người`, String(n)))
  }
  select.value = String(guest.seats)
}

/* Karaoke chỉ hỏi khi khách báo sẽ đến, và ô "bài tủ" chỉ hiện khi họ nhận hát.
   Ẩn tới đâu thì xoá giá trị tới đó — không thì khách đổi ý sang "không đến" mà
   vẫn gửi kèm tên bài, chủ thiệp đọc xong không hiểu ai hát. */
const karaBox = document.getElementById('kara')
const karaSong = document.getElementById('kara-song')

function syncKaraoke() {
  const going = form.elements.attendance.value === 'yes'
  karaBox.hidden = !going
  if (!going) form.elements.karaoke.value = ''
  const singing = going && form.elements.karaoke.value === 'yes'
  karaSong.hidden = !singing
  if (!singing) form.elements.song.value = ''
}

form.elements.attendance.addEventListener('change', syncKaraoke)
form.elements.karaoke.addEventListener('change', syncKaraoke)
syncKaraoke()

function buildPayload() {
  const payload = {
    name: form.elements.name.value.trim(),
    attendance: form.elements.attendance.value,
    guests: Number(form.elements.guests.value),
    karaoke: form.elements.karaoke.value,
    song: form.elements.song.value.trim(),
    message: form.elements.message.value.trim(),
  }
  if (guest.code) payload.guestCode = guest.code
  return payload
}

function sendRsvp(payload) {
  if (!wedding.rsvp.endpoint) return Promise.resolve() // chế độ demo: chưa gắn backend
  return fetch(wedding.rsvp.endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((response) => {
    if (!response.ok) throw new Error(`RSVP failed: ${response.status}`)
  })
}

function setLoading(isLoading) {
  submitButton.disabled = isLoading
  submitButton.textContent = isLoading ? 'Đang gửi...' : submitLabel
}

form.addEventListener('submit', (event) => {
  event.preventDefault()
  formError.textContent = ''
  if (!validate()) return

  setLoading(true)
  sendRsvp(buildPayload())
    .then(() => {
      form.hidden = true
      // Khách vừa báo không đến mà vẫn đọc "mong được gặp bạn trong ngày vui"
      // thì vô duyên — đổi lời theo đúng câu trả lời.
      const line = success.querySelector('p')
      if (line) {
        line.textContent = form.elements.attendance.value === 'no'
          ? 'Chúng mình đã ghi nhận. Tiếc là lần này chưa gặp được bạn, hẹn bạn dịp khác nhé.'
          : 'Chúng mình đã ghi nhận phản hồi và mong được gặp bạn trong ngày vui.'
      }
      success.classList.add('show')
      success.focus()
    })
    .catch(() => {
      formError.textContent = 'Gửi phản hồi chưa thành công. Bạn thử lại giúp mình nhé.'
    })
    .finally(() => setLoading(false))
})

RULES.forEach((rule) => {
  form.elements[rule.name].addEventListener('input', () => {
    if (form.elements[rule.name].value.trim()) {
      document.getElementById(rule.errorId).textContent = ''
      form.elements[rule.name].setAttribute('aria-invalid', 'false')
    }
  })
})

/* ---------------------------------------------------------
   Sổ lời chúc — tường giấy nhớ

   Chủ ý thiết kế, đừng "cải tiến" ngược lại: KHÔNG hiện tổng số lời chúc, KHÔNG
   hiện dấu thời gian, KHÔNG đảo mới nhất lên đầu, KHÔNG phân trang. Bộ khuôn đó
   (avatar tròn + tên đậm + "5 phút trước" + nút xem thêm) là khuôn của mục đánh
   giá sản phẩm; người đọc nhận ra nó trước cả khi kịp đọc nội dung. Sổ thật thì
   mỗi người một nét chữ, không ai đánh số và không ai sắp xếp lại.

   Nét chữ / màu giấy / độ nghiêng do server quyết (xem wishStyle trong
   admin/server.js) để trang thiệp và bản in PDF hiện y hệt nhau.
   --------------------------------------------------------- */

const wishSection = document.getElementById('wishes')

if (wishSection && wedding.wishes.endpoint) {
  const wishForm = document.getElementById('wish-form')
  const wishWall = document.getElementById('wish-wall')
  const wishEmpty = document.getElementById('wish-empty')
  const wishThanks = document.getElementById('wish-thanks')
  const wishError = document.getElementById('wish-error')
  const wishSubmit = wishForm.querySelector('[type="submit"]')
  const wishSubmitLabel = wishSubmit.textContent

  const WISH_FONTS = 4 // khớp .wish--f0..f3 trong styles.css
  const WISH_PAPERS = 5 // khớp .wish--p0..p4

  /* Bỏ [hidden] NGAY, trước khi observer reveal ở khối dưới bắt đầu quan sát:
     phần tử đang hidden không có layout nên IntersectionObserver không bao giờ
     bắn, và mọi [data-reveal] bên trong sẽ kẹt ở opacity 0 vĩnh viễn. */
  wishSection.hidden = false
  const successNext = document.getElementById('success-next')
  if (successNext) successNext.hidden = false

  const wishIndex = (value, max) => (Number.isInteger(value) && value >= 0 && value < max ? value : 0)

  function wishNode(wish) {
    const note = document.createElement('figure')
    note.className = `wish wish--f${wishIndex(wish.font, WISH_FONTS)} wish--p${wishIndex(wish.paper, WISH_PAPERS)}`
    if (wish.id) note.dataset.id = wish.id
    const tilt = Number(wish.tilt)
    note.style.setProperty('--tilt', `${Number.isFinite(tilt) ? tilt : 0}deg`)

    const body = document.createElement('blockquote')
    // textContent chứ không innerHTML: đây là chữ người lạ gửi lên
    body.textContent = String(wish.text || '')
    const sign = document.createElement('figcaption')
    sign.textContent = String(wish.name || '')
    note.append(body, sign)
    return note
  }

  /* Chỉ báo tường trống khi đã đọc được dữ liệu. Gọi API hỏng mà vẫn thông báo
     "chưa ai viết gì" là nói sai — có thể đang có cả trăm mẩu ở đó. */
  let wishesLoaded = false
  const syncWishEmpty = () => { wishEmpty.hidden = !wishesLoaded || wishWall.childElementCount > 0 }

  function loadWishes() {
    fetch(wedding.wishes.endpoint, { headers: { Accept: 'application/json' } })
      .then((response) => {
        if (!response.ok) throw new Error(`Wishes failed: ${response.status}`)
        return response.json()
      })
      .then((data) => {
        const list = Array.isArray(data.wishes) ? data.wishes : []
        /* Khách viết xong trước khi tải xong thì mẩu của họ đã nằm trên tường,
           mà bản tải về cũng có thể đã chứa nó — bỏ qua id đã vẽ để không hiện
           thành hai mẩu giống hệt nhau. */
        const drawn = new Set([...wishWall.children].map((node) => node.dataset.id))
        const frame = document.createDocumentFragment()
        list.forEach((wish) => { if (!drawn.has(wish.id)) frame.append(wishNode(wish)) })
        wishWall.prepend(frame) // chèn TRƯỚC mẩu khách vừa ghim, giữ đúng thứ tự sổ
        wishesLoaded = true
        syncWishEmpty()
      })
      .catch(() => {
        // Im lặng: sổ đọc không được thì vẫn để nguyên ô viết, khách vẫn gửi được.
      })
  }

  /* Tải lúc trình duyệt rảnh, không tranh chỗ với font và ảnh của bìa thiệp.
     Cố tình KHÔNG dùng IntersectionObserver ("chờ cuộn tới sổ rồi mới tải"):
     tab đang ở nền hoặc trang bị prerender thì IO không bắn lần nào, và khách
     mở ra chỉ thấy ô viết với một cái tường trống mà không hiểu vì sao. Đổi lại
     chỉ là một request vài KB cho người không cuộn xuống — quá rẻ so với việc
     mất hẳn phần được dùng nhiều nhất. */
  if ('requestIdleCallback' in window) window.requestIdleCallback(loadWishes, { timeout: 3000 })
  else window.setTimeout(loadWishes, 1200)

  // Thiệp riêng đã biết tên khách thì ký sẵn hộ, họ chỉ việc viết.
  if (guest.name && !wishForm.elements.name.value) wishForm.elements.name.value = guest.name

  const WISH_RULES = [
    { name: 'text', errorId: 'err-wish-text', message: 'Viết giúp tụi mình vài chữ nhé.' },
    { name: 'name', errorId: 'err-wish-name', message: 'Ký tên để tụi mình biết là ai.' },
  ]

  function validateWish() {
    let firstInvalid = null
    for (const rule of WISH_RULES) {
      const field = wishForm.elements[rule.name]
      const isEmpty = !field.value.trim()
      document.getElementById(rule.errorId).textContent = isEmpty ? rule.message : ''
      field.setAttribute('aria-invalid', isEmpty ? 'true' : 'false')
      if (isEmpty && !firstInvalid) firstInvalid = field
    }
    if (firstInvalid) firstInvalid.focus()
    return !firstInvalid
  }

  WISH_RULES.forEach((rule) => {
    wishForm.elements[rule.name].addEventListener('input', () => {
      if (!wishForm.elements[rule.name].value.trim()) return
      document.getElementById(rule.errorId).textContent = ''
      wishForm.elements[rule.name].setAttribute('aria-invalid', 'false')
    })
  })

  wishForm.addEventListener('submit', (event) => {
    event.preventDefault()
    wishError.textContent = ''
    if (!validateWish()) return

    const payload = {
      name: wishForm.elements.name.value.trim(),
      text: wishForm.elements.text.value.trim(),
    }
    if (guest.code) payload.guestCode = guest.code

    wishSubmit.disabled = true
    wishSubmit.textContent = 'Đang ghim…'

    fetch(wedding.wishes.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
      // Đọc body cả khi lỗi: server có câu từ chối riêng (vd. gửi quá nhiều lần),
      // hiện đúng câu đó thì khách biết phải làm gì, chứ báo chung chung thì không.
      .then((response) => response.json().catch(() => ({})).then((data) => {
        if (!response.ok) throw new Error(data.error || '')
        return data
      }))
      .then((data) => {
        const note = wishNode(data.wish || { ...payload, font: 0, paper: 0, tilt: 0 })
        // viết mẩu thứ hai thì chỉ mẩu mới nhất được đánh dấu
        wishWall.querySelector('.is-mine')?.classList.remove('is-mine')
        note.classList.add('is-mine')
        wishWall.append(note)
        syncWishEmpty()

        wishForm.hidden = true
        wishThanks.hidden = false
        // preventScroll: giữ được focus cho bàn phím mà không giật màn hình,
        // để dành cú cuộn cho đúng mẩu giấy họ vừa ghim — đó mới là phần thưởng.
        wishThanks.focus({ preventScroll: true })
        note.scrollIntoView({
          block: 'center',
          behavior: reducedMotion.matches ? 'auto' : 'smooth',
        })
      })
      .catch((err) => {
        wishError.textContent = err.message || 'Chưa ghim lên được. Bạn thử lại giúp mình nhé.'
      })
      .finally(() => {
        wishSubmit.disabled = false
        wishSubmit.textContent = wishSubmitLabel
      })
  })

  document.getElementById('wish-again').addEventListener('click', () => {
    wishThanks.hidden = true
    wishForm.hidden = false
    wishForm.elements.text.value = '' // giữ nguyên tên đã ký, chỉ xoá nội dung
    wishForm.elements.text.focus()
  })
}

/* ---------------------------------------------------------
   Reveal khi cuộn + parallax hoa (HANDOFF §7)
   --------------------------------------------------------- */

/* Stagger: chỉ bật khi có JS, nếu không thì CSS không ẩn con và nội dung hiện bình thường. */
document.querySelectorAll('[data-stagger]').forEach((box) => {
  Array.from(box.children).forEach((child, i) => child.style.setProperty('--i', i))
  box.classList.add('stagger-on')
})

const revealTargets = document.querySelectorAll('[data-reveal]')
if ('IntersectionObserver' in window) {
  const revealObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible')
        observer.unobserve(entry.target)
      }
    })
  }, { threshold: 0.12 })
  revealTargets.forEach((element) => revealObserver.observe(element))
} else {
  revealTargets.forEach((element) => element.classList.add('visible'))
}

/* ---------------------------------------------------------
   Chuyển động theo scroll (HANDOFF §7)

   Một vòng rAF duy nhất lo cả parallax, nav và thanh tiến độ. Giá trị được
   nội suy dần về đích (lerp) nên hoa trôi có quán tính thay vì bám cứng scroll.

   Chiều cao trang đo sẵn thay vì đọc scrollHeight mỗi khung hình — đọc
   scrollHeight giữa lúc cuộn ép trình duyệt layout đồng bộ. Máy mạnh không
   thấy khác biệt (đo được 60fps đều ở cả hai cách), nhưng máy yếu thì có.
   --------------------------------------------------------- */

const nav = document.querySelector('.nav')
const progressBar = document.querySelector('.nav-progress')
// Ghi thẳng vào property translate/rotate (thay vì transform + custom property)
// cho đồng bộ với cách các keyframes khác trong file này dùng translate/scale.
// Đã đo: không đổi gì về số lần recalc style so với cách cũ.
// baseRot phải là 0: bộ hoa cũ được cắt nghiêng nên cần xoay bù 6deg/-15deg,
// bộ mới (bloom-corner/bloom-side) đã cắt thẳng — giữ số cũ là ảnh bị xoay lệch
// ngay từ khung hình đầu.
const parallaxLayers = [
  { el: document.querySelector('.hero-flower--left'), rate: 0.11, min: 0, max: 90, baseRot: 0, rotAmp: 2.5 },
  { el: document.querySelector('.hero-flower--right'), rate: -0.085, min: -72, max: 0, baseRot: 0, rotAmp: -3 },
  // mây trôi chậm hơn hẳn để tạo chiều sâu, và không xoay (xoay dải nước nhìn giả)
  { el: document.querySelector('.hero-wave'), rate: 0.045, min: 0, max: 40, baseRot: 0, rotAmp: 0 },
]
const closingFlower = document.querySelector('.closing > img')

let maxScroll = 1
let lastY = 0
let rafId = 0
const eased = parallaxLayers.map(() => 0)
let easedClosing = 0

function measurePage() {
  maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight)
}

function paint() {
  rafId = 0
  const y = window.scrollY
  let moving = false

  parallaxLayers.forEach((layer, i) => {
    if (!layer.el) return
    const target = Math.min(layer.max, Math.max(layer.min, y * layer.rate))
    const delta = target - eased[i]
    if (Math.abs(delta) > 0.05) {
      eased[i] += delta * 0.12
      moving = true
    } else {
      eased[i] = target
    }
    layer.el.style.translate = `0 ${eased[i].toFixed(2)}px`
    // rotAmp 0 thì không ghi rotate: ghi "0deg" vẫn tạo thêm một thuộc tính phải
    // hợp nhất mỗi khung hình, mà dải mây thì không cần xoay
    if (layer.rotAmp) {
      layer.el.style.rotate = `${(layer.baseRot + eased[i] / 42 * layer.rotAmp).toFixed(2)}deg`
    }
  })

  if (closingFlower) {
    const target = Math.max((maxScroll - y) * 0.018, 0)
    const delta = target - easedClosing
    if (Math.abs(delta) > 0.05) {
      easedClosing += delta * 0.12
      moving = true
    } else {
      easedClosing = target
    }
    closingFlower.style.translate = `0 ${easedClosing.toFixed(2)}px`
  }

  if (moving) rafId = window.requestAnimationFrame(paint)
}

function onScroll() {
  const y = window.scrollY

  progressBar.style.setProperty('--progress', Math.min(1, y / maxScroll).toFixed(4))
  nav.classList.toggle('is-scrolled', y > 8)
  // giấu nav khi cuộn xuống, hiện lại ngay khi cuộn lên
  if (!reducedMotion.matches) {
    if (y > 240 && y > lastY + 4) nav.classList.add('is-hidden')
    else if (y < lastY - 4 || y < 240) nav.classList.remove('is-hidden')
  }
  lastY = y

  if (!reducedMotion.matches && !rafId) rafId = window.requestAnimationFrame(paint)
}

function clearParallax() {
  parallaxLayers.forEach((layer) => {
    if (!layer.el) return
    layer.el.style.translate = ''
    layer.el.style.rotate = ''
  })
  if (closingFlower) closingFlower.style.translate = ''
  nav.classList.remove('is-hidden')
}

measurePage()
onScroll()
window.addEventListener('scroll', onScroll, { passive: true })
window.addEventListener('resize', measurePage)
// nội dung reveal làm trang dài ra → đo lại để thanh tiến độ không lệch
if ('ResizeObserver' in window) new ResizeObserver(measurePage).observe(document.body)

reducedMotion.addEventListener('change', () => {
  if (reducedMotion.matches) clearParallax()
})
if (reducedMotion.matches) clearParallax()
