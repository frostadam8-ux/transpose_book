#!/usr/bin/env python3
"""
Children's Songbook -> B-flat (and/or E-flat) lead sheets (melody + chord symbols).

Pipeline
  1. (optional) split the whole songbook PDF into one PDF per song
  2. Audiveris OMR: PDF -> MusicXML (concert pitch, full score)
  3. Build a lead sheet: top staff of the first part, top melody voice,
     highest note of any chord, all chord symbols kept, lyrics removed
  4. Transpose notes, key signatures AND chord symbols
       --key Bb     up a major 2nd (default)
       --key Eb-down  down a minor 3rd
       --key Eb       up a major 6th (alto-sax octave)
       --key C        concert pitch (original key, same lead sheet + styling)
       --key Bb Eb C  several at once, from a single OMR pass
  5. MuseScore CLI: export a PDF and an .mscz for every song

Output folders (inside --out):
  Bb_PDF/   (and/or Eb_PDF/)     PDF lead sheet for each song
  Bb_MuseScore/ Eb_MuseScore/   .mscz for each song (reharmonize here)
  Bb_MusicXML/  Eb_MusicXML/    the same lead sheet as MusicXML
  Concert_PDF/ Concert_MuseScore/ Concert_MusicXML/   with --key C
  Concert_FullScore_MusicXML/   raw Audiveris result, concert pitch, piano score
  _audiveris/                   Audiveris working files (.omr books, logs)
  report.csv                    per-song stats to help you prioritize proofreading

Requirements: Python 3.8+, Audiveris 5.x, MuseScore 4 (3.6 also works).
pypdf is needed only for --book splitting (pip install pypdf).

Examples
  # one PDF per song already in ./song_pdfs
  python bb_trumpet_songbook.py --pdfs song_pdfs --out Songbook_Bb

  # whole book PDF + index CSV (columns: number,title,first_page,last_page)
  python bb_trumpet_songbook.py --book ChildrensSongbook.pdf --index index.csv --out Songbook_Bb

  # both B-flat and E-flat folders
  python bb_trumpet_songbook.py --book ChildrensSongbook.pdf --index index.csv --out Songbook_Bb --key Bb Eb

  # after OMR is done once, re-run just the transposition + export
  python bb_trumpet_songbook.py --pdfs song_pdfs --out Songbook_Bb --skip-omr
"""

import argparse
import copy
import csv
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

# ----------------------------------------------------------------------------
# Locating the programs
# ----------------------------------------------------------------------------

AUDIVERIS_GUESSES = {
    "Windows": [r"C:\Program Files\Audiveris\Audiveris.exe",
                r"C:\Program Files\Audiveris\bin\Audiveris.bat"],
    "Darwin": ["/Applications/Audiveris.app/Contents/MacOS/Audiveris"],
    "Linux": ["/opt/audiveris/bin/Audiveris", "/usr/bin/audiveris"],
}
MSCORE_GUESSES = {
    "Windows": [r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
                r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe"],
    "Darwin": ["/Applications/MuseScore 4.app/Contents/MacOS/mscore",
               "/Applications/MuseScore 3.app/Contents/MacOS/mscore"],
    "Linux": ["/usr/bin/mscore4portable", "/usr/bin/mscore", "/usr/bin/musescore"],
}


def find_exe(user_value, names, guesses):
    if user_value:
        return user_value
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in guesses.get(platform.system(), []):
        if os.path.exists(p):
            return p
    return None


# ----------------------------------------------------------------------------
# Step 1: split the book (optional)
# ----------------------------------------------------------------------------

def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*]', "", s).strip()
    return re.sub(r"\s+", " ", s)


def split_book(book_pdf, index_csv, out_dir):
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        sys.exit("Splitting needs pypdf:  pip install pypdf")
    os.makedirs(out_dir, exist_ok=True)
    reader = PdfReader(book_pdf)
    with open(index_csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        num = int(r["number"]) if r.get("number") else 0
        first, last = int(r["first_page"]), int(r["last_page"])
        name = f"{num:03d} {safe_name(r['title'])}.pdf"
        w = PdfWriter()
        for p in range(first - 1, last):  # CSV pages are 1-based PDF page numbers
            w.add_page(reader.pages[p])
        with open(os.path.join(out_dir, name), "wb") as fo:
            w.write(fo)
    print(f"Split {len(rows)} songs into {out_dir}")


# ----------------------------------------------------------------------------
# Step 2: Audiveris
# ----------------------------------------------------------------------------

def find_omr_outputs(work_dir, stem):
    hits = []
    for ext in ("mxl", "xml", "musicxml"):
        for pat in (glob.escape(stem) + "." + ext, glob.escape(stem) + ".mvt*." + ext):
            hits += glob.glob(os.path.join(work_dir, "**", pat), recursive=True)
    return sorted(set(hits))


def run_audiveris(audiveris, pdf, work_dir):
    stem = os.path.splitext(os.path.basename(pdf))[0]
    existing = find_omr_outputs(work_dir, stem)
    if existing:
        return stem, existing, "cached"
    cmd = [audiveris, "-batch", "-export", "-output", work_dir, "--", pdf]
    log = os.path.join(work_dir, stem + ".log")
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
    out = find_omr_outputs(work_dir, stem)
    return stem, out, "ok" if out else f"FAILED (see {log})"


def read_musicxml(path):
    """Return an ElementTree root from .xml/.musicxml or compressed .mxl."""
    if path.lower().endswith(".mxl"):
        with zipfile.ZipFile(path) as z:
            rootfile = None
            if "META-INF/container.xml" in z.namelist():
                c = ET.fromstring(z.read("META-INF/container.xml"))
                for el in c.iter():
                    if el.tag.endswith("rootfile") and el.get("full-path"):
                        rootfile = el.get("full-path")
                        break
            if rootfile is None:
                rootfile = next(n for n in z.namelist()
                                if n.endswith(".xml") and not n.startswith("META-INF"))
            return ET.fromstring(z.read(rootfile))
    return ET.parse(path).getroot()


def write_musicxml(root, path):
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ") if hasattr(ET, "indent") else None
    with open(path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n')
        f.write(b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 '
                b'Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)


# ----------------------------------------------------------------------------
# Step 3 & 4: lead sheet + transposition
# ----------------------------------------------------------------------------

STEPS = "CDEFGAB"
STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Target keys: (letter steps, semitones, part name)
#   Bb     : up a major 2nd                      (trumpet, clarinet, tenor sax*)
#   Eb-down     : down a minor 3rd                    (same note names as Eb parts)
#   Eb  : up a major 6th (standard alto sax / Eb horn written octave)
#   *tenor sax is conventionally written an octave higher still; note names match
KEYS = {
    "Bb": (1, 2, "Trumpet in B♭"),
    "Eb-down": (-2, -3, "E♭ Instrument"),
    "Eb": (5, 9, "E♭ Instrument"),
    "C": (0, 0, "Concert"),          # original key, no transposition
}


def folder_tag(key):
    """Output folder prefix: Bb_*, Eb_* (Eb and Eb-down share), Concert_*."""
    return {"C": "Concert"}.get(key, key.replace("-down", ""))
DIATONIC, CHROMATIC = 1, 2   # set per key by set_key()
PART_NAME = "Trumpet in B♭"
KEY_FIFTHS_SHIFT = 2

LINE_OF_FIFTHS = {"F": -1, "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5}


def set_key(key):
    global DIATONIC, CHROMATIC, PART_NAME, KEY_FIFTHS_SHIFT
    DIATONIC, CHROMATIC, PART_NAME = KEYS[key]
    s, a, _ = transpose_spelled("C", 0)  # where C lands tells us the key-signature shift
    KEY_FIFTHS_SHIFT = LINE_OF_FIFTHS[s] + 7 * int(a)


def transpose_spelled(step, alter, octave=None):
    """Transpose a spelled pitch by DIATONIC letters / CHROMATIC semitones."""
    idx = STEPS.index(step) + DIATONIC
    new_step = STEPS[idx % 7]
    oct_carry = idx // 7
    old_abs = STEP_PC[step] + alter
    new_alter = (old_abs + CHROMATIC) - (STEP_PC[new_step] + 12 * oct_carry)
    new_oct = None if octave is None else octave + oct_carry
    return new_step, new_alter, new_oct


def fnum(el, default=0.0):
    if el is None or el.text is None:
        return default
    try:
        return float(el.text)
    except ValueError:
        return default


def set_alter(parent, tag, alter, before_tag):
    """Write/remove an <alter>-style child, keeping schema order."""
    el = parent.find(tag)
    if alter == 0:
        if el is not None:
            parent.remove(el)
        return
    if el is None:
        el = ET.Element(tag)
        kids = list(parent)
        pos = next((i for i, k in enumerate(kids) if k.tag == before_tag), len(kids))
        parent.insert(pos, el)
    el.text = str(int(alter)) if float(alter).is_integer() else str(alter)


def transpose_pitch_el(pitch):
    st = pitch.find("step")
    al = fnum(pitch.find("alter"))
    oc = int(pitch.find("octave").text)
    ns, na, no = transpose_spelled(st.text.strip(), al, oc)
    st.text = ns
    set_alter(pitch, "alter", na, "octave")
    pitch.find("octave").text = str(no)


def transpose_harmony_el(h):
    for prefix in ("root", "bass"):
        node = h.find(prefix)
        if node is None:
            continue
        st = node.find(f"{prefix}-step")
        if st is None or not st.text:
            continue
        al = fnum(node.find(f"{prefix}-alter"))
        ns, na, _ = transpose_spelled(st.text.strip(), al)
        st.text = ns
        st.attrib.pop("text", None)  # stale display text would show the old root
        set_alter(node, f"{prefix}-alter", na, "__none__")


# Chord symbols that OMR read as plain text (e.g. <words>Gm7/D</words>)
CHORD_RE = re.compile(
    r"^\s*([A-G])([#b♯♭]?)"
    r"((?:maj|min|dim|aug|sus|add|alt|m|M|Δ|ø|°|o|\+|-|[0-9]|[#b♯♭]|\(|\)|,)*)"
    r"(?:/([A-G])([#b♯♭]?))?\s*$")
ACC = {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}


def spell(step, alter, unicode_style):
    if alter == 0:
        return step
    sharp, flat = ("♯", "♭") if unicode_style else ("#", "b")
    return step + (sharp * int(alter) if alter > 0 else flat * int(-alter))


def transpose_chord_text(text):
    m = CHORD_RE.match(text)
    if not m:
        return None
    uni = any(c in text for c in "♯♭")
    r_st, r_acc, quality, b_st, b_acc = m.groups()
    ns, na, _ = transpose_spelled(r_st, ACC[r_acc])
    out = spell(ns, na, uni) + quality
    if b_st:
        bs, ba, _ = transpose_spelled(b_st, ACC[b_acc or ""])
        out += "/" + spell(bs, ba, uni)
    return out


def midi_of(note):
    p = note.find("pitch")
    if p is None:
        return None
    return (STEP_PC[p.find("step").text.strip()] + fnum(p.find("alter"))
            + 12 * (int(p.find("octave").text) + 1))


def staff_of(el):
    s = el.find("staff")
    return s.text.strip() if s is not None and s.text else "1"


def voice_of(el):
    v = el.find("voice")
    return v.text.strip() if v is not None and v.text else "1"


DUR_TYPES = [(4, "whole", 0), (3, "half", 1), (2, "half", 0), (1.5, "quarter", 1),
             (1, "quarter", 0), (0.75, "eighth", 1), (0.5, "eighth", 0),
             (0.375, "16th", 1), (0.25, "16th", 0), (0.125, "32nd", 0)]


def make_rest(duration, divisions, whole_measure=False):
    n = ET.Element("note")
    r = ET.SubElement(n, "rest")
    if whole_measure:
        r.set("measure", "yes")
    ET.SubElement(n, "duration").text = str(int(duration))
    ET.SubElement(n, "voice").text = "1"
    if not whole_measure and divisions:
        q = duration / divisions
        for ql, typ, dots in DUR_TYPES:
            if abs(q - ql) < 1e-6:
                ET.SubElement(n, "type").text = typ
                for _ in range(dots):
                    ET.SubElement(n, "dot")
                break
    return n


def set_offset(el, value):
    """Place a harmony/direction <offset> (divisions) in schema position."""
    old = el.find("offset")
    if old is not None:
        el.remove(old)
    if value == 0:
        return
    off = ET.Element("offset")
    off.text = str(int(value)) if float(value).is_integer() else str(value)
    # offset goes after root/kind/degree (harmony) or direction-type (direction),
    # and before staff/sound/voice etc.
    kids = list(el)
    stop = {"staff", "sound", "voice", "listening", "frame", "footnote", "level"}
    pos = next((i for i, k in enumerate(kids) if k.tag in stop), len(kids))
    el.insert(pos, off)


def strip_note(n):
    """Clean a melody note for a single-staff lead sheet."""
    for tag in ("lyric", "staff", "beam", "stem", "accidental", "chord"):
        for c in n.findall(tag):
            n.remove(c)
    v = n.find("voice")
    if v is not None:
        v.text = "1"
    p = n.find("pitch")
    if p is not None:
        transpose_pitch_el(p)
    for x in ("default-x", "default-y", "relative-x", "relative-y"):
        n.attrib.pop(x, None)


def rebuild_measure(measure, divisions, stats):
    """Turn a (possibly 2-staff, multi-voice) measure into a single melody line."""
    pos = 0.0
    max_pos = 0.0
    last_start = 0.0
    notes = []   # (el, start, dur, staff, voice, is_chord, is_grace)
    others = []  # (el, offset, rank)
    for child in list(measure):
        tag = child.tag
        if tag == "note":
            is_chord = child.find("chord") is not None
            is_grace = child.find("grace") is not None
            dur = fnum(child.find("duration"))
            start = last_start if is_chord else pos
            if not is_chord and not is_grace:
                pos += dur
            last_start = start
            notes.append((child, start, dur, staff_of(child), voice_of(child),
                          is_chord, is_grace))
        elif tag == "backup":
            pos -= fnum(child.find("duration"))
        elif tag == "forward":
            pos += fnum(child.find("duration"))
        elif tag in ("harmony", "direction", "figured-bass"):
            off = pos + fnum(child.find("offset"))
            if tag == "direction" and staff_of(child) != "1":
                continue  # drop left-hand dynamics/pedal marks
            others.append((child, off, 2))
        elif tag == "attributes":
            if child.find("divisions") is not None:
                divisions = fnum(child.find("divisions"), divisions)
            others.append((child, pos, 1))
        elif tag == "barline":
            loc = child.get("location", "right")
            others.append((child, 0 if loc == "left" else float("inf"), 0 if loc == "left" else 9))
        elif tag in ("print",):
            others.append((child, 0, -1))
        else:  # sound, bookmark, link, grouping...
            others.append((child, pos, 2))
        max_pos = max(max_pos, pos)

    # --- choose the melody voice in staff 1: highest average pitch ---
    by_voice = {}
    for el, start, dur, st, vo, ch, gr in notes:
        if st != "1":
            continue
        m = midi_of(el)
        by_voice.setdefault(vo, [])
        if m is not None:
            by_voice[vo].append(m)
    if not by_voice:
        melody_voice = None
    else:
        sounding = {v: sum(p) / len(p) for v, p in by_voice.items() if p}
        if sounding:
            melody_voice = max(sounding, key=lambda v: (sounding[v], v == "1"))
        else:
            melody_voice = "1" if "1" in by_voice else sorted(by_voice)[0]
        if len(sounding) > 1:
            stats["multi_voice_measures"] += 1

    # --- collect melody events, keep the highest note of each chord ---
    groups = []  # list of lists of note tuples sharing a start (chord groups)
    for t in notes:
        el, start, dur, st, vo, ch, gr = t
        if st != "1" or vo != melody_voice:
            continue
        if ch and groups:
            groups[-1].append(t)
        else:
            groups.append([t])
    melody = []
    for g in groups:
        best = max(g, key=lambda t: (midi_of(t[0]) if midi_of(t[0]) is not None else -1))
        if len(g) > 1:
            stats["chords_reduced"] += 1
        el = best[0]
        if best is not g[0]:
            # the kept note was a <chord/> note: it needs the head note's duration/type
            el = copy.deepcopy(el)
        strip_note(el)
        melody.append((el, best[1], g[0][2], best[6]))

    # --- fill timeline gaps with rests ---
    timeline = []
    cur = 0.0
    for el, start, dur, gr in melody:
        if start > cur + 1e-9:
            timeline.append((make_rest(start - cur, divisions), cur, start - cur))
            stats["gap_rests"] += 1
        timeline.append((el, start, 0 if gr else dur))
        if not gr:
            cur = max(cur, start + dur)
    if max_pos > cur + 1e-9:
        whole = not timeline
        timeline.append((make_rest(max_pos - cur, divisions, whole), cur, max_pos - cur))
        if not whole:
            stats["gap_rests"] += 1

    # --- rebuild children in time order ---
    for c in list(measure):
        measure.remove(c)
    others.sort(key=lambda t: (t[1], t[2]))
    pending = list(others)

    def emit_upto(limit, at, inclusive=False):
        nonlocal pending
        keep = []
        for el, off, rank in pending:
            hit = off <= limit if inclusive else off < limit
            if hit and off != float("inf"):
                if el.tag in ("harmony", "direction"):
                    set_offset(el, max(0.0, off - at))
                    for s in el.findall("staff"):
                        el.remove(s)
                measure.append(el)
            else:
                keep.append((el, off, rank))
        pending = keep

    for el, start, dur in timeline:
        emit_upto(start + (dur if dur > 0 else 1e-9), start)
        measure.append(el)
    emit_upto(float("inf"), max(max_pos, cur), inclusive=True)
    for el, off, rank in pending:  # right barlines
        measure.append(el)
    return divisions


def clean_attributes(attr, stats):
    for s in attr.findall("staves"):
        attr.remove(s)
    for tag in ("clef", "staff-details", "transpose"):
        for c in attr.findall(tag):
            n = c.get("number")
            if tag == "transpose" or (n is not None and n != "1"):
                attr.remove(c)
            else:
                c.attrib.pop("number", None)
    for c in attr.findall("clef"):  # melody always in treble
        for k in list(c):
            c.remove(k)
        ET.SubElement(c, "sign").text = "G"
        ET.SubElement(c, "line").text = "2"
    keys = attr.findall("key")
    for i, k in enumerate(keys):
        if i > 0 or (k.get("number") not in (None, "1")):
            attr.remove(k)
            continue
        k.attrib.pop("number", None)
        f = k.find("fifths")
        if f is not None:
            nf = int(f.text) + KEY_FIFTHS_SHIFT
            if abs(nf) > 7:
                stats["warnings"].append(f"key with {nf} sharps")
            f.text = str(nf)
    times = attr.findall("time")
    for i, t in enumerate(times):
        if i > 0:
            attr.remove(t)
        else:
            t.attrib.pop("number", None)


def make_lead_sheet(root, title):
    stats = {"measures": 0, "harmony": 0, "chord_words": 0, "gap_rests": 0,
             "chords_reduced": 0, "multi_voice_measures": 0, "parts_dropped": 0,
             "warnings": []}

    # titles: replace scanned page text with a clean title
    for c in root.findall("credit"):
        root.remove(c)
    for tag in ("work", "movement-number", "movement-title"):
        for c in root.findall(tag):
            root.remove(c)
    kids = list(root)
    insert_at = 0
    work = ET.Element("work")
    ET.SubElement(work, "work-title").text = title
    mt = ET.Element("movement-title")
    mt.text = title
    root.insert(insert_at, work)
    root.insert(insert_at + 1, mt)

    # keep only the first part
    parts = root.findall("part")
    plist = root.find("part-list")
    first_id = parts[0].get("id")
    for p in parts[1:]:
        root.remove(p)
        stats["parts_dropped"] += 1
    for sp in list(plist):
        if sp.tag == "score-part" and sp.get("id") != first_id:
            plist.remove(sp)
        elif sp.tag == "part-group":
            plist.remove(sp)
    sp = plist.find("score-part")
    pn = sp.find("part-name")
    if pn is None:
        pn = ET.SubElement(sp, "part-name")
    pn.text = PART_NAME
    pn.set("print-object", "no")  # don't print the instrument name on the first line
    for tag in ("part-abbreviation", "part-name-display", "part-abbreviation-display"):
        for c in sp.findall(tag):
            sp.remove(c)
    for mi in sp.iter("midi-program"):
        mi.text = {"Trumpet in B♭": "57", "E♭ Instrument": "66"}.get(PART_NAME, "57")

    # hunt for chord symbols in all parts before dropping them (OMR sometimes
    # attaches chord names to the lower staff or a separate part) — they are
    # already carried by part 1 in nearly all cases, so we just count them
    part = parts[0]
    divisions = 1.0
    for m in part.findall("measure"):
        stats["measures"] += 1
        for k in ("width",):
            m.attrib.pop(k, None)
        divisions = rebuild_measure(m, divisions, stats)
        for a in m.findall("attributes"):
            clean_attributes(a, stats)
        for h in m.findall("harmony"):
            transpose_harmony_el(h)
            stats["harmony"] += 1
        for d in m.findall("direction"):
            for w in d.iter("words"):
                if w.text:
                    t = transpose_chord_text(w.text)
                    if t is not None:
                        w.text = t
                        stats["chord_words"] += 1
        for pr in m.findall("print"):
            for c in list(pr):
                if c.tag in ("staff-layout",):
                    pr.remove(c)
    return stats


# ----------------------------------------------------------------------------
# Step 5: MuseScore export
# ----------------------------------------------------------------------------

JAZZ_TEXT_FONT = "MuseJazz Text"


def _set_style_tag(style_xml, tag, value):
    """Set <tag>value</tag> inside a MuseScore <Style> block (insert if missing)."""
    pat = re.compile(rf"<{tag}>[^<]*</{tag}>|<{tag}/>")
    new = f"<{tag}>{value}</{tag}>"
    if pat.search(style_xml):
        return pat.sub(new, style_xml)
    return style_xml.replace("</Style>", f"  {new}\n    </Style>", 1)


def patch_style(style_xml, style, ms4):
    """Apply the look to a MuseScore style block (works on .mss and on .mscx)."""
    s = _set_style_tag(style_xml, "hideInstrumentNameIfOneInstrument", "1")
    if style != "jazz":
        return s
    if ms4:
        s = _set_style_tag(s, "musicalSymbolFont", "Petaluma")
        s = _set_style_tag(s, "musicalTextFont", "Petaluma Text")
    else:
        s = _set_style_tag(s, "musicalSymbolFont", "MuseJazz")
        s = _set_style_tag(s, "musicalTextFont", "MuseJazz Text")
    s = _set_style_tag(s, "chordStyle", "jazz")
    s = _set_style_tag(s, "chordsXmlFile", "0")
    s = _set_style_tag(s, "chordDescriptionFile", "chords_jazz.xml")
    # every text style (title, chord symbols, tempo, measure numbers, ...) in the jazz font
    faces = set(re.findall(r"<(\w+FontFace)>", s)) or {
        "defaultFontFace", "titleFontFace", "subTitleFontFace", "composerFontFace",
        "lyricistFontFace", "chordSymbolAFontFace", "chordSymbolBFontFace",
        "tempoFontFace", "metronomeFontFace", "measureNumberFontFace",
        "staffTextFontFace", "systemTextFontFace", "rehearsalMarkFontFace",
        "repeatLeftFontFace", "repeatRightFontFace", "voltaFontFace",
        "expressionFontFace", "headerFontFace", "footerFontFace"}
    for f in faces:
        s = _set_style_tag(s, f, JAZZ_TEXT_FONT)
    return s


def restyle_mscz(path, style):
    """Rewrite the style stored inside a .mscz (MuseScore 4 or 3)."""
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        ms4 = any(n.endswith("score_style.mss") for n in names)
        for info in zin.infolist():
            data = zin.read(info.filename)
            n = info.filename
            if n.endswith(".mss") or (not ms4 and n.endswith(".mscx")):
                txt = data.decode("utf-8")
                if "<Style>" in txt:
                    txt = patch_style(txt, style, ms4)
                elif not ms4 and "<Score>" in txt:  # MS3 score without a Style block
                    txt = txt.replace("<Score>", "<Score>\n    <Style>\n    </Style>", 1)
                    txt = patch_style(txt, style, ms4)
                data = txt.encode("utf-8")
            zout.writestr(info, data)
    os.replace(tmp, path)


def run_musescore(mscore, jobs, job_file):
    with open(job_file, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=1)
    print(f"MuseScore: exporting {len(jobs)} songs ...")
    r = subprocess.run([mscore, "-j", job_file], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        print("Batch export reported an error; retrying songs one at a time.")
        for j in jobs:
            for out in j["out"]:
                if not os.path.exists(out):
                    subprocess.run([mscore, "-o", out, j["in"]],
                                   capture_output=True, text=True)


# ----------------------------------------------------------------------------
# Master book: one PDF with a clickable table of contents and bookmarks
# ----------------------------------------------------------------------------

def refresh_pdfs_from_mscz(mscore, mscz_dir, pdf_dir, work_dir):
    """Re-export any song whose .mscz is newer than its PDF (i.e. you fixed it)."""
    jobs = []
    for m in sorted(glob.glob(os.path.join(mscz_dir, "*.mscz"))):
        pdf = os.path.join(pdf_dir, os.path.splitext(os.path.basename(m))[0] + ".pdf")
        if not os.path.exists(pdf) or os.path.getmtime(m) > os.path.getmtime(pdf):
            jobs.append({"in": m, "out": [pdf]})
    if jobs:
        print(f"  re-exporting {len(jobs)} edited songs from MuseScore files")
        run_musescore(mscore, jobs, os.path.join(work_dir, "musescore_refresh.json"))


def build_master_book(pdf_dir, out_pdf, book_title):
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.annotations import Link
    except ImportError:
        sys.exit("The master book needs pypdf:  pip install pypdf")
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
    except ImportError:
        canvas = None
        print("  (reportlab not installed: making bookmarks only, no contents page. "
              "pip install reportlab for a printed table of contents)")

    songs = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    if not songs:
        print(f"  no PDFs in {pdf_dir}")
        return
    readers = [PdfReader(p) for p in songs]
    names = [os.path.splitext(os.path.basename(p))[0] for p in songs]

    # --- table of contents layout (two columns) ---
    W, H = letter if canvas else (612, 792)
    top, bottom, line = H - 100, 50, 13.5
    rows_per_col = int((top - bottom) // line)
    per_page = rows_per_col * 2
    toc_pages = -(-len(songs) // per_page) if canvas else 0

    start_pages, p = [], toc_pages
    for r in readers:
        start_pages.append(p)
        p += len(r.pages)

    writer = PdfWriter()
    link_rects = []  # (toc_page_index, rect, target_page_index)
    if canvas:
        import io
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        col_w = (W - 100) / 2
        for i, (name, sp) in enumerate(zip(names, start_pages)):
            pg, k = divmod(i, per_page)
            col, row = divmod(k, rows_per_col)
            if k == 0:
                if i:
                    c.showPage()
                c.setFont("Helvetica-Bold", 18 if pg == 0 else 12)
                c.drawCentredString(W / 2, H - 60, book_title if pg == 0
                                    else f"{book_title} (contents, continued)")
            x = 50 + col * col_w
            y = top - row * line
            m = re.match(r"^(\d+)\s*(.*)$", name)
            num, title = (m.group(1).lstrip("0"), m.group(2)) if m else ("", name)
            c.setFont("Helvetica", 9.5)
            c.drawRightString(x + 22, y, num)
            label = title if len(title) <= 42 else title[:40] + "…"
            c.drawString(x + 28, y, label)
            c.drawRightString(x + col_w - 12, y, str(sp + 1))
            link_rects.append((pg, (x, y - 3, x + col_w - 10, y + 10), sp))
        c.save()
        buf.seek(0)
        for page in PdfReader(buf).pages:
            writer.add_page(page)

    for r in readers:
        for page in r.pages:
            writer.add_page(page)

    for pg, rect, target in link_rects:
        writer.add_annotation(page_number=pg,
                              annotation=Link(rect=rect, target_page_index=target))
    if toc_pages:
        writer.add_outline_item("Contents", 0)
    for name, sp in zip(names, start_pages):
        writer.add_outline_item(name, sp)
    writer.page_mode = "/UseOutlines"  # open with the bookmark sidebar showing
    with open(out_pdf, "wb") as f:
        writer.write(f)
    print(f"  {out_pdf}: {len(songs)} songs, {len(writer.pages)} pages")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def song_title(stem):
    return re.sub(r"^\d+\s*[-_.]?\s*", "", stem).strip() or stem


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--pdfs", help="folder with one PDF per song")
    src.add_argument("--book", help="whole songbook PDF (needs --index)")
    ap.add_argument("--index", help="CSV: number,title,first_page,last_page")
    ap.add_argument("--out", default="Songbook", help="output folder")
    ap.add_argument("--audiveris", help="path to Audiveris executable")
    ap.add_argument("--mscore", help="path to MuseScore executable")
    ap.add_argument("--jobs", type=int, default=2,
                    help="Audiveris runs in parallel (each uses ~1-2 GB RAM)")
    ap.add_argument("--skip-omr", action="store_true",
                    help="reuse existing Audiveris output")
    ap.add_argument("--skip-render", action="store_true",
                    help="stop after writing MusicXML (no MuseScore)")
    ap.add_argument("--key", nargs="+", default=["Bb"], choices=list(KEYS),
                    help="target key(s): Bb (up M2), Eb-down (down m3), Eb (up M6), C (concert, original key). "
                         "Several at once, e.g. --key Bb Eb")
    ap.add_argument("--style", choices=["jazz", "standard"], default="jazz",
                    help="jazz = Petaluma notes, MuseJazz text, jazz chord symbols "
                         "(default); standard = MuseScore's normal look")
    ap.add_argument("--restyle", action="store_true",
                    help="re-apply --style to every existing .mscz for the --key folders and "
                         "re-export the PDFs (never changes the key); use to switch looks on songs already converted")
    ap.add_argument("--make-book", action="store_true",
                    help="combine the song PDFs into one master book per key "
                         "(re-exports any .mscz you edited first). Can run on its own.")
    ap.add_argument("--book-title", default="Children's Songbook",
                    help="title on the contents page")
    a = ap.parse_args()

    out = os.path.abspath(a.out)

    def make_books():
        ms = find_exe(a.mscore, ["mscore4portable", "mscore", "MuseScore4", "musescore"],
                      MSCORE_GUESSES)
        for key in a.key:
            tag = folder_tag(key)
            pdf_dir = os.path.join(out, f"{tag}_PDF")
            mscz_dir = os.path.join(out, f"{tag}_MuseScore")
            print(f"Master book ({tag}):")
            if ms and os.path.isdir(mscz_dir):
                refresh_pdfs_from_mscz(ms, mscz_dir, pdf_dir,
                                       os.path.join(out, "_audiveris"))
            label = {"Bb": "B-flat", "Eb": "E-flat", "Concert": "Concert Pitch"}[tag]
            build_master_book(pdf_dir, os.path.join(out, f"{a.book_title} - {tag}.pdf"),
                              f"{a.book_title} ({label})")

    if a.restyle:
        ms = find_exe(a.mscore, ["mscore4portable", "mscore", "MuseScore4", "musescore"],
                      MSCORE_GUESSES)
        if not ms:
            sys.exit("MuseScore not found; pass --mscore PATH")
        for key in a.key:
            tag = folder_tag(key)
            mdir, pdir = os.path.join(out, f"{tag}_MuseScore"), os.path.join(out, f"{tag}_PDF")
            files = sorted(glob.glob(os.path.join(mdir, "*.mscz")))
            for m in files:
                restyle_mscz(m, a.style)
            jobs = [{"in": m, "out": [os.path.join(pdir, os.path.splitext(
                os.path.basename(m))[0] + ".pdf")]} for m in files]
            os.makedirs(pdir, exist_ok=True)
            if jobs:
                run_musescore(ms, jobs, os.path.join(out, f"restyle_{tag}.json"))
            print(f"{tag}: restyled {len(files)} songs")
        if a.make_book:
            make_books()
        return

    if not (a.pdfs or a.book):
        if a.make_book:
            make_books()
            return
        ap.error("one of --pdfs or --book is required (or use --make-book on its own)")
    d = {k: os.path.join(out, v) for k, v in {
        "full": "Concert_FullScore_MusicXML", "work": "_audiveris",
        "split": "_song_pdfs"}.items()}
    for key in a.key:
        tag = folder_tag(key)  # Eb and Eb-down both go in Eb_* folders
        for kind, suffix in (("pdf", "PDF"), ("mscz", "MuseScore"), ("xml", "MusicXML")):
            d[(key, kind)] = os.path.join(out, f"{tag}_{suffix}")
    if "Eb" in a.key and "Eb-down" in a.key:
        sys.exit("Choose either Eb or Eb-down, not both (they share the Eb_ folders)")
    for p in d.values():
        os.makedirs(p, exist_ok=True)

    # 1. source PDFs
    if a.book:
        if not a.index:
            sys.exit("--book needs --index")
        split_book(a.book, a.index, d["split"])
        pdf_dir = d["split"]
    else:
        pdf_dir = a.pdfs
    pdfs = sorted(glob.glob(os.path.join(pdf_dir, "*.pdf")))
    if not pdfs:
        sys.exit(f"No PDFs found in {pdf_dir}")

    # 2. OMR
    omr = {}
    if a.skip_omr:
        for p in pdfs:
            stem = os.path.splitext(os.path.basename(p))[0]
            omr[stem] = find_omr_outputs(d["work"], stem)
    else:
        aud = find_exe(a.audiveris, ["Audiveris", "audiveris"], AUDIVERIS_GUESSES)
        if not aud:
            sys.exit("Audiveris not found; pass --audiveris PATH")
        print(f"Audiveris: {len(pdfs)} songs, {a.jobs} at a time (this is the slow part)")
        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = [ex.submit(run_audiveris, aud, p, d["work"]) for p in pdfs]
            for i, f in enumerate(as_completed(futs), 1):
                stem, files, status = f.result()
                omr[stem] = files
                print(f"  [{i}/{len(pdfs)}] {stem}: {status}")

    # 3-4. lead sheets
    report, jobs = [], []
    for stem in sorted(omr):
        files = omr[stem]
        if not files:
            report.append({"song": stem, "status": "OMR failed"})
            continue
        for k, f in enumerate(files):
            name = stem if len(files) == 1 else f"{stem} (part {k + 1})"
            try:
                root = read_musicxml(f)
                write_musicxml(root, os.path.join(d["full"], name + ".musicxml"))
            except Exception as e:
                report.append({"song": name, "status": f"error reading OMR: {e}"})
                print(f"  ! {name}: {e}")
                continue
            for key in a.key:
                try:
                    set_key(key)
                    lead = copy.deepcopy(root)
                    st = make_lead_sheet(lead, song_title(name))
                    xml_out = os.path.join(d[(key, "xml")], name + ".musicxml")
                    write_musicxml(lead, xml_out)
                    jobs.append({"in": xml_out,
                                 "out": [os.path.join(d[(key, "pdf")], name + ".pdf"),
                                         os.path.join(d[(key, "mscz")], name + ".mscz")]})
                    flags = list(st.pop("warnings"))
                    if st["harmony"] + st["chord_words"] == 0:
                        flags.append("NO CHORDS FOUND")
                    if st["gap_rests"]:
                        flags.append("check rhythm")
                    report.append({"song": name, "key": key, "status": "ok", **st,
                                   "flags": "; ".join(flags)})
                except Exception as e:  # keep going; one bad scan shouldn't stop the batch
                    report.append({"song": name, "key": key, "status": f"error: {e}"})
                    print(f"  ! {name} ({key}): {e}")

    cols = ["song", "key", "status", "measures", "harmony", "chord_words", "chords_reduced",
            "gap_rests", "multi_voice_measures", "parts_dropped", "flags"]
    with open(os.path.join(out, "report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(report)

    # 5. render
    if not a.skip_render and jobs:
        ms = find_exe(a.mscore, ["mscore4portable", "mscore", "MuseScore4", "musescore"],
                      MSCORE_GUESSES)
        if not ms:
            sys.exit("MuseScore not found; pass --mscore PATH (MusicXML is already written)")
        # pass 1: MusicXML -> .mscz
        mscz_jobs = [{"in": j["in"], "out": [j["out"][1]]} for j in jobs]
        run_musescore(ms, mscz_jobs, os.path.join(d["work"], "musescore_jobs.json"))
        # restyle the .mscz files (jazz fonts, no instrument name)
        n = 0
        for j in mscz_jobs:
            if os.path.exists(j["out"][0]):
                try:
                    restyle_mscz(j["out"][0], a.style)
                    n += 1
                except Exception as e:
                    print(f"  ! could not restyle {j['out'][0]}: {e}")
        print(f"Applied '{a.style}' style to {n} MuseScore files")
        # pass 2: .mscz -> PDF, so the PDF matches the styled file
        pdf_jobs = [{"in": j["out"][1], "out": [j["out"][0]]} for j in jobs
                    if os.path.exists(j["out"][1])]
        run_musescore(ms, pdf_jobs, os.path.join(d["work"], "musescore_pdf_jobs.json"))

    if a.make_book and not a.skip_render:
        make_books()

    ok = sum(1 for r in report if r["status"] == "ok")
    flagged = sum(1 for r in report if r.get("flags"))
    print(f"\nDone: {ok}/{len(report)} songs converted, {flagged} flagged in report.csv")
    print(f"Output: {out}")


if __name__ == "__main__":
    main()