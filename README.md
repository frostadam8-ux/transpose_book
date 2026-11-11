# Children's Songbook → B♭ / E♭ Jazz Lead Sheets

Turns the PDF of the *Children's Songbook* of The Church of Jesus Christ of Latter-day Saints into lead sheets: melody plus chord symbols, transposed for B♭ (trumpet, clarinet, tenor sax) and/or E♭ (alto/bari sax, horn) or kept at concert pitch, and formatted in a jazz (Real Book) style.

What you get for each key:

- a **PDF** of every song
- an editable **MuseScore file** (`.mscz`) of every song, for reharmonizing or fixing mistakes
- the same lead sheets as **MusicXML**
- an optional **master book PDF** with a clickable table of contents and a bookmark for each song

Everything is automated with one Python script, `scan_and_transpose.py`.

## How it works

```
songbook PDF ──split──▶ one PDF per song ──Audiveris (OMR)──▶ MusicXML (concert, piano score)
        ──lead sheet + transpose──▶ MusicXML ──MuseScore──▶ .mscz (jazz style) ──▶ PDF ──▶ master book
```

1. **Split** the full book into one PDF per song, using `index.csv`.
2. **Read the music** with [Audiveris](https://github.com/Audiveris/audiveris), an open-source optical music recognition (OMR) program, which turns each PDF into MusicXML.
3. **Build a lead sheet.** The script keeps the top staff's melody voice and the highest note of any right-hand chord, keeps every chord symbol, and drops lyrics, the left hand and other parts.
4. **Transpose** notes, key signatures and chord symbols. This includes chord symbols that the OMR read as plain text, such as `Gm7/D`.
5. **Export** with the MuseScore command line, apply the jazz style, then render the PDFs.
6. **Combine** the PDFs into a master book, if you ask for it.

The script only uses the Python standard library, apart from `pypdf` and `reportlab` for splitting and building the book.

## Requirements

| Tool | Purpose | Notes |
|---|---|---|
| Python 3.9+ | runs the script | |
| [Audiveris](https://github.com/Audiveris/audiveris/releases) 5.x | reads the music in the PDFs | free, open source |
| [MuseScore](https://musescore.org) 4 (3.6 also works) | `.mscz` + PDF export | free |
| `pypdf`, `reportlab` | splitting the book, master book | `pip install pypdf reportlab` |

### macOS: "Audiveris can't be opened"

Audiveris isn't notarized by Apple, so Gatekeeper blocks it the first time. Download it only from the official GitHub releases page, then either:

- go to **System Settings → Privacy & Security** and click **Open Anyway**, or
- remove the quarantine flag (recommended, because the script launches Audiveris in the background):

  ```bash
  xattr -dr com.apple.quarantine /Applications/Audiveris.app
  ```

Check that it runs:

```bash
/Applications/Audiveris.app/Contents/MacOS/Audiveris -help
```

The script looks for Audiveris and MuseScore in their usual install locations on macOS, Windows and Linux. If it can't find them, pass `--audiveris PATH` or `--mscore PATH`.

## Quick start

1. Download the complete *Children's Songbook* PDF from the Church's music website (see [Copyright](#copyright-and-what-not-to-commit)) and put it next to the script.
2. Run:

```bash
python scan_and_transpose.py \
    --book complete_childrens_songbook_eng.pdf \
    --index index.csv \
    --out Songbook \
    --key Bb Eb \
    --jobs 4 \
    --make-book
```

Audiveris is the slow step: very roughly 30 seconds to 2 minutes per song. If the run is interrupted, run the same command again. Songs that have already been read are skipped.

## Output

```
Songbook/
├── Bb_PDF/                      one PDF per song (B♭)
├── Bb_MuseScore/                one .mscz per song (edit / reharmonize here)
├── Bb_MusicXML/
├── Eb_PDF/  Eb_MuseScore/  Eb_MusicXML/
├── Concert_PDF/  Concert_MuseScore/  Concert_MusicXML/   (with --key C)
├── Concert_FullScore_MusicXML/  untouched OMR result: concert pitch, full piano score
├── Children's Songbook - Bb.pdf master book (with --make-book)
├── Children's Songbook - Eb.pdf
├── report.csv                   per-song stats and flags for proofreading
├── _song_pdfs/                  the split song PDFs
└── _audiveris/                  OMR working files and logs
```

Files are named `NNN Title`, where `NNN` is the song's page in the PDF, so they sort in songbook order.

## Options

| Option | Meaning |
|---|---|
| `--book FILE --index CSV` | whole songbook PDF plus page index (see below) |
| `--pdfs DIR` | use a folder that already has one PDF per song |
| `--out DIR` | output folder (default `Songbook`) |
| `--key Bb Eb C` | target key(s). `Bb` = up a major 2nd (default); `Eb` = up a major 6th (standard alto-sax octave); `Eb-down` = down a minor 3rd (same notes an octave lower, also in the `Eb_` folders); `C` = concert pitch (original key, same melody-plus-chords lead sheet and styling). Every key comes from a single OMR pass. |
| `--jobs N` | number of Audiveris runs at once (default 2). Each needs about 1–2 GB of RAM; the CPU is usually the limit. |
| `--style jazz\|standard` | `jazz` (default): Petaluma notation, MuseJazz Text, jazz chord symbols. `standard`: MuseScore's default look. |
| `--skip-omr` | reuse existing Audiveris output and only redo the transposition and export (for example, to add another key) |
| `--skip-render` | stop after writing MusicXML |
| `--restyle` | re-apply `--style` to every existing `.mscz` for the keys given in `--key` and export the PDFs again. It never changes the key. |
| `--make-book` | build the master book(s). It can run on its own and re-exports any `.mscz` you've edited first. |
| `--book-title "..."` | title on the master book's contents page |
| `--audiveris PATH`, `--mscore PATH` | executable locations |

## `index.csv`

Columns: `number,title,first_page,last_page` (1-based PDF page numbers).

The included index matches the 2019 digital edition, `complete_childrens_songbook_eng.pdf`, which has 268 pages with one song per page. It lists **264 songs**. Four pages are left out because they only contain a notice that the Church can't publish that song in this format for licensing reasons: *When Grandpa Comes* (p. 173), *God Is Watching Over All* (p. 196), *Springtime Is Coming* (p. 205) and *Be Happy!* (p. 232).

Pages 256–268 are the instrumental preludes at the end of the book. They usually have no chord symbols. Delete those rows if you don't want them.

If you use a different edition of the PDF, make a matching index.

## Proofreading workflow

OMR is good but not perfect. Expect to fix things.

1. Open `report.csv`. Check these flags first:
   - **`NO CHORDS FOUND`**: Audiveris missed the chord symbols.
   - **`check rhythm`**: the script had to fill gaps in the melody with rests, which usually means a misread note or rhythm.
   - A high `multi_voice_measures` count means the melody line may have been picked incorrectly in places.
2. Open the song's `.mscz`, play it back, and fix it. The common problems are repeats, D.S./Coda markings, ties, triplets and pickup measures.
3. Run `python scan_and_transpose.py --make-book --out Songbook --key Bb Eb`. Edited songs get new PDFs automatically, and the master book is rebuilt.

Playback note: the lead sheets are written in the instrument's key (written pitch), so MuseScore plays them back a step or so off from concert pitch. That's deliberate. Marking the parts as transposing instruments makes MuseScore's MusicXML import shift chord symbols incorrectly.

## Limitations

- The melody is taken from the top staff and the highest voice. Songs where the melody is in the left hand or in an inner voice need manual fixing.
- Chord symbols are only as good as the OMR reads them. Unusual chord spellings may come out as plain text, which still gets transposed, or may be missed.
- Lyrics are removed on purpose.
- The jazz style setting names are based on MuseScore 4's style file. If a future MuseScore version renames them, the style may not apply. The music itself is unaffected.

## Copyright and what not to commit

The *Children's Songbook* is © by Intellectual Reserve, Inc., and many individual songs are also under copyright held by their composers or publishers. The Church allows the music to be copied for incidental, noncommercial church and home use. **This repository contains only code and a page index.** Don't commit the songbook PDF or any generated output (PDFs, `.mscz`, MusicXML). See the included `.gitignore`. Anyone using the script should download the songbook themselves from the Church's music site.

## License

