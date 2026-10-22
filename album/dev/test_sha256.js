// KAT cho static/sha256.js: node dev/test_sha256.js  (so với vector chuẩn và crypto của node)
const crypto = require("crypto"), path = require("path");
const Sha256 = require(path.join(__dirname, "..", "static", "sha256.js"));
let bad = 0;
const ref = b => crypto.createHash("sha256").update(b).digest("hex");
const chk = (name, got, want) => { const ok = got === want; if (!ok) bad++; console.log((ok ? "ok    " : "HỎNG  ") + name + (ok ? "" : ` ${got} != ${want}`)); };
const run = (b, cuts) => { const s = new Sha256(); let p = 0; for (const c of cuts) { s.update(b.subarray(p, c)); p = c; } s.update(b.subarray(p)); return s.hex(); };
chk("empty", run(Buffer.alloc(0), []), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
chk("abc", run(Buffer.from("abc"), []), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
chk("448-bit", run(Buffer.from("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"), []), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
const mib = Buffer.alloc(1048576); for (let i = 0; i < mib.length; i++) mib[i] = (i * 31 + (i >> 8)) & 255;
chk("1 MiB pattern", run(mib, []), ref(mib));
for (const n of [55, 56, 57, 63, 64, 65, 119, 120, 128, 129]) { const b = crypto.randomBytes(n); chk(`pad boundary ${n}`, run(b, []), ref(b)); }
const big = crypto.randomBytes(3 * 1048576 + 17);
for (const cuts of [[1], [63, 64, 65], [64, 128, 4096], [1048576, 2097152], [7, 8, 1048575, 1048577, 3 * 1048576]]) chk("slices " + cuts.join(","), run(big, cuts), ref(big));
// độ dài > 2^32 bit (512 MiB): 600 MiB toàn số 0, đưa theo lát 4 MiB, so với crypto của node (~vài giây)
{ const z = Buffer.alloc(4 * 1048576), s = new Sha256(), r = crypto.createHash("sha256"), t0 = Date.now();
  for (let i = 0; i < 150; i++) { s.update(z); r.update(z); }
  chk(`600 MiB zeros (length > 2^32 bits, ${Math.round((Date.now() - t0) / 100) / 10}s)`, s.hex(), r.digest("hex")); }
const fake = { size: big.length, slice: (a, b) => new Blob([big.subarray(a, b)]) };
Sha256.file(fake, null, 1000003).then(h => { chk("File.slice 1000003", h, ref(big)); if (bad) process.exit(1); console.log("sha256 KAT ok"); });
