"""Render the Flowtest product story. Optional dependency: Pillow 12.1.0."""

import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
W, H, SCALE = 1120, 640, 2
BG, INK, MUTED = "#F6F6FB", "#30364C", "#72798D"
BLUE, SOFT, EDGE = "#6569CB", "#EAEAF8", "#DDDFEC"
GREEN, GREEN_BG = "#28866D", "#E4F3EB"
RED, RED_BG = "#B45B62", "#FAEBED"
AMBER, AMBER_BG = "#946B30", "#F8EFDE"


def ease(v):
    v = max(0, min(1, v))
    return v * v * (3 - 2 * v)


def spring(v):
    return 1 - math.exp(-6 * max(0, v)) * math.cos(9 * max(0, v))


@lru_cache(None)
def font(size, bold=False):
    candidates = [
        os.environ.get("FLOWTEST_MEDIA_FONT", ""),
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size * SCALE)
    raise RuntimeError("Set FLOWTEST_MEDIA_FONT to an installed TrueType font.")


class Canvas:
    def __init__(self, title, subtitle, chapter, progress):
        self.im = Image.new("RGB", (W * SCALE, H * SCALE), BG)
        self.d = ImageDraw.Draw(self.im)
        self.rect((34, 27, 42, 70), BLUE, 4)
        self.text(57, 24, "Flowtest", 29, INK, True)
        self.text(886, 34, "CONCEPT DEMO / SAMPLE DATA", 11, MUTED)
        self.text(49, 101, title, 32, INK, True)
        self.text(50, 149, subtitle, 17, MUTED)
        self.line([(49, 585), (1071, 585)], EDGE, 1)
        self.text(50, 604, chapter, 13, BLUE, True)
        self.text(798, 604, "Record. Replay. Keep the evidence.", 14, MUTED)
        self.line([(50, 580), (50 + 1020 * progress, 580)], BLUE, 2)

    def rect(self, r, color, radius=12, outline=None):
        self.d.rounded_rectangle(
            tuple(int(v * SCALE) for v in r),
            radius=int(radius * SCALE),
            fill=color,
            outline=outline,
            width=SCALE,
        )

    def line(self, pts, color=BLUE, width=2):
        self.d.line([(int(x * SCALE), int(y * SCALE)) for x, y in pts], fill=color, width=width * SCALE)

    def text(self, x, y, text, size=18, color=INK, bold=False):
        self.d.text((int(x * SCALE), int(y * SCALE)), text, font=font(size, bold), fill=color)

    def circle(self, x, y, radius, fill):
        self.d.ellipse(tuple(int(v * SCALE) for v in (x - radius, y - radius, x + radius, y + radius)), fill=fill)

    def card(self, x, y, w, h, color="#FFFFFF", edge=EDGE):
        self.rect((x + 2, y + 10, x + w + 2, y + h + 10), "#E8E8F0", 18)
        self.rect((x, y + 5, x + w, y + h + 5), "#DADCE9", 18)
        self.rect((x, y, x + w, y + h), color, 18, edge)

    def pill(self, x, y, text, color=BLUE, bg=SOFT):
        width = self.d.textlength(text, font=font(14)) / SCALE + 26
        self.rect((x, y, x + width, y + 29), bg, 9)
        self.text(x + 13, y + 4, text, 14, color, True)

    def tick(self, x, y, color=GREEN):
        self.line([(x - 7, y), (x - 2, y + 5), (x + 9, y - 7)], color, 2)

    def cursor(self, x, y, click=False):
        if click:
            self.circle(x, y, 17, SOFT)
        self.d.polygon(
            [
                (x * SCALE, y * SCALE),
                ((x + 5) * SCALE, (y + 24) * SCALE),
                ((x + 11) * SCALE, (y + 16) * SCALE),
                ((x + 20) * SCALE, (y + 16) * SCALE),
            ],
            fill=INK,
        )

    def end(self):
        return self.im.resize((W, H), Image.Resampling.LANCZOS)


def moving_dot(c, a, b, progress, color=BLUE):
    p = ease(progress)
    c.line([a, b], EDGE, 2)
    x, y = a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p
    c.line([a, (x, y)], color, 2)
    c.circle(x, y, 7, SOFT)
    c.circle(x, y, 4, color)


def record(t, global_t):
    c = Canvas(
        "Turn a real browser flow into a test.",
        "Record interactions and assertions, or import a Playwright test.",
        "01 / CAPTURE THE FLOW",
        global_t / 24,
    )
    c.card(51, 231, 430, 289)
    c.rect((52, 232, 480, 267), SOFT, 16)
    c.text(75, 240, "Sample application / Checkout", 13, MUTED)
    c.text(78, 297, "Order reference", 18, INK, True)
    c.rect((77, 337, 335, 379), BG, 9, EDGE)
    typed = "demo-order"[: int(10 * ease((t - 0.4) / 1.5))]
    c.text(91, 346, typed, 18)
    c.rect((350, 337, 451, 379), BLUE, 9)
    c.text(365, 349, "Submit", 15, "#FFFFFF", True)
    if 1.7 < t < 3.1:
        c.cursor(403, 364, t > 2.3)
    if t >= 2.5:
        c.pill(78, 411, "Order created", GREEN, GREEN_BG)
        if t >= 3.1:
            c.rect((72, 404, 280, 448), None, 10, BLUE)
            c.text(78, 466, "Assert: visible confirmation", 15, BLUE)
    moving_dot(c, (491, 366), (573, 366), (t - 2.5) / 1.3)
    y = 230 + 20 * (1 - ease(t / 0.7))
    c.card(586, y, 478, 290)
    c.pill(607, y + 18, "PLAYWRIGHT SOURCE")
    lines = [
        "await page.goto(siteUrl);",
        "await reference.fill('demo-order');",
        "await submit.click();",
        "await expect(result).toBeVisible();",
    ]
    at = [0, 1.3, 2.5, 3.5]
    for i, line in enumerate(lines):
        if t >= at[i]:
            c.text(607, y + 73 + i * 35, f"{i + 1:02}", 13, MUTED)
            c.text(638, y + 70 + i * 35, line, 16, BLUE if i == 3 else INK)
    if t > 5.0:
        lift = 4 * math.exp(-(t - 5)) * math.sin((t - 5) * 8)
        c.pill(607, y + 239 - lift, "Save immutable version  v1")
        c.tick(1019, y + 254)
    return c.end()


def snapshot(t, global_t):
    c = Canvas(
        "Replay the same test. In the same context.",
        "A historical rerun keeps its original source and environment snapshot.",
        "02 / FREEZE THE CONTEXT",
        global_t / 24,
    )
    p = spring(min(t / 1.7, 1.6))
    x1, x2 = 70 + 40 * p, 990 - 40 * p
    c.card(x1, 259, 229, 173)
    c.pill(x1 + 19, 279, "SOURCE")
    c.text(x1 + 19, 326, "Checkout / v1", 24, INK, True)
    c.text(x1 + 19, 369, "actions + assertions", 16, MUTED)
    c.card(x2 - 229, 259, 229, 173)
    c.pill(x2 - 210, 279, "ENVIRONMENT")
    c.text(x2 - 210, 326, "Test site A", 24, INK, True)
    c.text(x2 - 210, 369, "roles + variables", 16, MUTED)
    moving_dot(c, (x1 + 241, 343), (453, 343), (t - 1) / 1.1)
    moving_dot(c, (x2 - 240, 343), (650, 343), (t - 1) / 1.1)
    c.card(467, 268, 173, 154, SOFT)
    c.text(493, 291, "RUN 001", 22, BLUE, True)
    c.text(493, 339, "v1 + site A", 18, INK)
    c.text(493, 373, "snapshot saved", 13, MUTED)
    if t > 3:
        c.pill(119, 469, "Current edits: v2 + site B", MUTED, "#ECECF2")
        c.text(121, 507, "New edits leave past runs intact.", 15, MUTED)
    if t > 4:
        yy = 461 + 16 * (1 - ease((t - 4) / 0.7))
        c.card(465, yy, 485, 81)
        c.text(487, yy + 15, "RERUN 002", 18, BLUE, True)
        c.text(487, yy + 46, "Same v1 + site A  /  new run record", 17)
        moving_dot(c, (550, 434), (550, yy - 8), (t - 4) / 1.1)
        if t > 5.5:
            c.tick(917, yy + 38)
    return c.end()


def evidence(t, global_t):
    c = Canvas(
        "A green retry never erases the first failure.",
        "Keep assertions, attempts and private Trace evidence attached to the run.",
        "03 / FOLLOW THE EVIDENCE",
        global_t / 24,
    )
    c.line([(115, 360), (1007, 360)], EDGE, 3)
    cards = [
        (61, "ATTEMPT 1", "Assertion failed", "expected: visible", "actual: hidden", RED, RED_BG),
        (411, "ATTEMPT 2", "Assertion passed", "expected: visible", "actual: visible", GREEN, GREEN_BG),
        (761, "RUN SUMMARY", "Passed / flaky", "First failure retained", "Trace + screenshot", AMBER, AMBER_BG),
    ]
    for i, (x, tag, title, a, b, color, bg) in enumerate(cards):
        start = i * 1.7
        if t < start:
            continue
        y = 258 + 32 * (1 - ease((t - start) / 0.7))
        if i:
            moving_dot(c, (x - 44, 361), (x - 11, 361), (t - start) / 0.7)
        c.card(x, y, 291, 215, "#FFFFFF", color if i == 0 else EDGE)
        c.pill(x + 20, y + 20, tag, color, bg)
        c.text(x + 20, y + 75, title, 23, color, True)
        c.text(x + 20, y + 121, a, 17, INK)
        c.text(x + 20, y + 151, b, 17, MUTED)
        if i == 2:
            c.rect((x + 20, y + 185, x + 130, y + 207), SOFT, 5)
            c.text(x + 30, y + 186, "Trace evidence", 12, BLUE)
    if t > 5.5:
        c.pill(295, 514, "No assertions?  Unverified.  Not a silent pass.", MUTED, "#ECECF2")
    return c.end()


def frame(t):
    scenes = [record, snapshot, evidence]
    i = min(2, int(t / 8))
    local = t - i * 8
    result = scenes[i](local, t)
    if i and local < 0.45:
        result = Image.blend(scenes[i - 1](7.9, t), result, ease(local / 0.45))
    return result


def main():
    sample = Image.new("RGB", (W, H * 3))
    for i, t in enumerate([6.5, 14.5, 23]):
        sample.paste(frame(t), (0, i * H))
    palette = sample.quantize(colors=192)
    frames = [frame(i / 12).quantize(palette=palette, dither=Image.Dither.NONE) for i in range(288)]
    durations = [80 if i % 3 else 90 for i in range(288)]
    durations[-1] = 1800
    frames[0].save(
        ROOT / "flowtest.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    frame(23).save(ROOT / "flowtest-poster.png")
    print(f"Rendered {len(frames)} frames at {W} x {H}.")


if __name__ == "__main__":
    main()
