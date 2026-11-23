"""Breath of the Wild 1.9.0's and 1.6.0's model detail distance, render
distance and depth of field (games/totk/runtime/botw_view_patches.h) without the game: the files' and environment's
values, the stubs' words and the patches on a fake main, with each stub run by a small interpreter (tests/test_botw_view_patches.c), and the
install in game_patches_botw.c read from the source (the Mac only, never stops the game, its room and data slots apart from
the other users of the padding and the page)."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'games/totk/runtime'
SOURCE = (RUNTIME / 'game_patches_botw.c').read_text()
HEADER = (RUNTIME / 'botw_view_patches.h').read_text()


def function(text, head):
    start = text.index(head)
    return text[start:text.index('\n}\n', start)]


class BotwViewPatches(unittest.TestCase):
    def test_view_patches(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('A C compiler is required')
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'botw_view_patches'
            build = subprocess.run(
                [compiler, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-ffp-contract=off',
                 str(ROOT / 'tests/test_botw_view_patches.c'), '-o', str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('botw view patches ok', run.stdout)

    def test_the_runtime_calls_it_on_both_versions_and_the_mac(self):
        self.assertIn('#include "botw_view_patches.h"', SOURCE)
        body = function(SOURCE, 'void runtime_game_patches(Module *m) {')
        self.assertIn('    patch_view(m);', body)
        self.assertLess(body.index('    patch_fov(m);'), body.index('patch_view(m);'))
        patch = function(SOURCE, 'static void patch_view(Module *m) {')
        self.assertEqual(patch.split('\n')[1].strip(), 'if (ios_code()) return;')

    def test_the_1_6_0_tables_and_the_live_write(self):
        self.assertIn('#ifdef GAME_BOTW160\n#define VIEW_LOD_PATCH view_lod160\n#define VIEW_LOD_APPLY view_lod160_apply\n'
                      '#define VIEW_DISP_PATCH view_far160\n#define VIEW_DISP_APPLY view_far160_apply\n'
                      '#define VIEW_DOF_PATCH view_dof160\n#define VIEW_DOF_APPLY view_dof160_apply\n#else\n'
                      '#define VIEW_LOD_PATCH view_lod\n#define VIEW_LOD_APPLY view_lod_apply\n#define VIEW_DISP_PATCH view_disp\n', SOURCE)
        patch = function(SOURCE, 'static void patch_view(Module *m) {')
        self.assertNotIn('g_lod_refused = 1', patch)   # 1.6.0 has the model detail distance: the row is available when the patch is in
        self.assertIn('VIEW_LOD_APPLY(m->base, base, m->text_end, data, m->size, g_lod_k, &found)', patch)
        # 1.6.0's render distance is code only (getTraverseDist's exit and the null paths read the page): the live change is
        # the slot's word alone, as on 1.9.0, and nothing writes the game's ActorDebug any more
        setter = function(SOURCE, 'void runtime_far_scale_set(double k) {')
        self.assertIn('if (g_disp_slot) atomic_store(g_disp_slot, bits);', setter)
        for text in ('view_actor_debug_set', 'VIEW_ACTOR_DEBUG'):
            self.assertNotIn(text, SOURCE + HEADER)
        self.assertIn('#define VIEW_TRAV160_SITE 0x1562038u', HEADER)

    def test_a_mismatch_never_stops_the_game(self):
        start = SOURCE.index('static _Atomic uint32_t *g_lod_slot')
        code = SOURCE[start:SOURCE.index('void runtime_game_patches(Module *m) {', start)] + HEADER
        for name in ('runtime_fail', 'adrp_reg', 'branch_to', 'put_word', 'abort'):
            self.assertNotIn(name + '(', code)
        self.assertIn('*refused = 1;', code)   # the row goes unavailable

    def test_the_slot_is_written_before_the_branch(self):
        apply = function(HEADER, 'static inline int view_apply(')
        order = [apply.index(s) for s in ('memcpy(main + data + p->slot, &slot_word, 4);',
                                          'memcpy(main + stub, w, 4 * p->words);', 'memcpy(main + p->site, &jump, 4);')]
        self.assertEqual(order, sorted(order))   # the word, the stub, then the branch: no call reads an empty word

    def test_env_files_and_log_lines(self):
        for text in ('"TOTK_LOD_SCALE"', '"TOTK_FAR_SCALE"', '[botw] lodscale: ', '[botw] farscale: ', '[botw] depth of field %s'):
            self.assertIn(text, SOURCE)
        for name in ('runtime_lod_scale', 'runtime_lod_scale_set', 'runtime_lod_scale_locked', 'runtime_lod_available',
                     'runtime_far_scale', 'runtime_far_scale_set', 'runtime_far_scale_locked', 'runtime_far_available',
                     'runtime_dof_set', 'runtime_dof_on', 'runtime_dof_available'):
            self.assertRegex(SOURCE, r'\b' + name + r'\(', name)
            self.assertRegex((RUNTIME / 'settings_panel.h').read_text() + (RUNTIME / 'settings_game.h').read_text(), r'\b' + name + r'\(', name)   # the shared panel, and the game's part of it

    def test_the_stubs_keep_apart_from_the_other_padding_users(self):
        # (offset, length) in the 16-byte aligned padding after the text end, on 1.9.0: the FOV stub, the camera blend, ours
        fov = (int(re.search(r'#else\s*#define FOV_SITES 1.*?#define FOV_STUB_SKIP (\d+)u', (RUNTIME / 'botw_fov.h').read_text(), re.S)[1]), 4 * 11)   # the 1.9.0 branch of botw_fov.h
        blend = int(re.search(r'patch_blend_first_step\(m, \(\(m->text_end \+ 15\) & ~15ULL\) \+ (\d+)\);\s*/\* after the FOV stub', SOURCE)[1])
        arrow = int(re.search(r'patch_arrow_gravity\(m, page, \(\(m->text_end \+ 15\) & ~15ULL\) \+ (\d+)\);', SOURCE)[1])
        vr_camera = int(re.search(r'stub=\(\(m->text_end\+15\)&~15ull\)\+(\d+);', (RUNTIME / 'botw_vr_camera.h').read_text())[1])
        vr_camera_bytes = 4 * len(re.search(r'uint32_t code\[\]=\{(.*?)\};', (RUNTIME / 'botw_vr_camera.h').read_text(), re.S)[1].replace('\n', '').split(','))
        cave_at = int(re.search(r'CAVE_AT = (0x[0-9a-f]+)', SOURCE)[1], 0)
        cave_bytes = 4 * int(re.search(r'g_sensor_cave\[(\d+)\]', SOURCE)[1])
        heap = int(re.search(r'\+ (0x[0-9a-f]+) \+ slot\*16', (RUNTIME / 'botw_heap_patch.h').read_text())[1], 0)
        self.assertTrue(cave_at < heap + 12 and cave_at + cave_bytes > heap)   # the caves overlap the 64-bit heap veneers (native 5K or HD heaps only): the one allowed overlap, noted in botw_view_patches.h
        spans = [fov, (blend, 4 * 6), (arrow, 4 * 6), (cave_at, cave_bytes)]   # + the sensor caves; the VR camera veneer's room is taken by the model detail distance stub (below)
        for name, words in (('lod', 4), ('disp', 9), ('dof', 6)):
            skip = int(re.search(rf'view_{name}_guard\[\].*?\}};\n', HEADER, re.S) and
                       re.search(rf'static const view_patch view_{name} = \{{[^;]*?\b0x[0-9a-f]+u, (-?\d+), {words},', HEADER)[1])
            if name == 'lod':
                self.assertTrue(vr_camera <= skip and skip + 4 * words <= vr_camera + vr_camera_bytes)   # in the VR camera veneer's room: the one allowed overlap besides the heap veneers
            else:
                spans.append((skip, 4 * words))
        spans.sort()
        for (a, n), (b, _) in zip(spans, spans[1:]):
            self.assertLessEqual(a + n, b, spans)
        self.assertLessEqual(spans[-1][0] + spans[-1][1], 0x390, spans)   # inside the page after the aligned text end (main+0x15a1c70 .. 0x15a2000)
        # 1.6.0: the sound-link veneer (4 words at +0), the camera blend (6 words, after it), ours (the 32 LOD stubs from +192,
        # then the depth of field's and the render distance's)
        self.assertIn('const uint64_t stub = (m->text_end + 15) & ~15ULL;', SOURCE[SOURCE.index('static void patch_slink_rate('):])
        blend160 = int(re.search(r'patch_blend_first_step\(m, \(\(m->text_end \+ 15\) & ~15ULL\) \+ (\d+)\);\s*/\* after the sound-link veneer \(4 words\)', SOURCE)[1])
        fov160 = int(re.search(r'#define FOV_SITES 3.*?#define FOV_STUB_SKIP (\d+)u', (RUNTIME / 'botw_fov.h').read_text(), re.S)[1])
        spans = [(0, 16), (blend160, 4 * 6), (fov160, 4 * 11 * 3)]   # the veneer, the blend, the 3 FOV stubs (botw_fov.h)
        self.assertIn('#define VIEW_FAR160_WORDS (VIEW_FAR160_EXIT + 3u * VIEW_FAR160_NULLS)', HEADER)
        far160 = int(re.search(r'#define VIEW_FAR160_EXIT (\d+)u', HEADER)[1]) + 3 * int(re.search(r'#define VIEW_FAR160_NULLS (\d+)u', HEADER)[1])
        for name, words in (('far160', 'VIEW_FAR160_WORDS'), ('dof160', '6')):
            skip = int(re.search(rf'static const view_patch view_{name} = \{{[^;]*?(?:\b0x[0-9a-f]+u|VIEW_TRAV160_SITE), (\d+), {words},', HEADER)[1])
            spans.append((skip, 4 * (far160 if name == 'far160' else int(words))))
        spans.append((int(re.search(r'static const view_patch view_lod160 = \{[^;]*?\b0x[0-9a-f]+u, (\d+), VIEW_LOD160_WORDS,', HEADER)[1]), 4 * 6 * 32))   # 32 stubs of 6 words
        spans.sort()
        for (a, n), (b, _) in zip(spans, spans[1:]):
            self.assertLessEqual(a + n, b, spans)
        self.assertLessEqual(spans[-1][0] + spans[-1][1], 0x450, spans)   # inside the page after the aligned text end (main+0x2129bb0 .. 0x212a000)
        # the data page: every other slot the BOTW runtime uses sits below +0x50
        used = [int(n, 0) for n in re.findall(r'\bpage \+ (0x[0-9a-f]+|\d+)\)', SOURCE)]
        self.assertTrue(used and max(used) < 0x50, used)   # +8 the pad tick, +16 the output size, +0x20 the aspect
        # the arrow log's 8 bytes (+0x58) and the view slots (4 bytes each) keep apart, and the FOV's two words sit below both
        arrow_slot = int(re.search(r'ARROW_SLOT = (0x[0-9a-f]+)', SOURCE)[1], 0)
        slots = sorted(int(re.search(rf'#define VIEW_{n}_SLOT (0x[0-9a-f]+)u', HEADER)[1], 0) for n in ('LOD', 'DISP', 'DOF'))
        self.assertGreaterEqual(arrow_slot, 0x50)
        self.assertLessEqual(arrow_slot + 8, slots[0], (arrow_slot, slots))
        self.assertEqual(len(set(slots)), 3)


if __name__ == '__main__':
    unittest.main()
