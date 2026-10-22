"""Controller pictures drawn by our own code for the controller tips of Breath of the Wild (botw_prompts.py).

The game shows the Switch in handheld mode on its controller tips page (layout PaAllControllerTipsNN_00) as two pictures, a front view
(Nt_NNillust_00, 576x288) and the top edge (Nt_NNillust_01, 576x100), and labelled lines (callouts) end on its buttons. This
module draws an Xbox controller in the same two pictures. Nothing of the game is copied: the pictures are made of discs, rounded
rectangles and the letters of the Switch system font (the `Letters` of botw_prompts.py), and the only game facts used are the
places of the buttons, so the game's callout lines still end on a button of the same kind:
  front view   the left stick, the d-pad cross, the right stick, the four face buttons, and View and Menu in the places of the
               Switch's - and +; the face buttons keep the Xbox layout (Y top, X left, B right, A bottom) whatever the controls.txt;
  top edge     the LT, LB, RT and RB shoulder buttons in the places of the Switch's ZL, L, ZR and R.
The in-game hint boxes (PaMessageTipsDrcImageNN_00) show four 228x228 pieces of the Switch and ring the button of a hint: a left
and a right shoulder view (Nt_GamePadIllustSnn_00, _01) and the right and left front of the handheld (_02: face buttons, right
stick; _03: left stick, d-pad). Those are drawn again as the pieces of an Xbox controller with the buttons in the same places.
The grey of each pixel and its alpha are the two channels of the game's R8G8 textures.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import botw_formats as bf       # noqa: E402

FRONT, TOP = (576, 288), (576, 100)         # the size of the two pictures
# Where the Switch pictures have a button, in pixels of the picture: the game's callout lines end there.
FRONT_STICKS = {'lstick': (58.5, 88.5), 'rstick': (517.5, 151.5)}
FRONT_DPAD = (59.0, 152.0)                  # the four d-pad buttons sit at (59, 135) (41, 152) (59, 169) (76.5, 152): a cross here
FRONT_FACE = {'x': (517.0, 71.5), 'y': (499.5, 88.5), 'a': (535.0, 88.5), 'b': (517.0, 105.5)}     # Switch button -> place
FRONT_MENUS = {'minus': (81.5, 53.5), 'plus': (494.5, 53.5)}
TOP_SHOULDERS = {'zl': (9, 10, 70, 34), 'l': (9, 38, 70, 58), 'zr': (505, 10, 566, 34), 'r': (505, 38, 566, 58)}   # x0, y0, x1, y1

# The face buttons keep the controller's own layout wherever the four are drawn together: the pad button that sits in each
# Switch button's place (Xbox Y top, X left, B right, A bottom; Triangle, Square, Circle, Cross). A controls.txt changes only
# which action a button does, so it changes the prompts, not these pictures.
FACE_LAYOUT = {'x': 'y', 'y': 'x', 'a': 'b', 'b': 'a'}

PIECE = (228, 228)                          # the four hint pieces
PIECE_RIGHT = {'face': {'x': (112.5, 79.5), 'y': (91.0, 100.0), 'a': (134.0, 100.0), 'b': (112.5, 120.0)}, 'stick': (112.5, 175.5),
               'menu': ('plus', (85.5, 57.5))}      # Nt_GamePadIllustSnn_02: where the Switch picture has these buttons
PIECE_LEFT = {'stick': (114.5, 100.0), 'dpad': (114.5, 175.5), 'menu': ('minus', (141.5, 58.0))}                      # _03
PIECE_SHOULDERS = {'zl': (63, 69, 135, 104), 'l': (54, 110, 135, 131)}      # _00: the left shoulder (_01 is its mirror image)

BODY, RIM, DARK, EDGE, LIGHT = 193, 135, 55, 13, 175      # grey levels (the game's pictures use 193 for the light plastic and
#                                                           55 inside dark buttons, 13 around them)


def circle(cx, cy, r):
    n = max(24, int(r * 4))
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def rounded_rect(x0, y0, x1, y1, r):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    points = []
    for cx, cy, start in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(9):
            a = math.radians(start + 90 * i / 8)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def outline(start, *segments, steps=14):
    """A contour from a start point and segments: a point is a straight line to it, three points are a cubic curve."""
    points, last = [start], start
    for seg in segments:
        if isinstance(seg[0], (int, float)):
            points.append(seg); last = seg
            continue
        p1, p2, p3 = seg
        for i in range(1, steps + 1):
            t = i / steps; u = 1 - t
            points.append((u**3 * last[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                           u**3 * last[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
        last = p3
    return points


def mirror(points, width):
    """The shape of `points` flipped left to right in a picture `width` wide (the point order is kept winding-correct)."""
    return [(width - x, y) for x, y in reversed(points)]


def inset(points, d):
    """The contour moved `d` pixels inwards (a negative `d` outwards), corners mitred."""
    n = len(points)
    area = sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))
    sign = 1 if area > 0 else -1

    def normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]; length = math.hypot(dx, dy) or 1.0
        return (-dy * sign / length, dx * sign / length)
    out = []
    for i in range(n):
        n1, n2 = normal(points[i - 1], points[i]), normal(points[i], points[(i + 1) % n])
        k = d / max(0.25, 1 + n1[0] * n2[0] + n1[1] * n2[1])
        out.append((points[i][0] + (n1[0] + n2[0]) * k, points[i][1] + (n1[1] + n2[1]) * k))
    return out


class Canvas:
    """A picture of grey levels and alpha, drawn on by painting shapes one over another (alpha blending, edges anti-aliased)."""
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.grey = [[255.0] * width for _ in range(height)]
        self.alpha = [[0.0] * width for _ in range(height)]

    def paint(self, shapes, grey, opacity=1.0):
        """Paint the union of `shapes` (contours, all wound the same way) in `grey`."""
        points = [p for shape in shapes for p in shape]
        x0, x1 = max(0, math.floor(min(p[0] for p in points))), min(self.width, math.ceil(max(p[0] for p in points)))
        y0, y1 = max(0, math.floor(min(p[1] for p in points))), min(self.height, math.ceil(max(p[1] for p in points)))
        if x1 <= x0 or y1 <= y0:
            return
        cover = bf.coverage_rect([[(x - x0, y - y0) for x, y in shape] for shape in shapes], x1 - x0, y1 - y0)
        for dy, row in enumerate(cover):
            grey_row, alpha_row = self.grey[y0 + dy], self.alpha[y0 + dy]
            for dx, c in enumerate(row):
                if not c:
                    continue
                a = c / 255 * opacity
                below = alpha_row[x0 + dx] * (1 - a)
                alpha_row[x0 + dx] = a + below
                grey_row[x0 + dx] = (grey * a + grey_row[x0 + dx] * below) / (a + below)

    def rows(self):
        """(grey rows, alpha rows) as bytes, the grey of a fully transparent pixel 255 like the game's pictures."""
        alpha = [bytes(min(255, round(a * 255)) for a in row) for row in self.alpha]
        grey = [bytes(min(255, round(g)) if a else 255 for g, a in zip(grow, arow)) for grow, arow in zip(self.grey, alpha)]
        return grey, alpha


def label_shape(letters, label, cx, cy, cap, max_width):
    return letters.text(label, cap, cx, cy, max_width)


def dark_button(canvas, cx, cy, r, letters=None, label=None, cap=None):
    """A round dark button (the game's look: a black ring round a dark disc) with the letters `label` in light grey."""
    canvas.paint([circle(cx, cy, r)], EDGE)
    canvas.paint([circle(cx, cy, r - 1.7)], DARK)
    if label and letters:
        canvas.paint(label_shape(letters, label, cx, cy, cap or r * 0.8, 2 * r - 6), LIGHT)


def menu_glyph(canvas, cx, cy, size):
    """Three bars, as on the Xbox Menu button."""
    for dy in (-size * 0.32, 0, size * 0.32):
        canvas.paint([rounded_rect(cx - size * 0.45, cy + dy - size * 0.07, cx + size * 0.45, cy + dy + size * 0.07, size * 0.07)], LIGHT)


def view_glyph(canvas, cx, cy, size):
    """Two overlapping squares, as on the Xbox View button."""
    canvas.paint([rounded_rect(cx - size * 0.42, cy - size * 0.2, cx + size * 0.08, cy + size * 0.38, size * 0.05)], LIGHT)
    canvas.paint([rounded_rect(cx - size * 0.28, cy - size * 0.38, cx + size * 0.42, cy + size * 0.22, size * 0.05)], EDGE)
    canvas.paint([rounded_rect(cx - size * 0.22, cy - size * 0.32, cx + size * 0.36, cy + size * 0.16, size * 0.04)], LIGHT)


def center_button(canvas, cx, cy, kind, letters, text, k=1.0):
    """A small dark button for View, Menu or any other pad button (`kind`, a key of prompt_layout.prompt_controls)."""
    dark_button(canvas, cx, cy, 8.5 * k)
    if kind == 'menu':
        menu_glyph(canvas, cx, cy, 13 * k)
    elif kind == 'view':
        view_glyph(canvas, cx, cy, 13 * k)
    elif text.get(kind):
        canvas.paint(label_shape(letters, text[kind], cx, cy, 6.0 * k, 13 * k), LIGHT)


def stick(canvas, cx, cy, k=1.0):
    """A thumbstick (`k` times the size of the front view's): a shallow dish in the body, a black ring, a dark cap with a ring."""
    canvas.paint([circle(cx, cy, 27 * k)], RIM)
    canvas.paint([circle(cx, cy, 25.6 * k)], 178)
    canvas.paint([circle(cx, cy, 19 * k)], EDGE)
    canvas.paint([circle(cx, cy, 17.2 * k)], DARK)
    canvas.paint([circle(cx, cy, 12.5 * k)], 36)
    canvas.paint([circle(cx, cy, 11.2 * k)], DARK)


def dpad(canvas, cx, cy, k=1.0):
    """The d-pad cross (`k` times the size of the front view's); its arms end where the Switch's four d-pad buttons are."""
    canvas.paint([circle(cx, cy, 31 * k)], RIM)
    canvas.paint([circle(cx, cy, 29.6 * k)], 178)

    def cross(grow):
        return [rounded_rect(cx - 27 * k - grow, cy - 9.5 * k - grow, cx + 27.5 * k + grow, cy + 9.5 * k + grow, 4 * k + grow),
                rounded_rect(cx - 9.5 * k - grow, cy - 27 * k - grow, cx + 9.5 * k + grow, cy + 27 * k + grow, 4 * k + grow)]
    canvas.paint(cross(1.7 * k), EDGE)
    canvas.paint(cross(0), DARK)
    canvas.paint([circle(cx, cy, 6.5 * k)], 38)


FRONT_BODY = outline((288, 36), (112, 36), ((66, 36), (19, 66), (19, 116)), (19, 214), ((19, 246), (42, 262), (72, 262)), (112, 262),
                     ((146, 262), (164, 238), (182, 216)), ((196, 200), (212, 196), (232, 196)), (288, 196))
FRONT_BODY = FRONT_BODY + mirror(FRONT_BODY, FRONT[0])[1:-1]


def front_view(controls, letters, text):
    """The front view of an Xbox controller as (grey rows, alpha rows), 576x288 pixels. `controls` is
    prompt_layout.prompt_controls(); `text` names the pad buttons (botw_prompts.XBOX_TEXT); `letters` is a botw_prompts.Letters."""
    canvas = Canvas(*FRONT)
    canvas.paint([FRONT_BODY], RIM)
    canvas.paint([inset(FRONT_BODY, 3.2)], BODY)
    canvas.paint([circle(288, 74, 15)], RIM)                # the guide button
    canvas.paint([circle(288, 74, 13.3)], 226)
    canvas.paint([circle(288, 74, 8)], 200)
    for name, (cx, cy) in FRONT_STICKS.items():
        stick(canvas, cx, cy)
    dpad(canvas, *FRONT_DPAD)
    for slot, (cx, cy) in FRONT_FACE.items():
        dark_button(canvas, cx, cy, 10, letters, text.get(FACE_LAYOUT[slot]))
    for slot, (cx, cy) in FRONT_MENUS.items():
        center_button(canvas, cx, cy, controls.get(slot), letters, text)
    return canvas.rows()


def shoulder(left, x0, y0, x1, y1, trigger, width=TOP[0]):
    """The outline of a trigger (curved on its outer top corner) or a bumper (a rounded bar) in the box x0 y0 x1 y1."""
    if not trigger:
        return rounded_rect(x0, y0, x1, y1, 9)
    if left:
        return outline((x0, y1), ((x0 + 5, y1 - 13), (x0 + 15, y0), (x0 + 32, y0)), (x1 - 12, y0), ((x1 - 4, y0), (x1, y0 + 8), (x1, y0 + 16)), (x1, y1))
    return mirror(shoulder(True, width - x1, y0, width - x0, y1, True, width), width)


def top_view(controls, letters, text):
    """The top edge of an Xbox controller as (grey rows, alpha rows), 576x100 pixels: the triggers and bumpers in the places of
    the Switch's ZL, L, ZR and R."""
    canvas = Canvas(*TOP)
    body = outline((60, 44), (516, 44), ((545, 44), (566, 56), (566, 66)), ((566, 80), (552, 88), (536, 88)), (40, 88),
                   ((24, 88), (10, 80), (10, 66)), ((10, 56), (31, 44), (60, 44)))
    canvas.paint([body], RIM)
    canvas.paint([inset(body, 3)], BODY)
    canvas.paint([rounded_rect(96, 49, 480, 53, 2)], 208)            # a light edge along the top face
    canvas.paint([rounded_rect(262, 56, 314, 67, 5)], EDGE)         # the USB-C port
    canvas.paint([rounded_rect(264.5, 58.5, 311.5, 64.5, 3)], DARK)
    canvas.paint([circle(352, 61, 5)], EDGE)                         # the pairing button
    canvas.paint([circle(352, 61, 3.4)], DARK)
    for slot, (x0, y0, x1, y1) in TOP_SHOULDERS.items():
        trigger = slot in ('zl', 'zr')
        shape = shoulder(x0 < TOP[0] / 2, x0, y0, x1, y1, trigger)
        canvas.paint([shape], EDGE)
        canvas.paint([inset(shape, 1.7)], DARK)
        label = text.get(controls.get(slot))
        if label:
            canvas.paint(label_shape(letters, label, (x0 + x1) / 2, (y0 + y1) / 2 + (2 if trigger else 0), 9.0 if trigger else 8.0, x1 - x0 - 14), LIGHT)
    return canvas.rows()


def wing(left, controls, letters, text):
    """Nt_GamePadIllustSnn_02 (the right front: face buttons and right stick, the body cut at its left) or _03 (the left front:
    left stick and d-pad, cut at its right) as (grey rows, alpha rows), 228x228 pixels."""
    canvas = Canvas(*PIECE)
    right_wing = outline((-10, 24), (118, 24), ((146, 24), (162, 44), (162, 78)), (162, 240), (-10, 240))
    body = mirror(right_wing, PIECE[0]) if left else right_wing
    canvas.paint([body], RIM)
    canvas.paint([inset(body, 3.2)], BODY)
    k = 1.2
    if left:
        stick(canvas, *PIECE_LEFT['stick'], k)
        dpad(canvas, *PIECE_LEFT['dpad'], k)
        slot, place = PIECE_LEFT['menu']
    else:
        for slot, (cx, cy) in PIECE_RIGHT['face'].items():
            dark_button(canvas, cx, cy, 12, letters, text.get(FACE_LAYOUT[slot]))
        stick(canvas, *PIECE_RIGHT['stick'], k)
        slot, place = PIECE_RIGHT['menu']
    center_button(canvas, *place, controls.get(slot), letters, text, k)
    return canvas.rows()


def shoulders(left, controls, letters, text):
    """Nt_GamePadIllustSnn_00 (the left shoulder: LT and LB) or _01 (the right: RT and RB) as (grey rows, alpha rows), 228x228
    pixels: the shoulder buttons over the body of the controller, which is cut at the picture's inner edge."""
    canvas = Canvas(*PIECE)
    width = PIECE[0]
    band = outline((60, 124), (248, 124), (248, 176), (84, 176), ((68, 176), (52, 160), (50, 142)), ((50, 132), (54, 124), (60, 124)))
    band = band if left else mirror(band, width)
    canvas.paint([band], RIM)
    canvas.paint([inset(band, 3)], BODY)
    port = 168 if left else width - 168
    canvas.paint([rounded_rect(port - 21, 140, port + 21, 150, 5)], EDGE)            # the USB-C port
    canvas.paint([rounded_rect(port - 18.5, 142.5, port + 18.5, 147.5, 3)], DARK)
    for slot, (x0, y0, x1, y1) in PIECE_SHOULDERS.items():
        if not left:
            x0, x1 = width - x1, width - x0
        trigger = slot == 'zl'
        shape = shoulder(left, x0, y0, x1, y1, trigger)
        canvas.paint([shape], EDGE)
        canvas.paint([inset(shape, 2.2)], DARK)
        label = text.get(controls.get(slot if left else {'zl': 'zr', 'l': 'r'}[slot]))
        if label:
            canvas.paint(label_shape(letters, label, (x0 + x1) / 2, (y0 + y1) / 2 + (3 if trigger else 0), 11.0 if trigger else 10.0, x1 - x0 - 20), LIGHT)
    return canvas.rows()


# texture name -> ((width, height), draw(controls, letters, text)): the pictures that replace the game's, in Common.sblarc's BNTX
PICTURES = {'Nt_NNillust_00^f': (FRONT, front_view), 'Nt_NNillust_01^f': (TOP, top_view),
            'Nt_GamePadIllustSnn_00^f': (PIECE, lambda *a: shoulders(True, *a)), 'Nt_GamePadIllustSnn_01^f': (PIECE, lambda *a: shoulders(False, *a)),
            'Nt_GamePadIllustSnn_02^f': (PIECE, lambda *a: wing(False, *a)), 'Nt_GamePadIllustSnn_03^f': (PIECE, lambda *a: wing(True, *a))}
