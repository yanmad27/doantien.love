const $ = (sel) => document.querySelector(sel)

const loginView = $('#login')
const appView = $('#app')
const toastEl = $('#toast')

let state = { guests: [], rsvps: [], siteOrigin: 'https://doantien.love' }
let filter = ''

/* --- tiện ích --- */

let toastTimer = 0
function toast(message) {
  toastEl.textContent = message
  toastEl.classList.add('show')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2200)
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { 'Content-Type': 'application/json' } : {},
    ...options,
  })
  if (res.status === 401) { showLogin(); throw new Error('Chưa đăng nhập') }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `Lỗi ${res.status}`)
  return data
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

const fullName = (g) => `${g.title || ''} ${g.name}`.trim()
const guestUrl = (g) => `${state.siteOrigin}/t/${g.code}/`

function csv(rows) {
  return rows.map((r) => r.map((cell) => {
    const v = String(cell ?? '')
    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
  }).join(',')).join('\n')
}

function download(filename, text) {
  const url = URL.createObjectURL(new Blob([`﻿${text}`], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/* --- hiển thị --- */

function latestRsvp(code) {
  if (!code) return null
  const list = state.rsvps.filter((r) => r.guestCode === code)
  return list.length ? list[list.length - 1] : null
}

function render() {
  const going = state.rsvps.filter((r) => r.attendance === 'yes')
  const heads = going.reduce((sum, r) => sum + (Number(r.guests) || 1), 0)
  $('#stats').innerHTML = [
    ['Khách mời', state.guests.length],
    ['Tổng chỗ mời', state.guests.reduce((sum, g) => sum + (Number(g.seats) || 1), 0)],
    ['Đã phản hồi', state.rsvps.length],
    ['Sẽ đến', going.length],
    ['Tổng số người', heads],
  ].map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`).join('')

  const needle = filter.trim().toLowerCase()
  const list = needle
    ? state.guests.filter((g) => fullName(g).toLowerCase().includes(needle) || g.code.includes(needle))
    : state.guests

  $('#guest-count').textContent = needle ? `(${list.length}/${state.guests.length})` : `(${state.guests.length})`
  $('#guests-empty').hidden = list.length > 0
  $('#guests tbody').innerHTML = list.map((g) => {
    const r = latestRsvp(g.code)
    const pill = !r
      ? '<span class="pill pill-none">chưa phản hồi</span>'
      : r.attendance === 'yes'
        ? `<span class="pill pill-yes">đến · ${r.guests} người</span>`
        : '<span class="pill pill-no">không đến</span>'
    return `<tr data-code="${esc(g.code)}">
      <td><span class="who">${esc(fullName(g))}</span>${g.note ? `<span class="note">${esc(g.note)}</span>` : ''}</td>
      <td class="seats">${esc(g.seats || 1)}</td>
      <td><div class="link-cell"><code>${esc(g.code)}</code>
        <button class="btn-mini" data-act="copy">Chép link</button>
        <a class="btn-mini" href="${esc(guestUrl(g))}" target="_blank" rel="noopener">Mở</a></div></td>
      <td>${pill}</td>
      <td><div class="actions">
        <button class="btn-mini" data-act="edit">Sửa</button>
        <button class="btn-mini btn-del" data-act="del">Xoá</button>
      </div></td>
    </tr>`
  }).join('')

  const rsvps = [...state.rsvps].reverse()
  $('#rsvp-count').textContent = `(${rsvps.length})`
  $('#rsvps-empty').hidden = rsvps.length > 0
  $('#rsvps tbody').innerHTML = rsvps.map((r) => `<tr data-id="${esc(r.id)}">
    <td>${new Date(r.at).toLocaleString('vi-VN')}</td>
    <td>${esc(r.name)}</td>
    <td>${r.attendance === 'yes'
      ? '<span class="pill pill-yes">có</span>'
      : '<span class="pill pill-no">không</span>'}</td>
    <td>${esc(r.guests)}</td>
    <td>${r.karaoke === 'yes'
      ? `<span class="pill pill-yes">hát</span>${r.song ? `<span class="note">${esc(r.song)}</span>` : ''}`
      : r.karaoke === 'no' ? '<span class="pill pill-none">không hát</span>' : ''}</td>
    <td>${esc(r.message)}</td>
    <td><div class="actions"><button class="btn-mini btn-del" data-act="del-rsvp">Xoá</button></div></td>
  </tr>`).join('')
}

async function refresh() {
  state = await api('/api/data')
  render()
}

function showLogin() {
  loginView.hidden = false
  appView.hidden = true
}

async function showApp() {
  loginView.hidden = true
  appView.hidden = false
  await refresh()
}

/* --- sự kiện --- */

$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const button = e.target.querySelector('button')
  const errorEl = $('#login-error')
  errorEl.textContent = ''
  button.disabled = true
  try {
    await api('/api/login', { method: 'POST', body: JSON.stringify({ password: e.target.password.value }) })
    e.target.reset()
    await showApp()
  } catch (err) {
    errorEl.textContent = err.message
  } finally {
    button.disabled = false
  }
})

$('#logout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' })
  showLogin()
})

$('#add-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const form = e.target
  try {
    await api('/api/guests', {
      method: 'POST',
      body: JSON.stringify({
        title: form.title.value,
        name: form.name.value,
        seats: form.seats.value,
        note: form.note.value,
      }),
    })
    form.reset()
    form.title.focus()
    await refresh()
    toast('Đã thêm khách')
  } catch (err) { toast(err.message) }
})

$('#bulk-add').addEventListener('click', async () => {
  const box = $('#bulk-text')
  if (!box.value.trim()) return
  try {
    const res = await api('/api/guests/bulk', { method: 'POST', body: JSON.stringify({ text: box.value }) })
    box.value = ''
    await refresh()
    toast(`Đã thêm ${res.added} khách`)
  } catch (err) { toast(err.message) }
})

$('#search').addEventListener('input', (e) => { filter = e.target.value; render() })

/* --- modal sửa khách --- */

const editDialog = $('#edit-dialog')
const editForm = $('#edit-form')
let editingCode = null

function openEditDialog(guest) {
  editingCode = guest.code
  $('#edit-who').textContent = `${fullName(guest)} · ${guest.code}`
  editForm.title.value = guest.title || ''
  editForm.name.value = guest.name || ''
  editForm.seats.value = guest.seats || 1
  editForm.note.value = guest.note || ''
  editDialog.showModal()
  // chọn sẵn nội dung ô tên: sửa tên là việc hay làm nhất
  editForm.name.focus()
  editForm.name.select()
}

editForm.addEventListener('submit', async (e) => {
  // method="dialog" tự đóng modal, nhưng phải chặn để còn gọi API rồi mới đóng
  e.preventDefault()
  const name = editForm.name.value.trim()
  if (!name) return editForm.name.focus()
  const body = JSON.stringify({
    title: editForm.title.value,
    name,
    seats: editForm.seats.value,
    note: editForm.note.value,
  })
  const code = editingCode
  try {
    await api(`/api/guests/${code}`, { method: 'PATCH', body })
    editDialog.close()
    await refresh()
    toast('Đã cập nhật')
  } catch (err) { toast(err.message) }
})

editDialog.addEventListener('click', (e) => {
  // bấm nền ngoài khung cũng đóng — <dialog> không tự làm việc này
  if (e.target === editDialog) editDialog.close()
  if (e.target.dataset.act === 'cancel') editDialog.close()
})

$('#guests').addEventListener('click', async (e) => {
  const button = e.target.closest('[data-act]')
  if (!button) return
  const code = button.closest('tr').dataset.code
  const guest = state.guests.find((g) => g.code === code)
  if (!guest) return

  if (button.dataset.act === 'copy') {
    const url = guestUrl(guest)
    try {
      await navigator.clipboard.writeText(url)
      toast('Đã chép link')
    } catch {
      // clipboard cần HTTPS hoặc localhost — nếu chặn thì hiện link để chép tay
      window.prompt('Chép link này:', url)
    }
    return
  }

  if (button.dataset.act === 'edit') {
    openEditDialog(guest)
    return
  }

  if (button.dataset.act === 'del') {
    if (!window.confirm(`Xoá "${fullName(guest)}"?\n\nLink đã gửi cho khách này sẽ ngừng hoạt động.`)) return
    await api(`/api/guests/${code}`, { method: 'DELETE' })
    await refresh()
    toast('Đã xoá')
  }
})

$('#rsvps').addEventListener('click', async (e) => {
  const button = e.target.closest('[data-act="del-rsvp"]')
  if (!button) return
  const id = button.closest('tr').dataset.id
  const rsvp = state.rsvps.find((r) => r.id === id)
  if (!rsvp) return
  const note = rsvp.message ? `\n\nLời nhắn sẽ mất: "${rsvp.message}"` : ''
  if (!window.confirm(`Xoá phản hồi của "${rsvp.name}"?${note}`)) return
  try {
    await api(`/api/rsvps/${id}`, { method: 'DELETE' })
    await refresh()
    toast('Đã xoá phản hồi')
  } catch (err) { toast(err.message) }
})

$('#export').addEventListener('click', () => {
  download('khach-moi.csv', csv([
    ['code', 'title', 'name', 'seats', 'note', 'link'],
    ...state.guests.map((g) => [g.code, g.title, g.name, g.seats || 1, g.note, guestUrl(g)]),
  ]))
})

$('#export-rsvp').addEventListener('click', () => {
  download('phan-hoi-rsvp.csv', csv([
    ['thoi_diem', 'ma_khach', 'ten', 'tham_du', 'so_nguoi', 'karaoke', 'bai_hat', 'loi_nhan'],
    ...state.rsvps.map((r) => [r.at, r.guestCode, r.name, r.attendance, r.guests, r.karaoke, r.song, r.message]),
  ]))
})

/* --- khởi động --- */
const { authed } = await api('/api/session')
if (authed) await showApp()
else showLogin()
