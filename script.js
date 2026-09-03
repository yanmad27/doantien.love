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

function updateCountdown() {
  const distance = Math.max(0, startAt.getTime() - Date.now())
  countdownFields.days.textContent = pad(Math.floor(distance / 86400000), 3)
  countdownFields.hours.textContent = pad(Math.floor((distance % 86400000) / 3600000))
  countdownFields.minutes.textContent = pad(Math.floor((distance % 3600000) / 60000))
  countdownFields.seconds.textContent = pad(Math.floor((distance % 60000) / 1000))
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

const revealObserver = new IntersectionObserver((entries, observer) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible')
      observer.unobserve(entry.target)
    }
  })
}, { threshold: 0.12 })

document.querySelectorAll('[data-reveal]').forEach((element) => revealObserver.observe(element))

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
let ticking = false

function updateFlowerMotion() {
  const scrollPosition = window.scrollY
  const root = document.documentElement.style
  root.setProperty('--parallax-left', `${Math.min(scrollPosition * 0.055, 42)}px`)
  root.setProperty('--parallax-right', `${Math.max(scrollPosition * -0.035, -32)}px`)
  root.setProperty('--closing-shift', `${Math.max((document.documentElement.scrollHeight - window.innerHeight - scrollPosition) * 0.018, 0)}px`)
  ticking = false
}

function resetFlowerMotion() {
  const root = document.documentElement.style
  root.setProperty('--parallax-left', '0px')
  root.setProperty('--parallax-right', '0px')
  root.setProperty('--closing-shift', '0px')
}

window.addEventListener('scroll', () => {
  if (reducedMotion.matches || ticking) return
  ticking = true
  window.requestAnimationFrame(updateFlowerMotion)
}, { passive: true })

reducedMotion.addEventListener('change', () => {
  if (reducedMotion.matches) resetFlowerMotion()
})
if (reducedMotion.matches) resetFlowerMotion()
