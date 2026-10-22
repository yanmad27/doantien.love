"""Chỉ dùng khi phát triển: bộ kiểm thử đầu-cuối trên backend THẬT (mô hình khuôn mặt thật) cho album hai ngày. Thường chạy qua run_all.sh.
  python -I e2e_run.py main --url http://127.0.0.1:8830 --token e2e-token --data-dir DATA --out OUT --dir0410 DIR --dir1710 DIR
Các bước (stage):
  main    tải lên trên trình duyệt (4/10 mặc định + 17/10 qua bộ chọn ngày) → Xử lý → đếm theo ngày → chụp màn hình → tìm selfie theo từng phạm vi
          → chuyển ngày + hoàn tác → ẩn một ảnh 17/10; ghi OUT/state.json
  verify  (sau khi khởi động lại máy chủ) so sánh ngày/ẩn/số đếm với state.json
  dbcmp   dbcmp --db-a A/index.db --db-b B/index.db: photo_event / photo_hidden / settings / id ảnh giống hệt (chỉ đọc)
Chỉ nói chuyện với --url (localhost); ảnh chỉ được đưa cho trình duyệt dưới dạng đường dẫn hoặc mở bằng PIL như dữ liệu, không bao giờ thực thi. Chạy bằng -I.
"""
import argparse, glob, hashlib, json, os, sqlite3, sys, time, urllib.request, urllib.parse
from urllib.parse import urlparse

E4, E17 = "2026-10-04", "2026-10-17"
ap = argparse.ArgumentParser()
ap.add_argument("stage", choices=["main", "verify", "dbcmp"])
ap.add_argument("--url", default="http://127.0.0.1:8830")
ap.add_argument("--token", default="e2e-token")
ap.add_argument("--data-dir", default="")
ap.add_argument("--out", default="")
ap.add_argument("--dir0410", default="")
ap.add_argument("--dir1710", default="")
ap.add_argument("--batch", type=int, default=150, help="files per set_input_files call")
ap.add_argument("--selfie", default="", help="use this selfie instead of cropping one from the cross-event best pair")
ap.add_argument("--db-a", default=""); ap.add_argument("--db-b", default="")
a = ap.parse_args()
LOG = []
def say(*x):
    s = " ".join(str(i) for i in x); print(s, flush=True); LOG.append(s)
def check(c, m):
    if not c:
        say("FAIL:", m); finish(1)
    say("ok:", m)
def finish(code):
    if a.out:
        open(os.path.join(a.out, f"log-{a.stage}.txt"), "w").write("\n".join(LOG) + "\n")
    sys.exit(code)
EXTS = (".jpg", ".jpeg", ".png", ".webp")
def photos_in(d):
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(EXTS) and not f.startswith("."))  # *.part etc. ignored

def ro(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30); return c
def db_state(dbf):
    c = ro(dbf)
    try:
        g = lambda q: c.execute(q).fetchall()
        return dict(photos=sorted(r[0] for r in g("select id from photos")), events=sorted(g("select photo_id,event from photo_event")),
                    hidden=sorted(r[0] for r in g("select id from photo_hidden")), settings=sorted(g("select key,value from settings")),
                    faces=g("select count(*) from faces")[0][0])
    finally:
        c.close()

if a.stage == "dbcmp":
    A, B = db_state(a.db_a), db_state(a.db_b)
    ok = True
    for k in ("events", "hidden", "photos"):
        same = A[k] == B[k]; ok &= same
        print(("ok:" if same else "FAIL:"), k, "identical" if same else f"differ A={len(A[k])} B={len(B[k])}", f"(n={len(A[k])})")
    sa, sb = dict(A["settings"]), dict(B["settings"])
    # settings_epoch may legitimately be re-created; the search flag must survive
    same = sa.get("search_includes_hidden") == sb.get("search_includes_hidden"); ok &= same
    print(("ok:" if same else "FAIL:"), "settings.search_includes_hidden", sa.get("search_includes_hidden"), "->", sb.get("search_includes_hidden"), "| all settings A", sa, "B", sb)
    print("faces A/B:", A["faces"], B["faces"])
    sys.exit(0 if ok else 1)

os.makedirs(a.out, exist_ok=True)
SH = os.path.join(a.out, "shots"); os.makedirs(SH, exist_ok=True)
STATE = os.path.join(a.out, "state.json")
from playwright.sync_api import sync_playwright
import numpy as np
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = None

def api(path):
    with urllib.request.urlopen(a.url + path) as f:
        return json.load(f)
def shot(pg, n, full=False):
    pg.wait_for_timeout(500); pg.screenshot(path=os.path.join(SH, n), full_page=full); say("shot:", os.path.join(SH, n))
def login(pg):
    pg.goto(a.url + "/manage")
    if pg.locator("#login-form").count():
        pg.fill("#token-in", a.token); pg.click("#login-btn")
    pg.wait_for_function("document.getElementById('disk') && document.getElementById('disk').textContent.includes('GB')", timeout=60000)
def admin_rows(pg):
    return pg.evaluate("fetch('/api/admin/photos').then(r => r.json())")["photos"]  # [..., id@0, burst@7?, ..., event@9]
def ev_map(pg):
    return {p[0]: p[9] for p in admin_rows(pg)}
def guest_ids(ev=None):
    rows = api("/api/gallery")["photos"]
    return sorted({p[0] for p in rows if ev is None or p[8] == ev})
def num(s):
    return int("".join(ch for ch in s if ch.isdigit()) or 0)
def capture_search(pg):
    got = []
    def on_route(route):
        r = route.fetch(); got.append(r.json()); route.fulfill(response=r)
    pg.route("**/api/search*", on_route)
    return got, lambda: pg.unroute("**/api/search*", on_route)

DESKTOP = dict(viewport={"width": 1280, "height": 800})
PHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)

def search_ui(bt, selfie, shotname, truth):  # one unscoped search (no scope chips): results of both events in one list
    ctx = bt.new_context(**PHONE); pg = ctx.new_page()
    try:
        pg.goto(a.url + "/"); pg.wait_for_selector("#vg-all .tile", timeout=60000)
        pg.evaluate("scrollTo(0, 0)")
        got, stop = capture_search(pg)
        t0 = time.time(); pg.set_input_files("#in-file", selfie)
        for _ in range(1200):  # response may legitimately carry 0 results (UI then shows the empty state, not #results)
            if got: break
            pg.wait_for_timeout(100)
        dt = time.time() - t0
        if not got:  # 422 no-face / error: nothing captured
            return dict(n=0, events=[], ev_ids={}, wrong_event=[], ids=[], seconds=round(dt, 2), server_ms=None, total_matches=None, no_json=True)
        pg.wait_for_timeout(900)
        r = got[-1]["results"]
        evs = sorted({x["ev"] for x in r})
        wrong = [x["id"] for x in r if truth.get(x["id"]) != x["ev"]]
        shot(pg, shotname)
        stop()
        return dict(n=len(r), events=evs, ev_ids={e: [x["id"] for x in r if x["ev"] == e] for e in (E4, E17)}, wrong_event=wrong, ids=[x["id"] for x in r], seconds=round(dt, 2),
                    server_ms=got[-1].get("timing_ms"), total_matches=got[-1].get("total_matches"))
    finally:
        ctx.close()

def make_selfie(dbf, orig_dir, truth, out):
    """face crop of the 17/10 face most similar to some 4/10 face (a person present in both events); fallback: any face"""
    c = ro(dbf)
    rows = c.execute("select photo_id,idx,x1,y1,x2,y2,score,emb from faces").fetchall(); c.close()
    paths = dict(ro(dbf).execute("select id,path from photos").fetchall())
    E = np.stack([np.frombuffer(r[7], dtype=np.float32) for r in rows]); E /= np.linalg.norm(E, axis=1, keepdims=True)
    ev = np.array([truth.get(r[0], E4) for r in rows]); big = np.array([min(r[4] - r[2], r[5] - r[3]) for r in rows])
    i4, i17 = np.where(ev == E4)[0], np.where(ev == E17)[0]
    info = {}
    if len(i4) and len(i17):
        S = E[i17] @ E[i4].T
        S[(big[i17] < 120)[:, None] | (big[i4] < 120)[None, :]] = -1  # prefer large, clear faces
        k = np.unravel_index(S.argmax(), S.shape); sim = float(S[k]); pick = i17[k[0]]
        info = dict(cross_event_sim=round(sim, 3), pair=[rows[i17[k[0]]][0], rows[i4[k[1]]][0]])
    else:
        pick = int(big.argmax()); info = dict(cross_event_sim=None)
    r = rows[pick]
    im = ImageOps.exif_transpose(Image.open(os.path.join(orig_dir, paths[r[0]]))).convert("RGB")
    w, h = r[4] - r[2], r[5] - r[3]; m = .45
    box = (max(0, r[2] - m * w), max(0, r[3] - m * h), min(im.width, r[4] + m * w), min(im.height, r[5] + m * h))
    im.crop(tuple(int(v) for v in box)).save(out, quality=92)
    info.update(src_photo=r[0], src_event=truth.get(r[0]), face_px=int(min(w, h)))
    return info

with sync_playwright() as pw:
    bt = pw.chromium.launch()
    if a.stage == "verify":
        S = json.load(open(STATE))
        ctx = bt.new_context(**DESKTOP); pg = ctx.new_page(); login(pg)
        truth = ev_map(pg)
        check(truth == S["event_map"], f"after restart: {len(truth)} photos, event of every photo unchanged ({sum(v == E17 for v in truth.values())} on 17/10)")
        pub17, pub4 = guest_ids(E17), guest_ids(E4)
        check(S["hidden_id"] not in pub17 and S["hidden_id"] not in pub4, "after restart: hidden photo still absent from public manifest")
        check(len(pub17) == S["public_counts"][E17] and len(pub4) == S["public_counts"][E4], f"public counts per event unchanged {S['public_counts']}")
        hid = pg.evaluate("fetch('/api/admin/photos').then(r => r.json())")
        say("admin counts:", json.dumps(hid.get("counts"), ensure_ascii=False) if isinstance(hid, dict) else "")
        ctx.close()
        gc = bt.new_context(**PHONE); gp = gc.new_page(); gp.goto(a.url + "/#ev=" + E17)
        gp.wait_for_selector("#ev-tabs:not([hidden]) button", timeout=60000); gp.wait_for_selector("#vg-all .tile")
        check(gp.get_attribute('#ev-tabs button[aria-selected="true"]', "data-ev") == E17 and num(gp.inner_text("#count")) == len(pub17), "guest: opens on 17/10 tab with right count after restart")
        shot(gp, "restart-guest-17.png"); gc.close(); bt.close(); finish(0)

    # ================= main =================
    f4, f17 = photos_in(a.dir0410), photos_in(a.dir1710)
    check(f4 and f17, f"inputs: {len(f4)} photos for 4/10, {len(f17)} for 17/10")
    S = dict(sent={E4: len(f4), E17: len(f17)})
    ctx = bt.new_context(**DESKTOP); pg = ctx.new_page()
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    external = []
    ctx.on("request", lambda r: external.append(r.url) if urlparse(r.url).netloc not in (urlparse(a.url).netloc,) and not r.url.startswith(("data:", "blob:")) else None)
    login(pg)
    check(pg.is_checked('input[name="up-ev"][value="%s"]' % E4), "upload chooser defaults to 4/10")
    shot(pg, "d-01-manage-empty-chooser.png")

    def upload(files, ev):
        if ev != E4:
            pg.check('input[name="up-ev"][value="%s"]' % ev, force=True)
        else:
            pg.check('input[name="up-ev"][value="%s"]' % E4, force=True)
        t0 = time.time()
        for i in range(0, len(files), a.batch):
            with pg.expect_request(lambda r: "/api/upload" in r.url) as ri:
                pg.set_input_files("#pick", files[i:i + a.batch])
            check("event=" + ev in ri.value.url, f"upload request carries event={ev}")
            pg.wait_for_function("document.getElementById('upsum').textContent.includes('Hoàn tất')", timeout=1800000)
            if i == 0:
                shot(pg, f"d-02-upload-{'0410' if ev == E4 else '1710'}.png")
        return round(time.time() - t0, 1)
    S["upload_seconds"] = {E4: upload(f4, E4), E17: upload(f17, E17)}
    S["upload_chips"] = pg.inner_text("#upchips").replace("\n", ", "); say("upload chips:", S["upload_chips"])
    shot(pg, "d-03-upload-done.png")

    # ---- Process ----
    t0 = time.time(); pg.click("#proc")
    pg.wait_for_function("document.getElementById('pmsg').textContent.includes('Hoàn tất') || document.getElementById('pmsg').textContent.includes('Có lỗi')", timeout=7200000)
    S["process_seconds"] = round(time.time() - t0, 1); n_all = len(f4) + len(f17)
    S["process_seconds_per_photo"] = round(S["process_seconds"] / n_all, 2)
    say(f"Process: {S['process_seconds']} s for {n_all} photos = {S['process_seconds_per_photo']} s/photo; msg={pg.inner_text('#pmsg')!r}; tiles={pg.inner_text('#ptiles')!r}")
    check("Hoàn tất" in pg.inner_text("#pmsg"), "Process finished without error")
    pg.wait_for_timeout(1500)
    stale = dict(cells=pg.locator("#vgrid .cell").count(), c_all=pg.inner_text("#c-all"), ce4=pg.inner_text("#ce-" + E4), ce17=pg.inner_text("#ce-" + E17),
                 v_state=pg.inner_text("#v-state") if pg.locator("#v-state").is_visible() else "", admin_rows=len(admin_rows(pg)))
    S["grid_after_process_no_reload"] = stale
    shot(pg, "d-03b-manage-grid-after-process-NO-RELOAD.png", full=False)  # lưới ngay sau Xử lý, KHÔNG tải lại trang
    S["grid_stale"] = stale["cells"] == 0 or num(stale["c_all"]) != stale["admin_rows"] or num(stale["ce4"]) + num(stale["ce17"]) != stale["admin_rows"]
    say(f"Lưới /manage ngay sau Xử lý, không tải lại: {stale} -> {'CŨ (lỗi: lưới không tự làm mới)' if S['grid_stale'] else 'đã tự làm mới'}")
    if S["grid_stale"]:
        say("RELOAD WORKAROUND applied (stale-grid issue)")
        pg.reload(); login(pg)
    pg.wait_for_selector("#vgrid .cell", timeout=60000)

    # ---- counts per event (admin API + index.db read-only) ----
    truth = ev_map(pg)
    dbf = os.path.join(a.data_dir, "index.db"); c = ro(dbf)
    indexed = {E4: 0, E17: 0}
    for pid, in c.execute("select id from photos"):
        indexed[truth.get(pid, E4)] += 1
    ev_db = dict(c.execute("select photo_id,event from photo_event").fetchall())
    failed = {E4: [], E17: []}
    for path, reason in c.execute("select path,reason from failed"):
        failed[ev_db.get(hashlib.sha1(path.encode()).hexdigest()[:16], E4)].append([path, reason])
    n_faces = c.execute("select count(*) from faces").fetchone()[0]
    nf0 = c.execute("select count(*) from photos where n_faces=0").fetchone()[0]
    n_orig = c.execute("select count(*) from originals").fetchone()[0]
    c.close()
    S["counts"] = {ev: dict(sent=S["sent"][ev], indexed=indexed[ev], failed=len(failed[ev])) for ev in (E4, E17)}
    S["failed_detail"] = failed; S["faces_total"] = n_faces; S["photos_without_faces"] = nf0; S["originals_rows"] = n_orig
    say("COUNTS per event (sent/indexed/failed):", json.dumps(S["counts"]), "faces:", n_faces, "photos with 0 faces:", nf0, "originals:", n_orig)
    check(indexed[E4] + indexed[E17] + len(failed[E4]) + len(failed[E17]) >= n_orig - 0 and len(truth) == indexed[E4] + indexed[E17], "indexed+failed account for every stored original")
    shot(pg, "d-04-manage-processed.png")
    pg.click('#evf .fchip[data-e="%s"]' % E17); shot(pg, "d-05-manage-filter-1710.png")
    check(pg.locator("#vgrid .cell").count() > 0 and all(pg.evaluate("[...document.querySelectorAll('#vgrid .tag--ev')].every(t => t.textContent.includes('17/10'))") for _ in [0]), "event filter 17/10 shows only 17/10 badges")
    pg.click('#evf .fchip[data-e="all"]')

    # ---- guest, phone: tabs + browsing ----
    gc = bt.new_context(**PHONE); gp = gc.new_page(); gerr = []; gp.on("pageerror", lambda e: gerr.append(str(e)))
    gp.goto(a.url + "/"); gp.wait_for_selector("#ev-tabs:not([hidden]) button", timeout=60000); gp.wait_for_selector("#vg-all .tile")
    pub = {ev: guest_ids(ev) for ev in (E4, E17)}
    S["public_counts"] = {ev: len(v) for ev, v in pub.items()}
    check(gp.locator("#ev-tabs button").count() == 2, "guest: two event tabs")
    check(gp.get_attribute('#ev-tabs button[aria-selected="true"]', "data-ev") == E4 and num(gp.inner_text("#count")) == len(pub[E4]), f"guest: default tab 4/10 shows {len(pub[E4])}")
    shot(gp, "m-01-guest-tab-0410.png"); gp.mouse.wheel(0, 1400); shot(gp, "m-02-guest-0410-scrolled.png")
    gp.evaluate("scrollTo(0, 0)"); gp.click('#ev-tabs button[data-ev="%s"]' % E17); gp.wait_for_function("location.hash === '#ev=%s'" % E17)
    check(num(gp.inner_text("#count")) == len(pub[E17]), f"guest: 17/10 tab shows {len(pub[E17])}")
    shot(gp, "m-03-guest-tab-1710.png"); gp.mouse.wheel(0, 1400); shot(gp, "m-04-guest-1710-scrolled.png")
    check(not gerr, f"no JS errors on guest page {gerr}")
    gc.close()

    # ---- selfie + one unscoped search (both events in one list) ----
    selfie = a.selfie or os.path.join(a.out, "selfie.jpg")
    if not a.selfie:
        S["selfie"] = make_selfie(dbf, os.path.join(a.data_dir, "originals"), truth, selfie); say("selfie:", S["selfie"])
    S["search"] = {}
    r = search_ui(bt, selfie, "m-05-search-both.png", truth)
    say(f"search (both events): {r['n']} results, events={r['events']}, {r['seconds']}s (server {r['server_ms']})")
    check(not r["wrong_event"], "search: no wrong-event photo in results")
    check(r["n"] > 0, "search: at least one result")
    # per-event views derived from the single response (the guest UI no longer scopes the request)
    S["search"]["all"] = r
    S["search"][E4] = dict(r, ids=r["ev_ids"][E4], n=len(r["ev_ids"][E4])); S["search"][E17] = dict(r, ids=r["ev_ids"][E17], n=len(r["ev_ids"][E17]))
    sa = S["search"]
    say("selfie person in both events:", "yes" if set(sa["all"]["events"]) == {E4, E17} else "NO (only %s)" % sa["all"]["events"])

    # ---- /manage: reassignment + undo ----
    rows = admin_rows(pg)
    singles = [p[0] for p in rows if p[9] == E4 and not p[7] and not p[8]][:2] or [p[0] for p in rows if p[9] == E4][:2]
    check(len(singles) >= 1, "have 4/10 photos to reassign")
    pg.click("#v-mode")
    for k in singles:
        pg.evaluate("k => document.querySelector('.cell[data-k=\"' + k + '\"]').scrollIntoView({ block: 'center' })", k)
        pg.locator('.cell[data-k="%s"] .tile' % k).click()
    with pg.expect_response(lambda r: r.url.endswith("/api/admin/event") and r.request.method == "POST") as rr:
        pg.click("#a-ev-" + E17)
    check(rr.value.status == 200, "POST /api/admin/event 200")
    pg.wait_for_timeout(300); t2 = ev_map(pg)
    check(all(t2[k] == E17 for k in singles), f"{len(singles)} photo(s) moved to 17/10 (admin)")
    check(all(k in guest_ids(E17) and k not in guest_ids(E4) for k in singles), "moved photo(s) now in public 17/10, not 4/10 (manifest)")
    shot(pg, "d-06-manage-reassigned-toast.png")
    pg.click("#toast-undo"); pg.wait_for_timeout(500)
    check(all(ev_map(pg)[k] == E4 for k in singles), "undo restored 4/10")
    shot(pg, "d-07-manage-after-undo.png")
    pg.click("#v-mode")  # leave select mode

    # ---- hidden photo per event: hide the top 17/10 hit ----
    hid = next(i for i in sa[E17]["ids"] if truth[i] == E17)
    pg.click('#evf .fchip[data-e="%s"]' % E17)
    pg.evaluate("k => document.querySelector('.cell[data-k=\"' + k + '\"]').scrollIntoView({ block: 'center' })", hid)
    pg.click("#v-mode"); pg.locator('.cell[data-k="%s"] .tile' % hid).click()
    with pg.expect_response(lambda r: r.url.endswith("/api/admin/visibility") and r.request.method == "POST") as rr:
        pg.click("#a-hide")
        if pg.locator("#cf-yes").is_visible():
            pg.click("#cf-yes")
    check(rr.value.status == 200, "hide: POST /api/admin/visibility 200")
    shot(pg, "d-08-manage-hidden.png")
    pg.click("#v-mode"); pg.click('#evf .fchip[data-e="all"]')
    S["hidden_id"] = hid; S["event_map"] = ev_map(pg)
    S["public_counts"] = {ev: len(guest_ids(ev)) for ev in (E4, E17)}
    check(hid not in guest_ids(E17) and S["public_counts"][E17] == len(pub[E17]) - 1, f"hidden 17/10 photo absent from public manifest ({len(pub[E17])} -> {S['public_counts'][E17]})")
    gc = bt.new_context(**PHONE); gp = gc.new_page(); gp.goto(a.url + "/#ev=" + E17); gp.wait_for_selector("#vg-all .tile", timeout=60000)
    check(num(gp.inner_text("#count")) == len(pub[E17]) - 1, "guest 17/10 tab count dropped by one")
    shot(gp, "m-06-guest-1710-after-hide.png"); gc.close()
    setp = lambda on: pg.evaluate("on => fetch('/api/admin/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({search_includes_hidden: on})}).then(r => r.status)", on)
    S["hidden_search"] = {}
    for on in (True, False):
        check(setp(on) == 200, f"settings search_includes_hidden={on}")
        r = search_ui(bt, selfie, f"m-07-search-hidden-incl-{int(on)}.png", ev_map(pg))
        r["ids"] = r["ev_ids"][E17]; r["n"] = len(r["ids"])
        S["hidden_search"][str(on)] = dict(n=r["n"], hidden_in_results=hid in r["ids"], wrong=r["wrong_event"])
        say(f"17/10 part of unscoped search, include_hidden={on}: {r['n']} results, hidden photo present={hid in r['ids']}")
        check(not r["wrong_event"], f"hidden test include={on}: no wrong-event photo")
        if not on:
            check(hid not in r["ids"], "include_hidden=false: hidden 17/10 photo absent from search")
    check(setp(True) == 200, "settings restored to default (include hidden)")
    check(not errs, f"no JS errors on /manage {errs}")
    check(not external, f"no request left localhost: {external[:3]}")
    json.dump(S, open(STATE, "w"), indent=1, ensure_ascii=False)
    ctx.close(); bt.close(); say("MAIN OK; state ->", STATE); finish(0)
