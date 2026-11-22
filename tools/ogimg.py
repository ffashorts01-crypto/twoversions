# -*- coding: utf-8 -*-
"""Social share images (1200x630 PNG) for cards: site name/logo, title, date, the sides' short versions, divergence badge.
No photos or external images; font: Noto Sans (SIL OFL), bundled in tools/fonts/. Pure Pillow, ~30 ms per image."""
import os, io, hashlib, json
from PIL import Image, ImageDraw, ImageFont
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, 'fonts', 'NotoSans-Variable.ttf')
VERSION = 'og2'
W, H = 1200, 630
BG = (31, 45, 61); FG = (255, 255, 255); MUT = (184, 196, 210)
SIDE = {'ru': (232, 166, 76), 'ua': (76, 192, 204), 'ind': (215, 140, 195)}
DIVC = {'agree': (120, 200, 140), 'details': (240, 190, 90), 'contradict': (240, 110, 100)}
_F = {}
def font(size, weight=400):
    k = (size, weight)
    if k not in _F:
        f = ImageFont.truetype(FONT, size)
        try: f.set_variation_by_axes([weight, 100])
        except Exception: pass
        _F[k] = f
    return _F[k]
def wrap(d, text, f, width, max_lines):
    words = (text or '').split(); lines = []; cur = ''
    i = 0
    while i < len(words):
        t = (cur + ' ' + words[i]).strip()
        if d.textlength(t, font=f) <= width: cur = t; i += 1; continue
        if not cur: cur = words[i]; i += 1
        lines.append(cur); cur = ''
        if len(lines) == max_lines: break
    if cur and len(lines) < max_lines: lines.append(cur); cur = ''
    if i < len(words) or cur:                         # truncated: clean ellipsis on the last line
        last = lines[-1]
        while last and d.textlength(last + '…', font=f) > width: last = last.rsplit(' ', 1)[0] if ' ' in last else last[:-1]
        lines[-1] = last.rstrip(' ,;:—–-') + '…'
    return lines
def spec_hash(spec): return hashlib.sha1((VERSION + json.dumps(spec, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:12]
def render(spec):
    """spec: site, domain, date, title, div (label text or ''), div_level, sides: [(key, label, text)]"""
    im = Image.new('RGB', (W, H), BG); d = ImageDraw.Draw(im)
    # logo + site name
    x0, y0 = 60, 44
    d.rounded_rectangle((x0, y0, x0 + 44, y0 + 44), 8, fill=(22, 33, 46))
    for i, k in enumerate(('ru', 'ua', 'ind')): d.rounded_rectangle((x0 + 7 + i * 11, y0 + 9, x0 + 15 + i * 11, y0 + 35), 2, fill=SIDE[k])
    d.text((x0 + 58, y0 + 22), spec['site'], font=font(26, 700), fill=FG, anchor='lm')
    d.text((W - 60, y0 + 22), spec['date'], font=font(26, 500), fill=MUT, anchor='rm')
    # title
    tf = font(46, 700); y = 116; step = 58
    tl = wrap(d, spec['title'], tf, W - 120, 2)
    if tl and tl[-1].endswith('…'):
        tf = font(38, 700); step = 48; tl = wrap(d, spec['title'], tf, W - 120, 3)
    for l in tl: d.text((60, y), l, font=tf, fill=FG); y += step
    # divergence badge
    y += 18
    if spec.get('div'):
        bf = font(24, 600); tw = d.textlength(spec['div'], font=bf)
        c = DIVC.get(spec.get('div_level'), MUT)
        d.rounded_rectangle((60, y, 60 + tw + 52, y + 40), 20, fill=(22, 33, 46), outline=c, width=2)
        d.ellipse((76, y + 13, 90, y + 27), fill=c)
        d.text((100, y + 20), spec['div'], font=bf, fill=FG, anchor='lm'); y += 58
    else: y += 10
    # side columns
    sides = spec['sides'][:3]; n = max(1, len(sides)); gap = 28
    cw = (W - 120 - gap * (n - 1)) / n; bottom = H - 70
    lf = font(25, 700); xf = font(24 if n < 3 else 22, 400); lh = 33 if n < 3 else 30
    max_lines = max(1, int((bottom - (y + 52)) // lh))
    for i, (k, lab, txt) in enumerate(sides):
        cx = 60 + i * (cw + gap)
        d.rectangle((cx, y, cx + 6, bottom), fill=SIDE[k])
        d.text((cx + 22, y - 4), lab, font=lf, fill=SIDE[k])
        yy = y + 44
        for l in wrap(d, txt, xf, cw - 26, max_lines): d.text((cx + 22, yy), l, font=xf, fill=(232, 236, 241)); yy += lh
    # footer strip + domain
    for i, k in enumerate(('ru', 'ua', 'ind')): d.rectangle((i * W // 3, H - 14, (i + 1) * W // 3, H), fill=SIDE[k])
    d.text((W - 60, H - 40), spec['domain'], font=font(22, 500), fill=MUT, anchor='rm')
    q = im.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    b = io.BytesIO(); q.save(b, 'PNG', optimize=True)
    return b.getvalue()
