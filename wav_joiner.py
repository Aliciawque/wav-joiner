#!/usr/bin/env python3
"""
WAV Joiner - joins Broadcast WAV files that a recorder split mid-take.

Two files are joined when:
  * they sit in the same folder,
  * their audio format is identical (channels, sample rate, bit depth, int/float),
  * the second file's BWF start timecode (bext TimeReference) is exactly the
    first file's start + its length in samples.

Audio is copied byte-for-byte (never decoded), so every bit depth and format
works and the result is bit-identical to the source. All metadata chunks
(bext, iXML, etc.) are taken from the first file of the take, so the joined
file keeps the original start timecode, scene/take, track names and so on.
Outputs larger than 4 GB are written as RF64 (the EBU standard for big BWFs).

Usage:  python3 wav_joiner.py [--no-move] <folder or files> [...]
Originals are never modified. Joined files are written next to the originals
as <first file name>_joined.wav; the source files that went into each join are
then moved into a merged_source_files/ subfolder (unless --no-move), and a
CSV report is written to each folder where something happened.

Standard library only; runs on the Python 3.9 that ships with macOS.
"""
import csv
import datetime
import os
import re
import shutil
import struct
import sys

SUFFIX = "_joined"
SOURCE_DIR = "merged_source_files"
REPORT_PREFIX = "wav_joiner_report_"
COPY_BLOCK = 16 * 1024 * 1024
RIFF_LIMIT = 0xFFFFFFFF
# Chunks we rebuild ourselves rather than copy from the source.
SKIP_CHUNKS = {b"data", b"JUNK", b"junk", b"ds64", b"PAD ", b"FLLR"}


class WavInfo(object):
    def __init__(self, path):
        self.path = path
        self.chunks = []          # list of (id, bytes) for non-audio chunks, in order
        self.fmt = None           # raw fmt chunk payload
        self.channels = 0
        self.sample_rate = 0
        self.block_align = 0
        self.bits = 0
        self.data_offset = 0
        self.data_size = 0
        self.time_ref = None      # samples since midnight, from bext
        self.format_tag = 0
        self.ixml = ""
        self._parse()

    @property
    def samples(self):
        return self.data_size // self.block_align

    @property
    def end_ref(self):
        return self.time_ref + self.samples

    def _parse(self):
        file_size = os.path.getsize(self.path)
        with open(self.path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[8:12] != b"WAVE" or head[:4] not in (b"RIFF", b"RF64"):
                raise ValueError("not a WAV file")
            is_rf64 = head[:4] == b"RF64"
            ds64_data_size = None
            pos = 12
            while pos + 8 <= file_size:
                f.seek(pos)
                cid = f.read(4)
                (size,) = struct.unpack("<I", f.read(4))
                if cid == b"ds64":
                    payload = f.read(size)
                    ds64_data_size = struct.unpack("<Q", payload[8:16])[0]
                elif cid == b"data":
                    if is_rf64 and size == 0xFFFFFFFF and ds64_data_size is not None:
                        size = ds64_data_size
                    # Guard against files truncated by a recorder power loss.
                    size = min(size, file_size - (pos + 8))
                    self.data_offset = pos + 8
                    self.data_size = size
                else:
                    payload = f.read(size)
                    if cid == b"fmt ":
                        self.fmt = payload
                        (self.format_tag, self.channels, self.sample_rate, _,
                         self.block_align, self.bits) = struct.unpack("<HHIIHH", payload[:16])
                        if self.format_tag == 0xFFFE and len(payload) >= 26:
                            self.format_tag = struct.unpack("<H", payload[24:26])[0]
                    elif cid == b"bext" and len(payload) >= 346:
                        lo, hi = struct.unpack("<II", payload[338:346])
                        self.time_ref = (hi << 32) | lo
                    elif cid == b"iXML":
                        self.ixml = payload.decode("utf-8", "replace")
                    if cid not in SKIP_CHUNKS:
                        self.chunks.append((cid, payload))
                pos += 8 + size + (size & 1)
        if self.fmt is None or self.data_offset == 0 or self.block_align == 0:
            raise ValueError("missing fmt or data chunk")
        self.data_size -= self.data_size % self.block_align

    def ixml_value(self, tag):
        m = re.search(r"<%s>\s*([^<]*?)\s*</%s>" % (tag, tag), self.ixml)
        return m.group(1) if m else ""

    @property
    def sample_format(self):
        return "float" if self.format_tag == 3 else "int"

    @property
    def tc_rate(self):
        """(numerator, denominator, drop_frame) from iXML, or None."""
        m = re.match(r"(\d+)\s*/\s*(\d+)", self.ixml_value("TIMECODE_RATE"))
        if not m or int(m.group(2)) == 0 or int(m.group(1)) == 0:
            return None
        return (int(m.group(1)), int(m.group(2)),
                self.ixml_value("TIMECODE_FLAG").upper() == "DF")

    @property
    def tc_rate_label(self):
        r = self.tc_rate
        if not r:
            return ""
        fps = r[0] / float(r[1])
        label = ("%.3f" % fps).rstrip("0").rstrip(".")
        return label + (" DF" if r[2] else "")


def tc(samples, rate):
    s = samples // rate
    return "%02d:%02d:%02d" % ((s // 3600) % 24, (s // 60) % 60, s % 60)


def tc_frames(samples, w):
    """Timecode with frames when the file's iXML gives a rate, else HH:MM:SS."""
    r = w.tc_rate
    if not r:
        return tc(samples, w.sample_rate)
    num, den, df = r
    nominal = int(round(num / float(den)))
    # +1 sample tolerates frame starts that recorders round down at 29.97/59.94.
    n = (samples + 1) * num // (w.sample_rate * den)
    sep = ":"
    if df and nominal in (30, 60):
        drop = nominal // 15
        per_min = nominal * 60 - drop
        per_10 = nominal * 600 - drop * 9
        d, m = divmod(n, per_10)
        n += drop * 9 * d + (drop * ((m - drop) // per_min) if m > drop else 0)
        sep = ";"
    ff = n % nominal
    secs = n // nominal
    return "%02d:%02d:%02d%s%02d" % ((secs // 3600) % 24, (secs // 60) % 60, secs % 60, sep, ff)


def hms(samples, rate):
    ms = samples * 1000 // rate
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d.%03d" % (s // 3600, (s // 60) % 60, s % 60, ms)


def duration(samples, rate):
    s = samples // rate
    if s >= 3600:
        return "%d:%02d:%02d long" % (s // 3600, (s // 60) % 60, s % 60)
    return "%d:%02d long" % (s // 60, s % 60)


def find_takes(files):
    """Group files into chains of exactly adjacent files with identical format."""
    by_start = {}
    for w in files:
        by_start.setdefault((w.fmt, w.time_ref), []).append(w)

    def successors(w):
        return by_start.get((w.fmt, w.end_ref), [])

    has_parent = set()
    for w in files:
        nxt = successors(w)
        if len(nxt) == 1:
            has_parent.add(nxt[0].path)

    takes, warnings = [], []
    for w in sorted(files, key=lambda x: (x.time_ref, x.path)):
        if w.path in has_parent:
            continue
        chain = [w]
        seen = {w.path}
        while True:
            nxt = successors(chain[-1])
            if len(nxt) != 1:
                if len(nxt) > 1:
                    warnings.append("Skipped ambiguous join after %s (%d possible next files)"
                                    % (os.path.basename(chain[-1].path), len(nxt)))
                break
            if nxt[0].path in seen:
                break
            chain.append(nxt[0])
            seen.add(nxt[0].path)
        if len(chain) > 1:
            takes.append(chain)
    return takes, warnings


def write_joined(chain, out_path):
    first = chain[0]
    total_data = sum(w.data_size for w in chain)
    total_samples = total_data // first.block_align

    meta = b""
    for cid, payload in first.chunks:
        if cid == b"fact" and len(payload) >= 4:
            payload = struct.pack("<I", min(total_samples, 0xFFFFFFFF)) + payload[4:]
        if len(payload) & 1:
            # Pad inside the chunk so readers that ignore RIFF pad bytes still parse it.
            payload += b"\0"
        meta += cid + struct.pack("<I", len(payload)) + payload

    # 28-byte JUNK placeholder, turned into ds64 if the file goes past 4 GB.
    header_len = 12 + 8 + 28 + len(meta) + 8
    pad = total_data & 1
    riff_size = header_len - 8 + total_data + pad
    big = riff_size > RIFF_LIMIT or total_data > RIFF_LIMIT

    tmp = out_path + ".partial"
    try:
        with open(tmp, "wb") as out:
            if big:
                out.write(b"RF64" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE")
                out.write(b"ds64" + struct.pack("<I", 28)
                          + struct.pack("<QQQI", riff_size, total_data, total_samples, 0))
            else:
                out.write(b"RIFF" + struct.pack("<I", riff_size) + b"WAVE")
                out.write(b"JUNK" + struct.pack("<I", 28) + b"\0" * 28)
            out.write(meta)
            out.write(b"data" + struct.pack("<I", 0xFFFFFFFF if big else total_data))
            for w in chain:
                with open(w.path, "rb") as src:
                    src.seek(w.data_offset)
                    remaining = w.data_size
                    while remaining:
                        block = src.read(min(COPY_BLOCK, remaining))
                        if not block:
                            raise IOError("unexpected end of audio in " + w.path)
                        out.write(block)
                        remaining -= len(block)
            if pad:
                out.write(b"\0")
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return big


def collect_dirs(paths):
    """Map folder -> list of wav paths, walking dropped folders recursively."""
    dirs = {}
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, subdirs, names in os.walk(p):
                subdirs[:] = [d for d in subdirs if not d.startswith(".") and d != SOURCE_DIR]
                for n in names:
                    if is_candidate(n):
                        dirs.setdefault(root, []).append(os.path.join(root, n))
        elif (os.path.isfile(p) and is_candidate(os.path.basename(p))
              and os.path.basename(os.path.dirname(p)) != SOURCE_DIR):
            dirs.setdefault(os.path.dirname(p), []).append(p)
    return dirs


def is_candidate(name):
    stem, ext = os.path.splitext(name)
    return (ext.lower() == ".wav" and not name.startswith(".")
            and not stem.endswith(SUFFIX))


def move_to_sources(path):
    """Move a source file into merged_source_files/ beside it; returns new path."""
    dest_dir = os.path.join(os.path.dirname(path), SOURCE_DIR)
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    stem, ext = os.path.splitext(os.path.basename(path))
    dest = os.path.join(dest_dir, stem + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, "%s_%d%s" % (stem, n, ext))
        n += 1
    shutil.move(path, dest)
    return dest


CSV_FIELDS = [
    "status", "output_file", "output_start_tc", "output_end_tc", "output_duration",
    "output_samples", "output_container", "segment", "source_file", "source_start_tc",
    "source_end_tc", "source_duration", "source_samples", "channels", "sample_rate",
    "bit_depth", "sample_format", "tc_rate", "scene", "take", "source_moved_to", "notes",
]


def file_fields(w):
    return {
        "source_file": os.path.basename(w.path),
        "source_start_tc": tc_frames(w.time_ref, w) if w.time_ref is not None else "",
        "source_end_tc": tc_frames(w.end_ref, w) if w.time_ref is not None else "",
        "source_duration": hms(w.samples, w.sample_rate),
        "source_samples": w.samples,
        "channels": w.channels,
        "sample_rate": w.sample_rate,
        "bit_depth": w.bits,
        "sample_format": w.sample_format,
        "tc_rate": w.tc_rate_label,
        "scene": w.ixml_value("SCENE"),
        "take": w.ixml_value("TAKE"),
    }


def process_folder(folder, paths, move_sources=True):
    """Join split takes in one folder. Returns (rows, joined_count, no_tc_count)."""
    rows, made, no_tc = [], 0, 0
    files = []
    for path in sorted(paths):
        try:
            w = WavInfo(path)
        except Exception as e:
            rows.append({"status": "unreadable", "source_file": os.path.basename(path),
                         "notes": str(e)})
            continue
        if w.time_ref is None:
            no_tc += 1
            continue
        files.append(w)

    by_name = dict((os.path.basename(w.path), w) for w in files)
    takes, warnings = find_takes(files)
    for msg in warnings:
        row = {"status": "skipped", "notes": msg}
        m = re.search(r"after (.+) \(", msg)
        if m and m.group(1) in by_name:
            row.update(file_fields(by_name[m.group(1)]))
        rows.append(row)

    for chain in takes:
        first = chain[0]
        stem = os.path.splitext(os.path.basename(first.path))[0]
        out_path = os.path.join(folder, stem + SUFFIX + ".wav")
        total = sum(w.samples for w in chain)
        base = {
            "output_file": os.path.basename(out_path),
            "output_start_tc": tc_frames(first.time_ref, first),
            "output_end_tc": tc_frames(first.time_ref + total, first),
            "output_duration": hms(total, first.sample_rate),
            "output_samples": total,
        }
        status, notes, big = "joined", "", False
        if os.path.exists(out_path):
            status, notes = "skipped", "output file already exists"
        else:
            try:
                big = write_joined(chain, out_path)
                made += 1
            except Exception as e:
                status, notes = "failed", str(e)
        base["output_container"] = ("RF64" if big else "WAV") if status == "joined" else ""
        for i, w in enumerate(chain):
            row = dict(base)
            row.update(file_fields(w))
            row.update({"status": status, "segment": "%d of %d" % (i + 1, len(chain)),
                        "notes": notes})
            if status == "joined" and move_sources:
                try:
                    row["source_moved_to"] = os.path.relpath(move_to_sources(w.path), folder)
                except Exception as e:
                    row["notes"] = "joined, but could not move source: %s" % e
            if status != "joined":
                row["output_file"] = row["output_file"] if status == "skipped" else ""
            rows.append(row)
    return rows, made, no_tc


def write_report(folder, rows, stamp):
    path = os.path.join(folder, REPORT_PREFIX + stamp + ".csv")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s%s_%d.csv" % (REPORT_PREFIX, stamp, n))
        n += 1
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict((k, row.get(k, "")) for k in CSV_FIELDS))
    return path


def main(argv):
    move_sources = True
    if "--no-move" in argv:
        argv = [a for a in argv if a != "--no-move"]
        move_sources = False
    if not argv:
        print(__doc__)
        return 1
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dirs = collect_dirs(argv)
    total_made, total_no_tc, lines, reports = 0, 0, [], []
    for folder in sorted(dirs):
        rows, made, no_tc = process_folder(folder, dirs[folder], move_sources)
        total_made += made
        total_no_tc += no_tc
        seen = set()
        for r in rows:
            if r.get("status") == "joined":
                if r["output_file"] not in seen:
                    seen.add(r["output_file"])
                    lines.append("Joined %s (%s, starts %s)" % (
                        r["output_file"], r["segment"].split(" of ")[1] + " files",
                        r["output_start_tc"]))
            else:
                lines.append("%s %s: %s" % (r.get("status", "").capitalize(),
                                            r.get("source_file") or r.get("output_file", ""),
                                            r.get("notes", "")))
        if rows:
            reports.append(write_report(folder, rows, stamp))

    summary = "%d joined file%s created." % (total_made, "" if total_made == 1 else "s")
    if total_made == 0 and not lines:
        summary = "No split takes found (no files with matching format and adjacent timecode)."
    if total_no_tc:
        summary += " %d file(s) had no BWF timecode and were ignored." % total_no_tc
    if total_made and move_sources:
        summary += " Source files moved to '%s'." % SOURCE_DIR
    print(summary)
    for line in lines:
        print("- " + line)
    for r in reports:
        print("REPORT: " + r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
