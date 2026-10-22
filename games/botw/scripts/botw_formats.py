"""File formats the Breath of the Wild prompt tool reads and writes (botw_prompts.py): Yaz0 with a local re-encode, BNTX
textures (Switch block-linear R8) and the glyph rasterizer. Plain Python, no game data, no fontTools.

Yaz0 is the game's compression. A compressed archive is changed by decoding it, editing bytes in place and writing the stream
back with yaz0_splice(): only the stretch around the edit is encoded again (as literals), everything before and after it keeps
the original stream, so a few kilobytes of texture cost a few kilobytes, not a recompression of 33 MB.
"""
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'totk' / 'scripts'))   # the shared Yaz0 reader and SARC tools
from prepare_shader_seed import yaz0 as yaz0_decode     # noqa: E402,F401
from make_romfs_patches import sarc_members             # noqa: E402,F401
from sarc_repack import repack as sarc_replace          # noqa: E402,F401

REACH = 4096        # a Yaz0 back reference reaches this far back
YAZ0_HEADER = 16


# ---- Yaz0 -------------------------------------------------------------------------------------------------------------
def yaz0_groups(data):
    """(stream offset, output position) at the start of every group (a code byte and its eight items) of a Yaz0 stream, plus
    one entry for the end of the stream."""
    size = struct.unpack_from('>I', data, 4)[0]
    out, i, groups = 0, YAZ0_HEADER, []
    while out < size:
        groups.append((i, out))
        code = data[i]; i += 1
        for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
            if out >= size:
                break
            if code & bit:
                out += 1; i += 1
            else:
                length = data[i] >> 4; i += 2
                if length:
                    length += 2
                else:
                    length = data[i] + 0x12; i += 1
                out += length
    groups.append((i, size))
    return groups


def yaz0_literals(chunk):
    """The bytes of `chunk` as Yaz0 groups of eight literals."""
    out = bytearray()
    for i in range(0, len(chunk), 8):
        block = chunk[i:i + 8]
        out.append((0xFF00 >> len(block)) & 0xFF)
        out += block
    return bytes(out)


def yaz0_splice(data, decoded, edits):
    """The Yaz0 stream `data` (which decodes to `decoded`) with `edits` applied: a list of (offset, new bytes) that keep the
    size. The stream before the first group at or before an edit and after the first group that starts REACH bytes past it is
    kept as it was; the stretch between is written as literals. Decodes the result and checks it before returning it."""
    expected = bytearray(decoded)
    for offset, blob in edits:
        expected[offset:offset + len(blob)] = blob
    spans = []      # merged (lo, hi) of the edited bytes, edits closer than REACH together
    for lo, hi in sorted((o, o + len(b)) for o, b in edits):
        if spans and lo - spans[-1][1] <= REACH:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    groups = yaz0_groups(data)
    out, taken = bytearray(data[:YAZ0_HEADER]), 0       # `taken`: stream offset up to which the original is copied
    while spans:
        lo, hi = spans.pop(0)
        before = [g for g in groups if g[1] <= lo and g[0] >= taken]
        if not before:
            raise ValueError('an edit starts inside an earlier stretch')
        start = before[-1]
        while True:
            # The literals fill whole groups of eight, so the stretch must be a multiple of 8 bytes (or reach the stream's end).
            after = [g for g in groups if g[1] >= hi + REACH and ((g[1] - start[1]) % 8 == 0 or g is groups[-1])]
            end = after[0] if after else groups[-1]
            if spans and spans[0][0] < end[1]:     # the stretch runs over the next edit: it becomes part of this stretch
                hi = max(hi, spans.pop(0)[1])
                continue
            break
        out += data[taken if taken else YAZ0_HEADER:start[0]]
        out += yaz0_literals(bytes(expected[start[1]:end[1]]))
        taken = end[0]
    out += data[taken:]
    result = bytes(out)
    if yaz0_decode(result) != bytes(expected):
        raise ValueError('the re-encoded Yaz0 stream does not decode to the intended bytes')
    return result


# ---- BNTX -------------------------------------------------------------------------------------------------------------
R8_UNORM = 0x201
TILE_BLOCK_LINEAR = 0


class Texture:
    """One BRTI of a BNTX: where its pixels are and how they are laid out."""
    def __init__(self, bntx, brti):
        (self.tile_mode, _, self.mips, _, self.format, _, self.width, self.height, self.depth, self.layers,
         layout) = struct.unpack_from('<HHHIIIIIIII', bntx, brti + 0x12)
        self.block_height = 1 << (layout & 7)
        self.image_size = struct.unpack_from('<I', bntx, brti + 0x50)[0]
        name = struct.unpack_from('<Q', bntx, brti + 0x60)[0]
        self.name = bntx[name + 2:name + 2 + struct.unpack_from('<H', bntx, name)[0]].decode()
        pointers = struct.unpack_from('<Q', bntx, brti + 0x70)[0]
        self.offset = struct.unpack_from('<Q', bntx, pointers)[0]


def bntx_textures(bntx):
    """name -> Texture for every texture of a BNTX."""
    if bntx[:4] != b'BNTX':
        raise ValueError('not a BNTX')
    return {t.name: t for t in (Texture(bntx, m.start()) for m in re.finditer(b'BRTI', bntx))}


def r8_address(x, y, width, block_height):
    """Byte offset of pixel (x, y) in a one-byte-per-pixel block-linear (GOB) texture."""
    gobs_wide = (width + 63) // 64
    gob = (y // (8 * block_height)) * 512 * block_height * gobs_wide + (x // 64) * 512 * block_height + (y % (8 * block_height) // 8) * 512
    return gob + ((x % 64) // 32) * 256 + ((y % 8) // 2) * 64 + ((x % 32) // 16) * 32 + (y % 2) * 16 + (x % 16)


def check_r8(texture):
    if (texture.format, texture.tile_mode, texture.mips, texture.layers, texture.depth) != (R8_UNORM, TILE_BLOCK_LINEAR, 1, 1, 1):
        raise ValueError(f'{texture.name}: expected a plain single-mip R8 block-linear texture, got format 0x{texture.format:x}, '
                         f'tile mode {texture.tile_mode}, {texture.mips} mips, {texture.layers} layers')


def r8_read(bntx, texture):
    """The pixels of an R8 texture as rows of bytes (list of bytes, top row first)."""
    check_r8(texture)
    base, w = texture.offset, texture.width
    return [bytes(bntx[base + r8_address(x, y, w, texture.block_height)] for x in range(w)) for y in range(texture.height)]


def r8_image(bntx, texture, rows):
    """The texture's image bytes (image_size of them, from the original) with the pixels replaced by `rows`; the padding bytes
    stay as they were."""
    check_r8(texture)
    if len(rows) != texture.height or any(len(r) != texture.width for r in rows):
        raise ValueError(f'{texture.name}: {texture.width}x{texture.height} pixels expected')
    out = bytearray(bntx[texture.offset:texture.offset + texture.image_size])
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            out[r8_address(x, y, texture.width, texture.block_height)] = value
    return bytes(out)


# ---- rasterizer -------------------------------------------------------------------------------------------------------
def flatten(operations, steps=12):
    """Contours (lists of points) from fontTools pen operations [(name, args), ...]; curves become `steps` line pieces."""
    contours, current, last = [], [], None
    for name, args in operations:
        if name == 'moveTo':
            current, last = [args[0]], args[0]
        elif name == 'lineTo':
            current.append(args[0]); last = args[0]
        elif name == 'curveTo':
            p0, (p1, p2, p3) = last, args[:3]
            for i in range(1, steps + 1):
                t = i / steps; u = 1 - t
                current.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                                u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
            last = p3
        elif name == 'qCurveTo':
            points = list(args)
            if points[-1] is None:
                raise ValueError('a quadratic contour without an on-curve point is not supported')
            pending, p0 = points[:-1], last
            while pending:      # implied on-curve points between consecutive off-curve points
                c = pending.pop(0)
                end = points[-1] if not pending else ((c[0] + pending[0][0]) / 2, (c[1] + pending[0][1]) / 2)
                for i in range(1, steps + 1):
                    t = i / steps; u = 1 - t
                    current.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * end[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * end[1]))
                p0 = end
            last = points[-1]
        elif name in ('closePath', 'endPath'):
            if current:
                contours.append(current)
            current = []
    return contours


def coverage(contours, size, oversample=8):
    """Coverage 0-255 of a shape over a size x size picture. `contours` are in pixels, x right and y down (nonzero winding: a
    contour wound the other way cuts a hole). Returns rows of bytes, top row first."""
    edges = []
    for contour in contours:
        for (ax, ay), (bx, by) in zip(contour, contour[1:] + contour[:1]):
            if ay != by:
                edges.append((ax, ay, bx, by))
    n = oversample
    rows = []
    for py in range(size):
        hits = [0] * (size * n)
        for sy in range(n):
            y = py + (sy + 0.5) / n
            crossings = sorted((ax + (y - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1)
                               for ax, ay, bx, by in edges if (ay <= y < by) or (by <= y < ay))
            winding, previous = 0, 0.0
            for x, direction in crossings:
                if winding:
                    for sx in range(max(0, round(previous * n)), min(size * n, round(x * n))):
                        hits[sx] += 1
                winding += direction; previous = x
        rows.append(bytes(min(255, round(255 * sum(hits[px * n:(px + 1) * n]) / (n * n))) for px in range(size)))
    return rows


def enclosed_marks(rows, window=(8, 8, 40, 40), dark=0xE0, largest=400):
    """The dark marks (a letter cut out of a white shape): connected groups of pixels below `dark` that lie inside `window`
    (x0, y0, x1, y1) and have at most `largest` pixels. Returns a list of pixel-coordinate sets; the outline of a shape is
    longer than `largest` and stays out."""
    height, width = len(rows), len(rows[0])
    seen, marks = set(), []
    for y in range(height):
        for x in range(width):
            if rows[y][x] >= dark or (x, y) in seen:
                continue
            group, todo = set(), [(x, y)]
            while todo:
                px, py = todo.pop()
                if (px, py) in group or not (0 <= px < width and 0 <= py < height) or rows[py][px] >= dark:
                    continue
                group.add((px, py))
                todo += [(px + dx, py + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
            seen |= group
            xs, ys = [p[0] for p in group], [p[1] for p in group]
            if len(group) <= largest and min(xs) >= window[0] and min(ys) >= window[1] and max(xs) < window[2] and max(ys) < window[3]:
                marks.append(group)
    return marks
