"""Breath of the Wild prompt tool (games/botw/scripts/botw_formats.py, botw_prompts.py) on small made-up archives: no game data."""
import random
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'games/botw/scripts'))
sys.path.insert(0, str(ROOT/'games/totk/scripts'))
try:
    import botw_formats as bf
    import botw_prompts as bp
except (SystemExit, ImportError):     # make_romfs_patches needs Python 3.14 (compression.zstd)
    bf = bp = None
try:
    import make_button_icons as mbi         # the PlayStation drawings; it needs fontTools and exits without it
except (SystemExit, ImportError):
    mbi = None
import prompt_layout as pl      # noqa: E402

NAMES = ['Nt_KeyTexA_00^d', 'Nt_KeyTexB_00^d', 'Nt_KeyTexX_00^d', 'Nt_KeyTexY_00^d', 'Nt_KeyTexL_00^d', 'Nt_KeyTexZL_00^d',
         'Nt_KeyTexLSt_00^d', 'Nt_KeyTexLSt_01^d']


def yaz0_encode(data):
    """A plain greedy Yaz0 encoder (back references up to 4096 bytes), enough to test the splice against references."""
    out, i, n, seen = bytearray(b'Yaz0' + struct.pack('>I', len(data)) + bytes(8)), 0, len(data), {}
    while i < n:
        code, body = 0, bytearray()
        for bit in range(8):
            if i >= n:
                break
            best, dist = 0, 0
            for j in reversed(seen.get(data[i:i + 3], [])[-16:]):
                if i - j > 4096:
                    break
                length = 3
                while i + length < n and length < 0x111 and data[j + length] == data[i + length]:
                    length += 1
                if length > best:
                    best, dist = length, i - j
            step = 1
            if best >= 3:
                d = dist - 1
                if best < 18:
                    body += bytes([(best - 2) << 4 | d >> 8, d & 255])
                else:
                    body += bytes([d >> 8, d & 255, best - 18])
                step = best
            else:
                code |= 0x80 >> bit; body.append(data[i])
            for k in range(i, i + step):
                seen.setdefault(data[k:k + 3], []).append(k)
            i += step
        out.append(code); out += body
    return bytes(out)


def sample(size, seed=1):
    rng = random.Random(seed)
    words = [bytes(rng.randrange(256) for _ in range(rng.randrange(3, 40))) for _ in range(30)]
    out = bytearray()
    while len(out) < size:
        out += rng.choice(words) if rng.random() < 0.8 else bytes([rng.randrange(256)])
    return bytes(out[:size])


def sarc(members):
    """A little-endian SARC (the layout make_romfs_patches.sarc_members reads), members 8-byte aligned."""
    names, table, data = bytearray(), bytearray(), bytearray()
    for name, blob in members.items():
        while len(data) % 8:
            data += b'\0'
        table += struct.pack('<4I', 0, 0x01000000 | len(names) // 4, len(data), len(data) + len(blob))
        names += name.encode() + b'\0'
        while len(names) % 4:
            names += b'\0'
        data += blob
    head = b'SFAT' + struct.pack('<HHI', 12, len(members), 0x65) + bytes(table) + b'SFNT' + struct.pack('<HH', 8, 0) + bytes(names)
    start = 0x14 + len(head); start += -start % 8
    out = bytearray(b'SARC' + struct.pack('<H', 0x14) + b'\xff\xfe' + struct.pack('<IIHH', 0, start, 0x100, 0) + head)
    out += b'\0' * (start - len(out)) + data
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def bntx(textures, fmt=bf.R8_UNORM if bf else 0x201, size=48):
    """A BNTX holding one BRTI per name, each a size x size R8 block-linear texture (block height 8, 4096 bytes), as in the game."""
    head = bytearray(b'BNTX' + bytes(0x1fc)); names = bytearray(); brti = bytearray(); pointers = bytearray(); data = bytearray()
    base = len(head)
    for i, (name, rows) in enumerate(textures.items()):
        raw = name.encode()
        name_at = base + len(names); names += struct.pack('<H', len(raw)) + raw + b'\0' * (-(2 + len(raw)) % 8)
        entry = bytearray(0xa0)
        entry[:4] = b'BRTI'
        struct.pack_into('<HHHIIIIIIII', entry, 0x12, 0, 0, 1, 1, fmt, 0, size, size, 1, 1, 3)
        struct.pack_into('<I', entry, 0x50, 4096)
        struct.pack_into('<Q', entry, 0x60, name_at)
        brti += entry; pointers += struct.pack('<Q', 0)
        image = bytearray(4096)
        for y, row in enumerate(rows):
            for x, v in enumerate(row):
                image[bf.r8_address(x, y, size, 8)] = v
        data += image
    brti_at = base + len(names); pointers_at = brti_at + len(brti); data_at = pointers_at + len(pointers)
    out = bytearray(head + names + brti + pointers + data)
    for i in range(len(textures)):
        o = brti_at + i * 0xa0
        struct.pack_into('<Q', out, o + 0x70, pointers_at + i * 8)
        struct.pack_into('<Q', out, pointers_at + i * 8, data_at + i * 4096)
    return bytes(out)


def picture(seed):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(48)) for _ in range(48)]


def key_picture(letter_box=(20, 16, 28, 30)):
    """A white key (a block of 255s with an empty margin) with a dark letter-like mark inside, and a long dark outline."""
    rows = [bytearray(48) for _ in range(48)]
    for y in range(6, 42):
        for x in range(6, 42):
            rows[y][x] = 255
    for x in range(6, 42):          # the outline: a long dark line, longer than any letter
        rows[44][x] = 0
    x0, y0, x1, y1 = letter_box
    for y in range(y0, y1):
        for x in range(x0, x1):
            rows[y][x] = 0
    return [bytes(r) for r in rows]


def disc_picture(cx, cy, radius, size=48):
    """A black picture with a white disc of the given centre and radius and a dark mark inside, like the game's face buttons."""
    rows = [bytearray(size) for _ in range(size)]
    for y in range(size):
        for x in range(size):
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= radius ** 2:
                rows[y][x] = 255
    for y in range(int(cy) - 4, int(cy) + 5):
        for x in range(int(cx) - 3, int(cx) + 4):
            rows[y][x] = 0
    return [bytes(r) for r in rows]


def same_disc(test, new, old, tolerance=2):
    """The white disc of `new` has the box of the disc of `old` within `tolerance` pixels."""
    for a, b in zip(bp.disc_of(new), bp.disc_of(old)):
        test.assertAlmostEqual(a, b, delta=tolerance)


class StubLetters:
    def text(self, label, cap, cx, cy, max_width):    # a square of side `cap`, so the new "letter" shows where it was put
        h = cap / 2
        return [[(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]]


@unittest.skipIf(bf is None, 'needs Python 3.14')
class Yaz0Splice(unittest.TestCase):
    def setUp(self):
        self.data = sample(60000)
        self.stream = yaz0_encode(self.data)

    def test_encoder_round_trips_and_uses_references(self):
        self.assertEqual(bf.yaz0_decode(self.stream), self.data)
        self.assertLess(len(self.stream), len(self.data))

    def test_splice_decodes_to_the_edit_and_keeps_both_ends_of_the_stream(self):
        edit = bytes(range(200))
        result = bf.yaz0_splice(self.stream, self.data, [(30000, edit)])
        expected = self.data[:30000] + edit + self.data[30200:]
        self.assertEqual(bf.yaz0_decode(result), expected)
        self.assertEqual(result[:600], self.stream[:600])         # before the edit: the original stream
        self.assertEqual(result[-600:], self.stream[-600:])       # well after it: the original stream again
        self.assertLess(len(result), len(self.stream) + 12000)    # only the stretch around the edit (about 8 KB) became literals

    def test_two_edits_far_apart_and_two_near_each_other(self):
        edits = [(5000, b'A' * 10), (5100, b'B' * 10), (50000, b'C' * 300)]
        expected = bytearray(self.data)
        for o, b in edits:
            expected[o:o + len(b)] = b
        self.assertEqual(bf.yaz0_decode(bf.yaz0_splice(self.stream, self.data, edits)), bytes(expected))

    def test_edits_a_little_further_apart_than_the_reach(self):
        for gap in (bf.REACH + 1, bf.REACH + 50, bf.REACH + 400, bf.REACH + 1500):
            edits = [(20000, b'P' * 4), (20000 + gap, b'Q' * 4)]
            expected = bytearray(self.data)
            for o, b in edits:
                expected[o:o + len(b)] = b
            with self.subTest(gap=gap):
                self.assertEqual(bf.yaz0_decode(bf.yaz0_splice(self.stream, self.data, edits)), bytes(expected))

    def test_edit_at_the_very_start_and_the_very_end(self):
        for offset in (0, len(self.data) - 5):
            result = bf.yaz0_splice(self.stream, self.data, [(offset, b'\x99' * 5)])
            self.assertEqual(bf.yaz0_decode(result)[offset:offset + 5], b'\x99' * 5)

    def test_a_wrong_decoded_copy_is_caught(self):      # negative control: the final decode check must see a mismatch
        wrong = bytearray(self.data); wrong[100] ^= 1
        with self.assertRaises(ValueError):
            bf.yaz0_splice(self.stream, bytes(wrong), [(30000, b'x')])


@unittest.skipIf(bf is None, 'needs Python 3.14')
class Textures(unittest.TestCase):
    def test_swizzle_is_a_permutation_of_the_image(self):
        addresses = {bf.r8_address(x, y, 48, 8) for x in range(48) for y in range(48)}
        self.assertEqual(len(addresses), 48 * 48)
        self.assertLess(max(addresses), 4096)

    def test_read_write_round_trip(self):
        pics = {n: picture(i) for i, n in enumerate(NAMES)}
        blob = bntx(pics)
        textures = bf.bntx_textures(blob)
        self.assertEqual(list(textures), NAMES)
        for name in NAMES:
            self.assertEqual(bf.r8_read(blob, textures[name]), pics[name])
        new = picture(99)
        image = bf.r8_image(blob, textures[NAMES[2]], new)
        self.assertEqual(len(image), 4096)
        patched = bytearray(blob); patched[textures[NAMES[2]].offset:textures[NAMES[2]].offset + 4096] = image
        self.assertEqual(bf.r8_read(bytes(patched), textures[NAMES[2]]), new)
        self.assertEqual(bf.r8_read(bytes(patched), textures[NAMES[0]]), pics[NAMES[0]])    # the others did not move

    def test_other_formats_and_sizes_are_refused(self):
        blob = bntx({NAMES[0]: picture(1)}, fmt=0x1d01)           # BC1, not R8
        with self.assertRaises(ValueError):
            bf.r8_read(blob, bf.bntx_textures(blob)[NAMES[0]])
        good = bntx({NAMES[0]: picture(1)}); texture = bf.bntx_textures(good)[NAMES[0]]
        with self.assertRaises(ValueError):
            bf.r8_image(good, texture, picture(1)[:47])          # not 48 rows
        with self.assertRaises(ValueError):
            bf.bntx_textures(b'NOPE' + bytes(100))

    def test_a_letter_is_found_and_an_outline_is_not(self):
        marks = bf.enclosed_marks(key_picture())
        self.assertEqual(len(marks), 1)
        self.assertEqual(min(p[0] for p in marks[0]), 20)

    def test_coverage_fills_a_shape_and_a_reversed_contour_cuts_a_hole(self):
        square = [(10, 10), (30, 10), (30, 30), (10, 30)]
        hole = [(15, 15), (15, 25), (25, 25), (25, 15)]          # wound the other way
        full = bf.coverage([square], 48)
        self.assertEqual((full[20][20], full[5][5], full[20][9]), (255, 0, 0))
        cut = bf.coverage([square, hole], 48)
        self.assertEqual((cut[20][20], cut[12][12]), (0, 255))

    def test_curves_are_flattened(self):
        ops = [('moveTo', ((0, 0),)), ('qCurveTo', ((10, 10), (20, 0))), ('lineTo', ((0, 0),)), ('closePath', ())]
        contour = bf.flatten(ops)[0]
        self.assertGreater(len(contour), 10)
        self.assertEqual(contour[12], (20.0, 0.0))


def bc4_bntx(width, height, block_height, rows):
    """A BNTX with one BC4 block-linear texture holding `rows` (a list of bytearray rows), as inside a BFFNT's glyph sheet."""
    blocks_w, blocks_h = width // 4, height // 4
    gobs = -(-blocks_h // (8 * block_height)) * block_height * (width * 2 // 64)
    image = bytearray(gobs * 512)
    for by in range(blocks_h):
        for bx in range(blocks_w):
            at = bf.gob_address(bx, by, blocks_w, 8, block_height)
            image[at:at + 8] = bf.bc4_encode([rows[by * 4 + i // 4][bx * 4 + i % 4] for i in range(16)])
    head = bytearray(b'BNTX' + bytes(0x1fc)); name = struct.pack('<H', 1) + b'x' + bytes(5)
    entry = bytearray(0xa0); entry[:4] = b'BRTI'
    struct.pack_into('<HHHIIIIIIII', entry, 0x12, 0, 0, 1, 1, bf.BC4_UNORM, 0, width, height, 1, 1, block_height.bit_length() - 1)
    struct.pack_into('<I', entry, 0x50, len(image))
    name_at, brti_at = len(head), len(head) + len(name)
    pointers_at = brti_at + len(entry); data_at = pointers_at + 8
    struct.pack_into('<Q', entry, 0x60, name_at); struct.pack_into('<Q', entry, 0x70, pointers_at)
    return bytes(head + name + entry + struct.pack('<Q', data_at) + image)


def bffnt(rows, columns=5, grid_rows=4, cell=50, block_height=1, codes=(0xE040, 0xE041, 0xE042, 0xE043)):
    """A BFFNT: header, TGLP with the sheet (a BNTX) and a CMAP table starting at 0xE040 (glyph i = code 0xE040 + i)."""
    sheet = bc4_bntx(256, grid_rows * 51 + 4, block_height, rows)
    tglp_at = 0x14
    sheet_at = 0x1000
    tglp = b'TGLP' + struct.pack('<I', sheet_at + len(sheet) - tglp_at) + struct.pack('<BBBBIHHHHHHI', cell, cell, 1, cell, len(sheet), 36, 12, columns, grid_rows, 256, grid_rows * 51 + 4, sheet_at)
    table = struct.pack('<%dH' % len(codes), *range(len(codes)))
    cmap = b'CMAP' + struct.pack('<I', 24 + len(table)) + struct.pack('<IIHHI', codes[0], codes[-1], 1, 0, 0) + table
    out = bytearray(b'FFNT' + b'\xff\xfe' + struct.pack('<H', 0x14) + bytes(4) + bytes(4) + struct.pack('<H', 2) + bytes(2))
    body = bytearray(out) + bytearray(tglp.ljust(sheet_at - len(out), b'\0')) + bytearray(sheet)
    cmap_at = len(body); body += cmap
    # the sections are found by walking sizes: TGLP's size reaches the CMAP
    struct.pack_into('<I', body, tglp_at + 4, cmap_at - tglp_at)
    return bytes(body)


@unittest.skipIf(bf is None, 'needs Python 3.14')
class FontGlyphs(unittest.TestCase):
    def sheet(self):
        rng = random.Random(3)
        rows = [bytearray(rng.choice((0, 255, 128)) for _ in range(256)) for _ in range(4 * 51 + 4)]
        return rows

    def test_bc4_block_round_trip_of_flat_and_two_level_blocks(self):
        for values in ([0] * 16, [255] * 16, [0, 255] * 8, [7] * 16):
            self.assertEqual(bf.bc4_decode(bf.bc4_encode(values)), values)

    def test_the_sheet_reads_back_and_a_cell_is_upright(self):
        rows = self.sheet()
        data = bffnt(rows)
        font = bf.Bffnt(data)
        self.assertEqual((font.cell, font.columns, font.rows), (50, 5, 4))
        self.assertEqual(sorted(font.cells), [0xE040, 0xE041, 0xE042, 0xE043])
        decoded = bf.bc4_read(data[font.sheet_at:], font.texture)
        x0, y0, x1, y1 = font.cell_box(0xE042)
        self.assertEqual(font.get(decoded, 0xE042), [bytes(r[x0:x1]) for r in reversed(decoded[y0:y1])])
        self.assertEqual(font.cell_box(0xE040)[1], (4 - 1) * 51)            # glyph 0 is the bottom row of the sheet

    def test_only_the_blocks_under_the_changed_cell_are_written_again(self):
        data = bffnt(self.sheet())
        font = bf.Bffnt(data)
        sheet = data[font.sheet_at:]
        rows = bf.bc4_read(sheet, font.texture)
        box = font.put(rows, 0xE041, [bytes([200]) * 50] * 50)
        image = bf.bc4_image(sheet, font.texture, rows, [box])
        old = sheet[font.texture.offset:font.texture.offset + font.texture.image_size]
        changed = [i for i in range(len(image)) if image[i] != old[i]]
        self.assertTrue(changed)
        self.assertLess(len(changed), len(image) // 3)                       # one cell of twenty: most blocks are untouched
        after = bf.bc4_read(sheet[:font.texture.offset] + image + sheet[font.texture.offset + len(image):], font.texture)
        self.assertEqual(font.get(after, 0xE041)[10][10], 200)
        self.assertEqual(font.get(after, 0xE042), font.get(rows, 0xE042))    # the neighbour cell is as it was

    def test_a_cell_of_the_wrong_size_and_a_wrong_format_are_refused(self):    # negative controls
        data = bffnt(self.sheet())
        font = bf.Bffnt(data)
        with self.assertRaises(ValueError):
            font.put(bf.bc4_read(data[font.sheet_at:], font.texture), 0xE040, [bytes(50)] * 49)
        r8 = bntx({NAMES[0]: picture(1)}); texture = bf.bntx_textures(r8)[NAMES[0]]
        with self.assertRaises(ValueError):
            bf.bc4_read(r8, texture)
        with self.assertRaises(ValueError):
            bf.Bffnt(b'NOPE' + bytes(100))

    def test_make_font_trades_the_face_glyphs_and_keeps_the_rest_of_the_archive(self):
        rows = self.sheet()
        data = bffnt(rows)
        archive = sarc({'External_00.bffnt': data, 'Other.bffnt': sample(2000, 9)})
        stream = yaz0_encode(archive)
        new_stream, cells, _ = bp.make_font(stream, pl.prompt_controls('', 'position'), StubLetters())
        self.assertEqual(sorted(cells), [0xE040, 0xE041, 0xE042, 0xE043])    # the synthetic font has no other glyphs
        new_archive = bf.yaz0_decode(new_stream)
        self.assertEqual(len(new_archive), len(archive))
        before, after = bf.sarc_members(archive), bf.sarc_members(new_archive)
        self.assertEqual(archive[slice(*before['Other.bffnt'])], new_archive[slice(*after['Other.bffnt'])])
        font = bf.Bffnt(archive[slice(*before['External_00.bffnt'])])
        new_data = new_archive[slice(*after['External_00.bffnt'])]
        old_rows = bf.bc4_read(archive[slice(*before['External_00.bffnt'])][font.sheet_at:], font.texture)
        new_rows = bf.bc4_read(new_data[font.sheet_at:], font.texture)
        # Switch A (E040) now shows the cell of Xbox B (E041) and the other way round, up to BC4's own rounding.
        for a, b in ((0xE040, 0xE041), (0xE041, 0xE040)):
            want, got = font.get(old_rows, b), font.get(new_rows, a)
            self.assertLess(max(abs(x - y) for rw, rg in zip(want, got) for x, y in zip(rw, rg)), 130)
            self.assertNotEqual(font.get(old_rows, a), got)

    def test_unknown_style_is_refused(self):      # negative control
        data = bffnt(self.sheet())
        with self.assertRaises(ValueError):
            bp.make_font(yaz0_encode(sarc({'External_00.bffnt': data})), pl.prompt_controls('', 'position'), StubLetters(), 'nintendo')

    @unittest.skipIf(mbi is None, 'needs fontTools')
    def test_playstation_font_redraws_the_face_glyphs_on_discs_like_the_originals(self):
        rows = [bytearray(256) for _ in range(4 * 51 + 4)]
        font = bf.Bffnt(bffnt(rows))
        for i, code in enumerate(sorted(font.cells)):          # a disc of a different size and place in each cell
            x0, y0, x1, y1 = font.cell_box(code)
            disc = disc_picture(25 + i, 25, 14 + i, 50)
            for y in range(50):
                rows[y0 + y][x0:x1] = disc[y]
        data = bffnt(rows)
        archive = sarc({'External_00.bffnt': data})
        controls = pl.prompt_controls('', 'position')
        _, xbox, _ = bp.make_font(yaz0_encode(archive), controls, StubLetters())
        new_stream, cells, _ = bp.make_font(yaz0_encode(archive), controls, StubLetters(), 'playstation')
        self.assertEqual(sorted(cells), [0xE040, 0xE041, 0xE042, 0xE043])
        font = bf.Bffnt(data)
        old_rows = bf.bc4_read(data[font.sheet_at:], font.texture)
        new_archive = bf.yaz0_decode(new_stream)
        new_data = new_archive[slice(*bf.sarc_members(new_archive)['External_00.bffnt'])]
        new_rows = bf.bc4_read(new_data[font.sheet_at:], font.texture)
        for code in cells:
            self.assertNotEqual(font.get(new_rows, code), font.get(old_rows, code))
            self.assertNotEqual(cells[code], xbox[code])
            same_disc(self, font.get(new_rows, code), font.get(old_rows, code))


@unittest.skipIf(bf is None, 'needs Python 3.14')
class SystemFont(unittest.TestCase):
    def test_bfttf_decodes_to_the_font_inside(self):
        font = b'\x00\x01\x00\x00' + bytes(range(30))       # a TrueType header and some bytes (30 + 4 is not a multiple of 4)
        key = 0x12345678
        words = font[:len(font) - len(font) % 4]
        blob = struct.pack('>II', bp.BFTTF_MAGIC ^ key, len(font) ^ key)
        blob += b''.join(struct.pack('>I', struct.unpack('>I', words[i:i + 4])[0] ^ key) for i in range(0, len(words), 4)) + font[len(words):]
        self.assertEqual(bp.bfttf_decode(blob), font)


@unittest.skipIf(bf is None, 'needs Python 3.14')
class MakePack(unittest.TestCase):
    def setUp(self):
        self.pics = {n: picture(i) for i, n in enumerate(NAMES)}
        for n in NAMES[4:]:
            self.pics[n] = key_picture()
        self.layout = sarc({'blyt/other.bflyt': sample(3000, 5), 'timg/__Combined.bntx': bntx(self.pics), 'anim/x.bflan': sample(500, 6)})
        self.pack = sarc({'Pack/other': sample(2000, 7), 'Layout/Common.sblarc': yaz0_encode(self.layout), 'Pack/tail': sample(900, 8)})

    def pictures(self, pack):
        layout = bf.yaz0_decode(pack[slice(*bf.sarc_members(pack)['Layout/Common.sblarc'])])
        inner = bf.sarc_members(layout); blob = layout[slice(*inner['timg/__Combined.bntx'])]
        return {n: bf.r8_read(blob, t) for n, t in bf.bntx_textures(blob).items()}

    def test_default_layout_trades_the_face_pictures_and_redraws_the_keys(self):
        new_pack, changed, notes, _ = bp.make_pack(self.pack, pl.prompt_controls('', 'position'), StubLetters())
        got = self.pictures(new_pack)
        self.assertEqual(got[NAMES[0]], self.pics[NAMES[1]])      # Switch A shows the Xbox B picture (position mode)
        self.assertEqual(got[NAMES[1]], self.pics[NAMES[0]])
        self.assertEqual(got[NAMES[2]], self.pics[NAMES[3]])
        self.assertEqual(got[NAMES[3]], self.pics[NAMES[2]])
        for name in NAMES[4:]:
            self.assertNotEqual(got[name], self.pics[name])       # the letter was replaced
            self.assertEqual(got[name][2], self.pics[name][2])    # the rest of the key is as it was
        self.assertEqual(sorted(changed), sorted(NAMES))

    def test_owner_layout_by_label(self):
        owner = 'north = a\nsouth = x\nwest = y\neast = b\n'
        new_pack, _, _, _ = bp.make_pack(self.pack, pl.prompt_controls(owner, 'label'), StubLetters())
        got = self.pictures(new_pack)
        self.assertEqual((got[NAMES[0]], got[NAMES[1]], got[NAMES[2]], got[NAMES[3]]),
                         (self.pics[NAMES[2]], self.pics[NAMES[0]], self.pics[NAMES[1]], self.pics[NAMES[3]]))   # A <- X, B <- A, X <- B, Y <- Y

    def test_everything_else_is_the_original(self):
        new_pack, _, _, sizes = bp.make_pack(self.pack, pl.prompt_controls('', 'position'), StubLetters())
        before, after = bf.sarc_members(self.pack), bf.sarc_members(new_pack)
        self.assertEqual(before.keys(), after.keys())
        for name in before:
            if name != 'Layout/Common.sblarc':
                self.assertEqual(self.pack[slice(*before[name])], new_pack[slice(*after[name])], name)
        self.assertEqual(sizes[2], len(self.layout))              # the decompressed size is unchanged
        old = bf.yaz0_decode(self.pack[slice(*before['Layout/Common.sblarc'])])
        new = bf.yaz0_decode(new_pack[slice(*after['Layout/Common.sblarc'])])
        a, b = bf.sarc_members(old), bf.sarc_members(new)
        for name in a:
            if name != 'timg/__Combined.bntx':
                self.assertEqual(old[slice(*a[name])], new[slice(*b[name])], name)

    def test_a_button_nothing_drives_keeps_its_picture(self):
        controls = pl.prompt_controls('menu = none\nl1 = none\nl2 = none\n', 'position')
        controls['lstick'] = None
        new_pack, changed, notes, _ = bp.make_pack(self.pack, controls, StubLetters())
        self.assertNotIn(NAMES[4], changed)       # nothing drives L, so its picture stays
        self.assertTrue(any(n.startswith('L:') for n in notes))

    def test_a_texture_in_another_format_stops_the_tool(self):     # negative control
        bad = sarc({'timg/__Combined.bntx': bntx({n: picture(1) for n in NAMES}, fmt=0x1d01)})
        pack = sarc({'Layout/Common.sblarc': yaz0_encode(bad)})
        with self.assertRaises(ValueError):
            bp.make_pack(pack, pl.prompt_controls('', 'position'), StubLetters())


@unittest.skipIf(bf is None, 'needs Python 3.14')
class MakePackPlayStation(unittest.TestCase):
    def setUp(self):
        self.pics = {n: disc_picture(24 + i * 0.5, 23 + i * 0.5, 17 + i * 0.5) for i, n in enumerate(NAMES[:4])}
        for n in NAMES[4:]:
            self.pics[n] = key_picture()
        self.layout = sarc({'blyt/other.bflyt': sample(3000, 5), 'timg/__Combined.bntx': bntx(self.pics), 'anim/x.bflan': sample(500, 6)})
        self.pack = sarc({'Pack/other': sample(2000, 7), 'Layout/Common.sblarc': yaz0_encode(self.layout), 'Pack/tail': sample(900, 8)})

    def pictures(self, pack):
        layout = bf.yaz0_decode(pack[slice(*bf.sarc_members(pack)['Layout/Common.sblarc'])])
        inner = bf.sarc_members(layout); blob = layout[slice(*inner['timg/__Combined.bntx'])]
        return {n: bf.r8_read(blob, t) for n, t in bf.bntx_textures(blob).items()}

    @unittest.skipIf(mbi is None, 'needs fontTools')
    def test_face_textures_are_drawn_on_discs_like_the_originals_and_the_rest_is_the_original(self):
        controls = pl.prompt_controls('', 'position')
        xbox_pack = bp.make_pack(self.pack, controls, StubLetters())[0]
        new_pack, changed, notes, sizes = bp.make_pack(self.pack, controls, StubLetters(), 'playstation')
        got, xbox = self.pictures(new_pack), self.pictures(xbox_pack)
        for name in NAMES[:4]:
            self.assertNotEqual(got[name], self.pics[name])
            self.assertNotEqual(got[name], xbox[name])
            same_disc(self, got[name], self.pics[name])
        for name in NAMES[4:]:
            self.assertNotEqual(got[name], self.pics[name])       # the key got its PlayStation letter
            self.assertEqual(got[name][2], self.pics[name][2])    # the rest of the key is as it was
        self.assertEqual((sorted(changed), notes), (sorted(NAMES), []))
        before, after = bf.sarc_members(self.pack), bf.sarc_members(new_pack)
        for name in before:
            if name != 'Layout/Common.sblarc':
                self.assertEqual(self.pack[slice(*before[name])], new_pack[slice(*after[name])], name)
        self.assertEqual(sizes[2], len(self.layout))
        old = bf.yaz0_decode(self.pack[slice(*before['Layout/Common.sblarc'])])
        new = bf.yaz0_decode(new_pack[slice(*after['Layout/Common.sblarc'])])
        a, b = bf.sarc_members(old), bf.sarc_members(new)
        for name in a:
            if name != 'timg/__Combined.bntx':
                self.assertEqual(old[slice(*a[name])], new[slice(*b[name])], name)

    @unittest.skipIf(mbi is None, 'needs fontTools')
    def test_a_face_button_without_a_playstation_picture_keeps_its_texture(self):
        controls = {**pl.prompt_controls('', 'position'), 'a': 'l1'}      # L1 has no face picture
        new_pack, changed, notes, _ = bp.make_pack(self.pack, controls, StubLetters(), 'playstation')
        self.assertNotIn(NAMES[0], changed)
        self.assertEqual(self.pictures(new_pack)[NAMES[0]], self.pics[NAMES[0]])
        self.assertTrue(any(n.startswith('A:') and 'PlayStation' not in n and 'face picture' in n for n in notes))

    def test_unknown_style_is_refused(self):      # negative control
        with self.assertRaises(ValueError):
            bp.make_pack(self.pack, pl.prompt_controls('', 'position'), StubLetters(), 'nintendo')


if __name__ == '__main__':
    unittest.main()
