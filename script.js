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
    welcomeStartAt: '2026-10-04T11:30:00+07:00',
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

/* Tên cô dâu chú rể là chữ duy nhất có mặt ở CẢ bìa lẫn trang thiệp. Để bìa tan
   rồi trang mới hiện thì mắt thấy đúng hai chữ đó biến mất rồi quay lại; cho hai
   lớp chồng nhau thì thành nhìn đôi. Nên thay vì tan, tên bay từ bìa về đúng vị
   trí của nó trong trang rồi nhường chỗ cho chữ thật — chữ có mặt liên tục suốt
   chuyển cảnh, không có khoảnh khắc nào mất.

   Bản sao phải nằm ngoài .cover vì .cover-card đang phóng to: mọi thứ bên trong
   nó, kể cả position: fixed, đều bị kéo theo transform của nó. */
const NAME_FLIGHT_MS = 780

function flyCoverNamesIntoPlace() {
  if (reducedMotion.matches || typeof Element.prototype.animate !== 'function') return false
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
  if (flyCoverNamesIntoPlace()) document.body.classList.add('names-flying')
  document.body.classList.add('opened')
  site.setAttribute('aria-hidden', 'false')
  // Phải gọi ngay trong handler của click: trình duyệt chỉ cho phát nhạc khi
  // lệnh play() nằm trong cùng cử chỉ của người dùng, đẩy vào setTimeout là bị chặn.
  prepareMusic()
  // 950ms = lúc bìa đã tan hẳn (xem khối chuyển cảnh trong styles.css).
  // Nhả focus sớm hơn thì màn hình giật về nav lúc bìa còn đang mở dở.
  window.setTimeout(() => {
    cover.setAttribute('aria-hidden', 'true')
    document.querySelector('.nav-brand').focus({ preventScroll: true })
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
