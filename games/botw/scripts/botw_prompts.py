#!/usr/bin/env python3
"""Make a RomFS mod that shows Xbox or PlayStation button names in Breath of the Wild's on-screen prompts, for the buttons you really press.

The game draws its main prompts ("A: Talk") as 48x48 textures of the button, one texture for each of A, B, X, Y, L, ZL and
the left stick press, in Pack/Bootup.pack > Layout/Common.sblarc > timg/__Combined.bntx. A button's texture is a white shape
with its letter cut out. This tool reads that archive from your own RomFS and, for each Switch button, shows the Xbox button
you press for it (the chain of prompt_layout.py: face buttons by position or label, then your controls.txt):
  A B X Y   take the picture of the letter you press (the four face textures trade places);
  L, ZL     keep the game's key shape, and the letter is replaced by LB, LT, RB or RT;
  left stick press   keeps its shape, and the letter becomes LS (or RS).
With --style playstation the keys read L1 L2 R1 R2 and L3 R3, and the face textures are drawn again (the game's textures carry
Xbox letters): a disc of the same size and place as the game's, with the Cross, Circle, Square or Triangle of make_button_icons.py.
Letters are drawn from the Switch's own system font, which your Breath of the Wild app already holds (shared-fonts/
0-standard.bfttf). Nothing of the game ships with the project: every picture comes from your files, and the result is written
only into the mod folder you name (then manifest.tsv, made with make_mod_manifest.py; runtime/romfs.c checks it at start).
The icons inside text (controller tips, the pick-up window, camera mode) are glyphs of the button font in Font/Font_*.sbfarc
(External_00.bffnt, a BC4 glyph sheet): the same changes are made there for A B X Y, L R ZL ZR (as LB RB LT RT), and + and -
(as the Menu and View buttons, or Options and Create). The stick press glyphs keep their Switch look: their letter touches the ring and is not found.

Needs Python 3.14 and fontTools (python3.14 -m pip install fonttools). An existing Pack/Bootup.pack in the mod folder (another
mod's, or an earlier run) is only overwritten with --replace.
usage: botw_prompts.py <mod folder> (--app <Breath of the Wild.app> | --romfs <folder>) [--version botw190]
                       [--style xbox|playstation] [--face-buttons position|label] [--controls controls.txt] [--replace] [--preview picture.png]
"""
import argparse
import io
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import botw_formats as bf                                                        # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'totk' / 'scripts'))
from make_mod_manifest import APP_VERSIONS, read_index, write_manifest          # noqa: E402
from prompt_layout import FACE_MODES, LayoutError, describe, prompt_controls    # noqa: E402

PACK, LAYOUT, BNTX = 'Pack/Bootup.pack', 'Layout/Common.sblarc', 'timg/__Combined.bntx'
FACE = {slot: f'Nt_KeyTex{slot.upper()}_00^d' for slot in 'abxy'}               # Switch button -> its texture
SHAPE = {'l': ['Nt_KeyTexL_00^d'], 'zl': ['Nt_KeyTexZL_00^d'], 'lstick': ['Nt_KeyTexLSt_00^d', 'Nt_KeyTexLSt_01^d']}
XBOX_TEXT = {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'l1': 'LB', 'r1': 'RB', 'l2': 'LT', 'r2': 'RT', 'l3': 'LS', 'r3': 'RS'}
PLAYSTATION_TEXT = {'a': 'Cross', 'b': 'Circle', 'x': 'Square', 'y': 'Triangle', 'l1': 'L1', 'r1': 'R1', 'l2': 'L2', 'r2': 'R2', 'l3': 'L3', 'r3': 'R3'}
STYLES = {'xbox': (XBOX_TEXT, 'Xbox'), 'playstation': (PLAYSTATION_TEXT, 'PlayStation')}      # style -> (button names, brand)
FONT = 'shared-fonts/0-standard.bfttf'
BFTTF_MAGIC = 0x7f9a0218
SIZE = 48


def bfttf_decode(blob):
    """A BFTTF (the Switch's system font files): big-endian words XOR-ed with word0 ^ 0x7f9a0218; word1 is the font's size."""
    key = struct.unpack_from('>I', blob, 0)[0] ^ BFTTF_MAGIC
    size = struct.unpack_from('>I', blob, 4)[0] ^ key
    body = blob[8:8 + size]
    words = struct.unpack(f'>{size // 4}I', body[:size - size % 4])
    return struct.pack(f'>{len(words)}I', *(w ^ key for w in words)) + body[size - size % 4:]


class Letters:
    """Outlines of the system font, as pixel contours."""
    def __init__(self, font):
        from fontTools.pens.recordingPen import RecordingPen
        self.font, self.pen = font, RecordingPen
        self.glyphs, self.cmap = font.getGlyphSet(), font.getBestCmap()
        self.em = font['head'].unitsPerEm

    def text(self, label, cap, cx, cy, max_width):
        """Contours of `label` with capital height `cap` pixels, centred on (cx, cy) (pixels, y down), at most max_width wide."""
        capital = self.pen(); self.glyphs[self.cmap[ord('H')]].draw(capital)
        ys = [p[1] for _, args in capital.value for p in args if p]
        scale = cap / (max(ys) - min(ys))
        x, parts = 0.0, []
        for ch in label:
            name = self.cmap[ord(ch)]
            rec = self.pen(); self.glyphs[name].draw(rec)
            parts.append((rec.value, x)); x += self.font['hmtx'][name][0]
        width = (x - 0) * scale
        if width > max_width:
            scale *= max_width / width; width = max_width
        contours = []
        for value, offset in parts:
            for contour in bf.flatten(value):
                contours.append([(cx - width / 2 + (px + offset) * scale, cy + cap / 2 - py * scale + (min(ys) * scale)) for px, py in contour])
        return contours


def png(rows, path, scale=4):
    raw = b''.join((b'\0' + b''.join(bytes([v]) * scale for v in r)) * scale for r in rows)
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', len(rows[0]) * scale, len(rows) * scale, 8, 0, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def relabel(rows, label, letters, dark=0xE0, grow=1):   # rows: a square picture (a 48 pixel texture or a 50 pixel font cell)
    """The key `rows` with its old letter replaced by `label`: the marks (the old letter) are filled white, and the new letter
    is cut out in the same place, as high as 70 % of the old one."""
    size = len(rows)
    marks = bf.enclosed_marks(rows, (3, 3, size - 3, size - 3), dark)
    if not marks:
        raise ValueError('no letter found in the texture')
    points = set().union(*marks)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    out = [bytearray(r) for r in rows]
    for x, y in points:
        for dy in range(-grow, grow + 1):
            for dx in range(-grow, grow + 1):
                if 0 <= y + dy < size and 0 <= x + dx < size:
                    out[y + dy][x + dx] = 255
    cx, cy = (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys) + 1) / 2
    cut = bf.coverage(letters.text(label, (max(ys) - min(ys) + 1) * 0.7, cx, cy, 26), size)
    # The game's letters are bold and the system font's regular: thicken the cut by about half a pixel.
    cut = [bytes(max(c, int(0.6 * max(cut[yy + dy][xx + dx] for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                                      if 0 <= yy + dy < size and 0 <= xx + dx < size)))
                 for xx, c in enumerate(row)) for yy, row in enumerate(cut)]
    return [bytes(min(o, 255 - c) for o, c in zip(out_row, cov_row)) for out_row, cov_row in zip(out, cut)]


def style_parts(style):
    """(button names, brand) of a style; a style other than xbox and playstation is refused."""
    if style not in STYLES:
        raise ValueError(f'style: {" or ".join(STYLES)}, not {style!r}')
    return STYLES[style]


def drawers_of(style):
    """pad control -> (advance, draw) of the style's buttons: the drawings of make_button_icons.py (they need no text font here)."""
    import make_button_icons as mbi
    return (mbi.xbox_drawers if style == 'xbox' else mbi.playstation_drawers)(None)[0]


def disc_of(rows, level=128):
    """(centre x, centre y, diameter) of the white disc of a face picture: the bounding box of its pixels above `level`."""
    points = [(x, y) for y, row in enumerate(rows) for x, v in enumerate(row) if v > level]
    if not points:
        raise ValueError('no disc found in the picture')
    x0, x1, y0, y1 = min(p[0] for p in points), max(p[0] for p in points), min(p[1] for p in points), max(p[1] for p in points)
    return (x0 + x1 + 1) / 2, (y0 + y1 + 1) / 2, ((x1 - x0 + 1) + (y1 - y0 + 1)) / 2


def draw_button(drawer, size, cx, cy, diameter):
    """The button drawn by `drawer` ((advance, draw) of make_button_icons.py) as a size x size picture, its circle `diameter` pixels
    across and centred on (cx, cy)."""
    from fontTools.pens.recordingPen import RecordingPen
    import make_button_icons as mbi
    pen = RecordingPen(); drawer[1](pen)
    scale = diameter / (2 * mbi.R)
    contours = [[(cx + (x - drawer[0] / 2) * scale, cy - (y - mbi.CY) * scale) for x, y in c] for c in bf.flatten(pen.value)]
    return bf.coverage(contours, size)


def draw_face(drawer, rows):
    """A new face picture for `rows` (a 48 pixel texture or a 50 pixel font cell): the drawer's button on a disc of the same
    size and place as the white disc of `rows`."""
    return draw_button(drawer, len(rows), *disc_of(rows))


def plan(bntx, textures, controls, letters, style='xbox'):
    """texture name -> new pixel rows for the prompts `controls` (prompt_layout.prompt_controls) asks for, and notes."""
    text, brand = style_parts(style)
    new, notes = {}, []
    for slot, name in FACE.items():
        pad = controls[slot]
        if pad in FACE and style == 'xbox':   # a face button: take the picture of the letter you press
            new[name] = bf.r8_read(bntx, textures[FACE[pad]])
        elif pad in FACE:   # the game's pictures carry Xbox letters: draw the button on a disc like the game's
            new[name] = draw_face(drawers_of(style)[pad], bf.r8_read(bntx, textures[name]))
        else:
            notes.append(f'{slot.upper()}: {pad or "nothing"} has no face picture; the Switch icon stays')
    for slot, names in SHAPE.items():
        label = text.get(controls[slot])
        if label is None:
            notes.append(f'{slot.upper()}: {controls[slot] or "nothing"} has no {brand} letter here; the Switch icon stays')
            continue
        for name in names:
            new[name] = relabel(bf.r8_read(bntx, textures[name]), label, letters)
    return new, notes


def make_pack(pack, controls, letters, style='xbox'):
    """Pack/Bootup.pack with the prompts of `controls` (prompt_layout.prompt_controls) redrawn. Returns (the new pack, texture
    name -> new pixel rows, notes, (Common.sblarc size before, after, decompressed size)). Only the changed textures' bytes
    differ: every other byte of the decompressed layout archive, and every other member of the pack, is the original."""
    members = bf.sarc_members(pack)
    stream = pack[slice(*members[LAYOUT])]
    layout = bf.yaz0_decode(stream)
    inner = bf.sarc_members(layout)
    start = inner[BNTX][0]
    bntx = layout[start:inner[BNTX][1]]
    textures = bf.bntx_textures(bntx)
    new, notes = plan(bntx, textures, controls, letters, style)
    edits = [(start + textures[name].offset, bf.r8_image(bntx, textures[name], rows)) for name, rows in sorted(new.items())]
    spliced = bf.yaz0_splice(stream, layout, edits)
    new_pack = bf.sarc_replace(pack, LAYOUT, spliced)
    return new_pack, new, notes, (len(stream), len(spliced), len(layout))


FONT_FACE = {'a': 0xE040, 'b': 0xE041, 'x': 0xE042, 'y': 0xE043}              # Switch button -> code point in External_00.bffnt
FONT_SHAPE = {'l': 0xE044, 'r': 0xE045, 'zl': 0xE046, 'zr': 0xE047, 'lstick': 0xE056, 'rstick': 0xE057}
FONT_MENU = {'plus': 0xE04B, 'minus': 0xE04C}
BFFNT = 'External_00.bffnt'


def button_cell(kind, size, drawers=None):
    """The Menu ('menu') or View ('view') button (of `drawers`, default the Xbox ones) as a size x size picture: the same drawing
    the TOTK tool uses."""
    drawers = drawers or drawers_of('xbox')
    return draw_button(drawers[kind], size, size / 2, size / 2, size * 0.56)      # the game's + and - are small discs in their cell


def font_cells(font, sheet_rows, controls, letters, style='xbox'):
    """code point -> new upright cell for the prompts `controls` asks for, and notes (as plan() does for the textures)."""
    text, brand = style_parts(style)
    new, notes = {}, []
    for slot, code in FONT_FACE.items():
        pad = controls[slot]
        if pad in FONT_FACE and style == 'xbox':
            new[code] = font.get(sheet_rows, FONT_FACE[pad])
        elif pad in FONT_FACE and code in font.cells:
            try:
                new[code] = draw_face(drawers_of(style)[pad], font.get(sheet_rows, code))
            except ValueError as error:
                notes.append(f'{slot.upper()} glyph: {error}; the Switch glyph stays')
        elif pad in FONT_FACE:
            continue
        else:
            notes.append(f'{slot.upper()}: {pad or "nothing"} has no face glyph; the Switch glyph stays')
    for slot, code in FONT_SHAPE.items():
        label = text.get(controls[slot])
        if code not in font.cells:
            continue
        if label is None:
            notes.append(f'{slot.upper()}: {controls[slot] or "nothing"} has no {brand} letter here; the Switch glyph stays')
            continue
        try:
            new[code] = relabel(font.get(sheet_rows, code), label, letters)
        except ValueError as error:     # the stick press glyph's letter touches its ring: not found, the glyph stays as it was
            notes.append(f'{slot.upper()} glyph: {error}; the Switch glyph stays')
    for slot, code in FONT_MENU.items():
        kind = controls[slot]
        if kind in ('menu', 'view') and code in font.cells:
            new[code] = button_cell(kind, font.cell, drawers_of(style))
        else:
            notes.append(f'{slot}: {kind or "nothing"} has no {brand} picture here; the Switch glyph stays')
    return new, notes


def make_font(stream, controls, letters, style='xbox'):
    """A Font/Font_<language>.sbfarc (Yaz0 of a SARC) with the glyphs of `controls` redrawn in its External_00.bffnt. Returns
    (the new file, glyph code point -> new cell, notes). Only the changed blocks of the glyph sheet differ."""
    archive = bf.yaz0_decode(stream)
    members = bf.sarc_members(archive)
    start, end = members[BFFNT]
    data = archive[start:end]
    font = bf.Bffnt(data)
    sheet = data[font.sheet_at:]
    rows = bf.bc4_read(sheet, font.texture)
    new, notes = font_cells(font, rows, controls, letters, style)
    boxes = [font.put(rows, code, cell) for code, cell in sorted(new.items())]
    image = bf.bc4_image(sheet, font.texture, rows, boxes)
    base = start + font.sheet_at + font.texture.offset
    old = archive[base:base + len(image)]
    edits, i = [], 0
    while i < len(image):       # the runs of bytes that changed
        if image[i] == old[i]:
            i += 1
            continue
        j = i
        while j < len(image) and (image[j] != old[j] or (j + 1 < len(image) and image[j + 1] != old[j + 1])):
            j += 1
        edits.append((base + i, image[i:j])); i = j
    return bf.yaz0_splice(stream, archive, edits), new, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('mod', type=Path, help='the mod folder to write into (other files in it are kept and listed too)')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--app', type=Path, help='the built Breath of the Wild.app')
    source.add_argument('--romfs', type=Path, help='a folder holding romfs.bin and runtime-index.bin')
    parser.add_argument('--version', choices=[v for v in APP_VERSIONS if v.startswith('botw')], help='the game version (default: the mod folder name)')
    parser.add_argument('--style', choices=list(STYLES), default='xbox', help='the buttons to show: xbox (default) or playstation')
    parser.add_argument('--face-buttons', choices=FACE_MODES, default='position',
                        help='how the controller\'s face buttons map (the runtime\'s TOTK_FACE_BUTTONS, or key 6): position (Xbox, PlayStation; default) or label (Switch)')
    parser.add_argument('--controls', type=Path, help='your controls.txt (the prompts then show the button you press)')
    parser.add_argument('--replace', action='store_true', help=f'overwrite a {PACK} or Font/Font_*.sbfarc already in the mod folder')
    parser.add_argument('--preview', type=Path, help='also write a picture of the changed textures (PNG)')
    args = parser.parse_args()
    version = args.version or (args.mod.resolve().name if args.mod.resolve().name in APP_VERSIONS else None)
    romfs, app_fonts = args.romfs, None
    if args.app:
        if not version:
            parser.error('name the game version with --version (or name the mod folder botw160 or botw190)')
        romfs = args.app / f'Contents/Resources/totk/extracted/{version}/romfs'
        app_fonts = args.app / 'Contents/Resources/totk/extracted/firmware'
    if app_fonts is None:
        app_fonts = romfs.parent.parent / 'firmware'
    for need in ('romfs.bin', 'runtime-index.bin'):
        if not (romfs / need).is_file():
            parser.error(f'{romfs / need} not found')
    if not (app_fonts / FONT).is_file():
        parser.error(f'{app_fonts / FONT} not found (the app\'s copy of the Switch system fonts)')
    if (args.mod / PACK).exists() and not args.replace:
        parser.error(f'{args.mod / PACK} already exists (another mod\'s, or an earlier run): pass --replace to overwrite it')
    font_notes = []
    try:
        controls = prompt_controls(args.controls.read_text() if args.controls else '', args.face_buttons)
    except LayoutError as error:
        parser.error(f'{args.controls}, {error}')
    except (OSError, UnicodeDecodeError) as error:
        parser.error(f'{args.controls}: {error}')
    print('\n'.join(describe(args.controls.read_text() if args.controls else '', args.face_buttons, args.style, (*FACE, *SHAPE))))

    from fontTools.ttLib import TTFont
    letters = Letters(TTFont(io.BytesIO(bfttf_decode((app_fonts / FONT).read_bytes()))))
    index = read_index(romfs / 'runtime-index.bin')
    with (romfs / 'romfs.bin').open('rb') as image:
        offset, size = index[PACK]; image.seek(offset); pack = image.read(size)
    new_pack, new, notes, layout_sizes = make_pack(pack, controls, letters, args.style)
    for note in notes:
        print('note:', note)
    target = args.mod / PACK
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new_pack)
    print(f'{target}: {len(new)} textures changed; {PACK} {len(pack)} -> {len(new_pack)} bytes, {LAYOUT} {layout_sizes[0]} -> '
          f'{layout_sizes[1]} (decompressed {layout_sizes[2]} bytes, unchanged)')
    if args.preview and new:
        names = sorted(new)
        rows = [b''] * SIZE
        for name in names:
            rows = [a + b + bytes([128]) for a, b in zip(rows, new[name])]
        png(rows, args.preview)
        print(f'{args.preview}: {", ".join(n.split("^")[0].replace("Nt_KeyTex", "") for n in names)}')
    fonts = sorted(n for n in index if n.startswith('Font/Font_') and n.endswith('.sbfarc'))
    exists = [n for n in fonts if (args.mod / n).exists()]
    if exists and not args.replace:
        sys.exit(f'{args.mod / exists[0]} already exists: pass --replace to overwrite it (nothing else was written to the fonts)')
    for name in fonts:
        offset, size = index[name]
        with (romfs / 'romfs.bin').open('rb') as image:
            image.seek(offset); stream = image.read(size)
        data, cells, font_notes = make_font(stream, controls, letters, args.style)
        (args.mod / name).parent.mkdir(parents=True, exist_ok=True)
        (args.mod / name).write_bytes(data)
        print(f'{args.mod / name}: {len(cells)} glyphs changed; {size} -> {len(data)} bytes')
    for note in dict.fromkeys(font_notes) if fonts else ():
        print('note (font glyphs):', note)
    found = write_manifest(args.mod, romfs)
    if found.misspelt:
        sys.exit('manifest not written: ' + ', '.join(f'{a} (the RomFS has {b})' for a, b in found.misspelt))
    print(f'{args.mod / "manifest.tsv"}: {len(found.lines)} files ({len(found.lines) - len(found.added)} replaced, {len(found.added)} added)')


if __name__ == '__main__':
    main()
