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
(as the Menu and View buttons, or Options and Create), the two stick press glyphs become plain LS and RS (or L3, R3)
icons, and the d-pad glyphs (four dots) become a d-pad cross, as does the d-pad of the shortcut icons on the screen
(ButtonIconL46NN_00). The glyphs of a stick being moved keep their Switch look.
The controller pictures of the controller tips page are drawn again too: the handheld Switch (Nt_NNillust_00, _01) and the four pieces
of the hint boxes (Nt_GamePadIllustSnn_00 to _03) become an Xbox controller (or with --style playstation a DualSense) drawn by
botw_controller_art.py, with the buttons in the places of the Switch's, so the game's callout lines still end on a button of the same
kind. --keep-switch-pictures leaves them alone.

Needs Python 3.14 and fontTools (python3.14 -m pip install fonttools). An existing Pack/Bootup.pack in the mod folder (another
mod's, or an earlier run) is only overwritten with --replace.
usage: botw_prompts.py <mod folder> (--app <Breath of the Wild.app> | --romfs <folder>) [--version botw190]
                       [--style xbox|playstation] [--face-buttons position|label] [--controls controls.txt] [--replace] [--keep-switch-pictures] [--preview picture.png]
"""
import argparse
import io
import math
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import botw_formats as bf                                                        # noqa: E402
import botw_controller_art as art                                                # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'totk' / 'scripts'))
from make_mod_manifest import APP_VERSIONS, read_index, write_manifest          # noqa: E402
from prompt_layout import FACE_MODES, LayoutError, describe, prompt_controls    # noqa: E402

PACK, LAYOUT, BNTX = 'Pack/Bootup.pack', 'Layout/Common.sblarc', 'timg/__Combined.bntx'
FACE = {slot: f'Nt_KeyTex{slot.upper()}_00^d' for slot in 'abxy'}               # Switch button -> its texture
SHAPE = {'l': ['Nt_KeyTexL_00^d'], 'zl': ['Nt_KeyTexZL_00^d'], 'lstick': ['Nt_KeyTexLSt_00^d', 'Nt_KeyTexLSt_01^d']}
XBOX_TEXT = {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'l1': 'LB', 'r1': 'RB', 'l2': 'LT', 'r2': 'RT', 'l3': 'LS', 'r3': 'RS'}
PICTURES = art.PICTURES      # the controller pictures of the controller tips page and the hint boxes
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
    return cut_label(out, label, letters, (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys) + 1) / 2, (max(ys) - min(ys) + 1) * 0.7, 26)


def cut_label(out, label, letters, cx, cy, cap, max_width, bold=0.6):
    """The square picture `out` (rows of bytes) with `label` cut out, centred on (cx, cy), capital height `cap` pixels, the
    strokes thickened by `bold` pixels."""
    size = len(out)
    cut = bf.coverage(letters.text(label, cap, cx, cy, max_width), size)
    # The game's letters are bold and the system font's regular: thicken the cut (by about half a pixel by default).
    cut = [bytes(max(c, int(bold * max(cut[yy + dy][xx + dx] for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                                      if 0 <= yy + dy < size and 0 <= xx + dx < size)))
                 for xx, c in enumerate(row)) for yy, row in enumerate(cut)]
    return [bytes(min(o, 255 - c) for o, c in zip(out_row, cov_row)) for out_row, cov_row in zip(out, cut)]


def stick_cell(rows, label, letters, bright=128):
    """A plain icon of the stick press `label` (LS / RS, or L3 / R3) for the stick press glyph `rows` (a font cell: the stick seen
    from the side, an arrow pressing down on its cap, a small L or R on it). The game's drawing has no room for two letters, so the
    whole glyph becomes a white disc with `label` cut out, as wide as the stick's body and on its place (the widest bright rows)."""
    spans = {y: (min(x for x, v in enumerate(row) if v > bright), max(x for x, v in enumerate(row) if v > bright))
             for y, row in enumerate(rows) if any(v > bright for v in row)}
    if not spans:
        raise ValueError('no stick found in the glyph')
    widest = max(b - a + 1 for a, b in spans.values())
    body = [y for y, (a, b) in spans.items() if b - a + 1 >= 0.6 * widest]
    r = widest / 2
    cx = sum(spans[y][0] + spans[y][1] + 1 for y in body) / (2 * len(body))
    cy = (min(body) + max(body) + 1) / 2
    size = len(rows)
    disc = [(cx + r * math.cos(2 * math.pi * i / 64), cy + r * math.sin(2 * math.pi * i / 64)) for i in range(64)]
    return cut_label(bf.coverage([disc], size), label, letters, cx, cy, r * 0.75, r * 1.45, 0.4)


def style_parts(style):
    """(button names, brand) of a style; a style other than xbox and playstation is refused."""
    if style not in STYLES:
        raise ValueError(f'style: {" or ".join(STYLES)}, not {style!r}')
    return STYLES[style]


def drawers_of(style):
    """pad control -> (advance, draw) of the style's buttons: the drawings of make_button_icons.py (they need no text font here)."""
    import make_button_icons as mbi
    return (mbi.xbox_drawers if style == 'xbox' else mbi.playstation_drawers)(None)[0]


DPAD_GLYPHS = {0xE049: ('up', 'down', 'left', 'right'), 0xE060: ('up',), 0xE061: ('down',), 0xE062: ('left',), 0xE063: ('right',),
               0xE064: ('up', 'down'), 0xE065: ('left', 'right')}      # the d-pad glyphs of the button font -> their lit directions
DPAD_TEXTURE = 'ButtonIconL46NN_00^d'       # the d-pad of the shortcut icons on the screen (four dots)


def dpad_cross(rows, lit=('up', 'down', 'left', 'right')):
    """A d-pad cross (the drawing of make_button_icons.py: arms 0.8 of the radius wide, an arrow cut out of each `lit` arm) as a
    picture the size of `rows`, on the box of the white shape of `rows` (the game's four dots)."""
    cx, cy, d = disc_of(rows)
    r = d / 2; arm = 0.4 * r; m, t, h = (r + arm) / 2, 0.72 * arm, 0.8 * arm

    def wound(points, sign):        # counter-clockwise on screen (y down) for a shape, the other way for a hole
        area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))
        return points if (area > 0) == (sign > 0) else points[::-1]
    cross = [(cx + r, cy - arm), (cx + r, cy + arm), (cx + arm, cy + arm), (cx + arm, cy + r), (cx - arm, cy + r), (cx - arm, cy + arm),
             (cx - r, cy + arm), (cx - r, cy - arm), (cx - arm, cy - arm), (cx - arm, cy - r), (cx + arm, cy - r), (cx + arm, cy - arm)]
    heads = {'up': [(cx - t, cy - m + h / 2), (cx, cy - m - h / 2), (cx + t, cy - m + h / 2)],
             'down': [(cx - t, cy + m - h / 2), (cx, cy + m + h / 2), (cx + t, cy + m - h / 2)],
             'left': [(cx - m + h / 2, cy - t), (cx - m - h / 2, cy), (cx - m + h / 2, cy + t)],
             'right': [(cx + m - h / 2, cy - t), (cx + m + h / 2, cy), (cx + m - h / 2, cy + t)]}
    return bf.coverage([wound(cross, 1)] + [wound(heads[k], -1) for k in lit], len(rows))


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
    if DPAD_TEXTURE in textures:
        new[DPAD_TEXTURE] = dpad_cross(bf.r8_read(bntx, textures[DPAD_TEXTURE]))
    else:
        notes.append(f'{DPAD_TEXTURE}: not found; the Switch d-pad icon stays')
    for slot, names in SHAPE.items():
        label = text.get(controls[slot])
        if label is None:
            notes.append(f'{slot.upper()}: {controls[slot] or "nothing"} has no {brand} letter here; the Switch icon stays')
            continue
        for name in names:
            new[name] = relabel(bf.r8_read(bntx, textures[name]), label, letters)
    return new, notes


def plan_pictures(bntx, textures, controls, letters, style='xbox'):
    """texture name -> (grey rows, alpha rows) of the controller pictures of the controller tips page, drawn again as an Xbox
    controller or a DualSense (botw_controller_art.py), and notes. A picture whose texture is not the size of the drawing
    (another game version) stays as it was."""
    text, _ = style_parts(style)
    new, notes = {}, []
    for name, ((width, height), draw) in PICTURES.items():
        texture = textures.get(name)
        if texture is None or (texture.width, texture.height) != (width, height):
            notes.append(f'{name}: not found as a {width}x{height} texture; the Switch picture stays')
            continue
        bf.check_rg8(texture)
        new[name] = draw(controls, letters, text, style=style)
    return new, notes


def make_pack(pack, controls, letters, style='xbox', pictures=True):
    """Pack/Bootup.pack with the prompts of `controls` (prompt_layout.prompt_controls) redrawn, and with `pictures` the controller
    pictures of the controller tips page too. Returns (the new pack, texture name -> new pixel rows, texture name -> (grey rows,
    alpha rows) of the pictures, notes, (Common.sblarc size before, after, decompressed size)). Only the changed textures' bytes
    differ: every other byte of the decompressed layout archive, and every other member of the pack, is the original."""
    members = bf.sarc_members(pack)
    stream = pack[slice(*members[LAYOUT])]
    layout = bf.yaz0_decode(stream)
    inner = bf.sarc_members(layout)
    start = inner[BNTX][0]
    bntx = layout[start:inner[BNTX][1]]
    textures = bf.bntx_textures(bntx)
    new, notes = plan(bntx, textures, controls, letters, style)
    drawn, more = plan_pictures(bntx, textures, controls, letters, style) if pictures else ({}, [])
    edits = [(start + textures[name].offset, bf.r8_image(bntx, textures[name], rows)) for name, rows in sorted(new.items())]
    edits += [(start + textures[name].offset, bf.rg8_image(bntx, textures[name], *drawn[name])) for name in sorted(drawn)]
    spliced = bf.yaz0_splice(stream, layout, edits)
    new_pack = bf.sarc_replace(pack, LAYOUT, spliced)
    return new_pack, new, drawn, notes + more, (len(stream), len(spliced), len(layout))


FONT_FACE = {'a': 0xE040, 'b': 0xE041, 'x': 0xE042, 'y': 0xE043}              # Switch button -> code point in External_00.bffnt
FONT_SHAPE = {'l': 0xE044, 'r': 0xE045, 'zl': 0xE046, 'zr': 0xE047, 'lstick': 0xE056, 'rstick': 0xE057}
FONT_MENU = {'plus': 0xE04B, 'minus': 0xE04C}
BFFNT = 'External_00.bffnt'


def button_cell(kind, rows, drawers=None):
    """The Menu ('menu') or View ('view') button (of `drawers`, default the Xbox ones) as a picture the size of the cell `rows`,
    on the disc of the game's + or -: the glyph sits in the left part of its cell and the game draws only that part."""
    drawers = drawers or drawers_of('xbox')
    return draw_button(drawers[kind], len(rows), *disc_of(rows))


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
            new[code] = (stick_cell if slot in ('lstick', 'rstick') else relabel)(font.get(sheet_rows, code), label, letters)
        except ValueError as error:     # no letter found: the glyph stays as it was
            notes.append(f'{slot.upper()} glyph: {error}; the Switch glyph stays')
    for code, lit in DPAD_GLYPHS.items():     # the d-pad: a cross on the place of the game's four dots
        if code in font.cells:
            try:
                new[code] = dpad_cross(font.get(sheet_rows, code), lit)
            except ValueError as error:
                notes.append(f'd-pad glyph {code:X}: {error}; the Switch glyph stays')
    for slot, code in FONT_MENU.items():
        kind = controls[slot]
        if kind in ('menu', 'view') and code in font.cells:
            new[code] = button_cell(kind, font.get(sheet_rows, code), drawers_of(style))
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
    parser.add_argument('--keep-switch-pictures', action='store_true',
                        help='leave the controller pictures of the controller tips page as the Switch\'s (otherwise an Xbox controller or a DualSense is drawn there)')
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
    index = read_index(romfs / 'runtime-index.bin')
    fonts = sorted(n for n in index if n.startswith('Font/Font_') and n.endswith('.sbfarc'))
    exists = [n for n in (PACK, *fonts) if (args.mod / n).exists()]
    if exists and not args.replace:      # checked before anything is written
        parser.error(f'{args.mod / exists[0]} already exists (another mod\'s, or an earlier run): pass --replace to overwrite it')
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
    with (romfs / 'romfs.bin').open('rb') as image:
        offset, size = index[PACK]; image.seek(offset); pack = image.read(size)
    new_pack, new, drawn, notes, layout_sizes = make_pack(pack, controls, letters, args.style, not args.keep_switch_pictures)
    for note in notes:
        print('note:', note)
    target = args.mod / PACK
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(new_pack)
    print(f'{target}: {len(new) + len(drawn)} textures changed ({len(drawn)} controller pictures); {PACK} {len(pack)} -> {len(new_pack)} bytes, {LAYOUT} {layout_sizes[0]} -> '
          f'{layout_sizes[1]} (decompressed {layout_sizes[2]} bytes, unchanged)')
    if args.preview and new:
        names = sorted(new)
        height = max(len(new[name]) for name in names)     # the keys are 48 pixels, the shortcut d-pad 64
        rows = [b''] * height
        for name in names:
            pic = [row for row in new[name]] + [bytes([128]) * len(new[name][0])] * (height - len(new[name]))
            rows = [a + b + bytes([128]) for a, b in zip(rows, pic)]
        png(rows, args.preview)
        print(f'{args.preview}: {", ".join(n.split("^")[0].replace("Nt_KeyTex", "") for n in names)}')
    if args.preview and drawn:
        width, rows = max(len(grey[0]) for grey, _ in drawn.values()), []
        for name in sorted(drawn, key=lambda n: (-len(drawn[n][0][0]), n)):       # the pictures over a mid grey, one under the other
            rows += [bytes((g * a + 90 * (255 - a)) // 255 for g, a in zip(grey, alpha)).ljust(width, bytes([128])) for grey, alpha in zip(*drawn[name])]
            rows.append(bytes([128]) * width)
        target = args.preview.with_name(args.preview.stem + '-pictures.png')
        png(rows, target, 2)
        print(f'{target}: {", ".join(sorted(drawn))}')
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
