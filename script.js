/* Nguồn dữ liệu duy nhất cho toàn trang (HANDOFF §11).
   Mọi section đọc từ đây qua data-w / data-w-href — không hard-code lặp lại. */
const wedding = {
  couple: {
    groom: { fullName: 'Phan Nguyễn Khắc Doãn', familyName: 'Phan Nguyễn', displayName: 'Khắc Doãn' },
    bride: { fullName: 'Trần Lưu Đông Tiên', familyName: 'Trần Lưu', displayName: 'Đông Tiên' },
  },
  ceremony: {
    startAt: '2026-10-04T11:30:00+07:00',
    endAt: '2026-10-04T15:00:00+07:00',
    lunarDate: '24 tháng 08 năm Bính Ngọ',
    venueShortName: 'Long Phụng',
    venueName: 'Nhà hàng Long Phụng',
    venueRegion: 'Khánh Hòa',
    venueAddress: '',
    // TODO: thay bằng URL địa điểm chính xác sau khi chủ thiệp xác nhận (HANDOFF §3)
    mapsUrl: 'https://www.google.com/maps/search/?api=1&query=Nha+hang+Long+Phung+Khanh+Hoa',
  },
  rsvp: {
    deadline: '2026-09-24T23:59:59+07:00',
    // Để null nếu chưa có backend: form sẽ chạy ở chế độ demo (HANDOFF §4.6)
    endpoint: null,
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

function openInvitation() {
  document.documentElement.classList.remove('is-locked')
  document.body.classList.remove('is-closed')
  document.body.classList.add('opened')
  site.setAttribute('aria-hidden', 'false')
  window.setTimeout(() => {
    cover.setAttribute('aria-hidden', 'true')
    document.querySelector('.nav-brand').focus({ preventScroll: true })
  }, 700)
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
  setDigits(countdownFields.days, pad(Math.floor(distance / 86400000), 3))
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

document.querySelectorAll('.save-date').forEach((button) => button.addEventListener('click', saveDate))

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
}

if (guest.name && !form.elements.name.value) {
  form.elements.name.value = guest.name
}

function buildPayload() {
  const payload = {
    name: form.elements.name.value.trim(),
    attendance: form.elements.attendance.value,
    guests: Number(form.elements.guests.value),
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
const parallaxLayers = [
  { el: document.querySelector('.hero-flower--left'), rate: 0.055, min: 0, max: 42, baseRot: 6, rotAmp: 1.5 },
  { el: document.querySelector('.hero-flower--right'), rate: -0.035, min: -32, max: 0, baseRot: -15, rotAmp: -2 },
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
    layer.el.style.rotate = `${(layer.baseRot + eased[i] / 42 * layer.rotAmp).toFixed(2)}deg`
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
