"use strict";
// Trang đăng nhập chủ nhà: gửi mã tới /api/admin-login (cùng nguồn gốc). Máy chủ đặt cookie HttpOnly; mã không được lưu ở trình duyệt.
const $ = id => document.getElementById(id);
const say = t => { $("login-msg").textContent = t; $("login-msg").hidden = !t; };
$("login-form").onsubmit = async e => {
  e.preventDefault();
  const token = $("token-in").value;
  if (!token) return;
  $("login-btn").disabled = true; say("");
  try {
    const r = await fetch("/api/admin-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    if (r.status === 204) { location.replace("/manage"); return; }
    if (r.status === 401) say("Mã không đúng. Bạn thử lại nhé.");
    else if (r.status === 429) say("Bạn đã thử sai nhiều lần. Vui lòng đợi một lát rồi thử lại.");
    else say("Chưa đăng nhập được lúc này. Bạn thử lại sau nhé.");
  } catch (err) { say("Chưa kết nối được. Bạn kiểm tra mạng rồi thử lại nhé."); }
  $("login-btn").disabled = false; $("token-in").select();
};
