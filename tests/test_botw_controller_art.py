"""The controller pictures of Breath of the Wild's controller tips page (games/botw/scripts/botw_controller_art.py, the R8G8 texture
code of botw_formats.py, and botw_prompts.make_pack with pictures) on made-up textures: no game data."""
import random
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'games/botw/scripts'))
sys.path.insert(0, str(ROOT/'games/totk/scripts'))
sys.path.insert(0, str(ROOT/'tests'))
try:
    import botw_formats as bf
    import botw_prompts as bp
    import botw_controller_art as art
    from test_botw_prompts import StubLetters, yaz0_encode, sarc, sample, NAMES, picture, key_picture, mbi
except (SystemExit, ImportError):     # make_romfs_patches needs Python 3.14 (compression.zstd)
    bf = bp = art = mbi = None
import prompt_layout as pl      # noqa: E402

FRONT, TOP = 'Nt_NNillust_00^f', 'Nt_NNillust_01^f'
SNN = ['Nt_GamePadIllustSnn_0%d^f' % i for i in range(4)]


class Recorder:
    """Letters that record each label with the place it is drawn at, and draw a small square."""
    def __init__(self):
        self.calls = []

    def text(self, label, cap, cx, cy, max_width):
        self.calls.append((label, cx, cy))
        h = cap / 2
        return [[(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]]

    def at(self, x, y, tolerance=2):
        return [label for label, cx, cy in self.calls if abs(cx - x) <= tolerance and abs(cy - y) <= tolerance]


def rg8_bntx(specs):
    """A BNTX with one R8G8 or R8 block-linear texture for each (name, format, width, height, log2 of the block height, grey rows,
    alpha rows or None for an R8 texture), laid out as the game's are."""
    head = bytearray(b'BNTX' + bytes(0x1fc)); names = bytearray(); brti = bytearray(); pointers = bytearray(); images = []
    base = len(head)
    for name, fmt, width, height, log2, grey, alpha in specs:
        element = 2 if alpha is not None else 1
        raw = name.encode()
        name_at = base + len(names); names += struct.pack('<H', len(raw)) + raw + b'\0' * (-(2 + len(raw)) % 8)
        block = 1 << log2
        gobs_wide = -(-width * element // 64)
        size = -(-(-(-height // 8)) // block) * block * gobs_wide * 512
        entry = bytearray(0xa0)
        entry[:4] = b'BRTI'
        struct.pack_into('<HHHIIIIIIII', entry, 0x12, 0, 0, 1, 1, fmt, 0, width, height, 1, 1, log2)
        struct.pack_into('<I', entry, 0x50, size)
        struct.pack_into('<Q', entry, 0x60, name_at)
        brti += entry; pointers += struct.pack('<Q', 0)
        image = bytearray(size)
        for y in range(height):
            for x in range(width):
                at = bf.gob_address(x, y, width, element, block)
                image[at] = grey[y][x]
                if alpha is not None:
                    image[at + 1] = alpha[y][x]
        images.append(image)
    brti_at = base + len(names); pointers_at = brti_at + len(brti)
    out = bytearray(head + names + brti + pointers)
    for i, image in enumerate(images):
        o = brti_at + i * 0xa0
        struct.pack_into('<Q', out, o + 0x70, pointers_at + i * 8)
        struct.pack_into('<Q', out, pointers_at + i * 8, len(out))
        out += image
    return bytes(out)


def noise(width, height, seed):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(width)) for _ in range(height)]


@unittest.skipIf(bf is None, 'needs Python 3.14')
class Rg8Textures(unittest.TestCase):
    def setUp(self):
        self.grey, self.alpha = noise(64, 32, 1), noise(64, 32, 2)
        self.blob = rg8_bntx([('t^f', bf.R8G8_UNORM, 64, 32, 1, self.grey, self.alpha)])
        self.texture = bf.bntx_textures(self.blob)['t^f']

    def test_the_two_channels_read_back_and_the_swizzle_keeps_them_apart(self):
        self.assertEqual(bf.rg8_read(self.blob, self.texture), (self.grey, self.alpha))
        addresses = {bf.gob_address(x, y, 64, 2, 2) + c for x in range(64) for y in range(32) for c in (0, 1)}
        self.assertEqual(len(addresses), 64 * 32 * 2)

    def test_only_the_pixels_change_and_the_padding_stays(self):
        grey, alpha = noise(64, 32, 3), noise(64, 32, 4)
        image = bf.rg8_image(self.blob, self.texture, grey, alpha)
        self.assertEqual(len(image), self.texture.image_size)
        patched = bytearray(self.blob); patched[self.texture.offset:self.texture.offset + len(image)] = image
        self.assertEqual(bf.rg8_read(bytes(patched), self.texture), (grey, alpha))
        used = {bf.gob_address(x, y, 64, 2, 2) + c for x in range(64) for y in range(32) for c in (0, 1)}
        original = self.blob[self.texture.offset:self.texture.offset + len(image)]
        for i in range(len(image)):
            if i not in used:
                self.assertEqual(image[i], original[i])

    def test_other_formats_and_sizes_are_refused(self):     # negative controls
        r8 = rg8_bntx([('t^f', bf.R8_UNORM, 64, 32, 1, self.grey, None)])
        with self.assertRaises(ValueError):
            bf.rg8_read(r8, bf.bntx_textures(r8)['t^f'])
        with self.assertRaises(ValueError):
            bf.r8_read(self.blob, self.texture)
        with self.assertRaises(ValueError):
            bf.rg8_image(self.blob, self.texture, self.grey[:31], self.alpha)
        with self.assertRaises(ValueError):
            bf.rg8_image(self.blob, self.texture, self.grey, [row[:63] for row in self.alpha])

    def test_coverage_of_a_wide_picture(self):
        wide = bf.coverage_rect([[(10, 4), (50, 4), (50, 12), (10, 12)]], 60, 16)
        self.assertEqual((len(wide), len(wide[0])), (16, 60))
        self.assertEqual((wide[8][30], wide[8][5], wide[2][30], wide[14][30]), (255, 0, 0, 0))
        square = [(3.3, 4.1), (40.7, 9.2), (22.5, 41.9)]
        self.assertEqual(bf.coverage([square], 48), bf.coverage_rect([square], 48, 48))


@unittest.skipIf(bf is None, 'needs Python 3.14')
class Drawing(unittest.TestCase):
    def draw(self, name, controls=None, letters=None):
        controls = controls or pl.prompt_controls('', 'position')
        (width, height), draw = art.PICTURES[name]
        grey, alpha = draw(controls, letters or StubLetters(), bp.XBOX_TEXT)
        self.assertEqual((len(grey), len(grey[0]), len(alpha), len(alpha[0])), (height, width, height, width))
        return grey, alpha

    def test_the_pictures_have_the_size_of_the_game_s_and_a_transparent_corner(self):
        for name in art.PICTURES:
            grey, alpha = self.draw(name)
            self.assertTrue(any(a for row in alpha for a in row), name)
            for y, row in enumerate(alpha):
                for x, a in enumerate(row):
                    if a == 0:
                        self.assertEqual(grey[y][x], 255)      # a transparent pixel is white, like the game's
        for name in (FRONT, TOP):
            self.assertEqual(self.draw(name)[1][0][0], 0)

    def test_the_buttons_are_where_the_switch_pictures_have_theirs(self):
        grey, alpha = self.draw(FRONT)
        for name, (cx, cy) in {**art.FRONT_STICKS, **{s: p for s, p in art.FRONT_FACE.items()}}.items():
            self.assertEqual((grey[int(cy)][int(cx) + 7 if name in art.FRONT_FACE else int(cx) + 15], alpha[int(cy)][int(cx)]),
                             (art.DARK, 255), name)
        self.assertEqual((grey[120][288], alpha[120][288]), (art.BODY, 255))       # the body between the grips
        self.assertEqual(alpha[250][288], 0)                                          # the notch under it is empty
        top = self.draw(TOP)[1]
        for x0, y0, x1, y1 in art.TOP_SHOULDERS.values():
            self.assertEqual(top[(y0 + y1) // 2][(x0 + x1) // 2], 255)

    def test_the_face_buttons_keep_the_xbox_layout_whatever_the_mode_and_controls(self):
        owner = 'north = a\nsouth = x\nwest = y\neast = b\n'
        for controls in (pl.prompt_controls('', 'position'), pl.prompt_controls('', 'label'), pl.prompt_controls(owner, 'position'),
                         pl.prompt_controls('west = a\nsouth = none\nnorth = none\n', 'position')):
            letters = Recorder()
            self.draw(FRONT, controls, letters)
            for slot, (cx, cy) in art.FRONT_FACE.items():           # Y top, X left, B right, A bottom
                self.assertEqual(letters.at(cx, cy), [{'x': 'Y', 'y': 'X', 'a': 'B', 'b': 'A'}[slot]], (controls, slot))

    def test_view_and_menu_are_drawn_where_minus_and_plus_are(self):
        controls = pl.prompt_controls('', 'position')
        self.assertEqual((controls['plus'], controls['minus']), ('menu', 'view'))
        grey, alpha = self.draw(FRONT)
        swapped = self.draw(FRONT, {**controls, 'plus': 'view', 'minus': 'menu'})[0]
        for slot, (cx, cy) in art.FRONT_MENUS.items():
            self.assertEqual(alpha[int(cy)][int(cx) + 6], 255)
        self.assertNotEqual(grey, swapped)

    def test_the_top_view_names_the_shoulder_buttons_in_the_places_of_zl_l_zr_and_r(self):
        letters = Recorder()
        self.draw(TOP, pl.prompt_controls('', 'position'), letters)
        for slot, label in {'zl': 'LT', 'l': 'LB', 'zr': 'RT', 'r': 'RB'}.items():
            x0, y0, x1, y1 = art.TOP_SHOULDERS[slot]
            self.assertEqual(letters.at((x0 + x1) / 2, (y0 + y1) / 2, 4), [label], slot)

    def test_the_hint_pieces(self):
        controls = pl.prompt_controls('', 'position')
        left, right, wing_right, wing_left = Recorder(), Recorder(), Recorder(), Recorder()
        self.draw(SNN[0], controls, left); self.draw(SNN[1], controls, right)
        self.draw(SNN[2], controls, wing_right); self.draw(SNN[3], controls, wing_left)
        self.assertEqual(sorted(label for label, *_ in left.calls), ['LB', 'LT'])
        self.assertEqual(sorted(label for label, *_ in right.calls), ['RB', 'RT'])
        for slot, (cx, cy) in art.PIECE_RIGHT['face'].items():
            self.assertEqual(wing_right.at(cx, cy), [{'x': 'Y', 'y': 'X', 'a': 'B', 'b': 'A'}[slot]])
        self.assertEqual(wing_left.calls, [])                 # left stick, d-pad and View carry no letter
        grey, alpha = self.draw(SNN[3])
        cx, cy = art.PIECE_LEFT['stick']
        self.assertEqual((grey[int(cy)][int(cx) + 18], alpha[int(cy)][int(cx)]), (art.DARK, 255))

    def test_the_playstation_pictures_keep_every_button_in_its_switch_place(self):
        controls = pl.prompt_controls('', 'position')
        draw = {name: art.PICTURES[name][1](controls, Recorder(), bp.PLAYSTATION_TEXT, style='playstation') for name in art.PICTURES}
        for name, (grey, alpha) in draw.items():
            size = art.PICTURES[name][0]
            self.assertEqual((len(grey[0]), len(grey)), size, name)
            if name not in SNN[:2]:                 # the shoulder pieces differ only in their labels (checked below)
                self.assertTrue((grey, alpha) != art.PICTURES[name][1](controls, StubLetters(), bp.XBOX_TEXT), name)
        grey, alpha = draw[FRONT]
        dpad = art.FRONT_DPAD
        points = [*art.FRONT_STICKS.values(), *art.FRONT_FACE.values(), *art.FRONT_MENUS.values(),
                  *[(dpad[0] + dx, dpad[1] + dy) for dx, dy in ((0, -17), (17, 0), (0, 17), (-17, 0))]]
        for cx, cy in points:                       # the end of each callout line is on a dark button
            self.assertEqual(alpha[int(cy)][int(cx)], 255, (cx, cy))
            self.assertLess(min(grey[int(cy) + dy][int(cx) + dx] for dx in range(-3, 4) for dy in range(-3, 4)), art.RIM, (cx, cy))
        self.assertGreater(grey[74][288], art.BODY)          # the touchpad, not the Xbox guide button, is at the top middle
        for slot, (cx, cy) in art.FRONT_FACE.items():       # the face buttons carry light symbols on dark discs
            ring = [grey[int(cy) + dy][int(cx) + dx] for dx in range(-6, 7) for dy in range(-6, 7)]
            self.assertTrue(max(ring) >= art.LIGHT - 5 and min(ring) <= art.DARK + 5, slot)

    def test_the_playstation_shoulders_read_l2_l1_r2_r1(self):
        controls = pl.prompt_controls('', 'position')
        top, left, right = Recorder(), Recorder(), Recorder()
        art.PICTURES[TOP][1](controls, top, bp.PLAYSTATION_TEXT, style='playstation')
        art.PICTURES[SNN[0]][1](controls, left, bp.PLAYSTATION_TEXT, style='playstation')
        art.PICTURES[SNN[1]][1](controls, right, bp.PLAYSTATION_TEXT, style='playstation')
        self.assertEqual(sorted(label for label, *_ in top.calls), ['L1', 'L2', 'R1', 'R2'])
        self.assertEqual(sorted(label for label, *_ in left.calls), ['L1', 'L2'])
        self.assertEqual(sorted(label for label, *_ in right.calls), ['R1', 'R2'])

    def test_canvas_blends_and_inset_shrinks(self):
        canvas = art.Canvas(20, 20)
        canvas.paint([art.rounded_rect(2, 2, 18, 18, 3)], 100)
        canvas.paint([art.rounded_rect(2, 2, 18, 18, 3)], 200, 0.5)
        grey, alpha = canvas.rows()
        self.assertEqual((grey[10][10], alpha[10][10], grey[0][0], alpha[0][0]), (150, 255, 255, 0))
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        for points in (square, square[::-1]):          # either winding
            xs = [p[0] for p in art.inset(points, 2)]
            self.assertEqual((min(xs), max(xs)), (2, 8))


@unittest.skipIf(bf is None, 'needs Python 3.14')
class MakePackPictures(unittest.TestCase):
    def setUp(self):
        self.keys = {n: picture(i) for i, n in enumerate(NAMES)}
        for n in NAMES[4:]:
            self.keys[n] = key_picture()
        self.front_grey, self.front_alpha = noise(576, 288, 11), noise(576, 288, 12)
        self.top_grey, self.top_alpha = noise(576, 100, 13), noise(576, 100, 14)
        self.small_grey, self.small_alpha = noise(64, 32, 15), noise(64, 32, 16)
        specs = [(n, bf.R8_UNORM, 48, 48, 3, rows, None) for n, rows in self.keys.items()]
        specs += [(FRONT, bf.R8G8_UNORM, 576, 288, 4, self.front_grey, self.front_alpha),
                  (TOP, bf.R8G8_UNORM, 576, 100, 4, self.top_grey, self.top_alpha),
                  (SNN[0], bf.R8G8_UNORM, 64, 32, 1, self.small_grey, self.small_alpha),       # not the size of the drawing
                  (SNN[1], bf.R8G8_UNORM, 228, 228, 4, noise(228, 228, 17), noise(228, 228, 18))]
        self.layout = sarc({'blyt/other.bflyt': sample(3000, 5), 'timg/__Combined.bntx': rg8_bntx(specs), 'anim/x.bflan': sample(500, 6)})
        self.pack = sarc({'Pack/other': sample(2000, 7), 'Layout/Common.sblarc': yaz0_encode(self.layout), 'Pack/tail': sample(900, 8)})

    def textures(self, pack):
        layout = bf.yaz0_decode(pack[slice(*bf.sarc_members(pack)['Layout/Common.sblarc'])])
        blob = layout[slice(*bf.sarc_members(layout)['timg/__Combined.bntx'])]
        return blob, bf.bntx_textures(blob)

    def test_the_two_big_pictures_are_drawn_and_nothing_else_of_the_pictures_changes(self):
        controls = pl.prompt_controls('', 'position')
        new_pack, changed, drawn, notes, sizes = bp.make_pack(self.pack, controls, StubLetters())
        self.assertEqual(sorted(drawn), sorted([FRONT, TOP, SNN[1]]))
        blob, textures = self.textures(new_pack)
        for name in (FRONT, TOP, SNN[1]):
            self.assertEqual(bf.rg8_read(blob, textures[name]), art.PICTURES[name][1](controls, StubLetters(), bp.XBOX_TEXT))
        self.assertEqual(bf.rg8_read(blob, textures[SNN[0]]), (self.small_grey, self.small_alpha))
        self.assertTrue(any(n.startswith(SNN[0]) and 'stays' in n for n in notes))
        self.assertEqual(sorted(changed), sorted(NAMES))
        self.assertEqual(sizes[2], len(self.layout))
        old_blob, old_textures = self.textures(self.pack)
        for name in old_textures:     # every other texture, and every other byte of the pack, is the original
            if name not in drawn and name not in changed:
                self.assertEqual(blob[textures[name].offset:textures[name].offset + textures[name].image_size],
                                 old_blob[old_textures[name].offset:old_textures[name].offset + old_textures[name].image_size], name)
        before, after = bf.sarc_members(self.pack), bf.sarc_members(new_pack)
        for name in before:
            if name != 'Layout/Common.sblarc':
                self.assertEqual(self.pack[slice(*before[name])], new_pack[slice(*after[name])], name)

    def test_the_switch_pictures_can_be_kept(self):
        new_pack, changed, drawn, notes, _ = bp.make_pack(self.pack, pl.prompt_controls('', 'position'), StubLetters(), 'xbox', pictures=False)
        self.assertEqual(drawn, {})
        blob, textures = self.textures(new_pack)
        self.assertEqual(bf.rg8_read(blob, textures[FRONT]), (self.front_grey, self.front_alpha))
        self.assertEqual(sorted(changed), sorted(NAMES))

    @unittest.skipIf(mbi is None, 'needs fontTools (the PlayStation drawers come from make_button_icons.py)')
    def test_playstation_draws_its_own_pictures(self):
        controls = pl.prompt_controls('', 'position')
        new_pack, _, drawn, notes, _ = bp.make_pack(self.pack, controls, StubLetters(), 'playstation')
        self.assertEqual(sorted(drawn), sorted([FRONT, TOP, SNN[1]]))
        self.assertFalse([n for n in notes if 'stay the Switch' in n])
        blob, textures = self.textures(new_pack)
        for name in drawn:
            self.assertEqual(bf.rg8_read(blob, textures[name]), art.PICTURES[name][1](controls, StubLetters(), bp.PLAYSTATION_TEXT, style='playstation'))
            if name != SNN[1]:
                self.assertTrue(drawn[name] != art.PICTURES[name][1](controls, StubLetters(), bp.XBOX_TEXT), name)

    def test_a_picture_in_another_format_stops_the_tool(self):      # negative control
        bad = [(n, bf.R8_UNORM, 48, 48, 3, rows, None) for n, rows in self.keys.items()] + [(FRONT, bf.R8_UNORM, 576, 288, 4, self.front_grey, None)]
        layout = sarc({'timg/__Combined.bntx': rg8_bntx(bad)})
        pack = sarc({'Layout/Common.sblarc': yaz0_encode(layout)})
        with self.assertRaises(ValueError):
            bp.make_pack(pack, pl.prompt_controls('', 'position'), StubLetters(), 'xbox', pictures=True)


if __name__ == '__main__':
    unittest.main()
