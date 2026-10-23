#!/usr/bin/env python3
"""Draws the docs' diagrams (the first-cluster guide's, and the PAC report's chart) as SVG, light and dark, in the portal's colours.

Each diagram is written twice next to this script: <slug>.svg (light) and <slug>-dark.svg. The pages show them with
<picture>, so GitHub picks the one matching the reader's theme. Change a diagram here and run:

    python3 docs/images/diagrams/build.py

With --preview <file.html>, it also writes one page showing every diagram in both themes, for checking by eye.
"""

import argparse
import html
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

# The portal's tokens (web/src/styles.css), mapped to the diagram roles. link is the portal's --idle teal, so
# connections that leave the cluster stand apart from the blue accent.
THEMES = {
    "light": {
        "paper": "#f5f7fa", "surface": "#ffffff", "ink": "#172030", "muted": "#536074", "soft": "#536074",
        "accent": "#1f4e8c", "link": "#0f6e74",
    },
    "dark": {
        "paper": "#10141a", "surface": "#171d25", "ink": "#e5e9ef", "muted": "#9ca8b8", "soft": "#9ca8b8",
        "accent": "#86b4ee", "link": "#5cc6cc",
    },
}

SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"


def rgba(hex_color, alpha):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def luminance(hex_color):
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def blend(base, over, alpha):
    """over at alpha on base, as a hex colour."""
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    o = [int(over[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(bc + (oc - bc) * alpha):02x}" for bc, oc in zip(b, o))


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_width(text, size, mono=False, tracking=0.0):
    """The style guide's width budget: 0.60em a character in sans, 0.62em in mono, plus letter-spacing."""
    return len(text) * size * ((0.62 if mono else 0.60) + tracking)


def esc(text):
    return html.escape(text, quote=True)


class Diagram:
    def __init__(self, slug, width, height, title, desc, theme):
        self.slug, self.w, self.h, self.title, self.desc = slug, width, height, title, desc
        self.t = THEMES[theme]
        self.variant = slug if theme == "light" else f"{slug}-dark"
        self.zones, self.arrows, self.labels, self.nodes, self.legend_parts = [], [], [], [], []

    # --- treatments (style guide: node type -> treatment) ---
    def treatment(self, kind):
        t = self.t
        return {
            "focal": (rgba(t["accent"], 0.08), t["accent"], ""),
            "backend": (t["surface"], t["ink"], ""),
            "store": (rgba(t["ink"], 0.05), t["muted"], ""),
            "external": (rgba(t["ink"], 0.03), rgba(t["ink"], 0.45), ""),
            "input": (rgba(t["muted"], 0.10), t["soft"], ""),
            "optional": (rgba(t["ink"], 0.02), t["muted"], ' stroke-dasharray="4,3"'),
            "security": (rgba(t["accent"], 0.05), rgba(t["accent"], 0.6), ' stroke-dasharray="4,4"'),
        }[kind]

    def check_fit(self, what, need, room):
        if need > room:
            raise SystemExit(f"{self.slug}: {what!r} needs {need:.0f}px, has {room}px")

    # --- primitives ---
    def zone(self, x, y, w, h, label, kind="zone", label_x=None):
        t = self.t
        if kind == "zone":
            fill, stroke, dash = rgba(t["ink"], 0.02), rgba(t["ink"], 0.14), ""
        elif kind == "boundary":
            fill, stroke, dash = rgba(t["ink"], 0.015), rgba(t["ink"], 0.35), ' stroke-dasharray="4,4"'
        else:
            fill, stroke, dash = self.treatment(kind)
        lx = x + 12 if label_x is None else label_x
        lw = text_width(label, 7, mono=True, tracking=0.14) + 8
        self.zones.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="0.8"{dash}/>'
            f'<rect x="{lx}" y="{y - 6}" width="{lw:.0f}" height="12" rx="2" fill="{t["paper"]}"/>'
            f'<text x="{lx + 4}" y="{y + 3}" class="e" fill="{t["soft"]}">{esc(label)}</text>')

    def lane(self, y, w, h, label, shaded):
        t = self.t
        fill = rgba(t["ink"], 0.025) if shaded else "none"
        self.zones.append(
            f'<rect x="16" y="{y}" width="{w}" height="{h}" fill="{fill}"/>'
            f'<line x1="16" y1="{y + h}" x2="{16 + w}" y2="{y + h}" stroke="{rgba(t["ink"], 0.12)}" stroke-width="0.8"/>'
            f'<text x="24" y="{y + h / 2 + 3}" class="e" fill="{t["soft"]}">{esc(label)}</text>')

    def node(self, x, y, w, h, name, sub=None, kind="backend", tag=None, badge=None):
        t = self.t
        fill, stroke, dash = self.treatment(kind)
        cx = x + w / 2
        self.check_fit(name, text_width(name, 12) + 16, w)
        if sub:
            self.check_fit(sub, text_width(sub, 9, mono=True) + 16, w)
        parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{t["paper"]}"/>',
                 f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>']
        tag_color = t["accent"] if kind in ("focal", "security") else t["muted"]
        if tag:
            tw = max(24, round((text_width(tag, 7, mono=True, tracking=0.08) + 8) / 4) * 4)
            parts.append(f'<rect x="{x + 8}" y="{y + 6}" width="{tw}" height="12" rx="2" fill="none" '
                         f'stroke="{rgba(t["ink"], 0.3)}" stroke-width="0.8"/>'
                         f'<text x="{x + 8 + tw / 2}" y="{y + 15}" class="t" text-anchor="middle" fill="{tag_color}">{esc(tag)}</text>')
        if badge:
            bw = max(24, round((text_width(badge, 8, mono=True, tracking=0.08) + 8) / 4) * 4)
            parts.append(f'<rect x="{x + w - 8 - bw}" y="{y + 6}" width="{bw}" height="12" rx="2" fill="none" '
                         f'stroke="{rgba(t["ink"], 0.3)}" stroke-width="0.8"/>'
                         f'<text x="{x + w - 8 - bw / 2}" y="{y + 15}" class="b" text-anchor="middle" fill="{tag_color}">{esc(badge)}</text>')
        if tag or badge:
            name_y, sub_y = (y + h - 21, y + h - 8) if sub else (y + h - 14, None)
        else:
            name_y, sub_y = (y + h / 2 - 2, y + h / 2 + 12) if sub else (y + h / 2 + 4, None)
        parts.append(f'<text x="{cx}" y="{name_y}" class="n" text-anchor="middle" fill="{t["ink"]}">{esc(name)}</text>')
        if sub:
            parts.append(f'<text x="{cx}" y="{sub_y}" class="s" text-anchor="middle" fill="{t["muted"]}">{esc(sub)}</text>')
        self.nodes.append("".join(parts))

    def host(self, x, y, w, h, name, tag, badge=None, kind="backend", note=None):
        """A server drawn as a container: tag and name in its header, chips inside."""
        t = self.t
        fill, stroke, dash = self.treatment(kind)
        tag_color = t["accent"] if kind in ("focal", "security") else t["muted"]
        tw = max(24, round((text_width(tag, 7, mono=True, tracking=0.08) + 8) / 4) * 4)
        parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{t["paper"]}"/>',
                 f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>',
                 f'<rect x="{x + 12}" y="{y + 12}" width="{tw}" height="12" rx="2" fill="none" stroke="{rgba(t["ink"], 0.3)}" stroke-width="0.8"/>',
                 f'<text x="{x + 12 + tw / 2}" y="{y + 21}" class="t" text-anchor="middle" fill="{tag_color}">{esc(tag)}</text>',
                 f'<text x="{x + 20 + tw}" y="{y + 22}" class="n" text-anchor="start" fill="{t["ink"]}">{esc(name)}</text>']
        if badge:
            bw = max(24, round((text_width(badge, 8, mono=True, tracking=0.08) + 8) / 4) * 4)
            parts.append(f'<rect x="{x + w - 12 - bw}" y="{y + 12}" width="{bw}" height="12" rx="2" fill="none" '
                         f'stroke="{rgba(t["ink"], 0.3)}" stroke-width="0.8"/>'
                         f'<text x="{x + w - 12 - bw / 2}" y="{y + 21}" class="b" text-anchor="middle" fill="{tag_color}">{esc(badge)}</text>')
        if note:
            self.check_fit(note, text_width(note, 9, mono=True) + 24, w)
            parts.append(f'<text x="{x + 12}" y="{y + h - 12}" class="s" text-anchor="start" fill="{t["muted"]}">{esc(note)}</text>')
        self.zones.append("".join(parts))  # under the arrows, which run into the chips it holds

    def chip(self, x, y, w, name, tag=None, kind="store"):
        t = self.t
        fill, stroke, dash = self.treatment(kind)
        need = text_width(name, 11) + (text_width(tag, 9, mono=True) + 8 if tag else 0) + 16
        self.check_fit(name, need, w)
        parts = [f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="4" fill="{t["paper"]}"/>',
                 f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="0.8"{dash}/>',
                 f'<text x="{x + 8}" y="{y + 16}" class="c" fill="{t["ink"]}">{esc(name)}</text>']
        if tag:
            tag_color = t["accent"] if kind == "focal" else t["muted"]
            parts.append(f'<text x="{x + w - 8}" y="{y + 16}" class="s" text-anchor="end" fill="{tag_color}">{esc(tag)}</text>')
        self.nodes.append("".join(parts))

    def arrow(self, d, kind="muted", dashed=False, head=True):
        t = self.t
        color = {"muted": t["muted"], "accent": t["accent"], "link": t["link"]}[kind]
        width = "1.4" if kind == "accent" else "1.2"
        dash = ' stroke-dasharray="5,4"' if dashed else ""
        marker = f' marker-end="url(#{self.variant}-arrow-{kind})"' if head else ""
        self.arrows.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash}{marker}/>')

    def label(self, x, y, text, kind="muted", anchor="middle"):
        """An arrow label on its mask. (x, y) is the mask's top-left corner, or its top-centre with anchor middle."""
        t = self.t
        color = {"muted": t["soft"], "accent": t["accent"], "link": t["link"]}[kind]
        w = round((text_width(text, 8, mono=True, tracking=0.06) + 8) / 4) * 4
        left = x - w / 2 if anchor == "middle" else x
        self.labels.append(f'<rect x="{left:g}" y="{y}" width="{w}" height="12" rx="2" fill="{t["paper"]}"/>'
                           f'<text x="{left + w / 2:g}" y="{y + 9}" class="a" text-anchor="middle" fill="{color}">{esc(text)}</text>')
        return left, left + w

    def text(self, x, y, text, size=11, color="muted", anchor="middle", mono=False):
        cls = "s" if mono else "p"
        self.nodes.append(f'<text x="{x}" y="{y}" class="{cls}" style="font-size:{size}px" text-anchor="{anchor}" '
                          f'fill="{self.t[color]}">{esc(text)}</text>')

    def hbars(self, x0, x1, y0, pitch, rows, vmax, ticks, focal=None):
        """A horizontal bar chart from zero: rows are (label, value); one focal row in the accent."""
        t = self.t
        scale = (x1 - x0) / vmax
        for v in ticks:
            x = x0 + v * scale
            self.zones.append(f'<line x1="{x:g}" y1="{y0 - 12}" x2="{x:g}" y2="{y0 + pitch * len(rows) - 8}" '
                              f'stroke="{rgba(t["ink"], 0.08 if v else 0.25)}" stroke-width="{0.8 if v else 1}"/>'
                              f'<text x="{x:g}" y="{y0 + pitch * len(rows) + 8}" class="a" text-anchor="middle" '
                              f'fill="{t["muted"]}">{v:,}</text>')
        for i, (label, value) in enumerate(rows):
            y = y0 + pitch * i
            w = max(4, value * scale)
            fill, stroke = ((rgba(t["accent"], 0.85), t["accent"]) if label == focal
                            else (rgba(t["muted"], 0.35), t["muted"]))
            r = min(4, w / 2)
            d = (f"M {x0},{y} H {x0 + w - r:g} Q {x0 + w:g},{y} {x0 + w:g},{y + r} V {y + 20 - r} "
                 f"Q {x0 + w:g},{y + 20} {x0 + w - r:g},{y + 20} H {x0} Z")
            self.check_fit(label, text_width(label, 11), x0 - 16)
            self.nodes.append(f'<text x="{x0 - 12}" y="{y + 14}" class="c" text-anchor="end" fill="{t["ink"]}">{esc(label)}</text>'
                              f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
                              f'<text x="{x0 + w + 8:g}" y="{y + 14}" class="s" style="font-size:10px" fill="{t["muted"]}">{value:,}</text>')

    def oval(self, x, y, w, h, name, sub=None, kind="backend"):
        self.node(x, y, w, h, name, sub, kind)
        self.nodes[-1] = self.nodes[-1].replace('rx="6"', f'rx="{h // 2}"')

    def diamond(self, cx, cy, w, h, lines, kind="backend"):
        t = self.t
        fill, stroke, dash = self.treatment(kind)
        pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
        parts = [f'<polygon points="{pts}" fill="{t["paper"]}"/>',
                 f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>']
        top = cy - 7 * (len(lines) - 1) + 4
        for i, line in enumerate(lines):
            self.check_fit(line, text_width(line, 11), w * 0.85)
            parts.append(f'<text x="{cx}" y="{top + 14 * i}" class="c" style="font-weight:600" text-anchor="middle" '
                         f'fill="{t["ink"]}">{esc(line)}</text>')
        self.nodes.append("".join(parts))

    def layer(self, x, y, w, h, index, name, lines, effect, kind="backend"):
        """A band in a layer stack: index tag, name, up to two mono lines, and its effect on the right."""
        t = self.t
        fill, stroke, dash = self.treatment(kind)
        accent = kind in ("focal", "security")
        parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{t["paper"]}"/>',
                 f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>',
                 f'<rect x="{x + 12}" y="{y + 12}" width="28" height="12" rx="2" fill="none" stroke="{rgba(t["ink"], 0.3)}" stroke-width="0.8"/>',
                 f'<text x="{x + 26}" y="{y + 21}" class="t" text-anchor="middle" fill="{t["accent"] if accent else t["muted"]}">{esc(index)}</text>',
                 f'<text x="{x + 52}" y="{y + 23}" class="n" style="font-size:13px" fill="{t["ink"]}">{esc(name)}</text>',
                 f'<text x="{x + w - 16}" y="{y + 23}" class="c" style="font-weight:600" text-anchor="end" '
                 f'fill="{t["accent"] if accent else t["muted"]}">{esc(effect)}</text>']
        for i, line in enumerate(lines):
            self.check_fit(line, text_width(line, 9, mono=True) + 68, w)
            parts.append(f'<text x="{x + 52}" y="{y + 42 + 14 * i}" class="s" fill="{t["muted"]}">{esc(line)}</text>')
        self.nodes.append("".join(parts))

    def sequence(self, actors, steps, top=16):
        """A sequence diagram. actors: (key, name, sub, kind). steps: ("call"|"ret"|"async"|"hl", from, to, text),
        ("self", actor, text), or ("frame", operator, guard, first actor, last actor, [steps]). A newline in a
        text starts a second line. Returns the y below the last step."""
        t = self.t
        n = len(actors)
        xs = {key: 96 + i * (self.w - 192) / (n - 1) for i, (key, *_rest) in enumerate(actors)}
        for key, name, sub, kind in actors:
            self.node(xs[key] - 72, top, 144, 48, name, sub, kind)
        y = top + 48 + 44

        def mask_text(x, y_base, line, anchor, framed):
            w = text_width(line, 10) + 8
            left = {"middle": x - w / 2, "start": x - 4, "end": x - w + 4}[anchor]
            fill = blend(t["paper"], t["ink"], 0.02) if framed else t["paper"]
            self.labels.append(f'<rect x="{left:.0f}" y="{y_base - 10}" width="{w:.0f}" height="13" fill="{fill}"/>'
                               f'<text x="{x:g}" y="{y_base}" class="p" text-anchor="{anchor}" fill="{t["muted"]}">{esc(line)}</text>')

        def run(steps, y, framed=False):
            for step in steps:
                if step[0] == "frame":
                    _, op, guard, a, b, inner = step
                    fx0 = max(8, min(xs[a], xs[b]) - 88)
                    fx1 = min(self.w - 8, max(xs[a], xs[b]) + 88)
                    fy = y - 24
                    end = run(inner, y + 16, True)
                    fh = end - 12 - fy
                    self.zones.append(
                        f'<rect x="{fx0:g}" y="{fy}" width="{fx1 - fx0:g}" height="{fh}" rx="4" fill="{rgba(t["ink"], 0.02)}" '
                        f'stroke="{rgba(t["ink"], 0.22)}" stroke-width="1"/>'
                        f'<rect x="{fx0:g}" y="{fy}" width="40" height="16" rx="2" fill="{t["paper"]}" stroke="{rgba(t["ink"], 0.22)}" stroke-width="1"/>'
                        f'<text x="{fx0 + 20:g}" y="{fy + 11}" class="b" text-anchor="middle" fill="{t["muted"]}">{esc(op)}</text>'
                        f'<text x="{fx0 + 48:g}" y="{fy + 11}" class="a" fill="{t["muted"]}">{esc(guard)}</text>')
                    y = end + 20
                    continue
                lines = step[-1].split("\n")
                if step[0] == "self":
                    x = xs[step[1]]
                    self.arrow(f"M {x + 4:g},{y} H {x + 28:g} V {y + 16} H {x + 6:g}")
                    right = x + 36 + max(text_width(line, 10) for line in lines) <= self.w - 8
                    for i, line in enumerate(lines):
                        if right:
                            mask_text(x + 36, y + 4 + 13 * i, line, "start", framed)
                        else:
                            mask_text(x - 10, y + 4 + 13 * i, line, "end", framed)
                    y += max(28, 13 * len(lines) + 12) + 12
                    continue
                kind, a, b, _text = step
                xa, xb = xs[a], xs[b]
                sign = 1 if xb > xa else -1
                y += 13 * (len(lines) - 1)
                for i, line in enumerate(lines):
                    mask_text((xa + xb) / 2, y - 7 - 13 * (len(lines) - 1 - i), line, "middle", framed)
                color = "accent" if kind == "hl" else "muted"
                self.arrow(f"M {xa + 4 * sign:g},{y} H {xb - 6 * sign:g}", color, dashed=kind in ("ret", "async"))
                y += 36
            return y

        end = run(steps, y)
        for key, *_rest in actors:
            self.zones.insert(0, f'<line x1="{xs[key]:g}" y1="{top + 48}" x2="{xs[key]:g}" y2="{end - 16}" '
                                 f'stroke="{rgba(t["ink"], 0.25)}" stroke-width="1" stroke-dasharray="4,4"/>')
        return end

    def legend(self, y, items):
        """A horizontal strip: items are (swatch, label) with swatch a node kind or arrow:<kind>[:dashed]."""
        t = self.t
        parts = [f'<line x1="16" y1="{y}" x2="{self.w - 16}" y2="{y}" stroke="{rgba(t["ink"], 0.12)}" stroke-width="0.8"/>',
                 f'<text x="16" y="{y + 20}" class="e" fill="{t["soft"]}">LEGEND</text>']
        x = 88
        for swatch, text in items:
            if swatch.startswith("arrow:"):
                _, kind, *rest = swatch.split(":")
                color = {"muted": t["muted"], "accent": t["accent"], "link": t["link"]}[kind]
                dash = ' stroke-dasharray="5,4"' if rest else ""
                parts.append(f'<line x1="{x}" y1="{y + 17}" x2="{x + 24}" y2="{y + 17}" stroke="{color}" stroke-width="1.2"{dash} '
                             f'marker-end="url(#{self.variant}-arrow-{kind})"/>')
                sw = 32
            else:
                fill, stroke, dash = self.treatment(swatch) if swatch not in ("zone", "boundary") else (
                    rgba(t["ink"], 0.02), rgba(t["ink"], 0.35 if swatch == "boundary" else 0.14),
                    ' stroke-dasharray="4,4"' if swatch == "boundary" else "")
                parts.append(f'<rect x="{x}" y="{y + 12}" width="16" height="10" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>')
                sw = 22
            parts.append(f'<text x="{x + sw}" y="{y + 20}" class="p" fill="{t["muted"]}">{esc(text)}</text>')
            x += sw + text_width(text, 10) + 24
        if x > self.w:
            raise SystemExit(f"{self.slug}: legend runs to {x:.0f}px")
        self.legend_parts.append("".join(parts))

    def svg(self):
        t = self.t
        markers = "".join(
            f'<marker id="{self.variant}-arrow-{k}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">'
            f'<polygon points="0 0, 8 3, 0 6" fill="{c}"/></marker>'
            for k, c in (("muted", t["muted"]), ("accent", t["accent"]), ("link", t["link"])))
        style = (f".n{{font:600 12px {SANS}}}"
                 f".c{{font:500 11px {SANS}}}"
                 f".p{{font:400 10px {SANS}}}"
                 f".s{{font:400 9px {MONO}}}"
                 f".e{{font:500 7px {MONO};letter-spacing:.14em}}"
                 f".t{{font:500 7px {MONO};letter-spacing:.08em}}"
                 f".b{{font:500 8px {MONO};letter-spacing:.08em}}"
                 f".a{{font:400 8px {MONO};letter-spacing:.06em}}")
        body = "\n".join(["".join(self.zones), "\n".join(self.arrows), "".join(self.labels), "\n".join(self.nodes),
                          "".join(self.legend_parts)])
        return (f'<svg viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" xmlns="http://www.w3.org/2000/svg" '
                f'role="img" aria-labelledby="{self.variant}-title {self.variant}-desc">\n'
                f'<title id="{self.variant}-title">{esc(self.title)}</title>\n'
                f'<desc id="{self.variant}-desc">{esc(self.desc)}</desc>\n'
                f'<defs>{markers}<style>{style}</style></defs>\n'
                f'<rect width="100%" height="100%" fill="{t["paper"]}"/>\n{body}\n</svg>\n')


# --- the diagrams ---

def service_overview(theme):
    d = Diagram("service-overview", 960, 648, "What the service runs",
                "The service's parts on the cluster: people reach the ingress controller over HTTPS; the portal (web, "
                "portal-api, portal-worker, optional clamav) sends documents to the engine (engine-api, engine-worker, "
                "verapdf, litellm); both keep their data in block storage and the shared job folder; only the AI "
                "gateway, through the optional egress proxy, and the portal's mail leave the cluster.", theme)
    d.zone(16, 96, 720, 472, "KUBERNETES CLUSTER · 3 NODES · NAMESPACE PDF-REMEDIATION", kind="boundary", label_x=264)
    d.zone(40, 208, 208, 168, "PORTAL")
    d.zone(280, 208, 208, 168, "ENGINE · NO INTERNET")
    d.zone(40, 408, 448, 136, "DATA")

    d.arrow("M 144,72 V 128", "link")
    d.label(152, 94, "HTTPS 443", "link", anchor="start")
    d.arrow("M 144,176 V 240")
    d.arrow("M 240,316 H 256 Q 264,316 264,308 V 260 Q 264,252 272,252 H 288", "accent")
    d.arrow("M 144,376 V 440")
    d.arrow("M 384,376 V 440")
    d.arrow("M 480,348 H 520", "link")
    d.label(482, 328, "HTTPS", "link", anchor="start")
    d.arrow("M 712,348 H 784", "link")
    d.label(720, 328, "HTTPS 443", "link", anchor="start")
    d.arrow("M 224,208 V 200 Q 224,192 232,192 H 856 Q 864,192 864,184 V 176", "link")
    d.label(742, 174, "SMTP", "link", anchor="start")

    d.node(40, 16, 208, 56, "People", "a browser", kind="input", tag="USER")
    d.node(40, 128, 208, 48, "Ingress controller", "Traefik · on every node")
    for i, (name, tag, kind) in enumerate([("web", "app · /api/", "backend"), ("portal-api", "sign-in · uploads", "backend"),
                                           ("portal-worker", "engine · email", "backend"), ("clamav", "optional scan", "optional")]):
        d.chip(48, 240 + 32 * i, 192, name, tag, kind)
    for i, (name, tag, kind) in enumerate([("engine-api", "takes PDFs in", "backend"), ("engine-worker", "1 to 4 · KEDA", "focal"),
                                           ("verapdf", "PDF/UA check", "backend"), ("litellm", "AI gateway", "backend")]):
        d.chip(288, 240 + 32 * i, 192, name, tag, kind)
    for i, (name, tag) in enumerate([("postgres", "block"), ("redis-portal", "keeps nothing"), ("seaweedfs", "block · encrypted")]):
        d.chip(48, 440 + 32 * i, 192, name, tag)
    for i, (name, tag) in enumerate([("redis", "block · queue"), ("job folder", "NFS · shared")]):
        d.chip(288, 440 + 32 * i, 192, name, tag)
    d.node(520, 208, 192, 56, "Monitoring", "prometheus · grafana")
    d.node(520, 320, 192, 56, "egress-proxy", "optional · allow-list", kind="optional")
    d.node(784, 128, 160, 48, "Mail relay", "<smtp-relay>", kind="external")
    d.node(784, 320, 160, 56, "AI provider", "AWS Bedrock", kind="external", tag="EXT")

    d.legend(584, [("focal", "Remediates documents"), ("backend", "Program"), ("store", "Keeps data"),
                   ("optional", "Optional"), ("external", "Outside"), ("arrow:accent", "Documents"),
                   ("arrow:link", "Crosses the boundary")])
    return d


def install_order(theme):
    d = Diagram("install-order", 960, 456, "Who does what, in order",
                "The nine install steps by team: all teams decide the install values; network and storage then work in "
                "parallel; the OS team builds the servers once the network is ready; the middleware team installs the "
                "cluster, adds the add-ons once storage is ready too, and deploys; all teams work through the "
                "checklists; the middleware and OS teams rehearse a restore and a node going down.", theme)
    lanes = ["ALL TEAMS", "NETWORK", "STORAGE", "OS", "MIDDLEWARE"]
    for i, name in enumerate(lanes):
        d.lane(24 + 72 * i, 928, 72, name, shaded=i % 2 == 0)
    col = {c: 128 + 100 * (c - 1) for c in range(1, 9)}
    row = {lane: 32 + 72 * i for i, lane in enumerate(lanes)}

    d.arrow("M 176,88 V 124 Q 176,132 184,132 H 228")       # 1 -> 2
    d.arrow("M 152,88 V 196 Q 152,204 160,204 H 228")       # 1 -> 3
    d.arrow("M 308,132 H 360 Q 368,132 368,140 V 248")      # 2 -> 4
    d.arrow("M 308,204 H 360 a 8,8 0 0,1 16,0 H 560 Q 568,204 568,212 V 320")  # 3 -> 6, hopping 2 -> 4
    d.arrow("M 408,276 H 460 Q 468,276 468,284 V 320")      # 4 -> 5
    d.arrow("M 508,348 H 528")                              # 5 -> 6
    d.arrow("M 608,348 H 628")                              # 6 -> 7
    d.arrow("M 708,348 H 760 Q 768,348 768,340 V 88")       # 7 -> 8
    d.arrow("M 808,60 H 860 Q 868,60 868,68 V 320")         # 8 -> 9

    steps = [(1, "ALL TEAMS", "Decide", "a meeting"), (2, "NETWORK", "Network", "1–2 days"),
             (3, "STORAGE", "Storage", "varies"), (4, "OS", "Servers", "1 day"), (5, "MIDDLEWARE", "Cluster", "½ day"),
             (6, "MIDDLEWARE", "Add-ons", "1 day"), (7, "MIDDLEWARE", "Deploy", "½ day"),
             (8, "ALL TEAMS", "Sign off", "checklists"), (9, "MIDDLEWARE", "Rehearse", "½ day")]
    columns = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8}
    for n, lane, name, sub in steps:
        d.node(col[columns[n]], row[lane], 80, 56, name, sub, kind="focal" if n == 1 else "backend", tag=f"{n:02d}")
    d.text(128, 412, "Order runs left to right. Add-ons wait for both the cluster and storage; Rehearse is with the OS team.",
           anchor="start")
    d.legend(424, [("focal", "Decide at install: can't change later"), ("backend", "A step"),
                   ("arrow:muted", "Needs the step before")])
    return d


def network_paths(theme):
    d = Diagram("network-paths", 960, 560, "Ways in and out of the cluster",
                "People reach the service address on port 443 and administrators reach the API address on 6443 and the "
                "nodes on SSH, each address landing on any node; the cluster ports run between the three nodes only; "
                "outbound, the nodes reach DNS, NTP and package and image registries, the service reaches its allowed "
                "hosts only through the egress proxy, and the portal reaches the mail relay directly.", theme)
    d.zone(432, 32, 272, 464, "CLUSTER · ONE SUBNET, NO NAT", kind="boundary")

    d.arrow("M 160,100 H 224", "link")
    d.label(192, 80, "HTTPS 443", "link")
    d.arrow("M 368,100 H 432", "link")
    d.label(400, 80, "ANY NODE", "link")
    d.arrow("M 160,372 H 224", "link")
    d.label(192, 352, "6443", "link")
    d.arrow("M 368,372 H 432", "link")
    d.label(400, 352, "ANY NODE", "link")
    d.arrow("M 88,400 V 464 Q 88,472 96,472 H 432", "link")
    d.label(304, 452, "SSH 22", "link")
    d.arrow("M 704,100 H 768", "link")
    d.label(736, 80, "53 · 123", "link")
    d.arrow("M 704,180 H 768", "link")
    d.label(736, 160, "443", "link")
    d.arrow("M 680,356 H 768", "accent")
    d.label(740, 336, "443 ONLY", "accent")
    d.arrow("M 704,436 H 768", "link")
    d.label(736, 416, "SMTP", "link")

    d.node(16, 72, 144, 56, "People", "browsers", kind="input", tag="USER")
    d.node(16, 344, 144, 56, "Administrators", "<admin-network>", kind="input", tag="ADMIN")
    d.node(224, 72, 144, 56, "Service address", "<service-vip>", kind="focal", tag="VIP")
    d.node(224, 344, 144, 56, "API address", "<api-vip>", tag="VIP")
    for i in range(3):
        d.node(456, 72 + 80 * i, 224, 56, f"node-{i + 1}", "RKE2 server · ingress", tag="VM")
    d.node(456, 328, 224, 56, "egress-proxy", "EGRESS_PROXY=enforce", kind="focal", tag="POD")
    d.text(456, 424, "BETWEEN THE NODES ONLY", size=7, color="soft", anchor="start", mono=True)
    for i, line in enumerate(["6443 API · 9345 join · 10250 kubelet", "2379–2381 etcd · 51820/udp WireGuard",
                              "pods 10.42/16 · services 10.43/16"]):
        d.text(456, 444 + 16 * i, line, size=9, anchor="start", mono=True)
    d.node(768, 72, 176, 56, "Campus DNS · NTP", "names and time", kind="external")
    d.node(768, 152, 176, 56, "Mirrors, registries", "OS · RKE2 · images", kind="external")
    d.node(768, 328, 176, 56, "Allowed hosts", "Bedrock · Adobe · ClamAV", kind="external")
    d.node(768, 408, 176, 56, "Mail relay", "<smtp-relay> · 25, 587, 465", kind="external")

    d.legend(512, [("input", "People"), ("focal", "The only doors for users and the service"), ("backend", "Server or pod"),
                   ("external", "Campus or internet"), ("arrow:link", "Allowed path")])
    return d


def storage_kinds(theme):
    d = Diagram("storage-kinds", 960, 504, "Two kinds of storage",
                "A database's block disk is attached to one server at a time, the one running its pod, and the storage "
                "driver moves it when the pod moves; the engine's job folder is one NFS share that every server mounts "
                "at once.", theme)
    d.arrow("M 256,248 V 168", "accent")
    d.label(196, 202, "ATTACHED", "accent", anchor="start")
    d.arrow("M 384,248 V 168", dashed=True)
    d.label(392, 202, "AFTER A MOVE", anchor="start")
    for x in (88, 568, 784):
        d.arrow(f"M {x},376 V 168")
    d.label(792, 266, "MOUNTED", anchor="start")

    for i, x in enumerate((40, 344, 648)):
        d.host(x, 40, 272, 128, f"server-{i + 1}", "VM")
    d.chip(56, 80, 240, "postgres", "on the block disk", "focal")
    d.chip(56, 112, 240, "engine-worker", "job folder")
    d.chip(360, 80, 240, "postgres", "if its pod moves here", "optional")
    d.chip(360, 112, 240, "engine-worker", "job folder")
    d.chip(664, 112, 240, "engine-worker", "job folder")
    d.node(192, 248, 256, 56, "Block disk", "one server at a time · RWO", kind="focal", tag="DISK")
    d.node(40, 376, 880, 56, "Shared folder (NFS)", "the engine's job folder · ReadWriteMany · every server mounts it",
           kind="store", tag="NFS")

    d.legend(456, [("focal", "A database and its disk"), ("backend", "Server"), ("store", "Shared, by design"),
                   ("optional", "Where it goes when moved"), ("arrow:muted:dashed", "Moved by the driver")])
    return d


def patching_cycle(theme):
    d = Diagram("patching-cycle", 960, 288, "Patching one server at a time",
                "Each server in turn: drained so its pods move to the other two and documents being remediated finish, "
                "patched and rebooted, back as Ready but not scheduling, then uncordoned before the next server.", theme)
    d.arrow("M 200,104 H 280")
    d.label(240, 84, "DRAIN")
    d.arrow("M 432,104 H 512")
    d.label(472, 84, "DRAINED")
    d.arrow("M 664,104 H 744")
    d.label(704, 84, "REBOOTED")
    d.arrow("M 820,136 V 192 Q 820,200 812,200 H 132 Q 124,200 124,192 V 136")
    d.label(472, 180, "UNCORDON")

    d.node(48, 72, 152, 64, "Ready", "running its pods")
    d.node(280, 72, 152, 64, "Draining", "pods move to the others", kind="focal")
    d.node(512, 72, 152, 64, "Patching", "patch · reboot")
    d.node(744, 72, 152, 64, "Back, cordoned", "SchedulingDisabled")
    d.text(472, 240, "Then the next server, once every pod in pdf-remediation shows Running.")
    d.text(472, 256, "With the drain-on-shutdown service installed, the drain and uncordon happen on their own at reboot.")
    return d


def engine_nodes(theme):
    d = Diagram("engine-nodes", 960, 392, "The PDF engine on nodes of its own",
                "With ENGINE_NODES set, the pods that open uploaded documents run only on labelled, tainted agent nodes, "
                "and the portal, data, shared services and the cluster's control plane stay on the three server nodes.",
                theme)
    d.zone(24, 40, 424, 288, "SERVER NODES · CONTROL PLANE AND ETCD")
    d.zone(512, 40, 424, 288, "ENGINE NODES · AGENT ONLY", kind="security")

    d.arrow("M 408,140 H 552", "link")
    d.label(482, 120, "DOCUMENTS", "link")

    d.host(48, 72, 376, 232, "server node", "VM", badge="×3")
    for i, (name, tag) in enumerate([("web · portal-api · portal-worker", "portal"), ("control plane · etcd", "RKE2 server"),
                                     ("postgres · redis · seaweedfs", "data"), ("litellm · verapdf · monitoring", "shared")]):
        d.chip(64, 128 + 32 * i, 344, name, tag, "backend")
    d.host(536, 72, 376, 232, "engine node", "VM", badge="×2", note="label + taint pdfrem/role=engine:NoSchedule")
    for i, (name, tag, kind) in enumerate([("engine-api · scheduler", "takes PDFs in", "backend"),
                                           ("engine-worker", "1 to ENGINE_WORKERS_MAX", "focal"),
                                           ("adobe-worker", "if Adobe is on", "backend"),
                                           ("music · formula readers", "on demand", "backend")]):
        d.chip(552, 128 + 32 * i, 344, name, tag, kind)

    d.legend(344, [("security", "Only the engine's pods may run here"), ("backend", "Pods"),
                   ("focal", "Opens uploaded documents"), ("arrow:link", "Between nodes")])
    return d


# --- architecture ---

def how_it_works(theme):
    d = Diagram("how-it-works", 960, 408, "How it works",
                "People use the portal in a browser; the portal keeps records and files and sends documents to the "
                "engine, which checks them with veraPDF and Tesseract and makes its AI calls through the AI gateway to "
                "local models, or to cloud models and Adobe PDF Services when those are turned on.", theme)
    d.arrow("M 144,124 H 192", "link")
    d.label(168, 104, "HTTPS", "link")
    d.arrow("M 368,124 H 448", "accent")
    d.label(408, 104, "SUBMIT, POLL", "accent")
    d.arrow("M 280,152 V 208")
    d.arrow("M 624,124 H 720")
    d.label(672, 104, "CHECK, OCR")
    d.arrow("M 600,96 V 48 Q 600,40 608,40 H 720", dashed=True)
    d.label(664, 22, "OPT-IN")
    d.arrow("M 536,152 V 208")
    d.label(544, 174, "AI CALLS", anchor="start")
    d.arrow("M 624,236 H 720")
    d.arrow("M 600,264 V 308 Q 600,316 608,316 H 720", dashed=True)
    d.label(664, 298, "OPT-IN")

    d.node(16, 96, 128, 56, "Browser", "people", kind="input", tag="USER")
    d.node(192, 96, 176, 56, "Portal", "web app, API, worker")
    d.node(448, 96, 176, 56, "Engine", "API + workers", kind="focal")
    for i, (name, tag) in enumerate([("PostgreSQL", "records"), ("Redis", "queues, limits"), ("S3 storage", "encrypted")]):
        d.chip(192, 208 + 32 * i, 176, name, tag)
    d.node(448, 208, 176, 56, "AI gateway", "LiteLLM")
    d.node(720, 16, 224, 48, "Adobe PDF Services", "Autotag, if turned on", kind="optional")
    d.node(720, 96, 224, 56, "veraPDF · Tesseract", "PDF/UA checks, OCR")
    d.node(720, 208, 224, 56, "Local models", "LocalAI, vLLM, Ollama")
    d.node(720, 288, 224, 56, "Cloud models", "AI levels, if turned on", kind="optional")
    d.legend(368, [("input", "People"), ("focal", "Remediates documents"), ("backend", "Program"), ("store", "Keeps data"),
                   ("optional", "Only if turned on"), ("arrow:accent", "Documents")])
    return d


def system_context(theme):
    d = Diagram("system-context", 960, 640, "System context",
                "The portal (web, portal-api, portal-worker, optional ClamAV, with PostgreSQL, its own Redis and an "
                "encrypted object store) sends documents to the engine (engine API, workers, Adobe worker, veraPDF and "
                "Tesseract, with a Redis queue and a shared job folder); every AI call goes through the AI gateway to "
                "local models, or cloud models when turned on; the Adobe worker reaches Adobe PDF Services when turned "
                "on; browsers and scripts with a personal API token reach the portal over HTTPS.", theme)
    d.zone(176, 40, 360, 224, "PORTAL")
    d.zone(176, 304, 360, 216, "ENGINE")
    d.zone(584, 336, 192, 136, "AI GATEWAY")

    d.arrow("M 144,78 H 192", "link")
    d.label(168, 58, "HTTPS", "link")
    d.arrow("M 144,148 H 152 Q 160,148 160,140 V 98 Q 160,90 168,90 H 192", "link", dashed=True)
    d.arrow("M 272,192 V 336", "accent")
    d.label(280, 278, "SUBMIT, POLL", "accent", anchor="start")
    d.arrow("M 536,84 H 600")
    d.label(568, 64, "MAIL")
    d.arrow("M 536,388 H 600")
    d.label(568, 368, "AI CALLS")
    d.arrow("M 760,388 H 800")
    d.arrow("M 760,404 H 772 Q 780,404 780,412 V 460 Q 780,468 788,468 H 800", dashed=True)
    d.arrow("M 320,520 V 540 Q 320,548 328,548 H 800", dashed=True)
    d.label(600, 528, "ADOBE QUEUE")

    d.node(16, 50, 128, 56, "Browser", "React app", kind="input", tag="USER")
    d.node(16, 120, 128, 56, "Scripts", "personal API token", kind="input")
    for i, (name, tag, kind) in enumerate([("web", "nginx", "backend"), ("portal-api", "FastAPI", "backend"),
                                           ("clamav", "optional", "optional"), ("portal-worker", "jobs", "backend")]):
        d.chip(192, 72 + 32 * i, 160, name, tag, kind)
    for i, (name, tag) in enumerate([("PostgreSQL", "records"), ("Redis", "limits"), ("object store", "S3")]):
        d.chip(368, 72 + 32 * i, 152, name, tag)
    for i, (name, tag, kind) in enumerate([("engine API", "FastAPI", "focal"), ("workers", "Celery", "backend"),
                                           ("adobe-worker", "if on", "backend"), ("veraPDF", "service", "backend"),
                                           ("Tesseract", "OCR", "backend")]):
        d.chip(192, 336 + 32 * i, 160, name, tag, kind)
    for i, (name, tag) in enumerate([("Redis", "the queue"), ("job folder", "shared")]):
        d.chip(368, 336 + 32 * i, 152, name, tag)
    d.node(600, 360, 160, 56, "AI gateway", "LiteLLM")
    d.chip(600, 432, 160, "LiteLLM DB", "spend, keys")
    d.node(600, 56, 176, 56, "Mail", "SMTP, SES or an API", kind="external")
    d.node(800, 360, 144, 56, "Local models", "LocalAI, vLLM", kind="external")
    d.node(800, 440, 144, 56, "Cloud models", "AI levels", kind="optional")
    d.node(800, 520, 144, 56, "Adobe", "PDF Services", kind="optional")
    d.legend(592, [("input", "People and scripts"), ("focal", "Takes documents in"), ("backend", "Program"),
                   ("store", "Keeps data"), ("optional", "Only if turned on"), ("arrow:accent", "Documents")])
    return d


def engine_pipeline(theme):
    d = Diagram("engine-pipeline", 960, 952, "The engine's pipeline",
                "Each document is validated, has its font maps fixed and its layout analyzed; scanned pages get an OCR "
                "text layer; the AI adds alt text, tables, reading order and notation; it is tagged without changing "
                "how it looks and validated, with a modified file only if fonts still fail; every output is verified, "
                "and an integrity failure fails the job; otherwise the reports are written.", theme)
    steps = [(80, "1. Validate the original", "veraPDF and the audit: the 'before'"),
             (152, "2. Fix font Unicode maps", "no visual change"),
             (224, "3. Analyze the layout", "text, headings, lists, tables, images, columns"),
             (384, "4. AI enrichment, through the gateway", "alt text, tables, reading order, notation"),
             (456, "5. Tag, non-destructively", "nested lists, heading levels, read-aloud math"),
             (528, "6. Validate, fix glyph widths", "only if the pages stay pixel-identical"),
             (688, "8. Verify every output", "integrity, semantics, AI review, the tag map"),
             (848, "9. Reports", "before and after, the review list; Markdown, JSON, PDF")]
    for a, b in [(56, 80), (128, 152), (200, 224), (272, 296), (360, 384), (432, 456), (504, 528), (576, 600),
                 (664, 688), (736, 760), (824, 848)]:
        d.arrow(f"M 240,{a} V {b}")
    d.label(248, 366, "NO", anchor="start")
    d.label(248, 670, "NO", anchor="start")
    d.label(248, 830, "YES", anchor="start")
    d.arrow("M 320,328 H 536")
    d.label(428, 308, "YES")
    d.arrow("M 716,356 V 400 Q 716,408 708,408 H 440")
    d.arrow("M 320,632 H 536")
    d.label(428, 612, "YES")
    d.arrow("M 716,660 V 704 Q 716,712 708,712 H 440")
    d.arrow("M 320,792 H 536", "accent")
    d.label(428, 772, "NO", "accent")

    d.oval(140, 16, 200, 40, "Upload")
    for y, name, sub in steps:
        d.node(40, y, 400, 48, name, sub, kind="store" if name.startswith("9") else "backend")
    d.diamond(240, 328, 160, 64, ["Scanned", "pages?"])
    d.diamond(240, 632, 160, 64, ["Font failures", "left?"])
    d.diamond(240, 792, 160, 64, ["Integrity", "OK?"])
    d.node(536, 300, 360, 56, "OCR: an invisible text layer", "AI fixes unsure words; figures found in the scan")
    d.node(536, 604, 360, 56, "7. A modified file", "font substitutes; failing pages flattened if needed")
    d.node(536, 764, 360, 56, "The job fails", "tag_integrity_failed; outputs kept to inspect", kind="focal")
    d.legend(912, [("backend", "A step"), ("store", "The result"), ("focal", "Stops the job")])
    return d


def tag_editor_save(theme):
    d = Diagram("tag-editor-save", 960, 512, "Saving edits in the tag manager",
                "A reviewer edits tags in the tag manager, which offers only valid changes; saving queues a new "
                "version, the portal worker sends the base version's PDF and the edits to the engine, which applies "
                "them with the pages unchanged and checks integrity, semantics, veraPDF and, if asked, AI review; the "
                "new version then appears with its tag map and findings.", theme)
    end = d.sequence(
        [("R", "Reviewer", "a person", "input"), ("V", "Tag manager", "in the browser", "backend"),
         ("P", "Portal API", "portal-api", "backend"), ("W", "Portal worker", "portal-worker", "backend"),
         ("E", "Engine", "engine API, worker", "focal")],
        [("call", "R", "V", "edit: role, alt text, cells, order,\nmerge, split, areas"),
         ("self", "V", "offers only valid changes\n(roles.ts mirrors the engine)"),
         ("call", "R", "P", "save, with AI review if wanted"),
         ("call", "P", "W", "new version 'Human reviewed N', queued"),
         ("call", "W", "E", "POST /edit: base PDF + edits"),
         ("self", "E", "apply them;\npages stay pixel-identical"),
         ("self", "E", "integrity (fails the save),\nsemantics, veraPDF, AI review"),
         ("hl", "W", "P", "new version ready: tag map, findings")])
    d.h = end + 8
    return d


def service_access(theme):
    d = Diagram("service-access", 960, 488, "Who can use a service, and where pages come from",
                "A service is used only after a super admin switches it on, a group admin enables it for the group "
                "(never an external one for a local-only group), it is given to everyone or person by person, and the "
                "person picks it; then pages are reserved on every service the document uses, from the global pool, "
                "the group pool and the person's limit at once; if any is short the upload is refused, naming it, "
                "otherwise it is queued.", theme)
    d.zone(16, 232, 432, 136, "PAGES, PER SERVICE · ALL THREE CHECKED")
    for a, b in [(224, 256), (464, 496), (704, 736)]:
        d.arrow(f"M {a},76 H {b}")
    d.arrow("M 840,104 V 292 Q 840,300 832,300 H 680")
    d.arrow("M 448,300 H 520", dashed=True)
    d.label(484, 280, "PAGES")
    d.arrow("M 600,260 V 208", "accent")
    d.label(608, 228, "ANY SHORT", "accent", anchor="start")
    d.arrow("M 600,340 V 372")

    d.node(16, 48, 208, 56, "Super admin", "switches it on")
    d.node(256, 48, 208, 56, "Group admin", "enables it for the group")
    d.node(496, 48, 208, 56, "To the people", "everyone, or one by one")
    d.node(736, 48, 208, 56, "The person picks it", "at upload, or a fixed level", kind="input")
    d.text(256, 128, "A group admin can't enable an external service for a local-only group.", anchor="start")
    for i, (name, tag) in enumerate([("Global pool", "optional, for everyone"), ("Group pool", "ticketed top-ups"),
                                     ("Person's limit", "their own, or the group's default")]):
        d.chip(32, 264 + 32 * i, 400, name, tag)
    d.diamond(600, 300, 160, 80, ["Reserve", "pages?"])
    d.node(504, 152, 192, 56, "Refused", "names the service and level", kind="focal")
    d.oval(520, 372, 160, 56, "Queued", "pages reserved")
    d.legend(448, [("backend", "Who switches it on"), ("input", "The person"), ("store", "Pages"),
                   ("focal", "Refused, with the reason")])
    return d


def document_path(theme):
    d = Diagram("document-path", 960, 824, "One document's path",
                "A person uploads with a processing service, AI level and kind; the portal checks access and counts "
                "pages in a limited child process, holds the document if the service asks for confirmation or the PDF "
                "is signed, then reserves pages; the worker orders the queue, optionally has the engine classify the "
                "document, and submits it; the engine's AI calls go through the gateway, which returns answers and "
                "costs; the worker polls, fetches the files, stores the versions, commits or releases the pages, and "
                "emails the person.", theme)
    end = d.sequence(
        [("U", "Person", "in the browser", "input"), ("P", "Portal API", "portal-api", "backend"),
         ("W", "Portal worker", "portal-worker", "backend"), ("E", "Engine", "API + workers", "focal"),
         ("G", "AI gateway", "LiteLLM", "backend")],
        [("call", "U", "P", "upload: service, AI level, kind"),
         ("self", "P", "access checks; pages counted in a\nlimited child process; per-document limit"),
         ("frame", "OPT", "[the service asks first, or the PDF is signed]", "U", "P",
          [("ret", "P", "U", "held: nothing reserved"), ("call", "U", "P", "confirm")]),
         ("self", "P", "reserve pages (ledger: reserve)"),
         ("self", "W", "queue order: per-person limit, large\ndocuments, daily caps, paused queues"),
         ("frame", "OPT", "[Let AI decide]", "W", "E", [("call", "W", "E", "classify: the first pages' text")]),
         ("call", "W", "E", "submit, with the level's or kind's models"),
         ("call", "E", "G", "AI calls, tagged with job and level"),
         ("ret", "G", "E", "answers + cost per call"),
         ("call", "W", "E", "poll, then fetch the files"),
         ("self", "W", "store versions and AI cost; ledger:\ncommit, or release on failure"),
         ("hl", "W", "U", "email: at once, or in a daily summary")])
    d.h = end + 8
    return d


def adobe_route(theme):
    d = Diagram("adobe-route", 960, 736, "The Adobe route",
                "The engine queues an Adobe job; the Adobe worker checks the original, splits it if needed, and for "
                "each part, a few at a time, takes a rate-limit slot, uploads it to Adobe PDF Services, starts Autotag "
                "and waits, downloads the tagged PDF and report, and deletes the files at Adobe; it then merges the "
                "parts, checks Adobe's file, adds AI alt text and writes the improved copy, which the portal worker "
                "fetches.", theme)
    end = d.sequence(
        [("W", "Portal worker", "portal-worker", "backend"), ("E", "Engine API", "engine-api", "backend"),
         ("A", "Adobe worker", "adobe-worker", "focal"), ("L", "Rate limiter", "Redis", "store"),
         ("X", "Adobe", "PDF Services", "external")],
        [("call", "W", "E", "submit, route = adobe"),
         ("call", "E", "A", "queue 'adobe'"),
         ("self", "A", "veraPDF and audit of the original;\nsplit if over Adobe's limits"),
         ("frame", "LOOP", "[each part, ADOBE_MAX_CONCURRENT at once]", "A", "X",
          [("call", "A", "L", "a slot and request budget"),
           ("call", "A", "X", "token (cached), create asset, upload"),
           ("call", "A", "X", "start Autotag; poll until done"),
           ("ret", "X", "A", "tagged PDF and Adobe's report"),
           ("hl", "A", "X", "delete the assets")]),
         ("self", "A", "merge the parts; veraPDF,\ntag integrity, tag map"),
         ("self", "A", "AI alt text where it's missing\n(on by default)"),
         ("self", "A", "improved copy: alt text, document\nand font fixes, bookmarks, checks"),
         ("call", "W", "E", "poll, then fetch the files")])
    d.h = end + 8
    return d


def portal_architecture(theme):
    d = Diagram("portal-architecture", 960, 472, "The portal as built",
                "The browser's React app reaches nginx, which serves the app and passes /api to the portal API; the API "
                "uses PostgreSQL, a Redis for rate limits, an S3 object store and mail, with SAML sign-in designed but "
                "on hold; the portal worker shares the database and store and submits documents to the engine over "
                "internal HTTP with an API key; the engine makes all AI calls through the AI gateway.", theme)
    d.zone(560, 48, 192, 168, "STORES")
    d.arrow("M 144,108 H 176", "link")
    d.arrow("M 320,108 H 352")
    d.arrow("M 512,108 H 560")
    d.arrow("M 480,80 V 32 Q 480,24 488,24 H 864 Q 872,24 872,32 V 80")
    d.label(680, 6, "MAIL")
    d.arrow("M 384,136 V 268 Q 384,276 376,276 H 320", dashed=True)
    d.label(348, 256, "SAML")
    d.arrow("M 576,248 V 216")
    d.arrow("M 592,276 H 672", "accent")
    d.label(632, 256, "SUBMIT", "accent")
    d.arrow("M 744,304 V 360")
    d.label(752, 326, "AI CALLS", anchor="start")

    d.node(16, 80, 128, 56, "Browser", "React app", kind="input", tag="USER")
    d.node(176, 80, 144, 56, "nginx", "app + /api proxy")
    d.node(352, 80, 160, 56, "Portal API", "FastAPI")
    for i, (name, tag) in enumerate([("PostgreSQL", "records"), ("Redis", "rate limits"), ("object store", "S3, SeaweedFS")]):
        d.chip(568, 80 + 32 * i, 176, name, tag)
    d.node(800, 80, 144, 56, "Mail", "SMTP or a mail API", kind="external")
    d.node(176, 248, 144, 56, "Identity provider", "SAML · on hold", kind="optional")
    d.node(432, 248, 160, 56, "Portal worker", "jobs, mail, reports")
    d.node(672, 248, 144, 56, "Engine", "API + workers", kind="focal")
    d.node(672, 360, 144, 56, "AI gateway", "LiteLLM")
    d.legend(432, [("input", "People"), ("backend", "Program"), ("store", "Keeps data"), ("optional", "On hold"),
                   ("arrow:accent", "Documents")])
    return d


def verification_layers(theme):
    d = Diagram("verification-layers", 960, 384, "Three layers of checks, then a person",
                "Every output is checked in three layers: integrity, which fails the job or save on any error; "
                "semantics and an AI review by a model of another family, which add to the review list; and the "
                "review list goes to a person, who fixes what matters in the tag manager.", theme)
    for y in (88, 168, 248):
        d.arrow(f"M 480,{y} V {y + 8}")
    d.layer(16, 16, 928, 72, "L1", "Integrity",
            ["pikepdf and its own content-stream scan, no code shared with the tagger",
             "each sequence in one tag; ParentTree, roles, balanced marked content, nesting"],
            "Fails the job (or the save)", kind="focal")
    d.layer(16, 96, 928, 72, "L2", "Semantics",
            ["pdfplumber, independent of PyMuPDF",
             "headings, figures, hidden text, tables, lists, links, order, language, contrast"], "Review list")
    d.layer(16, 176, 928, 72, "L3", "AI review",
            ["rendered images and a review model from another family",
             "alt text right, partly wrong or wrong; charts, formulas, music; 10% or every figure"], "Review list")
    d.layer(16, 256, 928, 72, "→", "A person",
            ["the Needs review list, most important first",
             "fixes in the tag manager, which runs these checks again on every save"], "Decides", kind="optional")
    d.legend(344, [("focal", "Blocks the result"), ("backend", "Adds to the review list"), ("optional", "A person")])
    return d


def sign_in(theme):
    d = Diagram("sign-in", 960, 664, "Signing in",
                "A person enters their email; the portal applies rate limits, makes a 6-digit code kept only as an "
                "HMAC, and gives the same answer for every address, emailing the code afterwards; the person types it "
                "in, and the portal returns a 10-minute access token kept in memory and a refresh cookie that is "
                "rotated on each use, reuse of an old one ending the session.", theme)
    end = d.sequence(
        [("U", "Person", "a browser", "input"), ("B", "Web app", "React", "backend"),
         ("P", "Portal API", "portal-api", "focal"), ("M", "Mail", "the campus relay", "external")],
        [("call", "U", "B", "enter work email"),
         ("call", "B", "P", "ask for a code (bot check if on)"),
         ("self", "P", "rate limits per address and IP;\na 6-digit code, kept as an HMAC"),
         ("ret", "P", "B", "'code sent': the same for every address"),
         ("async", "P", "M", "the code, after the answer"),
         ("async", "M", "U", "email: the code, valid 10 minutes"),
         ("call", "U", "B", "type the code"),
         ("call", "B", "P", "check it (a few attempts)"),
         ("hl", "P", "B", "access token (10 min, in memory)\n+ refresh cookie (HttpOnly, rotated)"),
         ("frame", "LOOP", "[while signed in]", "B", "P",
          [("call", "B", "P", "API calls with the access token"),
           ("call", "B", "P", "refresh: new tokens; reusing\nan old one ends the session")])])
    d.h = end + 8
    return d


# --- user guide ---

def document_lifecycle(theme):
    d = Diagram("document-lifecycle", 960, 376, "A document's life",
                "A document waits in the queue (after being held for confirmation, for options that ask first), is "
                "processed and is then done, or fails with its pages given back; held documents can be canceled; a "
                "done document can be marked done, with 24 hours to undo, after which its files are removed, as they "
                "also are when the retention period ends.", theme)
    for a, b in [(152, 184), (304, 336), (456, 488), (608, 640), (760, 792)]:
        d.arrow(f"M {a},148 H {b}")
    d.arrow("M 92,176 V 232")
    d.label(100, 198, "CANCEL", anchor="start")
    d.arrow("M 396,176 V 232")
    d.label(404, 198, "FAILS", anchor="start")
    d.arrow("M 548,120 V 80 Q 548,72 556,72 H 844 Q 852,72 852,80 V 120", dashed=True)
    d.label(700, 54, "RETENTION ENDS")
    d.arrow("M 716,176 V 200 Q 716,208 708,208 H 564 Q 556,208 556,200 V 176", dashed=True)
    d.label(636, 214, "KEEP THE FILES")

    d.node(32, 120, 120, 56, "Held", "confirm to send", kind="optional")
    d.node(184, 120, 120, 56, "Waiting", "in the queue")
    d.node(336, 120, 120, 56, "Processing", "usually a minute")
    d.node(488, 120, 120, 56, "Done", "ready to download", kind="focal")
    d.node(640, 120, 120, 56, "Marked done", "24 h to undo")
    d.node(792, 120, 120, 56, "Removed", "files gone", kind="store")
    d.node(32, 232, 120, 56, "Canceled", "nothing used", kind="store")
    d.node(336, 232, 120, 56, "Failed", "pages given back", kind="store")
    d.text(32, 320, "Held only with an option that asks before sending. Scripts see these as held, queued, "
           "processing, completed, failed and canceled.", anchor="start")
    d.legend(336, [("optional", "Only for options that ask"), ("backend", "On its way"), ("focal", "Your results"),
                   ("store", "The end")])
    return d


def scripting_flow(theme):
    d = Diagram("scripting-flow", 960, 584, "A script's round trip",
                "A script posts PDFs with its token and learns, for each, its job id or why it was refused; documents "
                "for an option that asks first wait for the person to send them, unless the token confirms "
                "automatically; with status access the script checks each job; with download access it lists the "
                "finished documents it hasn't fetched yet and downloads each one's zip.", theme)
    end = d.sequence(
        [("S", "Script", "with its token", "input"), ("P", "Portal API", "/api/v1", "focal"),
         ("U", "Person", "My documents", "input")],
        [("call", "S", "P", "POST /api/v1/jobs with the PDFs"),
         ("ret", "P", "S", "each file: accepted with job.id, or why not"),
         ("frame", "OPT", "[an option that asks before sending]", "P", "U",
          [("call", "U", "P", "Send (or the token confirms automatically)")]),
         ("frame", "LOOP", "[a token that may check status]", "S", "P",
          [("call", "S", "P", "GET /api/v1/jobs/{id}"),
           ("ret", "P", "S", "status: queued ... completed, failed, held")]),
         ("call", "S", "P", "GET /documents?status=done\n&not_downloaded=true"),
         ("ret", "P", "S", "finished documents this token hasn't fetched"),
         ("call", "S", "P", "GET /jobs/{id}/files.zip"),
         ("hl", "P", "S", "each document's zip")])
    d.h = end + 8
    return d


# --- operations ---

def rollback_decision(theme):
    d = Diagram("rollback-decision", 960, 392, "Rolling back: which way",
                "When a release breaks something: a small, clear fix goes forward; otherwise, a release without a "
                "migration is rolled back by deploying the earlier images; with a migration the database goes back "
                "first, by downgrade when that is safe, or by restoring the backup deploy.sh made, and then the "
                "earlier release is deployed.", theme)
    d.arrow("M 176,68 H 212")
    d.arrow("M 348,68 H 412")
    d.label(380, 48, "NO")
    d.arrow("M 548,68 H 612")
    d.label(580, 48, "YES")
    d.arrow("M 748,68 H 784")
    d.label(766, 48, "YES")
    d.arrow("M 280,108 V 160")
    d.label(288, 128, "YES", anchor="start")
    d.arrow("M 480,108 V 160")
    d.label(488, 128, "NO", anchor="start")
    d.arrow("M 680,108 V 160")
    d.label(688, 128, "NO", anchor="start")
    d.arrow("M 680,216 V 256 Q 680,264 672,264 H 504 Q 496,264 496,256 V 216")
    d.arrow("M 864,96 V 284 Q 864,292 856,292 H 472 Q 464,292 464,284 V 216")

    d.oval(16, 40, 160, 56, "A release breaks", "decide quickly", kind="input")
    d.diamond(280, 68, 136, 80, ["Small, clear", "fix?"])
    d.diamond(480, 68, 136, 80, ["A migration in", "the release?"])
    d.diamond(680, 68, 136, 80, ["Downgrade", "safe?"])
    d.node(784, 40, 160, 56, "Downgrade", "deploy.sh downgrade", kind="store")
    d.node(208, 160, 144, 56, "Fix forward", "build and deploy")
    d.node(392, 160, 176, 56, "The earlier release", "§3, then check (§6)", kind="focal")
    d.node(600, 160, 160, 56, "Restore", "restore.sh the backup", kind="store")
    d.text(16, 332, "A downgrade loses what the undone migrations added; a restore loses everything since the backup "
           "deploy.sh made before migrating.", anchor="start")
    d.legend(352, [("input", "Start"), ("store", "The database goes back first"), ("focal", "Where every rollback ends")])
    return d


def secret_flow(theme):
    d = Diagram("secret-flow", 960, 360, "How secrets are kept",
                "The values live in engine.env and portal.env on the administrator's machine, or the password manager; "
                "deploy.sh splits them into Kubernetes secrets so each pod gets only what it reads, and restarts the "
                "pods whose settings changed; rotate-secret.sh makes a new value, sets a database role's password "
                "first, writes the file and runs deploy.sh.", theme)
    d.zone(16, 40, 400, 264, "THE ADMINISTRATOR'S MACHINE")
    d.zone(480, 40, 464, 264, "THE CLUSTER · ONE SECRET PER KIND OF POD")
    d.arrow("M 232,100 H 208")
    d.arrow("M 352,128 V 200")
    d.label(360, 156, "RUNS", anchor="start")
    d.arrow("M 208,124 H 256 Q 264,124 264,132 V 200")
    d.arrow("M 120,200 V 148", dashed=True)
    d.label(128, 166, "FILLED IN", anchor="start")
    d.arrow("M 400,228 H 496", "accent")
    d.label(448, 208, "SECRETS", "accent")

    d.chip(32, 76, 176, "engine.env", "engine, AI")
    d.chip(32, 112, 176, "portal.env", "portal, DB")
    d.node(232, 72, 168, 56, "rotate-secret.sh", "new value, file, deploy")
    d.node(32, 200, 176, 56, "Password manager", "a copy of each value", kind="external")
    d.node(232, 200, 168, 56, "deploy.sh", "splits, then restarts", kind="focal")
    for i, (name, tag) in enumerate([("pdfrem-engine-env", "engine pods, readers, Redis"),
                                     ("pdfrem-gateway-env", "the AI gateway"),
                                     ("pdfrem-adobe-env", "the Adobe workers"),
                                     ("pdfrem-db-env", "Postgres, migrations"),
                                     ("pdfrem-portal-env", "portal, its Redis, SeaweedFS"),
                                     ("pdfrem-monitoring-env", "exporter, Grafana (optional)")]):
        d.chip(496, 72 + 36 * i, 432, name, tag)
    d.legend(320, [("store", "Values"), ("focal", "Splits and deploys"), ("external", "Kept apart"),
                   ("arrow:accent", "Only what each pod reads")])
    return d


# --- deployment ---

def reference_rke2(theme):
    d = Diagram("reference-rke2", 960, 600, "The reference deployment",
                "People reach Cloudflare's edge, which passes traffic through an outbound-only tunnel to cloudflared in "
                "a three-node RKE2 cluster; the portal and engine run there with their data on the NAS, block storage "
                "over iSCSI and a shared NFS folder; the AI gateway reaches a GPU server on the LAN and, for opt-in "
                "levels, OpenRouter; mail goes out through Cloudflare's email service, and images come from GHCR.",
                theme)
    d.zone(16, 96, 720, 448, "RKE2 CLUSTER · 3 NODES · NAMESPACE PDF-REMEDIATION", kind="boundary", label_x=264)
    d.zone(40, 208, 208, 128, "PORTAL")
    d.zone(280, 208, 208, 128, "ENGINE")
    d.zone(40, 368, 448, 144, "DATA")
    d.zone(760, 432, 184, 104, "NAS")

    d.arrow("M 296,44 H 248", "link")
    d.label(272, 24, "HTTPS", "link")
    d.arrow("M 144,72 V 128", "link")
    d.label(152, 94, "TUNNEL", "link", anchor="start")
    d.arrow("M 144,176 V 240")
    d.arrow("M 240,316 H 256 Q 264,316 264,308 V 260 Q 264,252 272,252 H 288", "accent")
    d.arrow("M 144,336 V 400")
    d.arrow("M 384,336 V 400")
    d.arrow("M 488,448 H 760", dashed=True)
    d.label(600, 428, "BLOCK, NFS")
    d.arrow("M 480,310 H 784", "link")
    d.label(632, 290, "LAN", "link")
    d.arrow("M 480,322 H 744 Q 752,322 752,330 V 384 Q 752,392 760,392 H 784", "link", dashed=True)
    d.label(704, 344, "OPT-IN", "link")
    d.arrow("M 224,208 V 200 Q 224,192 232,192 H 856 Q 864,192 864,184 V 176", "link")
    d.label(742, 174, "SMTP", "link", anchor="start")
    d.arrow("M 784,44 H 712 Q 704,44 704,52 V 96", dashed=True)
    d.label(652, 64, "PULL")

    d.node(296, 16, 144, 56, "People", "browsers", kind="input", tag="USER")
    d.node(40, 16, 208, 56, "Cloudflare", "edge: TLS, WAF", kind="external", tag="EDGE")
    d.node(40, 128, 208, 48, "cloudflared ×2", "tunnel, outbound only")
    for i, (name, tag) in enumerate([("web", "nginx + app"), ("portal-api", "sign-in · uploads"),
                                     ("portal-worker", "engine · email")]):
        d.chip(48, 240 + 32 * i, 192, name, tag, "backend")
    for i, (name, tag, kind) in enumerate([("engine-api", "takes PDFs in", "backend"),
                                           ("engine-worker", "workers + beat", "focal"),
                                           ("litellm", "AI gateway", "backend")]):
        d.chip(288, 240 + 32 * i, 192, name, tag, kind)
    for i, (name, tag) in enumerate([("postgres", "iSCSI"), ("redis-portal", "keeps nothing"), ("seaweedfs", "iSCSI · S3")]):
        d.chip(48, 400 + 32 * i, 192, name, tag)
    for i, (name, tag) in enumerate([("redis", "iSCSI · queue"), ("jobdata", "NFS · shared")]):
        d.chip(288, 400 + 32 * i, 192, name, tag)
    d.chip(776, 460, 152, "iSCSI LUNs", "block")
    d.chip(776, 492, 152, "NFS share", "shared")
    d.node(784, 16, 160, 56, "GHCR", "private images", kind="external")
    d.node(784, 128, 160, 48, "Cloudflare Email", "SMTP", kind="external")
    d.node(784, 282, 160, 56, "GPU server", "LocalAI · the LAN", kind="external")
    d.node(784, 364, 160, 48, "OpenRouter", "opt-in levels", kind="optional")
    d.legend(560, [("focal", "Remediates documents"), ("backend", "Program"), ("store", "Keeps data"),
                   ("external", "Outside the cluster"), ("optional", "Opt-in"), ("arrow:link", "Crosses the boundary")])
    return d


def aws_plan(theme):
    d = Diagram("aws-plan", 960, 600, "The AWS plan",
                "People reach an Application Load Balancer with an ACM certificate; in the VPC's private subnets, ECS "
                "services run the web app, portal and engine, the AI gateway and the scheduler, with RDS PostgreSQL, "
                "ElastiCache Redis, an S3 bucket encrypted with KMS and EFS for the job folder; mail goes through SES, "
                "and the gateway reaches Bedrock, OpenRouter or a GPU instance. Not yet tested end to end.", theme)
    d.zone(16, 96, 720, 448, "VPC · PRIVATE SUBNETS · ECS SERVICES", kind="boundary", label_x=264)
    d.zone(40, 208, 208, 128, "PORTAL")
    d.zone(280, 208, 208, 160, "ENGINE")
    d.zone(40, 400, 448, 112, "MANAGED DATA")

    d.arrow("M 144,72 V 128", "link")
    d.label(152, 94, "HTTPS", "link", anchor="start")
    d.arrow("M 144,176 V 208")
    d.label(152, 184, "/, /API/*", anchor="start")
    d.arrow("M 240,316 H 256 Q 264,316 264,308 V 260 Q 264,252 272,252 H 288", "accent")
    d.arrow("M 144,336 V 432")
    d.arrow("M 384,368 V 432")
    d.arrow("M 480,348 H 784", "link")
    d.label(632, 328, "MODELS", "link")
    d.arrow("M 224,208 V 200 Q 224,192 232,192 H 856 Q 864,192 864,184 V 176", "link")
    d.label(742, 174, "SES", "link", anchor="start")

    d.node(40, 16, 208, 56, "People", "browsers", kind="input", tag="USER")
    d.node(40, 128, 208, 48, "Load balancer", "ALB · ACM certificate")
    for i, name in enumerate(["web", "portal-api", "portal-worker"]):
        d.chip(48, 240 + 32 * i, 192, name, "ECS", "backend")
    for i, (name, kind) in enumerate([("engine-api", "backend"), ("engine-worker", "focal"), ("engine-beat", "backend"),
                                      ("litellm", "backend")]):
        d.chip(288, 240 + 32 * i, 192, name, "ECS", kind)
    for i, (name, tag) in enumerate([("RDS PostgreSQL", "2 DBs"), ("S3", "SSE-KMS")]):
        d.chip(48, 432 + 32 * i, 192, name, tag)
    for i, (name, tag) in enumerate([("ElastiCache", "Redis"), ("EFS", "job folder")]):
        d.chip(288, 432 + 32 * i, 192, name, tag)
    d.node(784, 128, 160, 48, "SES", "email", kind="external")
    d.node(784, 320, 160, 56, "Models", "Bedrock, OpenRouter, GPU", kind="external")
    d.legend(560, [("focal", "Remediates documents"), ("backend", "ECS service"), ("store", "Managed data"),
                   ("external", "Outside the VPC"), ("arrow:link", "Crosses the boundary")])
    return d


def adobe_findings(theme):
    d = Diagram("pac-adobe-findings", 960, 392, "PAC failures on the Adobe route, by document",
                "PAC 2026 failures in Adobe's tagging on the regression quick set, 35,976 in all: music_math alone has "
                "27,236, about three quarters; the product roadmap 2,769, the Beethoven score 1,793 and the signals "
                "textbook chapter 1,256; the other 25 documents 2,922 between them, and Adobe failed on the thesis.",
                theme)
    rows = [("music_math", 27236), ("product roadmap", 2769), ("Beethoven score", 1793),
            ("signals textbook ch. 2", 1256), ("medical admission test", 543), ("chemistry lab manual", 365),
            ("FLIR datasheet", 271), ("the other 22 documents", 1743)]
    d.hbars(216, 904, 40, 36, rows, 30000, [0, 10000, 20000, 30000], focal="music_math")
    d.text(216, 356, "PAC 26.1, PDF/UA-1, failures only (warnings: 826 in all). Adobe failed on akram_phd_thesis, so "
           "29 documents.", anchor="start")
    d.text(216, 372, "Quick set, 20 October 2026. The service's improved copy of Adobe's output adds no findings.",
           anchor="start")
    return d


DIAGRAMS = [service_overview, install_order, network_paths, storage_kinds, patching_cycle, engine_nodes,
            adobe_findings, how_it_works, system_context, engine_pipeline, tag_editor_save, service_access,
            document_path, adobe_route, portal_architecture, verification_layers, sign_in, document_lifecycle,
            scripting_flow, rollback_decision, secret_flow, reference_rke2, aws_plan]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", type=pathlib.Path, help="also write an HTML page showing every diagram")
    args = parser.parse_args()

    for name, t in THEMES.items():
        for role in ("ink", "muted", "soft", "accent", "link"):
            ratio = contrast(t[role], t["paper"])
            if ratio < 4.5:
                raise SystemExit(f"{name} {role} is {ratio:.2f}:1 on paper, under 4.5:1")

    previews = []
    for make in DIAGRAMS:
        for theme in THEMES:
            d = make(theme)
            svg = d.svg()
            (HERE / f"{d.variant}.svg").write_text(svg)
            previews.append((d, theme, svg))
            print(f"wrote {d.variant}.svg")

    if args.preview:
        sections = "".join(
            f'<section class="{theme}"><h2>{esc(d.title)} ({theme})</h2><div class="scroll">{svg}</div></section>'
            for d, theme, svg in previews)
        args.preview.write_text(
            '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>First-cluster diagrams</title>'
            f'<style>body{{margin:0;padding:24px 16px;font-family:{SANS};background:#f5f7fa;color:#172030}}'
            'section{max-width:1000px;margin:0 auto 32px}section.dark{background:#10141a;color:#e5e9ef;padding:8px}'
            'h2{font-size:1rem;margin:0 0 8px}.scroll{overflow-x:auto}svg{display:block;max-width:none}</style>'
            f'</head><body><h1>First-cluster diagrams</h1>{sections}</body></html>\n')
        print(f"wrote {args.preview}")


if __name__ == "__main__":
    main()
