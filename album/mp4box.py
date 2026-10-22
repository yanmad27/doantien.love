"""Kiểm MP4 do chủ nhà tải lên bằng bộ duyệt box thuần Python: không bao giờ đọc phần dữ liệu mdat, chỉ nạp moov (có chặn kích thước).
Chấp nhận: ftyp đầu tiên, moov TRƯỚC mdat (faststart), đúng một track video H.264 (avc1/avc3), tối đa một track âm thanh mp4a.
Mọi thứ khác → Mp4Error(lý do)."""
import os
import struct

MAX_MOOV = 64 * 1024 * 1024
MAX_TOP_BOXES = 100_000
MAX_DIM = 16384


class Mp4Error(ValueError):
    pass


def _header(read_at, pos, end):
    """(loại, độ dài header, vị trí hết box) của box tại pos trong [pos, end). size=1 → largesize 64 bit; size=0 → đến hết vùng chứa."""
    if end - pos < 8:
        raise Mp4Error("box bị cắt cụt")
    head = read_at(pos, 8)
    if len(head) < 8:
        raise Mp4Error("box bị cắt cụt")
    size, typ = struct.unpack(">I4s", head)
    hl = 8
    if size == 1:
        ext = read_at(pos + 8, 8)
        if len(ext) < 8 or end - pos < 16:
            raise Mp4Error("box bị cắt cụt")
        size, hl = struct.unpack(">Q", ext)[0], 16
    elif size == 0:
        size = end - pos
    if size < hl:
        raise Mp4Error("kích thước box không hợp lệ")
    if pos + size > end:
        raise Mp4Error("box vượt quá vùng chứa (file bị cắt cụt hoặc hỏng)")
    return typ, hl, pos + size


def _children(buf, start, end):
    pos = start
    while pos < end:
        typ, hl, stop = _header(lambda o, n: buf[o:o + n], pos, end)
        yield typ, pos + hl, stop
        pos = stop


def _find(buf, start, end, typ):
    return [(s, e) for t, s, e in _children(buf, start, end) if t == typ]


def _u32(buf, o):
    if o + 4 > len(buf):
        raise Mp4Error("box quá ngắn")
    return struct.unpack_from(">I", buf, o)[0]


def _fullbox_times(buf, s, e):
    """mvhd/mdhd → (timescale, duration)."""
    if e - s < 4:
        raise Mp4Error("box quá ngắn")
    ver = buf[s]
    if ver == 0:
        if e - s < 20:
            raise Mp4Error("box quá ngắn")
        return struct.unpack_from(">II", buf, s + 12)
    if ver == 1:
        if e - s < 32:
            raise Mp4Error("box quá ngắn")
        ts, dur = struct.unpack_from(">IQ", buf, s + 20)
        return ts, dur
    raise Mp4Error("phiên bản box không hỗ trợ")


def _tkhd(buf, s, e):
    """→ (rộng, cao hiển thị) sau khi áp ma trận xoay."""
    if e - s < 4:
        raise Mp4Error("tkhd quá ngắn")
    ver = buf[s]
    mo, wo = (40, 76) if ver == 0 else (52, 88) if ver == 1 else (None, None)
    if mo is None or e - s < wo + 8:
        raise Mp4Error("tkhd không hợp lệ")
    a, b, u, c, d, v, x, y, w9 = struct.unpack_from(">9i", buf, s + mo)
    one = 0x10000
    if (u, v, x, y, w9) != (0, 0, 0, 0, 0x40000000) or (a, b, c, d) not in (
            (one, 0, 0, one), (0, one, -one, 0), (-one, 0, 0, -one), (0, -one, one, 0)):  # chỉ đơn vị và xoay đúng 90/180/270
        raise Mp4Error("ma trận tkhd không phải góc xoay chuẩn (0/90/180/270)")
    w, h = _u32(buf, s + wo) / 65536.0, _u32(buf, s + wo + 4) / 65536.0
    return (h, w) if a == 0 else (w, h)


def _trak(buf, s, e):
    tk = _find(buf, s, e, b"tkhd")
    md = _find(buf, s, e, b"mdia")
    if len(tk) != 1 or len(md) != 1:
        raise Mp4Error("trak thiếu tkhd/mdia")
    ms, me = md[0]
    hd, mh, mi = _find(buf, ms, me, b"hdlr"), _find(buf, ms, me, b"mdhd"), _find(buf, ms, me, b"minf")
    if len(hd) != 1 or len(mh) != 1 or len(mi) != 1:
        raise Mp4Error("trak thiếu hdlr/mdhd/minf")
    if hd[0][1] - hd[0][0] < 12:
        raise Mp4Error("hdlr quá ngắn")
    kind = bytes(buf[hd[0][0] + 8:hd[0][0] + 12])
    stbl = _find(buf, mi[0][0], mi[0][1], b"stbl")
    if len(stbl) != 1:
        raise Mp4Error("thiếu stbl")
    stsd = _find(buf, stbl[0][0], stbl[0][1], b"stsd")
    if len(stsd) != 1:
        raise Mp4Error("thiếu stsd")
    ss, se = stsd[0]
    if se - ss < 8:
        raise Mp4Error("stsd quá ngắn")
    count, pos, codecs = _u32(buf, ss + 4), ss + 8, []
    if count < 1:
        raise Mp4Error("stsd rỗng")
    for _ in range(count):  # duyệt đủ entry_count mẫu; mỗi mẫu ≥ 16 byte (header 8 + reserved 6 + data_reference_index 2) và nằm trong stsd
        if se - pos < 16:
            raise Mp4Error("stsd có ít mẫu hơn entry_count hoặc bị cắt cụt")
        size = _u32(buf, pos)
        if size < 16 or pos + size > se:
            raise Mp4Error("kích thước mẫu trong stsd không hợp lệ")
        codecs.append(bytes(buf[pos + 4:pos + 8]))
        pos += size
    if pos != se:
        raise Mp4Error("stsd có dữ liệu thừa sau các mẫu")
    codec = codecs
    return kind, codec, _tkhd(buf, *tk[0]), _fullbox_times(buf, *mh[0])


def parse_moov(buf):
    """buf: phần thân của box moov."""
    ms, me = 0, len(buf)  # buf = phần thân của moov
    mvhd = _find(buf, ms, me, b"mvhd")
    if len(mvhd) != 1:
        raise Mp4Error("thiếu mvhd")
    ts, dur = _fullbox_times(buf, *mvhd[0])
    video, audio = [], []
    for s, e in _find(buf, ms, me, b"trak"):
        kind, codec, disp, mdhd = _trak(buf, s, e)
        if kind == b"vide":
            video.append((codec, disp, mdhd))
        elif kind == b"soun":
            audio.append(codec)
        else:
            raise Mp4Error("chỉ nhận track video + âm thanh")
    if len(video) != 1:
        raise Mp4Error("cần đúng một track video")
    if len(audio) > 1:
        raise Mp4Error("chỉ nhận tối đa một track âm thanh")
    codecs, (dw, dh), (vts, vdur) = video[0]
    for c in codecs:
        if c not in (b"avc1", b"avc3"):
            raise Mp4Error(f"codec video {c.decode('latin-1')!r} không được hỗ trợ (cần H.264: avc1/avc3)")
    for c in audio[0] if audio else ():
        if c != b"mp4a":
            raise Mp4Error(f"codec âm thanh {c.decode('latin-1')!r} không được hỗ trợ (cần mp4a)")
    codec = codecs[0]
    w, h = int(round(dw)), int(round(dh))
    if not (0 < w <= MAX_DIM and 0 < h <= MAX_DIM):
        raise Mp4Error("kích thước hình không hợp lệ")
    seconds = dur / ts if ts and dur else (vdur / vts if vts and vdur else 0.0)
    if not seconds > 0:
        raise Mp4Error("thời lượng không hợp lệ")
    return dict(w=w, h=h, duration=round(seconds, 3), codec=codec.decode("latin-1"), audio=bool(audio))


def probe(path):
    """Duyệt cấp cao nhất của file; trả dict(w, h, duration, codec, audio) hoặc ném Mp4Error."""
    with open(path, "rb") as fh:
        total = os.fstat(fh.fileno()).st_size

        def read_at(o, n):
            fh.seek(o)
            return fh.read(n)

        pos, n, seen, info = 0, 0, [], None
        while pos < total:
            n += 1
            if n > MAX_TOP_BOXES:
                raise Mp4Error("quá nhiều box")
            typ, hl, stop = _header(read_at, pos, total)
            if not seen and typ != b"ftyp":
                raise Mp4Error("ftyp phải là box đầu tiên")
            if typ == b"moov":
                if b"moov" in seen:
                    raise Mp4Error("cần đúng một moov")
                if b"mdat" in seen:
                    raise Mp4Error("moov phải đứng trước mdat (faststart)")
                if stop - pos > MAX_MOOV:
                    raise Mp4Error("moov quá lớn")
                info = parse_moov(read_at(pos + hl, stop - pos - hl))
            seen.append(typ)
            pos = stop
        if b"ftyp" not in seen:
            raise Mp4Error("thiếu ftyp")
        if info is None:
            raise Mp4Error("thiếu moov")
        if b"mdat" not in seen:
            raise Mp4Error("thiếu mdat")
        return info
