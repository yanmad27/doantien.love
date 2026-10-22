"use strict";
// SHA-256 tăng dần (crypto.subtle.digest không đọc theo luồng; video tới 2 GiB không thể nạp cả vào bộ nhớ).
(function (root) {
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  const W = new Uint32Array(64);
  class Sha256 {
    constructor() {
      this.h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
      this.buf = new Uint8Array(64); this.fill = 0; this.len = 0;
    }
    block(b, o) {
      const h = this.h;
      for (let i = 0; i < 16; i++, o += 4) W[i] = (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3];
      for (let i = 16; i < 64; i++) {
        const x = W[i - 15], y = W[i - 2];
        W[i] = (W[i - 16] + (((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3)) + W[i - 7] + (((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10))) | 0;
      }
      let a = h[0], b2 = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))) + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        const t2 = ((((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))) + ((a & b2) ^ (a & c) ^ (b2 & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b2; b2 = a; a = (t1 + t2) | 0;
      }
      h[0] += a; h[1] += b2; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    update(u8) {
      let i = 0; const n = u8.length; this.len += n;
      if (this.fill) {
        const take = Math.min(64 - this.fill, n);
        this.buf.set(u8.subarray(0, take), this.fill); this.fill += take; i = take;
        if (this.fill < 64) return this;
        this.block(this.buf, 0); this.fill = 0;
      }
      for (; i + 64 <= n; i += 64) this.block(u8, i);
      if (i < n) { this.buf.set(u8.subarray(i), 0); this.fill = n - i; }
      return this;
    }
    hex() {
      const bits = this.len * 8, pad = new Uint8Array(((this.fill < 56 ? 56 : 120) - this.fill) + 8);
      pad[0] = 0x80;
      const dv = new DataView(pad.buffer);
      dv.setUint32(pad.length - 8, Math.floor(bits / 4294967296)); dv.setUint32(pad.length - 4, bits >>> 0);
      const keep = this.len; this.update(pad); this.len = keep;
      return Array.from(this.h, x => x.toString(16).padStart(8, "0")).join("");
    }
  }
  // Băm File theo từng lát, nhường luồng giao diện giữa các lát; onProgress(bytesDone) sau mỗi lát; signal.aborted → ném AbortError
  Sha256.file = async function (file, onProgress, sliceSize = 4 * 1048576, signal) {
    const s = new Sha256();
    for (let off = 0; off < file.size; off += sliceSize) {
      if (signal && signal.aborted) throw new DOMException("aborted", "AbortError");
      s.update(new Uint8Array(await file.slice(off, Math.min(file.size, off + sliceSize)).arrayBuffer()));
      if (onProgress) onProgress(Math.min(file.size, off + sliceSize));
      await new Promise(r => setTimeout(r, 0));
    }
    return s.hex();
  };
  if (typeof module !== "undefined" && module.exports) module.exports = Sha256; else root.Sha256 = Sha256;
})(typeof self !== "undefined" ? self : this);
