"""Measure OCR word accuracy on simulated poor scans, with and without AI correction.

Takes a text PDF (whose text is the ground truth), renders chosen pages to images, damages them like a poor
scan, OCRs them, optionally lets the AI correct the OCR, and reports word accuracy and text-layer alignment.

    python -m harness.ocr_eval samples/syllabus_NOTaccessible.pdf --pages 0 --level light --model local-vision

Run it where the Tesseract command line is installed (the engine image). --model is a gateway model name;
"none" measures Tesseract alone. Paid models cost money: only run them when that's been agreed.
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import io
import random
import re
import tempfile
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageFilter

from app.ai.registry import get_provider
from app.pipeline.enrichment import EnrichmentStats
from app.pipeline.ocr import ocr_lines, write_ocr_layer
from app.pipeline.ocr_ai import correct_lines, mode

LEVELS = {  # dpi, blur radius (px), speckle share, JPEG quality
    "clean": (300, 0.0, 0.0, 95),
    "light": (150, 0.8, 0.002, 40),
    "heavy": (110, 1.1, 0.006, 25),
}


def degrade(src: str, pages: list[int], out: str, dpi: int, blur: float, noise: float, quality: int,
            seed: int = 1) -> list[str]:
    """Write an image-only PDF of the damaged pages; return each page's true text."""
    random.seed(seed)
    doc, new, truth = fitz.open(src), fitz.open(), []
    for pno in pages:
        page = doc[pno]
        truth.append(page.get_text())
        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        px = img.load()
        for _ in range(int(img.width * img.height * noise)):
            x, y = random.randrange(img.width), random.randrange(img.height)
            px[x, y] = random.choice((0, 255))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        np_ = new.new_page(width=page.rect.width, height=page.rect.height)
        np_.insert_image(np_.rect, stream=buf.getvalue())
    new.save(out)
    return truth


def _words(text: str) -> list[str]:
    return re.findall(r"[\w'’-]+|[^\w\s]", text)


def word_accuracy(truth: str, text: str) -> float:
    """Share of the true words that appear, in order, in the OCR text."""
    a, b = _words(truth), _words(text)
    m = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(bl.size for bl in m.get_matching_blocks()) / max(1, len(a))


def alignment(scan: str, lines) -> tuple[int, int]:
    """(lines whose written text overlaps the printed line by more than half, lines)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "ocr.pdf")
        write_ocr_layer(scan, out, lines)
        doc = fitz.open(out)
        spans = {}
        for pno in range(doc.page_count):
            spans[pno] = [s["bbox"] for b in doc[pno].get_text("dict")["blocks"] for ln in b.get("lines", [])
                          for s in ln["spans"]]

    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
        return ix * iy / u if u else 0

    good = sum(1 for ln in lines if max((iou(ln.bbox, s) for s in spans.get(ln.page, [])), default=0) > 0.5)
    return good, len(lines)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--pages", default="0", help="0-based page numbers, comma separated")
    ap.add_argument("--level", choices=sorted(LEVELS), default="light")
    ap.add_argument("--model", default="none", help='gateway model for AI correction, or "none"')
    ap.add_argument("--seed", type=int, default=1, help="where the speckle noise falls; results vary by a few points")
    args = ap.parse_args()
    pages = [int(p) for p in args.pages.split(",")]
    with tempfile.TemporaryDirectory() as tmp:
        scan = str(Path(tmp) / "scan.pdf")
        truth = degrade(args.pdf, pages, scan, *LEVELS[args.level], seed=args.seed)
        lines = ocr_lines(scan, list(range(len(pages))))

        def text(page):
            return "\n".join(ln.text for ln in lines if ln.page == page)

        before = [word_accuracy(t, text(i)) for i, t in enumerate(truth)]
        print(f"{Path(args.pdf).name} pages {args.pages}, {args.level}: {len(lines)} OCR lines, "
              f"{sum(1 for ln in lines if mode(ln))} uncertain")
        print(f"  Tesseract word accuracy {sum(before) / len(before):.3f}; "
              f"text-layer alignment {'%d of %d' % alignment(scan, lines)}")
        if args.model != "none":
            stats = EnrichmentStats()
            ai = get_provider("gateway", args.model)
            async with ai:
                await correct_lines(ai, scan, lines, stats)
            after = [word_accuracy(t, text(i)) for i, t in enumerate(truth)]
            print(f"  with AI ({args.model}): {sum(after) / len(after):.3f}; "
                  f"{sum(o['mode'] == 'correct' for o in stats.ocr)} line(s) corrected, "
                  f"{sum(o['mode'] == 'transcribe' for o in stats.ocr)} transcribed, {stats.ocr_rejected} rejected, "
                  f"{ai.calls} AI call(s), ${ai.usage.cost_usd:.4f}")
            for o in stats.ocr[:10]:
                print(f"    {o['mode']}: {o['before'][:50]!r} -> {o['after'][:50]!r}")


if __name__ == "__main__":
    asyncio.run(main())
