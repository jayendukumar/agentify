"""Builds a PPTX version of the v2 case-study deck.

The slide text is taken from build_deck_v2.py (the "story" section is executed
with slide functions that return data instead of HTML), so both decks stay in sync.
"""
import io
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

HERE = Path(__file__).parent
SHOTS = HERE / "screenshots"
OUT = Path(sys.argv[1])

NAVY, DEEP, BLUE, SKY = RGBColor(0x16, 0x24, 0x5C), RGBColor(0x0D, 0x15, 0x30), RGBColor(0x2F, 0x5B, 0xD3), RGBColor(0x8F, 0xB3, 0xFF)
PALE, LIGHT, GREY_BG, WHITE = RGBColor(0xDF, 0xE7, 0xFF), RGBColor(0xF6, 0xF8, 0xFC), RGBColor(0xE9, 0xED, 0xF6), RGBColor(0xFF, 0xFF, 0xFF)
INK, MUTED, BEFORE, AMBER = RGBColor(0x3B, 0x4A, 0x75), RGBColor(0x5D, 0x6A, 0x90), RGBColor(0xEC, 0xEE, 0xF4), RGBColor(0xE5, 0x9B, 0x2F)
FOCUS_BG, BORDER = RGBColor(0xE6, 0xED, 0xFF), RGBColor(0xC9, 0xD3, 0xEE)
FONT = "Segoe UI"

W, H = Inches(13.333), Inches(7.5)

# ---- load the story from the HTML builder -------------------------------------

STEPS = []


def title_slide(kicker, title, sub, hint=""):
    return {"kind": "title", "kicker": kicker, "title": title, "sub": sub}


def text_slide(kicker, title, intro, cards, note=""):
    return {"kind": "text", "kicker": kicker, "title": title, "intro": intro, "cards": cards, "note": note}


def compare_slide(kicker, title, left_h, left, right_h, right):
    return {"kind": "compare", "kicker": kicker, "title": title, "left": (left_h, left), "right": (right_h, right)}


def flow_slide():
    return {"kind": "flow"}


def journey_slide():
    return {"kind": "journey"}


def section_slide(n):
    return {"kind": "section", "n": n}


def shot_slide(step, shot, title, what, why):
    return {"kind": "shot", "step": step, "shot": shot, "title": title, "what": what, "why": why}


src = (HERE / "build_deck_v2.py").read_text(encoding="utf-8")
steps_src = src[src.index("STEPS = ["):src.index("# ---- slide types")]
trim_src = src[src.index("def trim("):src.index("def data_uri(")]
story_src = src[src.index("# ---- the story"):src.index("total = len(slides)")]
from PIL import ImageChops  # noqa: E402,F401  (used by the exec'd trim)
exec(trim_src)
exec(steps_src)
exec(story_src)

# ---- drawing helpers ----------------------------------------------------------

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]


def rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.RECTANGLE, line=None):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line:
        s.line.color.rgb, s.line.width = line
    else:
        s.line.fill.background()
    s.shadow.inherit = False
    return s


def text(slide, x, y, w, h, runs, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT, margin=0):
    """runs: list of paragraphs; each paragraph is (text, size, color, bold[, opts])."""
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Emu(margin)
    for i, para in enumerate(runs):
        t, size, color, bold, *opts = para
        opts = opts[0] if opts else {}
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(opts.get("after", 0))
        if opts.get("bullet"):
            t = "•  " + t
        r = p.add_run()
        r.text = t
        f = r.font
        f.name, f.size, f.color.rgb, f.bold = FONT, Pt(size), color, bold
        f.italic = opts.get("italic", False)
    return tb


def background(slide, color):
    rect(slide, 0, 0, W, H, color)


def number(slide, i, color):
    text(slide, W - Inches(1.4), H - Inches(0.5), Inches(1.1), Inches(0.3),
         [(f"{i} / {len(slides)}", 10, color, False)], align=PP_ALIGN.RIGHT)


def heading(slide, kicker, title, y=Inches(0.55)):
    text(slide, Inches(0.8), y, Inches(11.7), Inches(0.35), [(kicker.upper(), 12, BLUE, True)])
    text(slide, Inches(0.8), y + Inches(0.4), Inches(11.7), Inches(0.9), [(title, 32, NAVY, True)])


def notes(slide, body):
    slide.notes_slide.notes_text_frame.text = body


def image_stream(name):
    gif = SHOTS / f"{name}.gif"
    if gif.exists():
        return gif.open("rb"), Image.open(gif).size
    im = trim(Image.open(SHOTS / f"{name}.png").convert("RGB"))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    buf.seek(0)
    return buf, im.size


# ---- slide renderers ----------------------------------------------------------

def render_title(s, d, section=False):
    rect(s, 0, 0, W, H, NAVY)
    band = rect(s, 0, 0, W, H, DEEP)
    band.fill.gradient()
    band.fill.gradient_angle = 135
    band.fill.gradient_stops[0].color.rgb, band.fill.gradient_stops[1].color.rgb = NAVY, DEEP
    paras = [(d["kicker"].upper(), 14, SKY, True, {"after": 10}), (d["title"], 48 if section else 40, WHITE, True, {"after": 12})]
    if d.get("sub"):
        paras.append((d["sub"], 20, PALE, False))
    text(s, Inches(1.3), Inches(1), Inches(10.7), Inches(5.5), paras, anchor=MSO_ANCHOR.MIDDLE)


def render_text(s, d):
    background(s, LIGHT)
    heading(s, d["kicker"], d["title"])
    y = Inches(1.95) + (Inches(0.55) if len(d["title"]) > 44 else 0)
    if d["intro"]:
        text(s, Inches(0.8), y, Inches(11.2), Inches(1.1), [(d["intro"], 18, INK, False)])
        y += Inches(0.32) * (len(d["intro"]) // 100 + 1) + Inches(0.4)
    else:
        y += Inches(0.2)
    cards, n = d["cards"], len(d["cards"])
    cols = 3 if n == 3 else 2
    rows = (n + cols - 1) // cols
    gap = Inches(0.3)
    cw = (Inches(11.7) - gap * (cols - 1)) // cols
    bottom = H - Inches(0.85 if d["note"] else 0.8)
    ch = min(Inches(2.5), (bottom - y - gap * (rows - 1)) // rows)
    for k, (h, t) in enumerate(cards):
        x = Inches(0.8) + (cw + gap) * (k % cols)
        cy = y + (ch + gap) * (k // cols)
        rect(s, x, cy, cw, ch, WHITE, MSO_SHAPE.RECTANGLE)
        rect(s, x, cy, cw, Inches(0.07), BLUE)
        text(s, x + Inches(0.3), cy + Inches(0.25), cw - Inches(0.6), ch - Inches(0.4),
             [(h, 22, NAVY, True, {"after": 8}), (t, 17, INK, False)])
    if d["note"]:
        text(s, Inches(0.8), H - Inches(0.65), Inches(10.5), Inches(0.35), [(d["note"], 11, MUTED, False, {"italic": True})])


def render_compare(s, d):
    background(s, LIGHT)
    heading(s, d["kicker"], d["title"])
    cw, y, ch = Inches(5.7), Inches(2.1), Inches(4.6)
    for k, ((h, items), fill, head, body) in enumerate([(d["left"], BEFORE, RGBColor(0x4A, 0x53, 0x70), RGBColor(0x4A, 0x53, 0x70)),
                                                        (d["right"], NAVY, WHITE, PALE)]):
        x = Inches(0.8) + (cw + Inches(0.3)) * k
        rect(s, x, y, cw, ch, fill)
        text(s, x + Inches(0.4), y + Inches(0.35), cw - Inches(0.8), ch - Inches(0.6),
             [(h, 22, head, True, {"after": 12})] + [(i, 16, body, False, {"bullet": True, "after": 10}) for i in items])


def render_flow(s, d):
    background(s, LIGHT)
    heading(s, "The process in plain words", "How a new retail customer is onboarded today")
    main = [("Application received", "Onboarding Officer"), ("Verify identity", "Onboarding Officer"),
            ("Screen against EU sanctions list", "Onboarding Officer"), ("Assess and set credit limit", "Credit Analyst"),
            ("Activate account", "Onboarding Officer"), ("Customer onboarded", "")]
    bw, aw, y, bh = Inches(1.65), Inches(0.36), Inches(2.15), Inches(1.35)
    for k, (n, r) in enumerate(main):
        x = Inches(0.8) + (bw + aw) * k
        end, focus = k in (0, len(main) - 1), "credit" in n
        box = rect(s, x, y, bw, bh, FOCUS_BG if focus else (RGBColor(0xEE, 0xF2, 0xFB) if end else WHITE),
                   MSO_SHAPE.ROUNDED_RECTANGLE, (BLUE if focus else BORDER, Pt(2.5 if focus else 1.25)))
        box.adjustments[0] = 0.5 if end else 0.12
        paras = [(n, 13, NAVY, True)] + ([(r, 10, MUTED, False)] if r else [])
        text(s, x + Inches(0.08), y, bw - Inches(0.16), bh, paras, anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER)
        if k < len(main) - 1:
            text(s, x + bw, y, aw, bh, [("➜", 18, BLUE, False)], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER)
    branches = [("Identity can't be verified?", "The application is rejected and the customer is told."),
                ("Possible sanctions match?", "The Money Laundering Reporting Officer (MLRO) reviews it before anything else happens."),
                ("Credit limit above EUR 50,000?", "The Chief Risk Officer must approve it. Up to EUR 50,000, the Head of Operations can.")]
    bw2, by, bh2 = Inches(3.7), Inches(4.0), Inches(1.75)
    for k, (h, t) in enumerate(branches):
        x = Inches(0.8) + (bw2 + Inches(0.3)) * k
        rect(s, x, by, bw2, bh2, WHITE)
        rect(s, x, by, Inches(0.07), bh2, AMBER)
        text(s, x + Inches(0.3), by + Inches(0.2), bw2 - Inches(0.5), bh2 - Inches(0.3), [(h, 14, NAVY, True, {"after": 4}), (t, 13, INK, False)])
    text(s, Inches(0.8), H - Inches(1.05), Inches(11.7), Inches(0.5),
         [("Four roles take part: Onboarding Officer, Credit Analyst, MLRO and Chief Risk Officer. "
           "The highlighted step is the focus of this study.", 12, MUTED, False, {"italic": True})])


def render_journey(s, d):
    background(s, LIGHT)
    heading(s, "The journey at a glance", "From a process document to a go / no-go decision")
    cw, ch, gap = Inches(3.7), Inches(1.2), Inches(0.3)
    for k, step in enumerate(STEPS):
        x, y = Inches(0.8) + (cw + gap) * (k % 3), Inches(2.2) + (ch + gap) * (k // 3)
        rect(s, x, y, cw, ch, WHITE)
        rect(s, x + Inches(0.3), y + (ch - Inches(0.55)) // 2, Inches(0.55), Inches(0.55), BLUE, MSO_SHAPE.OVAL)
        text(s, x + Inches(0.3), y + (ch - Inches(0.55)) // 2, Inches(0.55), Inches(0.55), [(str(k + 1), 15, WHITE, True)],
             anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER)
        text(s, x + Inches(1.05), y, cw - Inches(1.2), ch, [(step, 17, NAVY, True)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, Inches(0.8), H - Inches(1.05), Inches(11.7), Inches(0.4),
         [("Each step is shown on the following slides with real screens from Axyntro.", 12, MUTED, False, {"italic": True})])


def render_shot(s, d):
    left_w = int(W * 0.7)
    rect(s, 0, 0, left_w, H, GREY_BG)
    rect(s, left_w, 0, W - left_w, H, NAVY)
    stream, (iw, ih) = image_stream(d["shot"])
    pad = Inches(0.2)
    bw, bh = left_w - 2 * pad, H - 2 * pad
    scale = min(bw / iw, bh / ih)
    pw, ph = int(iw * scale), int(ih * scale)
    s.shapes.add_picture(stream, (left_w - pw) // 2, (H - ph) // 2, pw, ph)
    px, pw2 = left_w + Inches(0.35), W - left_w - Inches(0.7)
    paras = [(f"STEP {d['step']} · {STEPS[d['step'] - 1].upper()}", 10, SKY, True, {"after": 8}),
             (d["title"], 21, WHITE, True, {"after": 12})]
    paras += [(w, 12.5, PALE, False, {"bullet": True, "after": 7}) for w in d["what"]]
    text(s, px, Inches(0.5), pw2, Inches(4.9), paras)
    if d["why"]:
        rect(s, px, Inches(5.35), pw2, Inches(1.45), RGBColor(0x2A, 0x3B, 0x78))
        rect(s, px, Inches(5.35), Inches(0.06), Inches(1.45), SKY)
        text(s, px + Inches(0.2), Inches(5.45), pw2 - Inches(0.3), Inches(1.3),
             [("WHY IT MATTERS", 9.5, SKY, True, {"after": 3}), (d["why"], 12, WHITE, False)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, d["title"] + "\n\n" + "\n".join("- " + w for w in d["what"]) + (f"\n\nWhy it matters: {d['why']}" if d["why"] else ""))


for i, d in enumerate(slides, 1):
    s = prs.slides.add_slide(BLANK)
    k = d["kind"]
    if k == "title":
        render_title(s, d)
    elif k == "section":
        render_title(s, {"kicker": f"Step {d['n']} of {len(STEPS)}", "title": STEPS[d["n"] - 1]}, section=True)
    else:
        {"text": render_text, "compare": render_compare, "flow": render_flow,
         "journey": render_journey, "shot": render_shot}[k](s, d)
    number(s, i, SKY if k in ("title", "section", "shot") else MUTED)

prs.save(OUT)
print(OUT, OUT.stat().st_size // 1024, "KB,", len(slides), "slides")
