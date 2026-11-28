"""Read from the sources (PR #1602): Breath of the Wild 1.9.0's ambient occlusion switch (the AO patch's helper returns
"nothing occluded" while c1[3].w bit 0 is set, before any other use of its parameters; dm_gfx_fx_available opens it on
1.9.0 only), its shadows switch (P9: the comparison samplers only, an Off choice in the shadow row), the grass and volumetric fog keys of 1.9.0 and 1.6.0 and its effects page (runtime/botw_effects.h: included on 1.9.0 only, the rows in Tears of the
Kingdom 1.4.3's order, graphics-effects.txt written and read as on 1.4.3, Reset turning the switches back on).
Read, not run: the BotW build and the game run are in the story."""
from pathlib import Path
import re
import unittest

TOTK = Path(__file__).resolve().parents[1] / 'games/totk'
NVN = TOTK / 'third_party/d2r_gfx/gfx/nvn'
SSAO = (NVN / 'totk_ssao.h').read_text()
FX = (NVN / 'totk_fx.h').read_text()
PANEL = (TOTK / 'runtime/settings_game.h').read_text()   # the game's part of the shared panel (shared/runtime/settings_panel.h)
PAGE = (TOTK / 'runtime/botw_effects.h').read_text()


def helper(name):
    """The MSL text of one helper string (its C string literals joined)."""
    body = re.search(rf'static const char {name}\[\] =\n(.*?);\n', SSAO, re.S)[1]
    return ''.join(re.findall(r'^\s*"(.*)\\n"', body, re.M))


def branch(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


class AoSwitch(unittest.TestCase):
    def test_the_botw_helper_returns_unoccluded_first_when_bit_0_is_set(self):
        body = helper('k_ssao_helper_botw').split('static inline float totk_ssao_botw(')[1]
        switch = 'if (fmod(p.w, 2.0) >= 0.5) return 1.0;'
        self.assertEqual(body.count(switch), 1)
        after_nan = body.index('p = select(p, float4(0.0), isnan(p));') + len('p = select(p, float4(0.0), isnan(p));')
        self.assertEqual(body[after_nan:].lstrip().index(switch), 0)        # the first statement after the NaN guard
        self.assertLess(body.index(switch), body.index('if (all(p == float4(0.0))) return stock;'))
        self.assertIn('if (fmod(p.w, 2.0) >= 0.5)', helper('k_ssao_helper_totk'))   # TOTK's own, unchanged

    def test_ao_on_both_botw_and_the_switches_with_keys(self):
        avail = branch(FX, 'int dm_gfx_fx_available(int i) {', '\n}\n')
        botw190 = branch(avail, '#if defined(GAME_BOTW190)', '#elif')
        botw160 = branch(avail, '#elif defined(GAME_BOTW)', '#else')   # 1.6.0: its graphics layer has GAME_BOTW alone
        for b in (botw190, botw160):
            self.assertIn('if (i == FX_AO) return ssao_on();', b)
            self.assertIn('return i > FX_SHADOW && i < FX_N && k_fx[i].fs[0];', b)
        self.assertIn('if (i == FX_SHADOW) return 1;', botw190)   # 1.9.0: the comparison samplers (PR #1753); 1.6.0 has no shadow switch
        self.assertNotIn('FX_SHADOW) return 1;', botw160)
        self.assertIn('{"ao", ', FX)
        self.assertIn('if (i == FX_AO) ssao_set_off(!on);', FX)              # the switch writes the helper's w

    def test_grass_is_its_own_fragment_vertex_pair_on_190(self):
        table = branch(FX, '#if defined(GAME_BOTW190)\n', '#elif defined(GAME_BOTW)\n')
        self.assertIn('{"grass", "草", "Grass", {"d0cead9357ae8c7b/9a3f53268b7c3433"}, {0}},', table)   # P8: the pair seen
        totk = branch(FX, '#else\nstatic const fx_effect', '#endif\nenum { FX_N')
        self.assertIn('{"grass", "草", "Grass", {"33099f8c133950f5"}, {0}},', totk)   # TOTK's unchanged
        self.assertEqual(FX.count('{"grass", '), 3)
        self.assertIn('FX_GRASS = 2', FX)
        for t in (table, totk):
            ids = re.findall(r'\{"(\w+)", "', t)
            self.assertEqual(ids[:3], ['ao', 'shadow', 'grass'])   # FX_GRASS = 2 names it, in every table

    def test_shadows_off_is_the_samplers_alone_on_botw(self):
        body = branch(FX, 'if (i == FX_SHADOW) {', '\n    }\n')
        self.assertIn('#ifndef GAME_BOTW\n        ssao_set_shadow_off(!on);', body)   # TOTK's pre-pass bit, not BotW's
        self.assertIn('dm_gfx_shadow_samplers_off(!on);', body.split('#endif')[1])


class Keys(unittest.TestCase):
    """Each version has its own table (grass: 1.9.0 the blades' fragment / vertex pair, 1.6.0 tall blades + short tufts; volumetric fog: the 1/8 and 1/16 slices; fog has
    no draw of its own); TOTK keeps its table in the #else branch."""
    def table(self, start, end):
        return dict(re.findall(r'\{"(\w+)", "[^"]*", "[^"]*", \{([^}]*)\}', branch(FX, start, end)))

    def test_tables(self):
        b190 = self.table('#if defined(GAME_BOTW190)\n', '#elif defined(GAME_BOTW)\n')
        b160 = self.table('#elif defined(GAME_BOTW)\nstatic const fx_effect', '#else\nstatic const fx_effect')
        totk = self.table('#else\nstatic const fx_effect', '#endif\nenum { FX_N')
        for t in (b190, b160, totk):
            self.assertEqual(list(t), ['ao', 'shadow', 'grass', 'vfog', 'fog'])   # same ids and order: graphics-effects.txt
        self.assertEqual(b190['grass'], '"d0cead9357ae8c7b/9a3f53268b7c3433"')   # upstream's pair (PR #1758)
        self.assertEqual(b160['grass'], '"757497916e7cd6c6", "e0b7871f7d0b84c4"')
        self.assertEqual(b190['vfog'], b160['vfog'])
        self.assertEqual(b190['vfog'], '"7a8bb7784aec9536"')
        self.assertEqual((b190['fog'], b160['fog']), ('0', '0'))
        self.assertEqual(totk['vfog'], '"3231347c1ef88f06"')


class Page(unittest.TestCase):
    def test_included_on_190_only(self):
        self.assertEqual(PANEL.count('#include "botw_effects.h"'), 1)
        before = PANEL[:PANEL.index('#include "botw_effects.h"')]
        self.assertTrue(before.rstrip().endswith('#ifdef SETTINGS_BOTW_PAGE'))   # 1.9.0 or 1.6.0
        self.assertIn('#elif defined(SETTINGS_BOTW_PAGE)\nstatic void settings_effects_restore(void);', PANEL)
        reset = branch(PANEL, 'static void settings_effects_reset(void) {', '\n}\n')
        self.assertIn('settings_botw_effects_reset();', branch(reset, '#ifdef SETTINGS_BOTW_PAGE', '#endif'))

    def test_rows_in_the_order_of_143(self):
        rows = branch(PAGE, 'settings_effect_rows(void) {', '\n}\n')
        titles = re.findall(r'settings_text\("[^"]*","([^"]+)"\)|(settings_botw_(?:fov|shadow|dof)_row\(\)|settings_botw_distance_row\([01]\))', rows)
        self.assertEqual([a or b for a, b in titles][1:12],                # [0] is "Not in this version"
                         ['settings_botw_fov_row()', 'settings_botw_distance_row(1)', 'settings_botw_distance_row(0)',
                          'settings_botw_shadow_row()', 'Anti-aliasing method', 'Anti-aliasing quality', 'MetalFX replaces it',
                          'Off', 'Original', 'settings_botw_dof_row()', 'Sharpening'])   # 1.4.3's anti-aliasing rows in 1.4.3's place
        self.assertIn('if(!strcmp(dm_gfx_fx_id(i),"shadow"))continue;', rows)
        self.assertIn('if(!dm_gfx_fx_available(i)){[rows addObject:settings_fixed(t,none)];continue;}', rows)

    def test_the_view_rows_are_working_rows(self):
        dist = branch(PAGE, 'static TOTKSettingRow *settings_botw_distance_row(int lod) {', '\n}\n')
        self.assertIn('settings_text("模型细节距离（LOD）","Model detail distance (LOD)"):settings_text("渲染距离","Render distance")', dist)
        self.assertIn('if(lod?!runtime_lod_available():!runtime_far_available())return settings_fixed(t,settings_text("这个版本没有","Not in this version"));', dist)
        self.assertIn('if(lod?runtime_lod_scale_locked():runtime_far_scale_locked())return settings_fixed(t,text(now));', dist)
        self.assertIn('@[@100,@200,@400,@1000,@2000,@5000,@10000,@-1]:@[@1000,@1250,@1500]', dist)   # 1.4.3's LOD values; 1x .. 1.5x (2x crashed the world load)
        self.assertIn('settings_text("始终最高精度","Always the finest")', dist)
        self.assertIn('runtime_setting_save("graphics-lod.txt","max","model detail distance")', dist)
        self.assertIn('settings_save_int("graphics-lod.txt",v,"model detail distance")', dist)
        self.assertIn('snprintf(word,sizeof word,"%g",v/10.0);runtime_far_scale_set(v/1000.0);runtime_setting_save("graphics-far.txt",word,"render distance")', dist)
        dof = branch(PAGE, 'static TOTKSettingRow *settings_botw_dof_row(void) {', '\n}\n')
        self.assertIn('if(!runtime_dof_available())return settings_fixed(t,settings_text("这个版本没有","Not in this version"));', dof)
        self.assertIn('runtime_setting_save("graphics-dof.txt",v?"on":"off","depth of field")', dof)
        self.assertNotIn('settings_fixed(settings_text("景深"', PAGE)

    def test_the_view_files_are_restored_unless_locked_and_reset(self):
        restore = branch(PAGE, 'static void settings_effects_restore(void) {', '\n}\n')
        self.assertIn('if(!runtime_lod_scale_locked()){', restore)
        self.assertIn('!strncmp(word,"max",3))runtime_lod_scale_set(SETTINGS_LOD_MAX)', restore)
        self.assertIn('settings_read_number("graphics-lod.txt",100,10000,&pct))runtime_lod_scale_set(pct/100)', restore)
        self.assertIn('if(!runtime_far_scale_locked()&&settings_read_number("graphics-far.txt",10,150,&pct))runtime_far_scale_set(pct/100);', restore)
        self.assertIn('runtime_setting_word("graphics-dof.txt",word,sizeof word)&&!strncmp(word,"off",3))runtime_dof_set(0);', restore)
        reset = branch(PAGE, 'static void settings_botw_effects_reset(void) {', '\n}\n')
        for call in ('runtime_lod_available()&&!runtime_lod_scale_locked()){runtime_lod_scale_set(1);runtime_setting_remove("graphics-lod.txt");}',
                     'runtime_far_available()&&!runtime_far_scale_locked()){runtime_far_scale_set(1);runtime_setting_remove("graphics-far.txt");}',
                     'runtime_dof_available()){runtime_dof_set(1);runtime_setting_remove("graphics-dof.txt");}'):
            self.assertIn(call, reset)

    def test_the_file_is_143s(self):
        self.assertEqual(PAGE.count('runtime_setting_save("graphics-effects.txt",n?line:"none","effects");'), 1)
        self.assertIn('if(!strcmp(w,dm_gfx_fx_id(i)))dm_gfx_fx_set(i,0);', PAGE)
        reset = branch(PAGE, 'static void settings_botw_effects_reset(void) {', '\n}\n')
        self.assertIn('if(!dm_gfx_fx_on(i))dm_gfx_fx_set(i,1);', reset)
        for name in ('graphics-effects.txt', 'graphics-fxaa.txt', 'graphics-aa-method.txt', 'graphics-fxaa-quality.txt',
                     'graphics-smaa-quality.txt', 'graphics-sharpen.txt'):
            self.assertIn(f'"{name}"', reset)
        self.assertIn('runtime_setting_remove(*f);', reset)

    def test_the_shadow_row_offers_off_where_the_switch_acts(self):
        row = branch(PANEL, 'static TOTKSettingRow *settings_botw_shadow_row(void) {', '\n}\n')
        self.assertIn('return settings_botw190_shadow_row(t);', branch(row, '#else', '#endif'))
        self.assertIn('Not in this version', branch(row, '#ifndef SETTINGS_BOTW_PAGE', '#else'))   # the other games: greyed (1.6.0 has the page: no switch there, the sizes only)
        own = branch(PAGE, 'static TOTKSettingRow *settings_botw190_shadow_row(NSString *t) {', '\n}\n')
        plain, with_off = own.split('if(fx<0||!dm_gfx_fx_available(fx))')[1].split('\n return settings_row(', 1)
        self.assertIn('@[@0,@2048]', plain); self.assertNotIn('@-1', plain)   # without the switch: the sizes only
        self.assertIn('@[@-1,@0,@2048]', with_off)
        self.assertIn('^int{return dm_gfx_fx_on(fx)?runtime_shadow_res_saved():-1;}', with_off)
        self.assertIn('^(int v){dm_gfx_fx_set(fx,v>=0);if(v>=0)runtime_shadow_res_save(v);settings_effects_save();}', with_off)
        self.assertIn('"Off (no shadows, at once)"', with_off)

    def test_within_300_lines(self):
        self.assertLessEqual(len(PAGE.splitlines()), 300)


if __name__ == '__main__':
    unittest.main()
