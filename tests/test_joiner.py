"""Self-contained test: builds synthetic recorder files, joins them, checks results.

Run:  python3 tests/test_joiner.py
"""
import os, struct, shutil, sys, random, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wav_joiner as wj

random.seed(1)
T = tempfile.mkdtemp(prefix="wav_joiner_test_")

def chunk(cid, payload):
    return cid + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")

def bext(tr, desc=b"sSCENE=12\r\nsTAKE=3\r\n"):
    p = desc.ljust(256, b"\0") + b"SoundDevices".ljust(32, b"\0") + b"".ljust(32, b"\0")
    p += b"2026-10-02" + b"09:00:00" + struct.pack("<II", tr & 0xFFFFFFFF, tr >> 32)
    p += struct.pack("<H", 1) + b"\0" * 64 + b"\0" * 190 + b"coding history\r\n"
    return p

def fmt(ch, rate, bits, fl=False, ext=False):
    ba = ch * bits // 8
    if ext:
        guid = struct.pack("<H", 3 if fl else 1) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        return struct.pack("<HHIIHH", 0xFFFE, ch, rate, rate * ba, ba, bits) + struct.pack("<HHI", 22, bits, 0) + guid
    return struct.pack("<HHIIHH", 3 if fl else 1, ch, rate, rate * ba, ba, bits)

def make(name, tr, ch, bits, nsamp, fl=False, ext=False, ixml_after=False, folder=T):
    ba = ch * bits // 8
    audio = bytes(random.getrandbits(8) for _ in range(nsamp * ba))
    ix = chunk(b"iXML", b"<BWFXML><SCENE>12</SCENE><TAKE>3</TAKE><SPEED><TIMECODE_RATE>24000/1001</TIMECODE_RATE><TIMECODE_FLAG>NDF</TIMECODE_FLAG></SPEED><TRACK_LIST><TRACK><NAME>Boom</NAME></TRACK></TRACK_LIST></BWFXML>")
    body = chunk(b"fmt ", fmt(ch, 48000, bits, fl, ext)) + chunk(b"bext", bext(tr))
    if fl: body += chunk(b"fact", struct.pack("<I", nsamp))
    if not ixml_after: body += ix
    body += chunk(b"data", audio)
    if ixml_after: body += ix
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    return audio

def read(path):
    w = wj.WavInfo(path)
    with open(path, "rb") as f:
        f.seek(w.data_offset); a = f.read(w.data_size)
    return w, a

ok = True
def check(cond, msg):
    global ok
    print(("PASS " if cond else "FAIL ") + msg); ok &= bool(cond)

base = 9 * 3600 * 48000
# A: 24-bit poly 8ch split in 3 (iXML after data like some recorders)
a1 = make("T01A.wav", base, 8, 24, 1001, ixml_after=True)
a2 = make("T01A_1.wav", base + 1001, 8, 24, 777, ixml_after=True)
a3 = make("T01A_2.wav", base + 1778, 8, 24, 5)
# B: 32-bit float mono extensible split in 2
b1 = make("T02B.wav", base + 100000, 1, 32, 333, fl=True, ext=True)
b2 = make("T02B_1.wav", base + 100333, 1, 32, 444, fl=True, ext=True)
# C: 16-bit with 1-sample gap -> must not join
make("T03C.wav", base + 200000, 2, 16, 100); make("T03C_1.wav", base + 200101, 2, 16, 100)
# D: adjacent TC but channel count differs -> must not join
make("T04D.wav", base + 300000, 2, 24, 100); make("T04D_1.wav", base + 300100, 4, 24, 100)
# E: 32-bit int in subfolder, odd byte count (3ch*... no, use mono 24bit odd samples)
sub = os.path.join(T, "Day2"); os.makedirs(sub)
e1 = make("T05E.wav", base + 400000, 1, 24, 7, folder=sub); e2 = make("T05E_1.wav", base + 400007, 1, 24, 9, folder=sub)
f1 = make("T06F.wav", base + 500000, 2, 32, 50, folder=sub); f2 = make("T06F_1.wav", base + 500050, 2, 32, 50, folder=sub)

wj.main([T])

w, a = read(os.path.join(T, "T01A_joined.wav"))
check(a == a1 + a2 + a3, "24-bit poly 3-way audio bit-identical")
check(w.time_ref == base and w.channels == 8, "start TC + channels kept")
names = [c for c, _ in w.chunks]
check(b"iXML" in names and b"bext" in names, "bext + iXML retained")
w, a = read(os.path.join(T, "T02B_joined.wav"))
check(a == b1 + b2 and w.bits == 32, "32-bit float extensible joined")
fact = dict(w.chunks)[b"fact"]
check(struct.unpack("<I", fact[:4])[0] == 777, "fact sample count updated")
check(not os.path.exists(os.path.join(T, "T03C_joined.wav")), "1-sample gap not joined")
check(not os.path.exists(os.path.join(T, "T04D_joined.wav")), "channel mismatch not joined")
w, a = read(os.path.join(sub, "T05E_joined.wav"))
check(a == e1 + e2 and w.data_size % 2 == 0, "odd-length data, subfolder")
w, a = read(os.path.join(sub, "T06F_joined.wav"))
check(a == f1 + f2, "32-bit int joined")

# libsndfile cross-check
try:
    import soundfile as sf
    for p in ["T01A_joined.wav", "T02B_joined.wav", "Day2/T05E_joined.wav"]:
        i = sf.info(os.path.join(T, p)); print("  sndfile:", p, i.channels, i.subtype, i.frames)
except ImportError:
    print("  (soundfile not installed)")

import csv, glob
check(os.path.isdir(os.path.join(T, "merged_source_files")) and sorted(os.listdir(os.path.join(T, "merged_source_files"))) == ["T01A.wav","T01A_1.wav","T01A_2.wav","T02B.wav","T02B_1.wav"], "sources moved to merged_source_files")
check(os.path.exists(os.path.join(T, "T03C.wav")) and os.path.exists(os.path.join(T, "T04D_1.wav")), "unjoined files left in place")
check(os.path.isdir(os.path.join(sub, "merged_source_files")), "subfolder sources moved within subfolder")
reps = glob.glob(os.path.join(T, "wav_joiner_report_*.csv"))
check(len(reps) == 1 and glob.glob(os.path.join(sub, "wav_joiner_report_*.csv")), "CSV per folder")
rows = list(csv.DictReader(open(reps[0], encoding="utf-8-sig")))
for r in rows: print("  ", {k: r[k] for k in ["status","output_file","segment","source_file","source_start_tc","source_end_tc","output_start_tc","output_duration","tc_rate","scene","take","source_moved_to"]})
check(len(rows) == 5, "one CSV row per source file")
# Re-run: no duplicates
wj.main([T])

# Repeated or overlapping selections must not create false ambiguous joins.
for name in ["repeated_folder", "repeated_file", "repeated_first", "folder_and_file", "parent_and_child",
             "relative_and_absolute"]:
    parent = os.path.join(T, name); folder = os.path.join(parent, "Day1")
    os.makedirs(folder)
    first = os.path.join(folder, "Take.wav"); second = os.path.join(folder, "Take_1.wav")
    x1 = make("Take.wav", base, 2, 24, 100, folder=folder)
    x2 = make("Take_1.wav", base + 100, 2, 24, 120, folder=folder)
    selections = {
        "repeated_folder": [folder, folder],
        "repeated_file": [first, second, second],
        "repeated_first": [first, first, second],
        "folder_and_file": [folder, second],
        "parent_and_child": [parent, folder],
        "relative_and_absolute": [os.path.relpath(folder), folder],
    }
    paths = selections[name]
    found = wj.collect_dirs(paths)
    check(len(found.get(folder, [])) == 2 and set(found[folder]) == {first, second},
          name + ": each source collected once")
    wj.main(paths if name == "repeated_first" else ["--no-move"] + paths)
    joined = os.path.join(folder, "Take_joined.wav")
    check(os.path.exists(joined), name + ": overlapping selection joins take")
    if os.path.exists(joined):
        w, a = read(joined)
        check(a == x1 + x2, name + ": joined audio bit-identical")
    reports = glob.glob(os.path.join(folder, "wav_joiner_report_*.csv"))
    with open(reports[0], encoding="utf-8-sig", newline="") as report:
        selected_rows = list(csv.DictReader(report))
    check(len(selected_rows) == 2 and all(r["status"] == "joined" for r in selected_rows),
          name + ": one joined report row per source")
    if name == "repeated_first":
        check(not os.path.exists(first) and not os.path.exists(second)
              and sorted(os.listdir(os.path.join(folder, wj.SOURCE_DIR))) == ["Take.wav", "Take_1.wav"],
              name + ": each source is moved once")
    else:
        check(os.path.exists(first) and os.path.exists(second), name + ": no-move keeps sources")

# Different files with the same start time remain genuinely ambiguous.
amb = os.path.join(T, "ambiguous"); os.makedirs(amb)
make("Start.wav", base, 2, 24, 100, folder=amb)
make("NextA.wav", base + 100, 2, 24, 120, folder=amb)
make("NextB.wav", base + 100, 2, 24, 120, folder=amb)
found = wj.collect_dirs([amb, amb])
check(len(found[amb]) == 3, "distinct ambiguous sources are retained")
amb_rows, made, no_tc = wj.process_folder(amb, found[amb])
check(made == 0 and not os.path.exists(os.path.join(amb, "Start_joined.wav")),
      "genuinely ambiguous take is not joined")
check(len(amb_rows) == 1 and "2 possible next files" in amb_rows[0]["notes"],
      "genuinely ambiguous take is reported once")
check(all(os.path.exists(os.path.join(amb, n)) for n in ["Start.wav", "NextA.wav", "NextB.wav"]),
      "ambiguous sources are not moved")

# RF64 path with tiny limit
wj.RIFF_LIMIT = 1000
R = os.path.join(T, "rf"); os.makedirs(R)
r1 = make("R.wav", base, 2, 24, 200, folder=R); r2 = make("R_1.wav", base + 200, 2, 24, 300, folder=R)
wj.main([R])
p = os.path.join(R, "R_joined.wav")
with open(p, "rb") as f: hdr = f.read(4)
w, a = read(p)
check(hdr == b"RF64" and a == r1 + r2 and w.time_ref == base, "RF64 written and readable")
try:
    import soundfile as sf; print("  sndfile RF64:", sf.info(p).format, sf.info(p).frames)
except ImportError: pass
# RF64 input re-joined
r3 = make("R_2x.wav", base + 500, 2, 24, 10, folder=R)
os.rename(p, os.path.join(R, "RR.wav"))
wj.main([os.path.join(R, "RR.wav"), os.path.join(R, "R_2x.wav")])
w, a = read(os.path.join(R, "RR_joined.wav"))
check(a == r1 + r2 + r3, "RF64 input can be joined")
shutil.rmtree(T, ignore_errors=True)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
