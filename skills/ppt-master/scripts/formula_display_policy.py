from __future__ import annotations

import re


SHORT_FORMULA_MIN_HEIGHT = 28
SHORT_FORMULA_MAX_HEIGHT = 48
SHORT_FORMULA_ERROR_MIN_HEIGHT = 17
MEDIUM_FORMULA_MIN_HEIGHT = 40
MEDIUM_FORMULA_MAX_HEIGHT = 88
COMPACT_FORMULA_MIN_HEIGHT = 34
COMPACT_FORMULA_RECOMMENDED_FLOOR_RATIO = 0.50
DISPLAY_FORMULA_MIN_HEIGHT = 44
DISPLAY_FORMULA_RECOMMENDED_FLOOR_RATIO = 0.50

# Keep these floor values in sync with the "Formula Size Quick Table" in
# references/executor-base.md and the minimum readable size rule in
# references/shared-standards.md. They are the enforcement source used by
# svg_quality_checker.py through minimum_formula_display_floor().


def formula_compact_length(latex: str) -> int:
    """Return a rough visual complexity score for LaTeX text."""
    return len(re.sub(r"\s+", "", latex or ""))


def fit_formula_display(
    width: float,
    height: float,
    *,
    target_h: int,
    max_w: int,
) -> tuple[int, int]:
    """Scale formula dimensions to a bounded display box."""
    ratio = width / height
    display_h = max(1, target_h)
    display_w = int(round(display_h * ratio))
    if display_w > max_w:
        display_w = max_w
        display_h = max(1, int(round(display_w / ratio)))
    return display_w, display_h


def _recommended_display_for_size(
    width: float,
    height: float,
    content_w: int,
    content_h: int,
) -> dict[str, object]:
    """Compute a recommended display frame from measured width/height."""
    if width <= 0 or height <= 0:
        return {}

    ratio = width / height
    gap = 20
    min_text_h = 150
    min_text_w = 280

    if ratio > 1.5:
        img_w = content_w
        img_h = int(round(content_w / ratio))
        text_h = content_h - img_h - gap
        if text_h >= min_text_h:
            return {"layout": "top-bottom", "display_w": img_w, "display_h": img_h}

    img_h = content_h
    img_w = int(round(content_h * ratio))
    text_w = content_w - img_w - gap
    if text_w >= min_text_w:
        return {"layout": "left-right", "display_w": img_w, "display_h": img_h}

    img_w = int(round(content_w * 0.65))
    img_h = min(int(round(img_w / ratio)), content_h)
    return {"layout": "left-right-capped", "display_w": img_w, "display_h": img_h}


def recommend_formula_display(
    width: float,
    height: float,
    content_w: int,
    content_h: int,
    *,
    latex: str = "",
    display: bool = True,
) -> dict[str, object]:
    """Compute formula display dimensions without over-scaling short formulas."""
    if width <= 0 or height <= 0:
        return {}

    compact_len = formula_compact_length(latex)
    is_short = not display or width <= 60 or compact_len <= 45
    is_medium = width <= 140 or compact_len <= 110

    if is_short:
        target_h = int(round(max(SHORT_FORMULA_MIN_HEIGHT, min(SHORT_FORMULA_MAX_HEIGHT, height * 2.5))))
        display_w, display_h = fit_formula_display(
            width,
            height,
            target_h=target_h,
            max_w=min(content_w, 320),
        )
        return {
            "layout": "inline-or-callout",
            "display_w": display_w,
            "display_h": display_h,
            "scale_note": "short formula: keep near text scale, but never below the readable minimum",
        }

    if is_medium:
        target_h = int(round(max(MEDIUM_FORMULA_MIN_HEIGHT, min(MEDIUM_FORMULA_MAX_HEIGHT, height * 2.6))))
        display_w, display_h = fit_formula_display(
            width,
            height,
            target_h=target_h,
            max_w=min(content_w, 560),
        )
        return {
            "layout": "formula-compact",
            "display_w": display_w,
            "display_h": display_h,
            "scale_note": "compact formula: keep it readable; do not shrink below the recommended floor unless it becomes the slide's main object",
        }

    display_info = _recommended_display_for_size(width, height, content_w, content_h)
    if display_info:
        display_info["scale_note"] = "display equation: may use recommended full-width sizing, but never below the readable minimum"
    return display_info


def minimum_formula_display_floor(
    width: float,
    height: float,
    content_w: int,
    content_h: int,
    *,
    latex: str = "",
    display: bool = True,
) -> dict[str, object]:
    """Return the minimum readable on-slide display size for a formula SVG."""
    recommended = recommend_formula_display(
        width,
        height,
        content_w,
        content_h,
        latex=latex,
        display=display,
    )
    recommended_w = int(round(float(recommended.get("display_w") or 0)))
    recommended_h = int(round(float(recommended.get("display_h") or 0)))
    if recommended_w <= 0 or recommended_h <= 0 or width <= 0 or height <= 0:
        return {}

    layout = str(recommended.get("layout") or "")
    if not display or layout == "inline-or-callout":
        min_h = SHORT_FORMULA_ERROR_MIN_HEIGHT
    elif layout == "formula-compact":
        min_h = max(
            COMPACT_FORMULA_MIN_HEIGHT,
            int(round(recommended_h * COMPACT_FORMULA_RECOMMENDED_FLOOR_RATIO)),
        )
    else:
        min_h = max(
            DISPLAY_FORMULA_MIN_HEIGHT,
            int(round(recommended_h * DISPLAY_FORMULA_RECOMMENDED_FLOOR_RATIO)),
        )

    min_w = max(1, int(round((width / height) * min_h)))
    return {
        "layout": layout,
        "recommended_display_w": recommended_w,
        "recommended_display_h": recommended_h,
        "min_display_w": min_w,
        "min_display_h": min_h,
        "scale_note": recommended.get("scale_note") or "",
    }
