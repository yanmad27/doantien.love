/**
 * quanly.doantien.love — quản lý khách mời + nhận RSVP.
 *
 * Chỉ dùng thư viện chuẩn của Node, không có dependency ngoài.
 *
 * Phục vụ 3 thứ:
 *   1. UI quản trị          quanly.doantien.love/           (cần đăng nhập)
 *   2. API quản trị         quanly.doantien.love/api/*      (cần đăng nhập)
 *   3. Thiệp riêng của khách  doantien.love/t/<mã>          (công khai, Traefik trỏ sang)
 *      + POST /api/rsvp                                     (công khai, từ trang thiệp)
 *
 * Thiệp riêng render tại server nên preview Zalo/Messenger hiện đúng tên
 * người nhận — crawler không chạy JS.
 */
import http from 'node:http'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PORT = Number(process.env.PORT || 3000)
const DATA_DIR = process.env.DATA_DIR || '/data'
const DB_PATH = path.join(DATA_DIR, 'guests.json')
const SITE_ORIGIN = process.env.SITE_ORIGIN || 'https://doantien.love'
const TEMPLATE_PATH = path.join(HERE, 'invitation.html')
const SEED_CSV = path.join(HERE, 'guests.csv')

/* Mừng cưới qua SePay. Thiếu secret thì route webhook trả 503 chứ KHÔNG nhận
   payload chưa xác thực — ai biết URL cũng bắn tiền giả vào được. */
const SEPAY_WEBHOOK_SECRET = process.env.SEPAY_WEBHOOK_SECRET || ''
const SEPAY_ACCOUNT = process.env.SEPAY_ACCOUNT || '03009274502'
const SEPAY_BANK = process.env.SEPAY_BANK || 'TPBank'
// Phải khớp "Cấu trúc mã thanh toán" trên my.sepay.vn (tiền tố 2–5 ký tự).
const GIFT_PREFIX = (process.env.GIFT_CODE_PREFIX || 'MC').toUpperCase()
const SEPAY_SKEW_SEC = 300 // cửa sổ chống replay, đúng theo tài liệu SePay

// scrypt: "salt:hash" dạng hex. Sinh bằng: node admin/hash-password.js '<mật khẩu>'
const PASSWORD_HASH = process.env.ADMIN_PASSWORD_HASH || ''
const SESSION_SECRET = process.env.SESSION_SECRET || ''
const SESSION_HOURS = 12

if (!PASSWORD_HASH || !SESSION_SECRET) {
  console.error('Thiếu ADMIN_PASSWORD_HASH hoặc SESSION_SECRET trong biến môi trường.')
  process.exit(1)
}

const ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789' // bỏ l/o/0/1 cho dễ đọc miệng
const CODE_LEN = 6
const MAX_BODY = 64 * 1024

/* ---------------------------------------------------------
   Lưu trữ — một file JSON, ghi kiểu atomic (ghi tạm rồi rename)
   --------------------------------------------------------- */

let db = { guests: [], rsvps: [], gifts: [] }
let writeChain = Promise.resolve()

function parseSeedCsv() {
  if (!fs.existsSync(SEED_CSV)) return []
  const out = []
  for (const line of fs.readFileSync(SEED_CSV, 'utf8').split('\n')) {
    const row = line.trim()
    if (!row || row.startsWith('#') || row.startsWith('code,')) continue
    const [code, title, ...rest] = row.split(',')
    const name = rest.join(',').trim()
    if (code?.trim() && name) {
      out.push({ code: code.trim(), title: (title || '').trim(), name, seats: 1, note: '', createdAt: new Date().toISOString() })
    }
  }
  return out
}

async function loadDb() {
  await fsp.mkdir(DATA_DIR, { recursive: true })
  try {
    db = JSON.parse(await fsp.readFile(DB_PATH, 'utf8'))
    db.guests ||= []
    db.rsvps ||= []
    db.gifts ||= []
  } catch {
    // lần chạy đầu: nạp sẵn danh sách từ guests.csv để link đã gửi không chết
    db = { guests: parseSeedCsv(), rsvps: [], gifts: [] }
    await saveDb()
    console.log(`Khởi tạo dữ liệu với ${db.guests.length} khách từ guests.csv`)
  }
}

function saveDb() {
  writeChain = writeChain.then(async () => {
    const tmp = `${DB_PATH}.${process.pid}.tmp`
    await fsp.writeFile(tmp, JSON.stringify(db, null, 2), 'utf8')
    await fsp.rename(tmp, DB_PATH)
  }).catch((err) => console.error('Lỗi ghi dữ liệu:', err))
  return writeChain
}

/* ---------------------------------------------------------
   Đăng nhập
   --------------------------------------------------------- */

function verifyPassword(input) {
  const [saltHex, hashHex] = PASSWORD_HASH.split(':')
  if (!saltHex || !hashHex) return false
  const expected = Buffer.from(hashHex, 'hex')
  let actual
  try {
    actual = crypto.scryptSync(input, Buffer.from(saltHex, 'hex'), expected.length)
  } catch {
    return false
  }
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected)
}

function makeSession() {
  const expires = Date.now() + SESSION_HOURS * 3600_000
  const payload = String(expires)
  const sig = crypto.createHmac('sha256', SESSION_SECRET).update(payload).digest('hex')
  return `${payload}.${sig}`
}

function validSession(token) {
  if (!token) return false
  const [payload, sig] = token.split('.')
  if (!payload || !sig) return false
  const expect = crypto.createHmac('sha256', SESSION_SECRET).update(payload).digest('hex')
  const a = Buffer.from(sig)
  const b = Buffer.from(expect)
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return false
  return Number(payload) > Date.now()
}

function readCookie(req, name) {
  const raw = req.headers.cookie || ''
  for (const part of raw.split(';')) {
    const [k, ...v] = part.trim().split('=')
    if (k === name) return decodeURIComponent(v.join('='))
  }
  return ''
}

// chặn dò mật khẩu: tối đa 8 lần sai mỗi IP trong 15 phút
const attempts = new Map()
function tooManyAttempts(ip) {
  const rec = attempts.get(ip)
  if (!rec) return false
  if (Date.now() > rec.until) { attempts.delete(ip); return false }
  return rec.count >= 8
}
function noteFailure(ip) {
  const rec = attempts.get(ip)
  if (rec && Date.now() <= rec.until) rec.count += 1
  else attempts.set(ip, { count: 1, until: Date.now() + 15 * 60_000 })
}

/* ---------------------------------------------------------
   Render thiệp riêng — cùng cách thay thế với tools/guests.py
   --------------------------------------------------------- */

const template = fs.readFileSync(TEMPLATE_PATH, 'utf8')

const escapeHtml = (s) => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

function fullName(g) {
  return `${g.title || ''} ${g.name}`.trim()
}

/* Số chỗ mời cho một khách. Trần 20 cho khớp với giới hạn của API nhận RSVP —
   để lệch nhau thì auto-fill điền được một số mà form gửi lên lại bị cắt. */
function seatsOf(value, fallback = 1) {
  const n = Math.floor(Number(value))
  return Number.isFinite(n) ? Math.min(Math.max(n, 1), 20) : fallback
}

/* QR mừng cưới mang mã riêng của khách trong nội dung chuyển khoản, nhờ vậy
   webhook SePay biết ai vừa chuyển để thiệp của đúng người đó hiện lời cảm ơn.
   Dùng ảnh SePay sinh sẵn thay vì tự viết bộ mã hoá QR — giữ luật không
   dependency ngoài của app này. */
function giftQrUrl(code) {
  const q = new URLSearchParams({
    acc: SEPAY_ACCOUNT,
    bank: SEPAY_BANK,
    des: `${GIFT_PREFIX}${code}`,
    template: 'qronly', // chỉ ảnh QR, không khung/logo — CSS thiệp tự bọc khung
  })
  return `https://qr.sepay.vn/img?${q}`
}

function renderInvitation(guest) {
  const who = escapeHtml(fullName(guest))
  const code = escapeHtml(guest.code)
  const url = `${SITE_ORIGIN}/t/${guest.code}/`
  let doc = template

  const must = (pattern, replacement, label) => {
    const next = doc.replace(pattern, () => replacement)
    if (next === doc) throw new Error(`Template thiếu ${label} — index.html đã đổi?`)
    doc = next
  }

  must('<!--GUEST_COVER-->', `<p class="guest-cover">Thân mời <b>${who}</b></p>`, 'GUEST_COVER')
  must('<!--GUEST_HERO-->', `<p class="guest-hero">${who}</p>`, 'GUEST_HERO')
  must('<body class="is-closed">',
    `<body class="is-closed" data-guest-name="${who}" data-guest-code="${code}"`
    + ` data-guest-seats="${seatsOf(guest.seats)}">`, 'thẻ body')

  /* Đổi QR chung thành QR mang mã khách. Giữ luôn đường dẫn QR tĩnh làm dự
     phòng: qr.sepay.vn hỏng thì thiệp rơi về ảnh trong repo, khách vẫn chuyển
     khoản được — chỉ mất phần tự nhận ra ai gửi. Phải chạy TRƯỚC bước đổi "./
     thành "/ bên dưới, vì lúc đó chuỗi src cũ không còn nữa. */
  must('src="./assets/qr-mung-cuoi.png"',
    `src="${escapeHtml(giftQrUrl(guest.code))}" data-qr-fallback="/assets/qr-mung-cuoi.png"`,
    'ảnh QR mừng cưới')

  // thiệp nằm ở /t/<mã>/ nên đường dẫn tương đối ./ phải thành tuyệt đối /
  doc = doc.replaceAll('"./', '"/')

  must(/<title>.*?<\/title>/,
    `<title>Trân trọng kính mời ${who} — Khắc Doãn & Đông Tiên 04.10.2026</title>`, 'thẻ title')
  must(/<link rel="canonical" href="[^"]*">/,
    `<link rel="canonical" href="${url}">\n  <meta name="robots" content="noindex, nofollow">`, 'canonical')
  must(/<meta property="og:url" content="[^"]*">/,
    `<meta property="og:url" content="${url}">`, 'og:url')
  must(/<meta property="og:title" content="[^"]*">/,
    `<meta property="og:title" content="Trân trọng kính mời ${who}">`, 'og:title')
  must(/<meta property="og:description" content="[^"]*">/,
    `<meta property="og:description" content="Trân trọng mời ${who} đến chung vui cùng gia đình chúng tôi, `
    + `Chủ nhật 04.10.2026 tại Nhà hàng Long Phụng, Khánh Hòa.">`, 'og:description')
  return doc
}

/* ---------------------------------------------------------
   Tiện ích HTTP
   --------------------------------------------------------- */

function clientIp(req) {
  const fwd = req.headers['x-forwarded-for']
  if (typeof fwd === 'string' && fwd) return fwd.split(',')[0].trim()
  return req.socket.remoteAddress || 'unknown'
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    ...headers,
  })
  res.end(body)
}

const json = (res, status, obj, headers = {}) =>
  send(res, status, JSON.stringify(obj), { 'Content-Type': 'application/json; charset=utf-8', ...headers })

// Các route công khai gọi từ trang thiệp — có thể khác origin (subdomain quanly)
const PUBLIC_CORS = {
  'Access-Control-Allow-Origin': SITE_ORIGIN,
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
  'Access-Control-Max-Age': '86400',
}

/* Bytes thô, chưa parse. Chữ ký HMAC của SePay ký trên đúng chuỗi byte đã gửi,
   nên KHÔNG được parse rồi stringify lại: thứ tự khoá và cách escape Unicode
   đổi là chữ ký hỏng ngay. */
function readRawBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    req.on('data', (c) => {
      size += c.length
      if (size > MAX_BODY) { reject(new Error('quá lớn')); req.destroy(); return }
      chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

async function readBody(req) {
  const raw = await readRawBody(req)
  if (!raw.length) return {}
  try { return JSON.parse(raw.toString('utf8')) }
  catch { throw new Error('JSON không hợp lệ') }
}

function newCode() {
  const used = new Set(db.guests.map((g) => g.code))
  for (let i = 0; i < 200; i += 1) {
    let code = ''
    for (let j = 0; j < CODE_LEN; j += 1) code += ALPHABET[crypto.randomInt(ALPHABET.length)]
    if (!used.has(code)) return code
  }
  throw new Error('không sinh được mã mới')
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
}

async function serveStatic(res, urlPath) {
  const rel = urlPath === '/' ? 'index.html' : urlPath.replace(/^\/+/, '')
  const file = path.resolve(HERE, 'public', rel)
  // chặn path traversal
  if (!file.startsWith(path.resolve(HERE, 'public') + path.sep)) return send(res, 403, 'Forbidden')
  try {
    const body = await fsp.readFile(file)
    send(res, 200, body, {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
      'X-Robots-Tag': 'noindex, nofollow',
    })
  } catch {
    send(res, 404, 'Not found', { 'Content-Type': 'text/plain; charset=utf-8' })
  }
}

/* ---------------------------------------------------------
   Mừng cưới — nhận webhook SePay
   --------------------------------------------------------- */

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/* Tìm xem giao dịch này là của khách nào.

   SePay trả mã thanh toán VIẾT HOA (MCAB3K9Z) trong khi mã khách lưu viết
   thường — không hạ chữ trước khi so thì không bao giờ khớp.

   Trường `code` chỉ có khi đã khai "Cấu trúc mã thanh toán" trên my.sepay.vn.
   Chưa khai thì tự dò trong nội dung chuyển khoản, để tính năng vẫn chạy đúng
   mà không phụ thuộc một ô cấu hình trên dashboard. */
function matchGuestCode(code, content) {
  const prefix = GIFT_PREFIX.toLowerCase()
  const found = []
  if (typeof code === 'string' && code) found.push(code.toLowerCase())
  if (typeof content === 'string' && content) {
    const re = new RegExp(`${escapeRe(prefix)}([a-z0-9]{${CODE_LEN}})`, 'g')
    for (const m of content.toLowerCase().matchAll(re)) found.push(prefix + m[1])
  }
  for (const candidate of found) {
    if (!candidate.startsWith(prefix)) continue
    const guestCode = candidate.slice(prefix.length)
    if (db.guests.some((g) => g.code === guestCode)) return guestCode
  }
  return ''
}

/* Trả null nếu hợp lệ, hoặc { status, message } để từ chối.
   Chữ ký = HMAC-SHA256(secret, `${timestamp}.${body thô}`), gửi kèm dạng
   "sha256=<hex>". Timestamp lệch quá 5 phút coi như replay. */
function verifySepay(req, raw) {
  const tsRaw = String(req.headers['x-sepay-timestamp'] || '')
  const ts = Number(tsRaw)
  if (!tsRaw || !Number.isFinite(ts)) return { status: 401, message: 'Missing timestamp' }
  if (Math.abs(Date.now() / 1000 - ts) > SEPAY_SKEW_SEC) return { status: 401, message: 'Request expired' }

  const expected = 'sha256=' + crypto.createHmac('sha256', SEPAY_WEBHOOK_SECRET)
    .update(`${tsRaw}.`).update(raw).digest('hex')
  const got = Buffer.from(String(req.headers['x-sepay-signature'] || ''))
  const want = Buffer.from(expected)
  if (got.length !== want.length || !crypto.timingSafeEqual(got, want)) {
    return { status: 401, message: 'Invalid signature' }
  }
  return null
}

/* Chặn dò mã qua /api/gift-status. Thiệp hỏi 5s một lần nên 120 lượt/5 phút
   là thừa cho người dùng thật, nhưng đủ chật để không quét được cả bảng mã. */
const statusHits = new Map()
function tooManyStatus(ip) {
  const now = Date.now()
  const rec = statusHits.get(ip)
  if (!rec || now > rec.until) { statusHits.set(ip, { count: 1, until: now + 5 * 60_000 }); return false }
  rec.count += 1
  return rec.count > 120
}
// dọn định kỳ để Map không phình mãi; unref để không giữ tiến trình sống
setInterval(() => {
  const now = Date.now()
  for (const [ip, rec] of statusHits) if (now > rec.until) statusHits.delete(ip)
}, 10 * 60_000).unref()

/* ---------------------------------------------------------
   Router
   --------------------------------------------------------- */

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`)
  const p = url.pathname
  const authed = validSession(readCookie(req, 'session'))

  try {
    if (p === '/health') return send(res, 200, 'ok', { 'Content-Type': 'text/plain' })

    /* --- thiệp riêng: công khai --- */
    if (p === '/t' || p === '/t/') return send(res, 302, '', { Location: `${SITE_ORIGIN}/` })
    if (p.startsWith('/t/')) {
      const code = p.slice(3).replace(/\/$/, '')
      const guest = db.guests.find((g) => g.code === code)
      // mã sai → về thiệp chung, giống hành vi của app thiệp
      if (!guest) return send(res, 302, '', { Location: `${SITE_ORIGIN}/` })
      return send(res, 200, renderInvitation(guest), {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-cache',
        'X-Robots-Tag': 'noindex, nofollow',
      })
    }

    /* --- RSVP: công khai, gọi từ trang thiệp (khác origin) --- */
    if (p === '/api/rsvp') {
      const cors = PUBLIC_CORS
      if (req.method === 'OPTIONS') return send(res, 204, '', cors)
      if (req.method !== 'POST') return json(res, 405, { error: 'method' }, cors)
      const body = await readBody(req)
      const name = String(body.name || '').trim().slice(0, 120)
      const attendance = body.attendance === 'yes' || body.attendance === 'no' ? body.attendance : ''
      if (!name || !attendance) return json(res, 400, { error: 'thiếu name hoặc attendance' }, cors)
      const kara = body.karaoke === 'yes' || body.karaoke === 'no' ? body.karaoke : ''
      db.rsvps.push({
        id: crypto.randomUUID(),
        guestCode: String(body.guestCode || '').slice(0, 32),
        name,
        attendance,
        guests: Math.min(Math.max(Number(body.guests) || 1, 1), 20),
        // chỉ nhận 'yes'/'no', giá trị lạ thì coi như chưa trả lời
        karaoke: kara,
        // không nhận hát thì bỏ luôn tên bài: trang thiệp đã xoá ô đó khi ẩn,
        // nhưng gọi thẳng API vẫn gửi kèm được, lưu vào chỉ tổ gây khó hiểu
        song: kara === 'yes' ? String(body.song || '').trim().slice(0, 120) : '',
        message: String(body.message || '').trim().slice(0, 2000),
        at: new Date().toISOString(),
      })
      await saveDb()
      return json(res, 200, { ok: true }, cors)
    }

    /* --- Webhook SePay: công khai, nhưng phải có chữ ký hợp lệ mới nhận.
           Đặt TRƯỚC lớp kiểm tra đăng nhập bên dưới, nếu không SePay sẽ ăn 401. --- */
    if (p === '/api/sepay-webhook') {
      if (req.method !== 'POST') return json(res, 405, { success: false, message: 'Method not allowed' })
      if (!SEPAY_WEBHOOK_SECRET) {
        console.error('Thiếu SEPAY_WEBHOOK_SECRET — từ chối webhook thay vì nhận dữ liệu chưa xác thực.')
        return json(res, 503, { success: false, message: 'Webhook chưa được cấu hình' })
      }

      let raw
      try { raw = await readRawBody(req) }
      catch { return json(res, 400, { success: false, message: 'Body quá lớn' }) }

      const bad = verifySepay(req, raw)
      if (bad) return json(res, bad.status, { success: false, message: bad.message })

      let payload
      try { payload = JSON.parse(raw.toString('utf8')) }
      catch { return json(res, 400, { success: false, message: 'JSON không hợp lệ' }) }

      const sepayId = payload?.id === undefined || payload?.id === null ? '' : String(payload.id)
      if (!sepayId) return json(res, 400, { success: false, message: 'Thiếu id' })

      /* Từ đây trở xuống mọi nhánh đều trả 200 { success: true }: SePay coi bất
         cứ đáp án nào khác là thất bại và sẽ gửi lại mãi. Tiền ra, hay giao dịch
         đã ghi rồi, đều là "đã xử lý xong" chứ không phải lỗi. */
      if (payload.transferType !== 'in') return json(res, 200, { success: true })

      /* Chống trùng: id giữ nguyên qua mọi lần retry và mọi lần bấm gửi lại tay
         trên dashboard. Kiểm rồi push ngay, không chèn await vào giữa, để hai
         webhook tới sát nhau không cùng lọt qua. */
      if (!db.gifts.some((g) => g.id === sepayId)) {
        db.gifts.push({
          id: sepayId,
          guestCode: matchGuestCode(payload.code, payload.content),
          amount: Math.max(0, Math.floor(Number(payload.transferAmount) || 0)),
          content: String(payload.content || '').slice(0, 500),
          ref: String(payload.referenceCode || '').slice(0, 100),
          gateway: String(payload.gateway || '').slice(0, 60),
          at: new Date().toISOString(),
        })
        await saveDb()
      }
      return json(res, 200, { success: true })
    }

    /* --- Thiệp hỏi "tiền của mình tới chưa?" để chạy animation cảm ơn.
           CHỈ trả có/chưa — không bao giờ trả số tiền ra ngoài trang quản trị. --- */
    if (p === '/api/gift-status') {
      const cors = PUBLIC_CORS
      if (req.method === 'OPTIONS') return send(res, 204, '', cors)
      if (req.method !== 'GET') return json(res, 405, { error: 'method' }, cors)
      if (tooManyStatus(clientIp(req))) return json(res, 429, { error: 'Hỏi quá nhiều' }, cors)

      const code = String(url.searchParams.get('code') || '').toLowerCase().slice(0, 32)
      if (!code) return json(res, 400, { error: 'thiếu code' }, cors)
      const gift = db.gifts.find((g) => g.guestCode === code)
      return json(res, 200, { thanked: Boolean(gift), at: gift?.at || null },
        { ...cors, 'Cache-Control': 'no-store' })
    }

    /* --- đăng nhập --- */
    if (p === '/api/login' && req.method === 'POST') {
      const ip = clientIp(req)
      if (tooManyAttempts(ip)) return json(res, 429, { error: 'Sai quá nhiều lần, thử lại sau 15 phút.' })
      const body = await readBody(req)
      if (!verifyPassword(String(body.password || ''))) {
        noteFailure(ip)
        return json(res, 401, { error: 'Mật khẩu không đúng.' })
      }
      attempts.delete(ip)
      return json(res, 200, { ok: true }, {
        'Set-Cookie': `session=${makeSession()}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_HOURS * 3600}`,
      })
    }
    if (p === '/api/logout' && req.method === 'POST') {
      return json(res, 200, { ok: true }, { 'Set-Cookie': 'session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0' })
    }
    if (p === '/api/session') return json(res, 200, { authed })

    /* --- API quản trị: bắt buộc đăng nhập --- */
    if (p.startsWith('/api/')) {
      if (!authed) return json(res, 401, { error: 'Chưa đăng nhập' })

      if (p === '/api/data' && req.method === 'GET') {
        // gifts kèm số tiền — chỗ duy nhất lộ số, và đã nằm sau đăng nhập
        return json(res, 200, { guests: db.guests, rsvps: db.rsvps, gifts: db.gifts, siteOrigin: SITE_ORIGIN })
      }

      if (p === '/api/guests' && req.method === 'POST') {
        const body = await readBody(req)
        const name = String(body.name || '').trim().slice(0, 120)
        if (!name) return json(res, 400, { error: 'Thiếu tên khách' })
        const guest = {
          code: newCode(),
          title: String(body.title || '').trim().slice(0, 40),
          name,
          seats: seatsOf(body.seats),
          note: String(body.note || '').trim().slice(0, 200),
          createdAt: new Date().toISOString(),
        }
        db.guests.push(guest)
        await saveDb()
        return json(res, 200, guest)
      }

      if (p === '/api/guests/bulk' && req.method === 'POST') {
        const body = await readBody(req)
        const added = []
        for (const raw of String(body.text || '').split('\n')) {
          const line = raw.trim()
          if (!line) continue
          // "Anh, Nguyễn Văn Nam"  hoặc chỉ  "Nguyễn Văn Nam"
          const parts = line.split(',')
          const title = parts.length > 1 ? parts[0].trim().slice(0, 40) : ''
          const name = (parts.length > 1 ? parts.slice(1).join(',') : line).trim().slice(0, 120)
          if (!name) continue
          const guest = { code: newCode(), title, name, seats: 1, note: '', createdAt: new Date().toISOString() }
          db.guests.push(guest)
          added.push(guest)
        }
        await saveDb()
        return json(res, 200, { added: added.length, guests: added })
      }

      const rsvpMatch = p.match(/^\/api\/rsvps\/([0-9a-fA-F-]{36})$/)
      if (rsvpMatch && req.method === 'DELETE') {
        const before = db.rsvps.length
        db.rsvps = db.rsvps.filter((r) => r.id !== rsvpMatch[1])
        if (db.rsvps.length === before) return json(res, 404, { error: 'Không tìm thấy phản hồi' })
        await saveDb()
        return json(res, 200, { ok: true })
      }

      /* Xoá một khoản mừng cưới — cho giao dịch test hoặc ghi nhầm người.
         Xoá xong /api/gift-status trả lại "chưa nhận", nên thiệp của khách đó
         hiện lại QR. Đó chính là điều cần khi lỡ đánh dấu nhầm: không xoá thì
         khách mở thiệp ra đã thấy lời cảm ơn và không còn chỗ để mừng cưới.
         Lưu ý: chống trùng dựa vào id, nên nếu SePay gửi lại đúng giao dịch vừa
         xoá thì nó sẽ quay lại. Xoá giao dịch test sau khi đã tắt Test mode. */
      const giftMatch = p.match(/^\/api\/gifts\/([\w-]{1,64})$/)
      if (giftMatch && req.method === 'DELETE') {
        const before = db.gifts.length
        db.gifts = db.gifts.filter((g) => g.id !== giftMatch[1])
        if (db.gifts.length === before) return json(res, 404, { error: 'Không tìm thấy giao dịch' })
        await saveDb()
        return json(res, 200, { ok: true })
      }

      const editMatch = p.match(/^\/api\/guests\/([a-z0-9]{4,16})$/)
      if (editMatch) {
        const code = editMatch[1]
        const guest = db.guests.find((g) => g.code === code)
        if (!guest) return json(res, 404, { error: 'Không tìm thấy khách' })
        if (req.method === 'DELETE') {
          db.guests = db.guests.filter((g) => g.code !== code)
          await saveDb()
          return json(res, 200, { ok: true })
        }
        if (req.method === 'PATCH') {
          const body = await readBody(req)
          if (body.name !== undefined) guest.name = String(body.name).trim().slice(0, 120) || guest.name
          if (body.title !== undefined) guest.title = String(body.title).trim().slice(0, 40)
          if (body.note !== undefined) guest.note = String(body.note).trim().slice(0, 200)
          if (body.seats !== undefined) guest.seats = seatsOf(body.seats, guest.seats || 1)
          await saveDb()
          return json(res, 200, guest)
        }
      }

      return json(res, 404, { error: 'Không có route này' })
    }

    /* --- UI quản trị --- */
    return serveStatic(res, p)
  } catch (err) {
    console.error(err)
    return json(res, 500, { error: 'Lỗi máy chủ' })
  }
})

await loadDb()
server.listen(PORT, () => console.log(`quanly đang chạy ở cổng ${PORT}, dữ liệu tại ${DB_PATH}`))
