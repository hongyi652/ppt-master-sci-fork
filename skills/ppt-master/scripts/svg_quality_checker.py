#!/usr/bin/env python3
"""
PPT Master - SVG Quality Check Tool

Checks whether SVG files comply with project technical specifications.

Usage:
    python3 scripts/svg_quality_checker.py <svg_file>
    python3 scripts/svg_quality_checker.py <directory>
    python3 scripts/svg_quality_checker.py --all examples
"""

import io
import os
import sys
import re
import json
import html
from pathlib import Path
from typing import List, Dict, Tuple
from collections import defaultdict
from xml.etree import ElementTree as ET

# --- Windows GBK crash prevention ---
# On Windows the default console encoding is often GBK (cp936) which cannot
# encode many Unicode characters found in SVG content (subscripts, ©, etc.).
# Force UTF-8 on stdout/stderr so the checker never crashes mid-report.
if sys.platform == "win32":
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name)
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        elif hasattr(_stream, "buffer"):
            setattr(sys, _stream_name, io.TextIOWrapper(
                _stream.buffer, encoding="utf-8", errors="replace",
            ))
    # Also set the env var so child processes inherit the preference.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:
    from project_utils import CANVAS_FORMATS
    from error_helper import ErrorHelper
except ImportError:
    print("Warning: Unable to import dependency modules")
    CANVAS_FORMATS = {}
    ErrorHelper = None

try:
    from update_spec import parse_lock as _parse_spec_lock
except ImportError:
    _parse_spec_lock = None  # spec_lock drift check will be skipped

try:
    from formula_display_policy import minimum_formula_display_floor
except ImportError:
    minimum_formula_display_floor = None

try:
    from svg_to_pptx.animation_config import (
        load_animation_config as _load_animation_config,
        validate_animation_config as _validate_animation_config,
    )
except ImportError:
    _load_animation_config = None
    _validate_animation_config = None


HEX_VALUE_RE = re.compile(r"#[0-9A-Fa-f]{3,8}")

# Ramp envelope for font-size drift detection.
# From design_spec_reference.md §IV — Font Size Hierarchy: the ramp spans
# from page-number floor (0.5x body) to cover-title ceiling (5.0x body).
# Intermediate px values within this envelope are permitted per
# executor-base.md §2.1 ("Executor may use an intermediate size ... provided
# the size's ratio to body falls within the corresponding role's band"); only
# values outside every band — i.e. outside this envelope — are drift.
RAMP_MIN_RATIO = 0.5
RAMP_MAX_RATIO = 5.0


def _design_spec_is_brand(spec_path: Path) -> bool:
    """Return True when a design_spec.md frontmatter declares ``kind: brand``.

    Lightweight detector that does not require PyYAML — scans only the
    frontmatter block (``---`` delimited) for a ``kind:`` line whose value
    contains ``brand``. Used by ``check_directory`` to skip SVG validation
    on brand-only template directories.
    """
    try:
        text = spec_path.read_text(encoding='utf-8')
    except OSError:
        return False
    if not text.startswith('---\n'):
        return False
    end = text.find('\n---\n', 4)
    if end == -1:
        return False
    fm_block = text[4:end]
    for line in fm_block.splitlines():
        stripped = line.strip()
        if stripped.startswith('kind:'):
            value = stripped.split(':', 1)[1].strip().strip('"\'')
            return value == 'brand'
    return False


def _parse_placeholders_fallback(block: str) -> Dict[str, Tuple[str, ...]]:
    """Tiny YAML-free reader for the documented ``placeholders:`` shape.

    Used only when PyYAML is unavailable. Recognized lines (indentation-aware,
    two-space indent assumed):

    .. code-block:: yaml

        placeholders:
          01_cover: ["{{TITLE}}", "{{LOGO}}"]
          03_content: []
          03a_content_two_col:
            - "{{LEFT_TITLE}}"
            - "{{RIGHT_TITLE}}"

    Anything outside this minimal grammar is silently skipped — designers who
    rely on advanced YAML should install pyyaml.
    """
    out: Dict[str, Tuple[str, ...]] = {}
    inline_re = re.compile(
        r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[(.*)\]\s*$"
    )
    empty_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*\[\s*\]\s*$")
    block_header_re = re.compile(r"^\s{2}([A-Za-z0-9_]+)\s*:\s*$")
    item_re = re.compile(r'^\s{4}-\s*"?([^"]+)"?\s*$')

    in_section = False
    current_block_key: str | None = None
    current_items: List[str] = []

    def _flush_block() -> None:
        nonlocal current_block_key, current_items
        if current_block_key is not None:
            out[current_block_key] = tuple(current_items)
            current_block_key = None
            current_items = []

    for line in block.splitlines():
        if line.startswith("placeholders:"):
            in_section = True
            continue
        if not in_section:
            continue

        # End of section: dedent to a non-key line.
        if line and not line.startswith(" "):
            _flush_block()
            in_section = False
            continue

        if current_block_key is not None:
            m = item_re.match(line)
            if m:
                value = m.group(1).strip().strip('"').strip("'")
                if value:
                    current_items.append(value)
                continue
            # Block ended.
            _flush_block()

        if empty_re.match(line):
            key = empty_re.match(line).group(1)
            out[key] = ()
            continue

        m = inline_re.match(line)
        if m:
            key, raw = m.group(1), m.group(2)
            items = [p.strip().strip('"').strip("'") for p in raw.split(",")]
            out[key] = tuple(item for item in items if item)
            continue

        m = block_header_re.match(line)
        if m:
            current_block_key = m.group(1)
            current_items = []
            continue

    _flush_block()
    return out


class SVGQualityChecker:
    """SVG quality checker"""

    # Default placeholder convention per page-type prefix. This is a *hint*,
    # not a hard contract: templates may define their own placeholder vocabulary
    # via `placeholders:` in design_spec.md frontmatter (see
    # references/template-designer.md §4). Missing default placeholders surface
    # as warnings, never errors — designers may legitimately swap
    # `{{THANK_YOU}}` for `{{CLOSING_MESSAGE}}`, omit `{{DATE}}` when irrelevant,
    # or build content variants with bespoke slot vocabularies.
    #
    # Variants reuse the parent type's expectation (`03a_content_two_col.svg`
    # is matched by the same `03_content` rules as `03_content.svg`).
    DEFAULT_PLACEHOLDER_CONVENTION = {
        "01_cover": ("{{TITLE}}",),  # only the title is universally expected
        "02_chapter": ("{{CHAPTER_TITLE}}",),
        "02_toc": (),  # TOC layouts vary too widely to assert anything
        "03_content": ("{{PAGE_TITLE}}",),
        "04_ending": (),  # ending pages legitimately use varied vocabularies
    }

    def __init__(self, *, template_mode: bool = False):
        self.template_mode = template_mode
        self.results = []
        self.summary = {
            'total': 0,
            'passed': 0,
            'warnings': 0,
            'errors': 0
        }
        self.issue_types = defaultdict(int)
        # spec_lock drift state (populated only when _parse_spec_lock is available
        # and a spec_lock.md is found near the SVG)
        self._lock_cache: Dict[Path, Dict] = {}
        self._drift_summary: Dict[str, Dict[str, set]] = {
            'colors': defaultdict(set),
            'fonts': defaultdict(set),
            'sizes': defaultdict(set),
        }
        self._lock_seen = False  # True once we locate at least one spec_lock.md
        self._source_manifest_cache: Dict[Path, Dict] = {}
        self._formula_manifest_cache: Dict[Path, Dict[str, Dict]] = {}
        # Template-mode aggregation (populated by check_directory when
        # template_mode=True). Each entry is (severity, kind, message) where
        # severity is 'error' or 'warning'. Printed in print_summary.
        self._template_issues: List[Tuple[str, str, str]] = []
        self._animation_issues: List[Tuple[str, str]] = []

    def check_file(self, svg_file: str, expected_format: str = None) -> Dict:
        """
        Check a single SVG file

        Args:
            svg_file: SVG file path
            expected_format: Expected canvas format (e.g., 'ppt169')

        Returns:
            Check result dictionary
        """
        svg_path = Path(svg_file)

        if not svg_path.exists():
            return {
                'file': str(svg_file),
                'exists': False,
                'errors': ['File does not exist'],
                'warnings': [],
                'passed': False
            }

        result = {
            'file': svg_path.name,
            'path': str(svg_path),
            'exists': True,
            'errors': [],
            'warnings': [],
            'info': {},
            'passed': True
        }

        try:
            with open(svg_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # 0. Check XML well-formedness — every other check assumes the file
            # is valid XML.  Bail early on failure so the regex-based checks
            # below don't produce misleading errors on a broken document.
            if self._check_xml_well_formed(content, result):
                # 1. Check viewBox
                self._check_viewbox(content, result, expected_format)

                # 2. Check forbidden elements
                self._check_forbidden_elements(content, result)

                # 3. Check fonts
                self._check_fonts(content, result)

                # 4. Check width/height consistency with viewBox
                self._check_dimensions(content, result)

                # 5. Check text wrapping methods
                self._check_text_elements(content, result)

                # 6. Check image references (file existence and resolution)
                self._check_image_references(content, svg_path, result)

                # 7. Check object-level animation anchor quality.
                self._check_animation_group_ids(content, result)

                # 8. Check spec_lock drift (colors / font-family / font-size).
                #    Templates do not ship a spec_lock.md, so skip in template
                #    mode to avoid noise.
                if not self.template_mode:
                    self._check_spec_lock_drift(content, svg_path, result)

                # 9. Check web-sourced image attribution. Templates don't carry
                #    image_sources.json; skip in template mode.
                if not self.template_mode:
                    self._check_sourced_image_attribution(content, svg_path, result)

                # 10. Check plain-text formula violations (Iron Rule §4.1).
                #     Templates may contain placeholder math text; skip.
                if not self.template_mode:
                    self._check_plain_text_formulas(content, result)

                # 11. Check fake-superscript anti-pattern: adjacent <text>
                #     elements with mismatched font-size positioned to fake
                #     sub/superscripts.  Templates are excluded.
                if not self.template_mode:
                    self._check_fake_sub_superscript(content, result)

                # 12. Check split-sentence anti-pattern: adjacent <text>
                #     elements on the same line that should be one <text>
                #     with <tspan> children for inline styling.
                if not self.template_mode:
                    self._check_split_sentence(content, result)

            # Determine pass/fail
            result['passed'] = len(result['errors']) == 0

        except Exception as e:
            result['errors'].append(f"Failed to read file: {e}")
            result['passed'] = False

        # Update statistics
        self.summary['total'] += 1
        if result['passed']:
            if result['warnings']:
                self.summary['warnings'] += 1
            else:
                self.summary['passed'] += 1
        else:
            self.summary['errors'] += 1

        # Categorize issue types
        for error in result['errors']:
            self.issue_types[self._categorize_issue(error)] += 1

        self.results.append(result)
        return result

    # Pattern to detect illegal double-hyphen inside XML comments.
    # XML spec: within <!-- ... -->, the string "--" is forbidden.
    # AI frequently writes things like <!-- 10^-11 -- 10^-9 s -->.
    _COMMENT_DOUBLE_HYPHEN_RE: re.Pattern = re.compile(
        r'<!--(.*?)-->',
        re.DOTALL,
    )

    def _check_xml_well_formed(self, content: str, result: Dict) -> bool:
        """Check that the SVG content parses as well-formed XML.

        SVG is strict XML.  AI-generated decks frequently produce content that
        looks fine in HTML5-tolerant previews but fails strict XML parsing —
        common causes are HTML named entities (&nbsp; &mdash; &copy;…) and
        bare XML reserved characters in text (R&D, error < 5%).  Such pages
        cannot be exported to PPTX, so we surface them here as a hard error
        before any downstream check looks at them.

        Returns True when the document is well-formed; False otherwise.
        """
        # Pre-parse: detect illegal "--" inside XML comments (common AI mistake)
        for m in self._COMMENT_DOUBLE_HYPHEN_RE.finditer(content):
            body = m.group(1)
            if '--' in body:
                line_no = content[:m.start()].count('\n') + 1
                result['errors'].append(
                    f"Illegal '--' inside XML comment at line ~{line_no}: "
                    f"XML forbids double-hyphen in <!-- ... -->. "
                    f"Remove or rephrase the comment content."
                )

        try:
            ET.fromstring(content)
            return True
        except ET.ParseError as e:
            result['errors'].append(
                f"Invalid XML: {e} — SVG must be well-formed XML. "
                f"Use raw Unicode for typography (—, ©, →, NBSP); "
                f"escape XML reserved chars as &amp; &lt; &gt; &quot; &apos; "
                f"(see references/shared-standards.md §1)."
            )
            return False

    def _check_viewbox(self, content: str, result: Dict, expected_format: str = None):
        """Check viewBox attribute"""
        viewbox_match = re.search(r'viewBox="([^"]+)"', content)

        if not viewbox_match:
            result['errors'].append("Missing viewBox attribute")
            return

        viewbox = viewbox_match.group(1)
        result['info']['viewbox'] = viewbox

        # Check format
        if not re.match(r'0 0 \d+ \d+', viewbox):
            result['warnings'].append(f"Unusual viewBox format: {viewbox}")

        # Check if it matches expected format
        if expected_format and expected_format in CANVAS_FORMATS:
            expected_viewbox = CANVAS_FORMATS[expected_format]['viewbox']
            if viewbox != expected_viewbox:
                result['errors'].append(
                    f"viewBox mismatch: expected '{expected_viewbox}', got '{viewbox}'"
                )

    def _check_forbidden_elements(self, content: str, result: Dict):
        """Check forbidden elements (blocklist)"""
        content_lower = content.lower()

        # ============================================================
        # Forbidden elements blocklist - PPT incompatible
        # ============================================================

        # Clipping / masking
        # clipPath is allowed on <image> elements and on pptx_to_svg-generated
        # nested crop <svg data-pptx-crop="1"> wrappers. Both map back to
        # DrawingML picture geometry in the native converter.
        if '<clippath' in content_lower:
            # clip-path on non-image elements → error
            clip_on_non_image = re.search(
                r'<(?!image\b)(?!svg\b[^>]*\bdata-pptx-crop\s*=\s*["\']1["\'])\w+[^>]*\bclip-path\s*=',
                content,
                re.IGNORECASE,
            )
            if clip_on_non_image:
                result['errors'].append(
                    "clip-path is only allowed on <image> elements or "
                    "pptx_to_svg crop wrappers — for shapes, draw the target "
                    "shape directly instead of clipping")
            # Check that every clip-path reference has a matching <clipPath> def
            clip_refs = re.findall(r'clip-path\s*=\s*["\']url\(#([^)]+)\)', content)
            for ref_id in clip_refs:
                if f'id="{ref_id}"' not in content and f"id='{ref_id}'" not in content:
                    result['errors'].append(
                        f"clip-path references #{ref_id} but no matching "
                        f"<clipPath id=\"{ref_id}\"> definition found")
        if '<mask' in content_lower:
            result['errors'].append("Detected forbidden <mask> element (PPT does not support SVG masks)")

        # Style system
        if '<style' in content_lower:
            result['errors'].append("Detected forbidden <style> element (use inline attributes instead)")
        if re.search(r'\bclass\s*=', content):
            result['errors'].append("Detected forbidden class attribute (use inline styles instead)")
        # id attribute: only report error when <style> also exists (id is harmful only with CSS selectors)
        # id inside <defs> for linearGradient/filter etc. is required, Inkscape also auto-adds id to elements,
        # standalone id attributes have no impact on PPT export
        if '<style' in content_lower and re.search(r'\bid\s*=', content):
            result['errors'].append(
                "Detected id attribute used with <style> (CSS selectors forbidden, use inline styles instead)"
            )
        if re.search(r'<\?xml-stylesheet\b', content_lower):
            result['errors'].append("Detected forbidden xml-stylesheet (external CSS references forbidden)")
        if re.search(r'<link[^>]*rel\s*=\s*["\']stylesheet["\']', content_lower):
            result['errors'].append("Detected forbidden <link rel=\"stylesheet\"> (external CSS references forbidden)")
        if re.search(r'@import\s+', content_lower):
            result['errors'].append("Detected forbidden @import (external CSS references forbidden)")

        # Structure / nesting
        if '<foreignobject' in content_lower:
            result['errors'].append(
                "Detected forbidden <foreignObject> element (use <tspan> for manual line breaks)")
        has_symbol = '<symbol' in content_lower
        has_use = re.search(r'<use\b', content_lower) is not None
        if has_symbol and has_use:
            result['errors'].append("Detected forbidden <symbol> + <use> complex usage (use basic shapes or simple <use> instead)")
        # marker-start / marker-end are conditionally allowed (see shared-standards.md §1.1).
        # The converter maps qualifying <marker> defs to native DrawingML <a:headEnd>/<a:tailEnd>.
        # We only warn when a marker is used without an obvious <defs> definition in the same file.
        if re.search(r'\bmarker-(?:start|end)\s*=\s*["\']url\(#([^)]+)\)', content_lower):
            if '<marker' not in content_lower:
                result['errors'].append(
                    "Detected marker-start/marker-end referencing a marker id, "
                    "but no <marker> element found in the file")

        # Text / fonts
        if '<textpath' in content_lower:
            result['errors'].append("Detected forbidden <textPath> element (path text is incompatible with PPT)")
        if '@font-face' in content_lower:
            result['errors'].append("Detected forbidden @font-face (use system font stack)")

        # Animation / interaction
        if re.search(r'<animate', content_lower):
            result['errors'].append("Detected forbidden SMIL animation element <animate*> (SVG animations are not exported)")
        if re.search(r'<set\b', content_lower):
            result['errors'].append("Detected forbidden SMIL animation element <set> (SVG animations are not exported)")
        if '<script' in content_lower:
            result['errors'].append("Detected forbidden <script> element (scripts and event handlers forbidden)")
        if re.search(r'\bon\w+\s*=', content):  # onclick, onload etc.
            result['errors'].append("Detected forbidden event attributes (e.g., onclick, onload)")

        # Other discouraged elements
        if '<iframe' in content_lower:
            result['errors'].append("Detected <iframe> element (should not appear in SVG)")
        if re.search(r'rgba\s*\(', content_lower):
            result['errors'].append("Detected forbidden rgba() color (use fill-opacity/stroke-opacity instead)")
        if re.search(r'<g[^>]*\sopacity\s*=', content_lower):
            result['errors'].append("Detected forbidden <g opacity> (set opacity on each child element individually)")
        if re.search(r'<image[^>]*\sopacity\s*=', content_lower):
            result['errors'].append("Detected forbidden <image opacity> (use overlay mask approach)")

    def _check_fonts(self, content: str, result: Dict):
        """Check font usage.

        PPTX stores a single `typeface` per run with no runtime fallback, so every
        stack must END with a cross-platform pre-installed family. See
        strategist.md §g "PPT-safe font discipline".
        """
        font_matches = re.findall(
            r'font-family[:\s]*["\']([^"\']+)["\']', content, re.IGNORECASE)

        if not font_matches:
            return

        result['info']['fonts'] = list(set(font_matches))

        # Pre-installed on Windows + macOS out of the box (plus their direct
        # FONT_FALLBACK_WIN mappings). A stack whose last concrete family is in
        # this set survives the PPTX round-trip on any viewer machine.
        # KaiTi is explicitly BANNED (Iron Rule) — not in ppt_safe_tail.
        ppt_safe_tail = {
            'microsoft yahei', 'simhei', 'simsun', 'fangsong',
            'pingfang sc', 'heiti sc', 'songti sc', 'stsong',
            'arial', 'arial black', 'calibri', 'segoe ui', 'verdana',
            'helvetica', 'helvetica neue', 'tahoma', 'trebuchet ms',
            'times new roman', 'times', 'georgia', 'cambria', 'palatino',
            'consolas', 'courier new', 'menlo', 'monaco',
            'impact',
        }

        # Banned fonts — using any of these is an error, not a warning.
        banned_fonts = {'kaiti', '楷体'}

        for font_family in font_matches:
            # Drop the generic CSS fallback (sans-serif / serif / monospace)
            # and inspect the last concrete family.
            parts = [p.strip().strip('"').strip("'").lower()
                     for p in font_family.split(',')]
            parts = [p for p in parts
                     if p and p not in ('sans-serif', 'serif', 'monospace',
                                        'cursive', 'fantasy', 'system-ui')]
            if not parts:
                continue

            # Check for banned fonts anywhere in the stack
            for p in parts:
                if p in banned_fonts:
                    result['errors'].append(
                        f"Banned font '{p}' detected in stack: {font_family} "
                        f"— KaiTi (楷体) is forbidden (Iron Rule)")

            tail = parts[-1]
            if tail not in ppt_safe_tail:
                result['warnings'].append(
                    f"Font stack does not end on a PPT-safe family "
                    f"(expected e.g. Microsoft YaHei / SimSun / Arial / "
                    f"Times New Roman / Consolas): {font_family}"
                )
                break

    def _check_dimensions(self, content: str, result: Dict):
        """Check width/height consistency with viewBox"""
        width_match = re.search(r'width="(\d+)"', content)
        height_match = re.search(r'height="(\d+)"', content)

        if width_match and height_match:
            width = width_match.group(1)
            height = height_match.group(1)
            result['info']['dimensions'] = f"{width}x{height}"

            # Check consistency with viewBox
            if 'viewbox' in result['info']:
                viewbox_parts = result['info']['viewbox'].split()
                if len(viewbox_parts) == 4:
                    vb_width, vb_height = viewbox_parts[2], viewbox_parts[3]
                    if width != vb_width or height != vb_height:
                        result['warnings'].append(
                            f"width/height ({width}x{height}) does not match viewBox "
                            f"({vb_width}x{vb_height})"
                        )

    def _check_text_elements(self, content: str, result: Dict):
        """Check text elements and wrapping methods"""
        # Count text and tspan elements
        text_count = content.count('<text')
        tspan_count = content.count('<tspan')

        result['info']['text_elements'] = text_count
        result['info']['tspan_elements'] = tspan_count

        # Check for overly long single-line text (may need wrapping)
        text_matches = re.findall(r'<text[^>]*>([^<]{100,})</text>', content)
        if text_matches:
            result['warnings'].append(
                f"Detected {len(text_matches)} potentially overly long single-line text(s) (consider using tspan for wrapping)"
            )

    @staticmethod
    def _parse_svg_number(value: str | None) -> float | None:
        """Best-effort SVG numeric attribute parser."""
        if value is None:
            return None
        try:
            return float(re.sub(r'(px|pt|em|%|rem)$', '', value.strip()))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _coerce_positive_float(value: object) -> float | None:
        """Return a positive float when possible."""
        if isinstance(value, (int, float)):
            numeric = float(value)
            return numeric if numeric > 0 else None
        if isinstance(value, str):
            try:
                numeric = float(value.strip())
            except ValueError:
                return None
            return numeric if numeric > 0 else None
        return None

    @staticmethod
    def _measure_svg_root_dimensions(path: Path) -> tuple[float | None, float | None]:
        """Extract root SVG width/height for formula readability checks."""
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None, None
        width_match = re.search(r'<svg\b[^>]*\bwidth=["\']([0-9.]+)', text)
        height_match = re.search(r'<svg\b[^>]*\bheight=["\']([0-9.]+)', text)
        width = float(width_match.group(1)) if width_match else None
        height = float(height_match.group(1)) if height_match else None
        return width, height

    @staticmethod
    def _parse_image_par(value: str | None) -> tuple[str, str]:
        """Parse preserveAspectRatio into ``(align, mode)``."""
        raw = (value or 'xMidYMid meet').strip()
        if not raw:
            return 'xMidYMid', 'meet'
        parts = raw.split()
        align = parts[0]
        if align.lower() == 'none':
            return 'none', 'none'
        mode = parts[1] if len(parts) > 1 else 'meet'
        return align, mode

    def _effective_image_display_size(
        self,
        actual_w: float,
        actual_h: float,
        box_w: float,
        box_h: float,
        preserve_aspect_ratio: str | None,
    ) -> tuple[float, float]:
        """Return the real rendered image size inside an SVG image box."""
        align, mode = self._parse_image_par(preserve_aspect_ratio)
        if align == 'none' or actual_w <= 0 or actual_h <= 0 or box_w <= 0 or box_h <= 0:
            return box_w, box_h

        if mode == 'slice':
            scale = max(box_w / actual_w, box_h / actual_h)
        else:
            scale = min(box_w / actual_w, box_h / actual_h)
        return actual_w * scale, actual_h * scale

    @staticmethod
    def _content_box_from_viewbox(content: str) -> tuple[int, int]:
        """Approximate the usable content box for formula sizing checks."""
        match = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', content)
        if not match:
            return 1160, 600
        try:
            canvas_w = float(match.group(1))
            canvas_h = float(match.group(2))
        except ValueError:
            return 1160, 600
        content_w = max(320, int(round(canvas_w - 120)))
        content_h = max(200, int(round(canvas_h - 120)))
        return content_w, content_h

    def _check_formula_image_readability(
        self,
        attrs: str,
        href: str,
        svg_path: Path,
        img_path: Path,
        box_w: float,
        box_h: float,
        preserve_aspect_ratio: str | None,
        content: str,
        result: Dict,
    ) -> None:
        """Require formula SVGs to stay above a readable on-slide size floor."""
        if minimum_formula_display_floor is None:
            return

        entry = self._load_formula_lookup(svg_path).get(Path(href).name)
        if not entry:
            return

        natural_w = self._coerce_positive_float(entry.get('svg_width'))
        natural_h = self._coerce_positive_float(entry.get('svg_height'))
        if natural_w is None or natural_h is None:
            measured_w, measured_h = self._measure_svg_root_dimensions(img_path)
            natural_w = natural_w or measured_w
            natural_h = natural_h or measured_h
        if natural_w is None or natural_h is None:
            return

        effective_w, effective_h = self._effective_image_display_size(
            natural_w,
            natural_h,
            box_w,
            box_h,
            preserve_aspect_ratio,
        )
        if effective_w <= 0 or effective_h <= 0:
            return

        content_w, content_h = self._content_box_from_viewbox(content)
        floor = minimum_formula_display_floor(
            natural_w,
            natural_h,
            content_w,
            content_h,
            latex=str(entry.get('latex') or ''),
            display=bool(entry.get('display', True)),
        )
        min_h = self._coerce_positive_float(floor.get('min_display_h')) if floor else None
        min_w = self._coerce_positive_float(floor.get('min_display_w')) if floor else None
        if min_h is None or min_w is None:
            return
        if effective_h + 0.5 >= min_h:
            return

        recommended_w = int(round(self._coerce_positive_float(floor.get('recommended_display_w')) or min_w))
        recommended_h = int(round(self._coerce_positive_float(floor.get('recommended_display_h')) or min_h))
        result['errors'].append(
            f"Formula image {href} is displayed at {int(round(effective_w))}x{int(round(effective_h))}, "
            f"below the readable minimum {int(round(min_w))}x{int(round(min_h))}. "
            f"Recommended display is about {recommended_w}x{recommended_h}; enlarge the <image> frame instead of shrinking the formula below readable size."
        )

    def _check_image_references(self, content: str, svg_path: Path, result: Dict):
        """Check image file existence and resolution vs display size."""
        # Find all <image ...> elements (capture the full tag)
        img_tag_pattern = re.compile(r'<image\b([^>]*)/?>', re.IGNORECASE)

        svg_dir = svg_path.parent
        checked = set()

        for tag_match in img_tag_pattern.finditer(content):
            attrs = tag_match.group(1)

            # Extract href (prefer href over xlink:href)
            href_match = (
                re.search(r'\bhref="(?!data:)([^"]+)"', attrs) or
                re.search(r'\bxlink:href="(?!data:)([^"]+)"', attrs)
            )
            if not href_match:
                continue

            href = href_match.group(1)
            if href in checked:
                continue
            checked.add(href)
            self._check_formula_image_metadata(attrs, href, svg_path, result)

            # Resolve path relative to SVG file directory
            img_path = (svg_dir / href).resolve()

            if not img_path.exists():
                formula_alias_path = self._resolve_formula_image_alias(svg_path, href)
                if formula_alias_path is not None:
                    img_path = formula_alias_path.resolve()
                else:
                    result['errors'].append(
                        f"Image file not found: {href} (resolved to {img_path})")
                    continue

            if not img_path.exists():
                result['errors'].append(
                    f"Image file not found: {href} (resolved to {img_path})")
                continue

            # Iron Rule §13: preserveAspectRatio="none" stretches images.
            # Allowed only for full-bleed background images (covering full
            # canvas at position 0,0).  Content images must never be
            # stretched.
            par_match = re.search(
                r'\bpreserveAspectRatio="([^"]*)"', attrs)
            par_value = par_match.group(1).strip() if par_match else None
            if par_value and par_value.lower() == 'none':
                x_match = re.search(r'\bx="([^"]*)"', attrs)
                y_match = re.search(r'\by="([^"]*)"', attrs)
                x_val = self._parse_svg_number(x_match.group(1)) if x_match else -1
                y_val = self._parse_svg_number(y_match.group(1)) if y_match else -1
                is_full_bleed_bg = (x_val == 0 and y_val == 0)
                if not is_full_bleed_bg:
                    result['errors'].append(
                        f"Image {href} uses preserveAspectRatio=\"none\" "
                        f"(stretches/distorts the image). Use "
                        f"\"xMidYMid meet\" to preserve aspect ratio, "
                        f"or resize the container to match the image."
                    )

            # Check resolution vs display size
            w_match = re.search(r'\bwidth="([^"]+)"', attrs)
            h_match = re.search(r'\bheight="([^"]+)"', attrs)
            display_w_str = w_match.group(1) if w_match else None
            display_h_str = h_match.group(1) if h_match else None
            if not display_w_str or not display_h_str:
                continue

            try:
                display_w = self._parse_svg_number(display_w_str)
                display_h = self._parse_svg_number(display_h_str)
                if display_w is None or display_h is None:
                    continue
            except (ValueError, TypeError):
                continue

            self._check_formula_image_readability(
                attrs,
                href,
                svg_path,
                img_path,
                display_w,
                display_h,
                par_value,
                content,
                result,
            )

            try:
                from PIL import Image as PILImage
                with PILImage.open(img_path) as img:
                    actual_w, actual_h = img.size

                effective_w, effective_h = self._effective_image_display_size(
                    actual_w,
                    actual_h,
                    display_w,
                    display_h,
                    par_value,
                )
                if effective_w <= 0 or effective_h <= 0:
                    continue

                if actual_w < effective_w or actual_h < effective_h:
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} but displayed at "
                        f"{int(effective_w)}x{int(effective_h)} — may appear blurry")
                elif actual_w > effective_w * 4 and actual_h > effective_h * 4:
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} but displayed at "
                        f"{int(effective_w)}x{int(effective_h)} — consider downsizing "
                        f"to reduce file size")
            except ImportError:
                pass  # PIL not available, skip resolution check
            except Exception:
                pass  # Image unreadable, skip resolution check

    def _check_animation_group_ids(self, content: str, result: Dict):
        """Warn when visible top-level groups cannot be customized."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return

        non_visual = {'defs', 'title', 'desc', 'metadata', 'style'}
        for index, child in enumerate(list(root), start=1):
            tag = child.tag.split('}', 1)[-1]
            if tag in non_visual:
                continue
            if tag == 'g' and not child.get('id'):
                result['warnings'].append(
                    f"Top-level visible <g> #{index} has no id; "
                    "object-level animation config cannot reference it"
                )

    def _get_spec_lock(self, svg_path: Path):
        """Locate and parse spec_lock.md near the SVG. Returns dict or None.

        Looks in svg_path.parent and svg_path.parent.parent (covers the two
        common layouts: SVG directly under <project>/ or under
        <project>/svg_output/). Results are cached per lock path.
        """
        if _parse_spec_lock is None:
            return None
        for candidate in (svg_path.parent / 'spec_lock.md',
                          svg_path.parent.parent / 'spec_lock.md'):
            if candidate in self._lock_cache:
                return self._lock_cache[candidate]
            if candidate.exists():
                try:
                    data = _parse_spec_lock(candidate)
                except Exception:
                    data = None
                self._lock_cache[candidate] = data
                if data is not None:
                    self._lock_seen = True
                return data
        return None

    def _check_spec_lock_drift(self, content: str, svg_path: Path, result: Dict):
        """Detect values used in the SVG that fall outside spec_lock.md.

        Covers colors (fill / stroke / stop-color), font-family, and font-size.
        Emits per-file warnings summarising the drift counts; exact drifting
        values are accumulated in self._drift_summary for the end-of-run
        aggregation. When spec_lock.md is missing, silently skip (consistent
        with executor-base.md §2.1's 'missing lock → warn and proceed' policy).
        """
        lock = self._get_spec_lock(svg_path)
        if lock is None:
            return

        # Build allow-sets from the lock
        allowed_colors = set()
        for v in lock.get('colors', {}).values():
            if HEX_VALUE_RE.fullmatch(v):
                allowed_colors.add(v.upper())

        typo = lock.get('typography', {})
        # Font families: default `font_family` plus any per-role `*_family`
        # override (title_family / body_family / emphasis_family / code_family,
        # per spec_lock_reference.md). Any of these is a legitimate declared
        # value; an SVG that uses any one of them is not drifting.
        allowed_fonts = set()
        if typo:
            default_font = typo.get('font_family', '').strip()
            if default_font:
                allowed_fonts.add(default_font)
            for k, v in typo.items():
                if k == 'font_family' or not k.endswith('_family'):
                    continue
                v_clean = v.strip()
                # Skip placeholder text like "same as body (omit if identical)"
                if not v_clean or v_clean.lower().startswith('same as'):
                    continue
                allowed_fonts.add(v_clean)

        # Sizes: declared slots are anchors; body is the ramp baseline.
        allowed_sizes = set()
        body_px = None
        for k, v in typo.items():
            if k == 'font_family' or k.endswith('_family'):
                continue
            allowed_sizes.add(self._normalize_size(v))
            if k == 'body':
                try:
                    body_px = float(self._normalize_size(v))
                except (ValueError, TypeError):
                    body_px = None

        # Scan SVG for used values
        color_drifts = set()
        for attr in ('fill', 'stroke', 'stop-color'):
            pattern = re.compile(rf'\b{attr}\s*=\s*["\'](#[0-9A-Fa-f]{{3,8}})["\']')
            for m in pattern.finditer(content):
                val = m.group(1).upper()
                if val not in allowed_colors:
                    color_drifts.add(val)

        font_drifts = set()
        for m in re.finditer(r'font-family\s*=\s*["\']([^"\']+)["\']', content):
            val = m.group(1).strip()
            if not allowed_fonts:
                continue
            # Font stacks like "'Microsoft YaHei', 'PingFang SC', sans-serif"
            # match if ANY declared font name appears as a substring.
            # This handles the common case where spec_lock stores a single
            # family name but the SVG uses a full CSS fallback stack.
            if val not in allowed_fonts and not any(f in val for f in allowed_fonts):
                font_drifts.add(val)

        size_drifts = set()
        for m in re.finditer(r'font-size\s*=\s*["\']([^"\']+)["\']', content):
            val = self._normalize_size(m.group(1))
            if not allowed_sizes or val in allowed_sizes:
                continue
            # Intermediate values are allowed when they sit inside the ramp
            # envelope (ratio to body within [RAMP_MIN_RATIO, RAMP_MAX_RATIO]).
            if body_px and body_px > 0:
                try:
                    ratio = float(val) / body_px
                    if RAMP_MIN_RATIO <= ratio <= RAMP_MAX_RATIO:
                        continue
                except ValueError:
                    pass
            size_drifts.add(val)

        # Record in run-wide aggregation
        fname = svg_path.name
        for v in color_drifts:
            self._drift_summary['colors'][v].add(fname)
        for v in font_drifts:
            self._drift_summary['fonts'][v].add(fname)
        for v in size_drifts:
            self._drift_summary['sizes'][v].add(fname)

        # Per-file warning (one condensed line; details live in summary)
        parts = []
        if color_drifts:
            parts.append(f"{len(color_drifts)} color(s)")
        if font_drifts:
            parts.append(f"{len(font_drifts)} font-family value(s)")
        if size_drifts:
            parts.append(f"{len(size_drifts)} font-size value(s)")
        if parts:
            result['warnings'].append(
                f"spec_lock drift: {', '.join(parts)} not in spec_lock.md "
                "(see drift summary for details)"
            )

    def _find_image_sources_manifest(self, svg_path: Path) -> Path | None:
        """Locate image_sources.json for a project SVG.

        Quality checks run primarily on <project>/svg_output/*.svg, but this
        also supports SVGs checked from project root or svg_final.
        """
        bases = (svg_path.parent, svg_path.parent.parent, svg_path.parent.parent.parent)
        for base in bases:
            candidate = base / 'images' / 'image_sources.json'
            if candidate.exists():
                return candidate
        return None

    def _load_image_sources_manifest(self, svg_path: Path) -> Dict:
        manifest_path = self._find_image_sources_manifest(svg_path)
        if manifest_path is None:
            return {}
        if manifest_path in self._source_manifest_cache:
            return self._source_manifest_cache[manifest_path]
        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            payload = {}
        self._source_manifest_cache[manifest_path] = payload
        return payload

    def _find_formula_manifest(self, svg_path: Path) -> Path | None:
        """Locate formula_manifest.json for a project SVG."""
        bases = (svg_path.parent, svg_path.parent.parent, svg_path.parent.parent.parent)
        for base in bases:
            candidate = base / 'images' / 'formula_manifest.json'
            if candidate.exists():
                return candidate
        return None

    def _load_formula_lookup(self, svg_path: Path) -> Dict[str, Dict]:
        """Load formula SVG/alias/id lookup entries."""
        manifest_path = self._find_formula_manifest(svg_path)
        if manifest_path is None:
            return {}
        if manifest_path in self._formula_manifest_cache:
            return self._formula_manifest_cache[manifest_path]

        try:
            payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            payload = {}

        formulas = payload.get('formulas') if isinstance(payload, dict) else payload
        lookup: Dict[str, Dict] = {}
        if isinstance(formulas, list):
            for entry in formulas:
                if not isinstance(entry, dict):
                    continue
                keys = {
                    str(entry.get('id') or '').strip(),
                    Path(str(entry.get('svg_path') or '')).name,
                    Path(str(entry.get('filename') or '')).name,
                    Path(str(entry.get('short_alias') or '')).name,
                }
                for key in keys:
                    if key:
                        lookup[key] = entry

        self._formula_manifest_cache[manifest_path] = lookup
        return lookup

    def _resolve_formula_image_alias(self, svg_path: Path, href: str) -> Path | None:
        """Resolve a formula short_alias href to the actual formula SVG."""
        lookup = self._load_formula_lookup(svg_path)
        entry = lookup.get(Path(href).name)
        if not entry:
            return None
        svg_name = Path(str(entry.get('svg_path') or '')).name
        if not svg_name:
            return None
        manifest_path = self._find_formula_manifest(svg_path)
        if manifest_path is None:
            return None
        resolved = manifest_path.parent / svg_name
        return resolved if resolved.is_file() else None

    def _check_formula_image_metadata(
        self,
        attrs: str,
        href: str,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Check formula image references against formula_manifest.json."""
        lookup = self._load_formula_lookup(svg_path)
        filename = Path(href).name
        is_formula_like = filename.lower().startswith(('formula_', 'eq'))
        entry = lookup.get(filename)
        if not entry:
            if is_formula_like:
                result['warnings'].append(
                    f"Formula image {href} is not listed in formula_manifest.json; "
                    "AI cannot reliably map this SVG back to its LaTeX source."
                )
            return

        expected_id = str(entry.get('id') or '').strip()
        id_match = re.search(r'\bdata-formula-id="([^"]+)"', attrs)
        if not id_match:
            result['warnings'].append(
                f"Formula image {href} is missing data-formula-id=\"{expected_id}\"; "
                "add it so the slide preserves the manifest link."
            )
            return

        actual_id = id_match.group(1).strip()
        if expected_id and actual_id != expected_id:
            result['errors'].append(
                f"Formula image {href} has data-formula-id=\"{actual_id}\" "
                f"but formula_manifest.json expects \"{expected_id}\"."
            )

    def _check_sourced_image_attribution(self, content: str, svg_path: Path, result: Dict):
        """Require visible credit text for attribution-required web images.

        image_search.py records the legal tier in images/image_sources.json;
        Executor must render compact credit text into the SVG. This check
        prevents a quality-first CC BY / CC BY-SA image from silently reaching
        export without attribution.
        """
        manifest = self._load_image_sources_manifest(svg_path)
        items = manifest.get('items') or []
        if not items:
            return

        text_content = html.unescape(re.sub(r'<[^>]+>', ' ', content))
        text_content = re.sub(r'\s+', ' ', text_content)
        svg_stem = svg_path.stem

        for item in items:
            if not item.get('attribution_required') and item.get('license_tier') != 'attribution-required':
                continue

            filename = Path(str(item.get('filename') or '')).name
            slide = str(item.get('slide') or '').strip()
            referenced = bool(filename and filename in content)
            same_slide = bool(slide and slide == svg_stem)
            if not referenced and not same_slide:
                continue

            license_name = str(item.get('license_name') or '').upper()
            license_token = 'CC BY-SA' if 'BY-SA' in license_name else 'CC BY'
            has_credit = license_token in text_content.upper()
            if not has_credit:
                result['errors'].append(
                    f"Missing inline attribution for sourced image {filename or '(unknown)'} "
                    f"({license_token}). Add compact credit text per "
                    f"references/image-searcher.md §7."
                )

    @staticmethod
    def _normalize_size(value: str) -> str:
        """Normalize a font-size value for comparison: lowercase, strip spaces,
        strip trailing 'px'. Other units (em / rem / %) are kept as-is so that
        e.g. '1.5em' vs '24' stay distinct."""
        v = value.strip().lower()
        if v.endswith('px'):
            v = v[:-2].strip()
        return v

    # ------------------------------------------------------------------
    # Plain-text formula detection (Iron Rule §4.1)
    # ------------------------------------------------------------------

    # Patterns that indicate mathematical notation inside <text>/<tspan>.
    # Each tuple: (compiled regex, human-readable description).
    # Patterns are intentionally conservative — they target unambiguous
    # formula syntax, not every possible Unicode math character.
    #
    # NOTE: Unicode sub/superscript characters (², ³, ₂, ₑ, ⁻¹, etc.) are
    # explicitly ALLOWED as Tier A shorthand per §4.1 — do NOT add patterns
    # that flag them.  They render correctly via their own glyphs in PPT.
    _FORMULA_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Subscript notation:  a_1  T_e  ρ_L  x_{n+1}  H_2O
        (re.compile(r'[A-Za-z\u0370-\u03FF\u0400-\u04FF]_[\{\[A-Za-z0-9⊥∥]'),
         "scientific subscript notation (e.g. T_e, H_2O, x_{n+1})"),
        # Superscript notation:  a^2  x^n  e^{-x}  cm^{-3}
        (re.compile(r'[A-Za-z0-9\u0370-\u03FF\)]\^[\{\[A-Za-z0-9\-+]'),
         "superscript notation (e.g. x^2, e^{-x}, cm^{-3})"),
        # Summation / product / integral with limits:  ∑_{  ∏_{  ∫_  ∫_0^∞
        (re.compile(r'[∑∏∫][_^]'),
         "summation/product/integral with limits (e.g. ∑_{i=1}^{n})"),
        # Square root symbol: √
        (re.compile(r'[√∛∜]'),
         "radical symbol (√, ∛, ∜)"),
        # Fraction between variable-like tokens: v_⊥/ω_c, ΔT/Δt, dN/dt
        # but NOT abbreviation slashes (HS/VSS), unit rates (m/s, steps/sec),
        # or English-word slashes.  Post-match filter in _is_safe_slash().
        (re.compile(
            r'(?<![/\w])(?:[A-Z][a-z]?|[a-z]|[Δδ∂])[A-Za-z0-9_]*'
            r'/'
            r'(?:[A-Z][a-z]?|[a-z]|[Δδ∂])[A-Za-z0-9_]*(?![/\w])'
        ), "fraction between variables (e.g. a/b, ΔT/Δt, dN/dt)"),
        # NOTE: plain-text equations like "PV = nRT", "F = ma", "E = mc²"
        # are NOT flagged.  They render correctly as text in PPT because
        # they contain no structural math (subscripts, fractions, radicals).
        # The dangerous sub-patterns (caret ^, underscore _, √, ∫) are
        # caught by the patterns above.  Removing the equation-equals
        # pattern avoids false positives on definitions like
        # "OpenEdge = SPARTA DSMC 引擎".
    ]

    # Slash expressions that are NOT mathematical fractions.
    # Used by _is_safe_slash() to suppress false positives from the
    # fraction-detection pattern.
    _SAFE_SLASH_RE: re.Pattern = re.compile(
        r'^(?:'
        # Both sides all-uppercase (abbreviations): HS/VSS, E/B, AC/DC
        r'[A-Z]+/[A-Z]+'
        # Left side is 3+ alphabetic chars (English word): steps/sec, steps/s
        r'|[a-zA-Z]{3,}/[a-zA-Z]+'
        # Unit rates with single-letter unit on either side: K/min, J/mol
        r'|[A-Z]/[a-z]{2,}'
        # Chemical element slash (1 uppercase + optional lowercase): Be/W, Fe/Cr
        r'|[A-Z][a-z]?/[A-Z][a-z]?'
        r')$'
    )
    _SAFE_UNIT_FRACTIONS: frozenset = frozenset({
        'm/s', 'km/h', 'kg/m', 'J/K', 'W/m', 'V/m', 'A/m', 'N/m',
        'C/m', 'g/L', 'g/l', 'mg/L', 'rad/s', 'eV/K',
        'K/min', 'J/mol', 'g/mol', 'L/min', 'mL/min',
    })
    _SAFE_UNIT_TOKEN_RE: re.Pattern = re.compile(
        r'^(?:'
        r'(?:[pnumcdkMGT]?(?:m|g|s|A|K|mol|cd|Hz|N|Pa|J|W|C|V|F|S|'
        r'Wb|T|H|lm|lx|B|L|l|eV|rad|sr|min|h|sec))'
        r'(?:[-+]?\d{1,2})?'
        r')$'
    )

    def _check_plain_text_formulas(self, content: str, result: Dict):
        """Detect mathematical notation written as <text>/<tspan> content.

        Iron Rule §4.1 of shared-standards.md: formula-like text must use
        either Tier A (baseline-shift on tspan) or Tier B (<image>).
        Raw plain-text formulas without baseline-shift are errors.
        """
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return  # XML check already reported the failure

        violations: List[str] = []

        # Collect all text content from <text> elements.
        seen_elems = set()
        for elem in root.iter():
            local = elem.tag.split('}')[-1] if '}' in str(elem.tag) else str(elem.tag)
            if local == 'text' and id(elem) not in seen_elems:
                seen_elems.add(id(elem))
                self._scan_element_for_formulas(elem, violations)

        # Deduplicate
        seen = set()
        for v in violations:
            if v not in seen:
                seen.add(v)
                result['errors'].append(v)

    @staticmethod
    def _has_baseline_shift(elem) -> bool:
        """Check if element has baseline-shift attribute (Tier A rendering)."""
        bs = elem.get('baseline-shift')
        if bs:
            return True
        style = elem.get('style', '')
        return 'baseline-shift' in style

    def _scan_element_for_formulas(self, elem, violations: List[str]):
        """Recursively scan a <text> element's text/tail for formula patterns.

        Skips tspan elements that have baseline-shift set (Tier A — native
        sub/superscript), since those are properly handled by the converter.
        """
        for node in elem.iter():
            # Skip tspan nodes with baseline-shift — they use Tier A rendering
            local_tag = node.tag.split('}')[-1] if '}' in str(node.tag) else str(node.tag)
            if local_tag == 'tspan' and self._has_baseline_shift(node):
                # Still check tail text (text after the closing </tspan> tag
                # belongs to the parent, not this tspan)
                tail = (node.tail or '').strip()
                if tail:
                    self._check_fragment_for_formulas(tail, violations)
                continue

            for text_fragment in (node.text, node.tail):
                if not text_fragment or not text_fragment.strip():
                    continue
                self._check_fragment_for_formulas(text_fragment.strip(), violations)

    def _check_fragment_for_formulas(self, fragment: str, violations: List[str]):
        """Check a single text fragment against formula patterns."""
        for pattern, description in self._FORMULA_PATTERNS:
            match = pattern.search(fragment)
            if match:
                # Post-match false-positive guard for context-sensitive
                # patterns (fractions, equations).
                if self._is_formula_false_positive(match, description):
                    continue
                snippet = fragment[:80] + ('...' if len(fragment) > 80 else '')
                fix_hint = self._formula_fix_hint(description)
                violations.append(
                    f"Plain-text formula detected ({description}): "
                    f"\"{snippet}\" - {fix_hint}"
                )
                break  # one violation per fragment is enough

    @staticmethod
    def _formula_fix_hint(description: str) -> str:
        """Return a targeted fix hint for the detected formula pattern."""
        if 'subscript' in description:
            return (
                "scientific subscripts such as E_k/H_2O must be rendered with "
                "latex_to_svg.py (Tier B), or as one Tier A baseline-shift text "
                "frame only when the narrow inline exception applies"
            )
        if 'fraction' in description:
            return (
                "render variable fractions with latex_to_svg.py (Tier B); "
                "plain unit rates such as m2/s are allow-listed only when "
                "unambiguous"
            )
        return (
            "use baseline-shift (Tier A) only for the narrow inline exception, "
            "otherwise render with latex_to_svg.py (Tier B) per Iron Rule 4.1"
        )

    def _is_formula_false_positive(self, match: re.Match, description: str) -> bool:
        """Return True if the regex match is a known false positive.

        Suppresses common non-formula patterns that the broad regexes
        accidentally catch: abbreviation slashes (HS/VSS, E/B), unit rates
        (m/s, m2/s, cm2/s, kg/m3, steps/sec), and long-name definitions.
        """
        matched = match.group()
        if 'fraction' in description:
            # Known safe unit fractions
            if matched in self._SAFE_UNIT_FRACTIONS:
                return True
            parts = matched.split('/', 1)
            if len(parts) == 2 and all(self._SAFE_UNIT_TOKEN_RE.match(part) for part in parts):
                return True
            # Structural safe-slash patterns (abbreviations, English words)
            if self._SAFE_SLASH_RE.match(matched):
                return True
        return False

    # ------------------------------------------------------------------
    # Fake sub/superscript detection (Iron Rule §4.1 anti-pattern)
    # ------------------------------------------------------------------

    def _check_fake_sub_superscript(self, content: str, result: Dict):
        """Detect adjacent <text> elements faking sub/superscripts.

        Anti-pattern: placing "10" as one <text> and "2" as a separate
        smaller <text> positioned higher to visually approximate 10², or
        placing "m" and "-3" in separate text boxes to fake m^-3 inside
        running prose. This always causes spacing drift in PowerPoint.
        """
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return

        # Collect all <text> elements with their position, font-size, and content.
        text_elems: List[Dict] = []
        for elem in root.iter():
            local = elem.tag.split('}')[-1] if '}' in str(elem.tag) else str(elem.tag)
            if local != 'text':
                continue

            x_str = elem.get('x')
            y_str = elem.get('y')
            if x_str is None or y_str is None:
                continue

            try:
                x = float(x_str.split(',')[0].split()[0])
                y = float(y_str.split(',')[0].split()[0])
            except (ValueError, IndexError):
                continue

            # Collect all text content
            text_content = ''.join(elem.itertext()).strip()
            if not text_content:
                continue

            # Get font-size (from attribute or style)
            fs_raw = elem.get('font-size') or ''
            if not fs_raw:
                style = elem.get('style', '')
                for part in style.split(';'):
                    if 'font-size' in part and ':' in part:
                        fs_raw = part.split(':')[1].strip()
                        break
            try:
                fs = float(fs_raw.replace('px', '').replace('pt', '').strip())
            except (ValueError, AttributeError):
                fs = 0.0

            text_elems.append({
                'x': x, 'y': y, 'fs': fs,
                'text': text_content, 'len': len(text_content),
            })

        if len(text_elems) < 2:
            return

        def _approx_text_width(text: str, font_size: float) -> float:
            width_units = 0.0
            for char in text:
                if char.isspace():
                    width_units += 0.35
                elif ord(char) > 127:
                    width_units += 1.0
                elif char.isalpha():
                    width_units += 0.65
                elif char.isdigit():
                    width_units += 0.6
                else:
                    width_units += 0.45
            return width_units * font_size

        # Check every pair: a smaller text (1-3 chars) near the right edge of
        # a larger text, with vertical offset → likely fake sub/super.
        # The base is determined by font-size, not text length, so short-base
        # cases like "m" + "-3" and "cm" + "-3" are also caught.
        for i, a in enumerate(text_elems):
            for b in text_elems[i + 1:]:
                # Skip if either has zero font-size (unknown)
                if a['fs'] <= 0 or b['fs'] <= 0:
                    continue

                if a['fs'] > b['fs']:
                    base, short = a, b
                else:
                    base, short = b, a

                # The exponent/subscript candidate must stay short.
                if short['len'] > 3:
                    continue

                # The short element must be noticeably smaller.
                if base['fs'] <= short['fs'] * 1.1:
                    continue

                # Must be horizontally close — the short text should be near
                # the right edge of the base.  Approximate base width.
                approx_base_width = _approx_text_width(base['text'], base['fs'])
                dx = short['x'] - (base['x'] + approx_base_width)
                # Allow moderate overlap or a modest gap. Sentence-tail cases
                # such as "粒子密度单位为 m" + "-3" often overlap the final base
                # glyph slightly when the exponent is nudged upward.
                if dx < -base['fs'] * 1.5 or dx > base['fs'] * 3.0:
                    continue

                # Must be vertically offset (superscript = higher = smaller y;
                # subscript = lower = larger y).
                dy = abs(short['y'] - base['y'])
                if dy < base['fs'] * 0.15 or dy > base['fs'] * 1.0:
                    continue

                # This looks like a fake sub/superscript
                result['errors'].append(
                    f"Fake sub/superscript detected: \"{base['text']}\" "
                    f"(font-size {base['fs']}) + \"{short['text']}\" "
                    f"(font-size {short['fs']}) are separate <text> elements "
                    f"positioned to fake a superscript/subscript. Keep the "
                    f"whole phrase in one <text> / one PPT text frame, using "
                    f"<tspan baseline-shift=\"super/sub\" font-size=\"70%\"> "
                    f"for the narrow Tier A case, or latex_to_svg.py for Tier B. "
                    f"Do not keep separate text boxes and tweak x/y during QC repair."
                )

    def _check_split_sentence(self, content: str, result: Dict):
        """Detect adjacent <text> elements on the same line that form one sentence.

        Anti-pattern: splitting "实现**10倍**效率提升" into three side-by-side
        <text> elements to apply different colors/weights. This creates three
        independent text frames in PowerPoint with fragile spacing.
        Correct approach: one <text> with <tspan> children for inline styling.

        Triggers when 3+ <text> elements share the same y-coordinate (within
        tolerance), are sequentially positioned on x, and their combined text
        reads as a continuous phrase (no large horizontal gaps).
        """
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return

        text_elems: List[Dict] = []
        for elem in root.iter():
            local = elem.tag.split('}')[-1] if '}' in str(elem.tag) else str(elem.tag)
            if local != 'text':
                continue
            x_str = elem.get('x')
            y_str = elem.get('y')
            if x_str is None or y_str is None:
                continue
            try:
                x = float(x_str.split(',')[0].split()[0])
                y = float(y_str.split(',')[0].split()[0])
            except (ValueError, IndexError):
                continue
            text_content = ''.join(elem.itertext()).strip()
            if not text_content:
                continue
            fs_raw = elem.get('font-size') or ''
            try:
                fs = float(fs_raw.replace('px', '').replace('pt', '').strip())
            except (ValueError, AttributeError):
                fs = 20.0  # reasonable default
            text_elems.append({
                'x': x, 'y': y, 'fs': fs, 'text': text_content,
            })

        if len(text_elems) < 3:
            return

        # Group by y-coordinate (tolerance: within 2px = same line)
        y_tolerance = 2.0
        text_elems.sort(key=lambda e: (e['y'], e['x']))
        groups: List[List[Dict]] = []
        current_group: List[Dict] = [text_elems[0]]
        for elem in text_elems[1:]:
            if abs(elem['y'] - current_group[0]['y']) <= y_tolerance:
                current_group.append(elem)
            else:
                if len(current_group) >= 3:
                    groups.append(current_group)
                current_group = [elem]
        if len(current_group) >= 3:
            groups.append(current_group)

        for group in groups:
            group.sort(key=lambda e: e['x'])
            # Check for sequential x-positioning (each element starts near
            # where the previous one ends). Approximate width from char count.
            sequential = True
            for i in range(1, len(group)):
                prev = group[i - 1]
                curr = group[i]
                approx_prev_width = len(prev['text']) * prev['fs'] * 0.6
                gap = curr['x'] - (prev['x'] + approx_prev_width)
                # Allow small gap or slight overlap, but flag large gaps
                # (which indicate intentional separate columns, not a split
                # sentence).
                if gap > prev['fs'] * 3.0:
                    sequential = False
                    break

            if not sequential:
                continue

            combined = ''.join(e['text'] for e in group)
            previews = [f'"{e["text"]}"' for e in group]
            result['warnings'].append(
                f"Split-sentence detected: {len(group)} adjacent <text> "
                f"elements on the same line ({' + '.join(previews)}) should "
                f"be one <text> with <tspan> children for inline styling. "
                f"Combined text: \"{combined[:80]}\""
            )

    def _categorize_issue(self, error_msg: str) -> str:
        """Categorize issue type"""
        if 'Invalid XML' in error_msg:
            return 'XML well-formedness'
        elif 'viewBox' in error_msg:
            return 'viewBox issues'
        elif 'foreignObject' in error_msg:
            return 'foreignObject'
        elif 'Plain-text formula' in error_msg:
            return 'Plain-text formula (Iron Rule §4.1)'
        elif 'Formula image' in error_msg and 'readable minimum' in error_msg:
            return 'Formula image too small'
        elif 'Fake sub/superscript' in error_msg:
            return 'Fake sub/superscript (Iron Rule §4.1)'
        elif 'Split-sentence' in error_msg:
            return 'Split-sentence (use <tspan> for inline styling)'
        elif 'Banned font' in error_msg:
            return 'Banned font'
        elif 'font' in error_msg.lower():
            return 'Font issues'
        else:
            return 'Other'

    def check_directory(self, directory: str, expected_format: str = None) -> List[Dict]:
        """
        Check all SVG files in a directory

        Args:
            directory: Directory path
            expected_format: Expected canvas format

        Returns:
            List of check results
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            print(f"[ERROR] Directory does not exist: {directory}")
            return []

        # Brand-only template directories (templates/brands/<id>/) have no SVG
        # roster — design_spec.md frontmatter declares `kind: brand`. Skip SVG
        # checks entirely; brand validation lives in register_template.py.
        if self.template_mode and dir_path.is_dir():
            spec = dir_path / 'design_spec.md'
            if spec.exists() and _design_spec_is_brand(spec):
                print(
                    f"[INFO] Brand directory detected (kind: brand) — "
                    f"SVG checks skipped."
                )
                print(
                    f"[INFO] Validate brand specs via: "
                    f"python3 scripts/register_template.py "
                    f"--kind brand <brand_id> --dry-run"
                )
                return self.results

        # Find all SVG files
        if dir_path.is_file():
            svg_files = [dir_path]
        else:
            if self.template_mode:
                # Template directories live at templates/layouts/<id>/.
                svg_files = sorted(dir_path.glob('*.svg'))
            else:
                svg_output = dir_path / \
                    'svg_output' if (
                        dir_path / 'svg_output').exists() else dir_path
                svg_files = sorted(svg_output.glob('*.svg'))

        if not svg_files:
            print(f"[WARN] No SVG files found")
            return []

        print(f"\n[SCAN] Checking {len(svg_files)} SVG file(s)...\n")

        for svg_file in svg_files:
            result = self.check_file(str(svg_file), expected_format)
            self._print_result(result)

        if self.template_mode and dir_path.is_dir():
            self._check_template_contract(dir_path, svg_files)
        elif dir_path.is_dir():
            self._check_animation_config_contract(dir_path)

        return self.results

    def _check_animation_config_contract(self, dir_path: Path) -> None:
        """Project-level animations.json reference checks."""
        if _load_animation_config is None or _validate_animation_config is None:
            return
        project_path = dir_path if (dir_path / 'svg_output').exists() else dir_path.parent
        try:
            config = _load_animation_config(project_path)
        except Exception as exc:
            self._animation_issues.append(('error', f"animations.json is invalid: {exc}"))
            return
        if not config:
            return
        for warning in _validate_animation_config(project_path, config):
            self._animation_issues.append(('warning', warning))

    def _check_template_contract(self, dir_path: Path,
                                 svg_files: List[Path]) -> None:
        """Template-mode-only checks: roster ↔ design_spec consistency and
        per-page placeholder hints.

        - **Roster mismatch (orphan / missing)** is reported as an *error*: a
          stale roster will produce a wrong ``layouts_index.json`` entry.
        - **Placeholder gaps** are reported as *warnings*. Templates may
          legitimately omit conventional placeholders or swap them out (e.g.
          ``{{CLOSING_MESSAGE}}`` instead of ``{{THANK_YOU}}``), and a content
          variant may use a bespoke slot vocabulary. Designers can declare
          their own per-stem expectations via ``placeholders:`` frontmatter
          in ``design_spec.md`` to suppress these warnings explicitly.

        Issues are aggregated and printed in :py:meth:`print_summary` so the
        per-file report stays focused on intrinsic SVG validity.
        """
        spec_path = dir_path / 'design_spec.md'
        spec_text = spec_path.read_text(encoding='utf-8') if spec_path.exists() else ""
        spec_pages = self._extract_spec_roster(spec_text) if spec_text else []
        custom_contract = self._extract_frontmatter_placeholders(spec_text) if spec_text else {}

        on_disk = {p.stem for p in svg_files}

        if spec_pages:
            spec_set = set(spec_pages)
            orphan = sorted(on_disk - spec_set)
            missing = sorted(spec_set - on_disk)
            for page in orphan:
                self._template_issues.append((
                    'error',
                    'roster_orphan',
                    f"{page}.svg exists on disk but is not listed in design_spec.md Page Roster",
                ))
            for page in missing:
                self._template_issues.append((
                    'error',
                    'roster_missing',
                    f"design_spec.md Page Roster lists {page} but {page}.svg is missing on disk",
                ))
        elif spec_path.exists():
            # design_spec.md is present but the roster parser found nothing —
            # surface as a warning. Legacy specs may lack an explicit roster.
            self._template_issues.append((
                'warning',
                'roster_unknown',
                f"could not extract page roster from {spec_path.name}; "
                "skipping orphan/missing checks",
            ))
        else:
            self._template_issues.append((
                'error',
                'spec_missing',
                f"{spec_path.name} not found — required for every library template",
            ))

        # Per-file placeholder coverage. Variants reuse the parent type's set
        # (e.g. 03a_content_two_col.svg ↔ 03_content rules) unless the spec
        # frontmatter overrides that page (custom_contract takes precedence).
        for svg_file in svg_files:
            expected = self._lookup_template_contract(
                svg_file.stem, overrides=custom_contract,
            )
            if expected is None:
                continue  # extension pages or stems with no convention
            try:
                content = svg_file.read_text(encoding='utf-8')
            except OSError:
                continue
            for placeholder in expected:
                if placeholder not in content:
                    self._template_issues.append((
                        'warning',
                        'placeholder_hint',
                        f"{svg_file.name}: missing conventional placeholder {placeholder} "
                        "(declare 'placeholders:' frontmatter in design_spec.md to silence)",
                    ))

    @staticmethod
    def _extract_frontmatter_placeholders(spec_text: str) -> Dict[str, Tuple[str, ...]]:
        """Read the optional ``placeholders:`` map from design_spec.md frontmatter.

        Shape:

        .. code-block:: yaml

            placeholders:
              01_cover: ["{{TITLE}}", "{{BRAND_LOGO}}"]
              03_content: []        # explicitly assert "no expectation"
              03a_content_two_col:  # variant-specific override
                - "{{LEFT_TITLE}}"
                - "{{RIGHT_TITLE}}"

        Each key is a stem (full filename without ``.svg``) or page-type prefix
        (``01_cover``). An empty list silences the default convention for that
        stem; a populated list replaces the default. Stems / prefixes not
        listed fall back to ``DEFAULT_PLACEHOLDER_CONVENTION``.

        We parse with PyYAML when available; otherwise we fall back to a
        minimal regex that handles the documented shape.
        """
        if not spec_text.startswith("---\n"):
            return {}
        end = spec_text.find("\n---\n", 4)
        if end == -1:
            return {}
        block = spec_text[4:end]

        try:
            import yaml  # type: ignore
        except ImportError:
            return _parse_placeholders_fallback(block)

        try:
            data = yaml.safe_load(block) or {}
        except yaml.YAMLError:
            return {}
        if not isinstance(data, dict):
            return {}
        raw = data.get("placeholders")
        if not isinstance(raw, dict):
            return {}

        out: Dict[str, Tuple[str, ...]] = {}
        for stem, value in raw.items():
            if not isinstance(stem, str):
                continue
            if isinstance(value, list):
                out[stem] = tuple(str(v) for v in value)
            elif value is None:
                out[stem] = ()
        return out

    @staticmethod
    def _extract_spec_roster(spec_text: str) -> List[str]:
        """Best-effort: extract the page roster from design_spec.md.

        Templates do not share a uniform section index for the roster — the
        personality-only skeleton puts it at §V "Page Roster"; legacy specs use
        §VI "Page Roster" or bury filenames under §VII "Page Types" as
        ``### N. Cover Page (01_cover.svg)``. We match by title (any roman
        index), then fall back to scanning the whole document for any
        backtick-wrapped ``<stem>.svg`` reference.

        Returns the deduplicated stem list in document order. Empty result
        means we can't determine the roster confidently — caller should treat
        that as "skip orphan/missing checks", not as "no pages declared".
        """
        # Pass 1: explicit roster section, any roman numeral.
        section = re.search(
            r"^##\s+[IVX]+\.\s+(?:Page Roster|Page Structure|Pages|Page Types)\b.*?(?=^##\s+|\Z)",
            spec_text,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        scope = section.group(0) if section else None

        # Pass 2: full document. We *only* trust this scan when the explicit
        # roster scan came up empty (no `<stem>.svg` references inside it) —
        # otherwise the explicit section's deliberate roster wins over loose
        # mentions elsewhere.
        if scope and re.search(r"[`\(][0-9A-Za-z_]+\.svg[`\)]", scope):
            text = scope
        else:
            text = spec_text

        stems: List[str] = []
        seen: set = set()
        # Accept backtick-quoted (`01_cover.svg`) and parenthesized
        # (01_cover.svg) forms — existing specs use either.
        svg_ref_re = re.compile(r"[`\(]([0-9A-Za-z_]+\.svg)[`\)]")
        for match in svg_ref_re.finditer(text):
            stem = match.group(1)[:-4]
            if stem in seen or not re.match(r"^\d", stem):
                continue
            seen.add(stem)
            stems.append(stem)

        # If the explicit §VI scan listed bare stems (without .svg), accept
        # those as fallback — but only when they were inside that section.
        if not stems and scope:
            for match in re.finditer(r"`([0-9]{2}[a-z]?_[A-Za-z0-9_]+)`", scope):
                stem = match.group(1)
                if stem in seen:
                    continue
                seen.add(stem)
                stems.append(stem)

        return stems

    @classmethod
    def _lookup_template_contract(
        cls, stem: str, *,
        overrides: Dict[str, Tuple[str, ...]] | None = None,
    ) -> Tuple[str, ...] | None:
        """Resolve a SVG stem to its expected placeholder set.

        Resolution order, first hit wins:
        1. ``overrides[stem]`` — frontmatter entry for the exact filename
        2. ``overrides[<page_type_prefix>]`` — frontmatter entry for the
           variant's parent type (e.g. ``03_content`` for
           ``03a_content_two_col``)
        3. ``DEFAULT_PLACEHOLDER_CONVENTION[<page_type_prefix>]``

        Returns ``None`` for stems with no matching convention or override —
        e.g. extension pages like ``05_section_break``. ``()`` (empty tuple)
        is a valid value meaning "no expected placeholders" — used to
        explicitly silence the default convention.
        """
        overrides = overrides or {}
        if stem in overrides:
            return overrides[stem]

        # Variant convention: <NN><letter>?_<rest>; strip the letter to find
        # the parent type prefix, e.g. "03a_content_two_col" -> "03_content".
        match = re.match(r"^(\d{2})([a-z])?_([a-z]+)", stem)
        if not match:
            return None
        num, _letter, kind = match.groups()
        key = f"{num}_{kind}"
        if key in overrides:
            return overrides[key]
        return cls.DEFAULT_PLACEHOLDER_CONVENTION.get(key)

    def _print_result(self, result: Dict):
        """Print check result for a single file"""
        if result['passed']:
            if result['warnings']:
                icon = "[WARN]"
                status = "Passed (with warnings)"
            else:
                icon = "[OK]"
                status = "Passed"
        else:
            icon = "[ERROR]"
            status = "Failed"

        print(f"{icon} {result['file']} - {status}")

        # Display basic info
        if result['info']:
            info_items = []
            if 'viewbox' in result['info']:
                info_items.append(f"viewBox: {result['info']['viewbox']}")
            if info_items:
                print(f"   {' | '.join(info_items)}")

        # Display errors
        if result['errors']:
            for error in result['errors']:
                print(f"   [ERROR] {error}")

        # Display warnings
        if result['warnings']:
            for warning in result['warnings'][:2]:  # Only show first 2 warnings
                print(f"   [WARN] {warning}")
            if len(result['warnings']) > 2:
                print(f"   ... and {len(result['warnings']) - 2} more warning(s)")

        print()

    def print_summary(self):
        """Print check summary"""
        print("=" * 80)
        print("[SUMMARY] Check Summary")
        print("=" * 80)

        print(f"\nTotal files: {self.summary['total']}")
        print(
            f"  [OK] Fully passed: {self.summary['passed']} ({self._percentage(self.summary['passed'])}%)")
        print(
            f"  [WARN] With warnings: {self.summary['warnings']} ({self._percentage(self.summary['warnings'])}%)")
        print(
            f"  [ERROR] With errors: {self.summary['errors']} ({self._percentage(self.summary['errors'])}%)")

        if self.issue_types:
            print(f"\nIssue categories:")
            for issue_type, count in sorted(self.issue_types.items(), key=lambda x: x[1], reverse=True):
                print(f"  {issue_type}: {count}")

        # spec_lock drift aggregation (only printed when a lock was found)
        self._print_drift_summary()

        # Template-mode aggregation (orphan/missing roster + placeholder hints)
        self._print_template_summary()

        # Animation config aggregation.
        self._print_animation_summary()

        # Fix suggestions
        if self.summary['errors'] > 0 or self.summary['warnings'] > 0:
            print(f"\n[TIP] Common fixes:")
            print(f"  1. XML well-formedness: write typography as raw Unicode (—, ©, →, NBSP); escape XML reserved chars as &amp; &lt; &gt; &quot; &apos; — never use HTML named entities like &nbsp; &mdash; &copy;")
            print(f"  2. viewBox issues: Ensure consistency with canvas format (see references/canvas-formats.md)")
            print(f"  3. foreignObject: Use <text> + <tspan> for manual line breaks")
            print(f"  4. Font issues: end every font-family stack with a PPT-safe family (e.g. Microsoft YaHei / Arial / Consolas)")

    def _print_animation_summary(self):
        """Print animations.json validation issues if present."""
        if not self._animation_issues:
            return

        errors = [item for item in self._animation_issues if item[0] == 'error']
        warnings = [item for item in self._animation_issues if item[0] == 'warning']
        self.summary['errors'] += len(errors)
        self.summary['warnings'] += len(warnings)
        for severity, _msg in self._animation_issues:
            self.issue_types[f'animation_config_{severity}'] += 1

        print("\n[ANIMATION] animations.json checks")
        for _severity, msg in errors:
            print(f"  [ERROR] {msg}")
        for _severity, msg in warnings:
            print(f"  [WARN] {msg}")

    def _print_template_summary(self):
        """Aggregate template-mode roster / placeholder issues at the bottom.

        Errors land under the ``errors`` summary count (so the exit signal
        from ``main`` agrees), warnings under ``warnings``. Both are listed
        per file so the user can act on them directly.
        """
        if not self._template_issues:
            return

        errors = [item for item in self._template_issues if item[0] == 'error']
        warnings = [item for item in self._template_issues if item[0] == 'warning']

        # Mirror into the global summary so downstream "0 errors" gates honor
        # template-mode issues.
        self.summary['errors'] += len(errors)
        self.summary['warnings'] += len(warnings)
        for severity, kind, _msg in self._template_issues:
            self.issue_types[f"template_{kind}"] += 1

        print("\n[TEMPLATE] Template mode checks")
        if errors:
            print(f"  Errors ({len(errors)}):")
            for _sev, kind, msg in errors:
                print(f"    [{kind}] {msg}")
        if warnings:
            print(f"  Warnings ({len(warnings)}):")
            for _sev, kind, msg in warnings:
                print(f"    [{kind}] {msg}")
        if not errors:
            print("  No structural roster issues. Placeholder hints above are advisory only;")
            print("  declare 'placeholders:' frontmatter in design_spec.md to silence them.")

    def _print_drift_summary(self):
        """Print spec_lock drift aggregation if any was observed.

        Values are sorted by file-count descending so frequent drift surfaces
        first. Frequent drift usually means spec_lock.md is missing entries
        the Strategist should have included; rare drift is more likely actual
        Executor drift and warrants SVG review.
        """
        if not self._lock_seen:
            return
        has_drift = any(self._drift_summary[cat] for cat in self._drift_summary)
        if not has_drift:
            print("\n[OK] spec_lock drift: none — all colors, fonts, and sizes are anchored to spec_lock.md")
            return

        print("\nspec_lock drift — values used outside spec_lock.md:")
        labels = [('colors', 'Colors'),
                  ('fonts', 'Font families'),
                  ('sizes', 'Font sizes')]
        for category, label in labels:
            items = self._drift_summary.get(category, {})
            if not items:
                continue
            entries = sorted(items.items(), key=lambda x: (-len(x[1]), x[0]))
            print(f"  {label}:")
            for val, files in entries:
                n = len(files)
                suffix = "file" if n == 1 else "files"
                print(f"    {val}  ({n} {suffix})")
        print(
            "Tip: frequent out-of-lock values usually mean spec_lock.md is missing\n"
            "     entries — extend the lock (scripts/update_spec.py or manual edit).\n"
            "     Rare ones are likely Executor drift — review the affected SVGs."
        )

    def _percentage(self, count: int) -> int:
        """Calculate percentage"""
        if self.summary['total'] == 0:
            return 0
        return int(count / self.summary['total'] * 100)

    def export_report(self, output_file: str = 'svg_quality_report.txt'):
        """Export check report"""
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("PPT Master SVG Quality Check Report\n")
            f.write("=" * 80 + "\n\n")

            for result in self.results:
                status = "[OK] Passed" if result['passed'] else "[ERROR] Failed"
                f.write(f"{status} - {result['file']}\n")
                f.write(f"Path: {result.get('path', 'N/A')}\n")

                if result['info']:
                    f.write(f"Info: {result['info']}\n")

                if result['errors']:
                    f.write(f"\nErrors:\n")
                    for error in result['errors']:
                        f.write(f"  - {error}\n")

                if result['warnings']:
                    f.write(f"\nWarnings:\n")
                    for warning in result['warnings']:
                        f.write(f"  - {warning}\n")

                f.write("\n" + "-" * 80 + "\n\n")

            # Write summary
            f.write("\n" + "=" * 80 + "\n")
            f.write("Check Summary\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total files: {self.summary['total']}\n")
            f.write(f"Fully passed: {self.summary['passed']}\n")
            f.write(f"With warnings: {self.summary['warnings']}\n")
            f.write(f"With errors: {self.summary['errors']}\n")

        print(f"\n[REPORT] Check report exported: {output_file}")


def print_usage() -> None:
    """Print CLI usage information."""
    print("PPT Master - SVG Quality Check Tool\n")
    print("Usage:")
    print("  python3 scripts/svg_quality_checker.py <svg_file>")
    print("  python3 scripts/svg_quality_checker.py <directory>")
    print("  python3 scripts/svg_quality_checker.py <template_dir> --template-mode")
    print("  python3 scripts/svg_quality_checker.py --all examples")
    print("\nExamples:")
    print("  python3 scripts/svg_quality_checker.py examples/project/svg_output/slide_01.svg")
    print("  python3 scripts/svg_quality_checker.py examples/project/svg_output")
    print("  python3 scripts/svg_quality_checker.py examples/project")
    print("  python3 scripts/svg_quality_checker.py templates/layouts/academic_defense --template-mode")
    print("\nOptions:")
    print("  --format <ppt169|ppt43|...>   Expected canvas format")
    print("  --template-mode               Validate a templates/layouts/<id> directory:")
    print("                                  glob *.svg directly, skip spec_lock checks,")
    print("                                  enforce roster ↔ design_spec.md Page Roster consistency,")
    print("                                  and emit advisory placeholder-convention warnings.")


def main() -> None:
    """Run the CLI entry point."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)

    if sys.argv[1] in {"-h", "--help", "help"}:
        print_usage()
        sys.exit(0)

    if sys.argv[1].startswith("--") and sys.argv[1] not in {"--all"}:
        print(f"[ERROR] Missing target before option: {sys.argv[1]}")
        print_usage()
        sys.exit(1)

    template_mode = '--template-mode' in sys.argv
    checker = SVGQualityChecker(template_mode=template_mode)

    # Parse arguments
    target = sys.argv[1]
    expected_format = None

    if '--format' in sys.argv:
        idx = sys.argv.index('--format')
        if idx + 1 < len(sys.argv):
            expected_format = sys.argv[idx + 1]

    # Execute check
    if target == '--all':
        # Check all example projects
        base_dir = sys.argv[2] if len(sys.argv) > 2 else 'examples'
        from project_utils import find_all_projects
        projects = find_all_projects(base_dir)

        for project in projects:
            print(f"\n{'=' * 80}")
            print(f"Checking project: {project.name}")
            print('=' * 80)
            checker.check_directory(str(project))
    else:
        checker.check_directory(target, expected_format)

    # Print summary
    checker.print_summary()

    # Export report (if specified)
    if '--export' in sys.argv:
        output_file = 'svg_quality_report.txt'
        if '--output' in sys.argv:
            idx = sys.argv.index('--output')
            if idx + 1 < len(sys.argv):
                output_file = sys.argv[idx + 1]
        checker.export_report(output_file)

    # Return exit code
    if checker.summary['errors'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
