# Executor Common Guidelines

> Style-specific content is in the corresponding `executor-{style}.md`. Technical constraints are in shared-standards.md.

---

## 1. Template Adherence Rules

### 1.0 Pre-generation Batch Read

**Hard rule**: Before the first SVG page, batch-read every template SVG this deck will reference. Read once up front, never re-read during generation.

| Source list | Read path |
|---|---|
| Chosen template's `design_spec.md` (read frontmatter to detect `replication_mode`) | `templates/<chosen_template>/design_spec.md` |
| Every distinct `<basename>` in `spec_lock.md page_layouts` | `templates/<chosen_template>/<basename>.svg` |
| Every distinct chart name in `spec_lock.md page_charts` | `templates/charts/<chart_name>.svg` |
| Chart types in `design_spec.md §VII` not covered above | `templates/charts/<chart_name>.svg` |

**Forbidden — re-reading during generation**:
- Layout SVG already loaded in this batch
- Chart SVG already loaded in this batch

`spec_lock.md` is the only file re-read per page (§2.1).

**Exception**: user mid-deck adds pages or swaps templates introducing a basename/chart absent from the original batch → read the new file once, continue.

> Note: batched prefix reads stay in the cached prompt prefix; per-page `spec_lock.md` re-reads append below and benefit from that cache. Scattered on-demand reads of layout/chart SVGs would invalidate downstream cache and sit in the compression-vulnerable mid-context region.

Resolve the per-page template SVG via `spec_lock.md page_layouts` (authoritative). The legacy page-type table below is a **last-resort fallback** for legacy decks where `page_layouts` is missing.

**Resolution order (per page):**

1. **Mirror-mode template** (template's `design_spec.md` frontmatter has `replication_mode: mirror`) → see §1.1 below. The page is consumed as a **visual reference**, not as a placeholder shell.
2. `spec_lock.md page_layouts` has `P<NN>: <basename>` for this page → inherit the structure of `templates/<chosen_template>/<basename>.svg` (already in context from §1.0).
3. `page_layouts` exists but **no entry** for this page → **free design**, no template inheritance.
4. `page_layouts` section absent (legacy deck) **and** `templates/` directory exists → fall back to the page-type table below, matching by SVG filename keyword (cover/chapter/content/ending/toc). Read the matched file at first use if §1.0 batch did not cover it.
5. No template at all → free design.

> Note: `page_layouts` disambiguates the multiple content variants modern templates ship (e.g., `graduation_defense` has 8); the legacy table cannot.

### 1.1 Mirror-mode templates — reference-style consumption

When the project's chosen template is a `mirror` template (`design_spec.md` frontmatter declares `replication_mode: mirror`), Executor switches to a **reference-style** consumption path that bypasses placeholder substitution:

1. **Per-page reference selection** — Strategist selects one mirror page per project page via `spec_lock.md page_layouts` (e.g., `P04: 015_content`). The basename is the mirror filename without extension; Strategist made this choice by reading `design_spec.md §V Page Roster` descriptions, not by guessing.
2. **Copy, don't fill** — open the referenced mirror SVG (already in context from §1.0). **Copy it as the starting point for the project page**, then edit text elements in place to express the project's content for `P<NN>`. Preserve every non-text element verbatim: backgrounds, decorative shapes, sprite-cropped images, charts, icon usage, color values, font families, geometry, sprite `<svg viewBox>` wrappers, `<image>` references.
3. **What you may edit** — the visible text content of `<text>` / `<tspan>` elements that express slide-specific content (title, body, captions, KPI labels, dates, page numbers). Replace the source deck's example text with the project's text for this page from `design_spec.md §IX` and `notes/<NN>_*.md`.
4. **What you must not touch** — element positions, sizes, fonts, colors, fills, strokes, gradients, image hrefs, `<g>` grouping, sprite-sheet `<svg viewBox>` wrappers, decorative `<rect>` / `<path>` / `<circle>` / `<polygon>` shapes, `<use data-icon="...">` markers, embedded chart data structures. Mirror's value is preserving the source deck's visual identity — any geometric / decorative drift defeats the purpose.
5. **Content fit** — the mirror page was chosen by Strategist because its layout matches the content slot. If the project's content for `P<NN>` legitimately needs more / fewer items than the mirror page provides (e.g. mirror shows 3 KPI cards, project has 4 metrics), keep the mirror page's visual rhythm and either drop one metric to fit or split across two pages — do **not** restructure the mirror page's grid. If neither works, surface a `warning: P<NN> content does not fit mirror reference <basename>; suggest different reference page` and proceed with the closest-fit edit.
6. **No `{{}}` substitution** — mirror SVGs do not contain placeholder markers. Do not search for `{{TITLE}}` / `{{CONTENT_AREA}}` etc.; do not invent placeholders. The whole mirror contract is "verbatim source + in-place text edit".
7. **Output filename** — follow the standard project SVG naming convention (`<NN>_<page_name>.svg` where `<NN>` matches the project page index, not the mirror source index). The mirror filename is the *reference*, not the *output*.

**Detecting mirror mode**: read the chosen template's `design_spec.md` frontmatter once during §1.0 batch read. If `replication_mode: mirror`, every page that hits `page_layouts` follows §1.1 above; pages without a `page_layouts` entry still fall through to free design (resolution rule 3 above).

**Mirror + chart pages**: chart structures inside a mirror SVG are already drawn (axis, series, labels). Treat them as visual references — replace the data labels and series text content to match the project's chart spec, but do not redraw the chart from a `templates/charts/<name>.svg` baseline. A mirror template's `page_charts` entries are normally absent for this reason.

**Legacy fallback table** (used only when `page_layouts` is absent):

| Page Type | Corresponding Template | Adherence Rules |
|-----------|----------------------|-----------------|
| Cover | `01_cover.svg` | Inherit background, decorative elements, layout structure; replace placeholder content |
| Chapter | `02_chapter.svg` | Inherit numbering style, title position, decorative elements |
| Content | `03_content.svg` | Inherit header/footer styles; **content area may be freely laid out** |
| Ending | `04_ending.svg` | Inherit background, thank-you message position, contact info layout |
| TOC | `02_toc.svg` | **Optional**: Inherit TOC title, list styles |

### Page-Template Mapping Declaration (Required Output)

Before generating each page, output which template is used:

```
📝 **Template mapping**: `templates/<chosen_template>/03a_content_image_text.svg` (or "None (free design)")
🎯 **Adherence rules / layout strategy**: [specific description]
```

- **Content pages**: template defines only header/footer; content area is free
- **No template**: generate entirely per the Design Spec

---

## 2. Design Parameter Confirmation (Mandatory Step)

Before the first SVG page, output a confirmation listing: canvas dimensions, body font size, color scheme (primary/secondary/accent HEX), font plan. Prevents spec/execution drift.

### 2.1 Per-page spec_lock re-read (Mandatory)

> Long decks drift off the declared palette/icons mid-deck due to context compression. `spec_lock.md` is the canonical execution reference — re-read it per page to bypass model memory.

**Hard rule**: Before generating **each** SVG page, `read_file <project_path>/spec_lock.md`. Use only values from this file, not from memory. If context was auto-compacted, also `read_file <project_path>/design_spec.md` for the current page's §IX brief.

**If `spec_lock.md` is missing**: emit `warning: spec_lock.md missing — generating without execution lock` once, then proceed using `design_spec.md` values. Expected only for legacy projects; new projects MUST have it (see [strategist.md](strategist.md) §6 step 4).

**Forbidden — values outside the lock**:

- Colors (fill / stroke / stop-color) MUST come from `colors`
- Icons MUST come from `icons.inventory`; library MUST equal `icons.library`
- Text language MUST follow `language.content_language` when that section exists; if the lock omits it (legacy deck), follow `design_spec.md §I Project Information → Content Language`. Do not silently translate slide copy to another language.
- Font family from `typography`: use role override (`title_family` / `body_family` / `emphasis_family` / `code_family`) if declared, else fall back to `font_family`
- Font sizes follow a **ramp anchored on `typography.body`**, not a closed menu. Use the declared slots when they fit. Intermediate sizes (e.g., 40px hero number, 13px annotation) are allowed if the ratio to `body` falls within the role's band (see `design_spec.md §IV ramp table`). Sizes outside every band require extending the lock first.
- Images MUST reference files listed under `images`; no invented filenames
- **⛔ IRON RULE — No plain-text formulas / SVG-FIRST** — see [`shared-standards.md §4.1`](shared-standards.md) for the full rules. This is a **blocking error** — the page MUST NOT be saved with raw formula text. **NEVER split a formula across multiple `<text>` elements.** SVG-first policy:
  - **DEFAULT: Tier B — SVG image for ALL formulas** — including simple sub/superscripts (10², H₂O, Tₑ, m²). Before writing each page, call `latex_to_svg.py` for every formula-like expression:
    ```
    python3 ${SKILL_DIR}/scripts/latex_to_svg.py "<latex_expression>" -o <project_path>/images/formula_inline_<NNN>.svg
    ```
    Then embed as `<image href="../images/formula_inline_<NNN>.svg" .../>`. Counter `<NNN>` from 901.
  - **EXCEPTION ONLY: Tier A — baseline-shift** — permitted ONLY when ALL of: (a) single sub/super of 1–2 chars, (b) inline in prose where `<image>` breaks text flow, (c) max 1 Tier A per page. If in doubt, use Tier B.
  - **Inline super/sub stays one text frame** — for prose cases like `m^-3`, `cm^-3`, `H₂O`, `Tₑ`, Tier A means one `<text>` element / one PowerPoint text frame with inline `<tspan baseline-shift>`. Never place the base and exponent/subscript in separate adjacent `<text>` elements, and never "repair" QC output by nudging those separate boxes. This does **not** widen Tier A; if the case is not clearly eligible, use Tier B.
  - **Mandatory pre-scan**: before writing each SVG page, scan ALL planned text. Generate formula SVGs **before** writing the page. Raw patterns (`a_1`, `x^2`, `a/b`, `√x`) without `<image>` or `baseline-shift` are **blocking errors**.
  - **Formula SVG size floor** — when placing a formula `<image>`, treat `notes/formula_asset_table.md` `Recommended display` as the target size. The blocking quality gate uses the floor below; if the formula still feels cramped at that floor, open more layout room, split the page, or make the formula the main object.

  **Formula Size Quick Table**:

  | `formula_asset_table.md` layout | Minimum on-slide height |
  |---|---|
  | `inline-or-callout` | `17px` |
  | `formula-compact` | `max(34px, 50% of Recommended display height)` |
  | display equation / other | `max(44px, 50% of Recommended display height)` |

  - Common formulas that MUST use Tier B (not Tier A): `H₂O` → `latex_to_svg.py "\mathrm{H_2O}"`; `10²` → `latex_to_svg.py "10^{2}"`; `Tₑ` → `latex_to_svg.py "T_e"`
  - **⛔ FORBIDDEN — formula avoidance by text substitution**: when the quality checker flags a formula, the ONLY fix is `latex_to_svg.py` → `<image>`. Replacing the formula with plain text (e.g. `φ_burst` → "破裂填充比", `P = C₁ε^C₂/(1+C₃ε^C₄)` → "四参数 S 型曲线", `x/y` → "x与y") is **strictly forbidden** — it destroys scientific meaning. Always generate the SVG and embed it.
  - **⛔ FORBIDDEN — formula removal by rewording**: deleting the mathematical symbol and rewording the sentence is equally forbidden. Examples of violations: `v_⊥²/(v²B) 较大` → "低速粒子", `v_∥ 足够大` → "平行速度分量足够大", `v_∥ 大于阈值` → "平行速度超过阈值". These hide scientific content behind vague natural-language descriptions. The ONLY correct response is: (1) run `latex_to_svg.py` to render the formula, (2) embed the SVG via `<image>`, (3) keep the original mathematical notation intact. If the formula cannot compile, fix the LaTeX source — never delete the formula.

If a page needs a value not in `spec_lock.md`, surface it — do not silently invent one.

**Per-page layout rhythm — `page_rhythm` section**:

Before drawing each page, look up its entry in `page_rhythm` (key format `P<NN>` matching the page index in §IX of `design_spec.md`) and apply the corresponding layout discipline:

| Tag | Layout discipline |
|-----|-------------------|
| `anchor` | Structural page (cover / chapter / TOC / ending). Follow the matching template verbatim. |
| `dense` | Information-heavy. Card grids, multi-column layouts, KPI dashboards, tables, and charts are all permitted. This is the baseline behavior. |
| `breathing` | Low-density impact page. Avoid **multi-card grid layouts** — do not organize content as multiple parallel rounded containers (3-card row, 4-card KPI grid, 2×2 matrix rendered as cards). Use naked text blocks, dividers, whitespace, or full-bleed imagery as the content structure. Single rounded visual elements (hero image corners, callouts, tags, one emphasis block) are fine — the rule is about grid structure, not about the `rx` attribute. Proportions follow information weight (not a preset ratio). Typical forms: hero quote, single large number with one-line interpretation, full-bleed image with floating caption, section transition. |

> Without rhythm variation, every page defaults to card grids (the "AI-generated" look). `page_rhythm` is the only narrative lever that survives context compression.

**Missing `page_rhythm` section** → emit `warning: spec_lock.md missing page_rhythm — defaulting all pages to dense` once, fall back to `dense` for all pages.

**Tag not found for current page** → fall back to `dense` silently. Do not invent a tag.

**Per-page template lookup — `page_layouts` section**:

Before drawing each page, look up its entry in `page_layouts` to decide which basename to inherit (the SVG itself was loaded in §1.0):

- Entry present (e.g., `P04: 03a_content_image_text`) → inherit the corresponding SVG already in context. The basename **must match** an actual file in the chosen template directory; if it doesn't, emit `warning: page_layouts P<NN> references missing file <basename>.svg — falling back to free design` and proceed.
- No entry for this page → free design, no inheritance. **Not an error** — Strategist intentionally left this page free.
- Whole section absent → see §1 fallback (legacy page-type matching).

Do **not** invent a layout entry, and do **not** assume a template just because `templates/` exists — if `page_layouts` is present but silent for this page, that silence is the instruction.

**Per-page chart reference — `page_charts` section**:

Before drawing each page, look up its entry in `page_charts` to decide which chart structure applies (the SVG itself was loaded in §1.0):

- Entry present (e.g., `P09: timeline_horizontal`) → adapt the corresponding chart SVG already in context. Apply project colors/typography/density; do not copy verbatim. Cross-reference `templates/charts/charts_index.json` for the chart's purpose summary if needed.
- No entry for this page → either no chart on this page, or a chart that didn't match any catalog template (Strategist's `no-template-match` fallback). Design the visualization from scratch using `design_spec.md §VII` for guidance.
- Whole section absent → no chart pages in this deck.

---

## 3. Execution Guidelines

- **Proximity**: group related elements with tight spacing; separate unrelated groups
- **Spec adherence**: follow color, layout, canvas format, and typography in the spec
- **Template structure**: if templates exist, inherit the visual framework
- **Main-agent ownership**: SVG generation must run in the main agent (not sub-agents) — pages share upstream context for cross-page visual continuity
- **Generation rhythm**: lock global design context first, then generate pages sequentially in one continuous context. No batched groups (e.g., 5 at a time).
- **Phased batch generation** (recommended):
  1. **Visual Construction Phase**: generate all SVG pages sequentially for visual consistency. Use layout judgment for chart marks during the draft. **MUST embed plot-area markers** per §3.1 below on every chart page — coordinate calibration is a post-generation step (see [`workflows/verify-charts.md`](../workflows/verify-charts.md)) that depends on these markers.
  2. **Pre-QC Draft PPTX Export**: run `python3 scripts/svg_to_pptx.py <project_path> -o <project_path>/exports/pre_qc_draft.pptx --no-notes --no-open -a none` before the quality checker. This creates a no-notes PPTX snapshot from `svg_output/`. If export fails, fix the export-blocking SVG issue and retry before quality check.
  3. **Quality Check Gate**: run `python3 scripts/svg_quality_checker.py <project_path>` on `svg_output/`. **Every `error` is a BLOCKING gate** — formula violations, fake sub/superscript text-box splits (`m` + `-3`, `H` + `2`, etc.), formula SVGs placed below their readable-size floor, `preserveAspectRatio="none"`, banned features, viewBox mismatch, spec_lock drift, non-PPT-safe font, etc. You MUST fix each error on the offending page and re-run the checker until 0 errors remain. Do NOT skip or defer errors and proceed to export — that defeats the quality gate. Address `warning`s when straightforward. Do NOT defer to after `finalize_svg.py` — finalize rewrites SVG and masks some violations.
  4. **Logic Construction Phase**: after SVGs pass the quality check, batch-generate speaker notes for narrative continuity.

### 3.1 Chart Plot-Area Marker (MANDATORY on every chart page)

> The [`verify-charts`](../workflows/verify-charts.md) workflow enumerates chart pages from `design_spec.md §VII`, then reads each page's plot-area marker to feed `svg_position_calculator.py`. Missing marker → verify-charts has to re-derive the plot area from axis lines, paying the cost on every run.

Every SVG page that contains a data visualization chart MUST include a plot-area marker inside `<g id="chartArea">`, placed **after axis lines** and **before the first data element** (bar, line, area, point).

**Rectangular plot area** (bar / horizontal_bar / grouped_bar / stacked_bar / line / area / stacked_area / scatter / waterfall / pareto / butterfly):

```xml
<!-- chart-plot-area: x_min,y_min,x_max,y_max -->
```

**Radial charts** (pie / donut / radar):

```xml
<!-- chart-plot-area: pie | center: cx,cy | radius: r -->
<!-- chart-plot-area: donut | center: cx,cy | outer-radius: r1 | inner-radius: r2 -->
<!-- chart-plot-area: radar | center: cx,cy | radius: r -->
```

**How to determine coordinate values**:

| Value | Derivation |
|-------|------------|
| `x_min` | X coordinate of the Y-axis line (leftmost data boundary) |
| `y_min` | Y coordinate of the topmost grid line (highest data boundary) |
| `x_max` | X coordinate of the rightmost axis endpoint or grid line |
| `y_max` | Y coordinate of the X-axis baseline |
| `cx, cy` | Center point of pie/donut/radar (accounting for `transform="translate()"`) |
| `r` | Outer radius of the chart |

**Per-page verification** — after writing each chart SVG, confirm the marker exists:

```bash
grep "chart-plot-area" <project_path>/svg_output/<current_page>.svg
```

> All chart templates in `templates/charts/` include this marker as a reference. If you are drawing a chart and the marker is absent, you have a bug.
- **Technical specs**: see [shared-standards.md](shared-standards.md) for SVG/PPT constraints
- **Card containers — use the documented patterns**: when a content page needs section cards (4 quadrants, parallel aspects, capability blocks, info cards), use the patterns codified in [`templates/charts/CHART_STYLE_GUIDE.md`](../templates/charts/CHART_STYLE_GUIDE.md) §11 — half-rounded section tab (§11.1), nested card border without stroke (§11.2), card-grid skeletons (§11.3), diagonal dashed connector for cross-quadrant relationships (§11.5), ground-anchor ellipse as a non-filter depth marker (§11.6), bidirectional interaction arrows for paired protocols (§11.7). Do not reinvent the "tinted full-rounded rect + white cover-rect to hide the bottom corners" hack; it survives in older templates but breaks SVG→PPTX color editing. Reference templates: [`labeled_card.svg`](../templates/charts/labeled_card.svg), [`quadrant_text_bullets.svg`](../templates/charts/quadrant_text_bullets.svg), [`kpi_cards.svg`](../templates/charts/kpi_cards.svg), [`matrix_2x2.svg`](../templates/charts/matrix_2x2.svg), [`team_roster.svg`](../templates/charts/team_roster.svg), [`client_server_flow.svg`](../templates/charts/client_server_flow.svg).
- **Semantic shapes over preset stacks**: when a slide needs to express "ascending / converging / breaking through / stacking" — i.e., a relationship that goes beyond a generic arrow — prefer a single custom `<polygon>` or `<path>` that encodes the semantics geometrically, rather than stacking multiple preset arrows. A converging-tip path or a podium polygon reads faster than three arrows pointing at a label. Examples of this technique appear in many imported corporate decks; see `projects/01_template_import/svg_output/slide_01.svg` shape-158 for a reference (gradient-filled inward-pointing arrow). Do not codify these as templates — they are page-specific; the rule is just "consider polygon before stacking presets."
- **Visual depth — through restraint**: layered depth comes from rhythm (flat vs lifted, dense vs spacious), not from shadows everywhere. Apply shadow to at most 2-3 genuinely floating elements per page (cards on photos, primary CTA, overlays); keep peer-grid cards, dividers, body containers flat. Reach for typography weight, spacing, accent bars, subtle tints **before** shadow. Full rules in shared-standards.md §6.

### SVG File Naming Convention

Format: `<NN>_<page_name>.svg` (two-digit number from 01; name matches the deck's language and the page title in the Design Spec).

Examples: `01_封面.svg` / `02_目录.svg` / `03_核心优势.svg`; `01_cover.svg` / `02_agenda.svg` / `03_key_benefits.svg`.

---

## 4. Icon Usage

Strategist chooses the library and inventory; Executor only implements. Library details and one-library rule: [`../templates/icons/README.md`](../templates/icons/README.md). This section defines placeholder syntax.

**Built-in icons — Placeholder method (recommended)**:

```xml
<!-- chunk-filled (straight-line geometry, sharp corners, structured) -->
<use data-icon="chunk-filled/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- tabler-filled (bezier-curve forms, smooth & rounded contours) -->
<use data-icon="tabler-filled/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- tabler-outline (light, line-art style — screen-only decks) -->
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- phosphor-duotone (single color + 20% backplate — soft depth without solid weight) -->
<use data-icon="phosphor-duotone/house" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- simple-icons (brand logos — used alongside the deck's primary library, only for real company/product marks) -->
<use data-icon="simple-icons/github" x="100" y="200" width="48" height="48" fill="#181717"/>

<!-- tabler-outline with thin / bold stroke (stroke-style libraries only) -->
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587" stroke-width="1.5"/>
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587" stroke-width="3"/>
```

> ⚠️ **Color**: ALWAYS use `fill="#HEX"` on `<use data-icon="...">`. NEVER use `stroke` or `fill="none"`, even for stroke-style libraries.
>
> **stroke-width** (stroke-style libraries only, currently `tabler-outline`): allowed values `{1.5, 2, 3}`. If `spec_lock.md icons.stroke_width` is declared, all placeholders MUST use that value deck-wide. Default `2` if absent (legacy). Ignored on non-stroke libraries.
>
> Icons are auto-embedded by `finalize_svg.py` — no need to run `embed_icons.py` manually.

**Searching for icons** — use terminal, zero token cost:
```bash
ls skills/ppt-master/templates/icons/chunk-filled/ | grep home
ls skills/ppt-master/templates/icons/tabler-filled/ | grep home
ls skills/ppt-master/templates/icons/tabler-outline/ | grep chart
ls skills/ppt-master/templates/icons/phosphor-duotone/ | grep house
ls skills/ppt-master/templates/icons/simple-icons/ | grep github
```

**Abstract concept → icon name** (names for `chunk-filled`; tabler libraries use their own equivalents — verify with `ls | grep`):

| Concept | chunk-filled | tabler-filled / tabler-outline |
|---------|-------|-------------------------------|
| Growth / Increase | `arrow-trend-up` | same |
| Decline / Decrease | `arrow-trend-down` | same |
| Success / Complete | `circle-checkmark` | `circle-check` |
| Warning / Risk | `triangle-exclamation` | `alert-triangle` |
| Innovation / Idea | `lightbulb` | `bulb` |
| Strategy / Goal | `target` | same |
| Efficiency / Speed | `bolt` | same |
| Collaboration / Team | `users` | same |
| Settings / Config | `cog` | `settings` |
| Security / Trust | `shield` | same |
| Money / Finance | `dollar` | `currency-dollar` |
| Time / Deadline | `clock` | same |
| Location / Region | `map-pin` | same |
| Communication | `comment` | `message` |
| Analysis / Data | `chart-bar` | same |
| Process / Flow | `arrows-rotate-clockwise` | `refresh` |
| Global / World | `globe` | `world` |
| Excellence / Award | `star` | same |
| Expand / Scale | `maximize` | same |
| Problem / Issue | `bug` | same |

> For self-evident names (home, user, file, search, arrow, etc.) — just `grep chunk-filled/` directly without consulting the table.

> ⚠️ **Icon validation**: only use icons from the Design Spec's approved inventory. Verify each via `ls | grep` before use. Mixing libraries within one deck is FORBIDDEN.

---

## 5. Visualization Reference

Chart SVGs referenced in **VII. Visualization Reference List** are loaded once via the §1.0 batch read. This section governs adaptation only.

**Hard rule**: adapt the loaded chart SVG; do not improvise from memory and do not replicate verbatim. Apply project colors, typography, content; preserve visualization type.

**Adaptation rules**:
- **Preserve**: visualization type (bar/line/pie/timeline/process/framework…) as specified
- **Adapt**: data, labels, colors (project scheme), dimensions
- **Freely adjust**: composition, axis ranges, grid, legend, spacing, decoration — as long as the chart stays accurate and readable
- **Forbidden**: changing visualization type without spec justification; omitting data points or structural elements from the outline

> Templates: `templates/charts/` (70 types). Index: `templates/charts/charts_index.json`

### 5.1 Chart Coordinate Calibration

Coordinate calibration runs as a **standalone post-generation workflow**, not inside the executor pipeline. After SVG generation completes, if the deck contains data charts, run [`workflows/verify-charts.md`](../workflows/verify-charts.md) before post-processing.

The executor's only obligation here is upstream: embed the `<!-- chart-plot-area ... -->` marker on every chart page during initial draft (§3.1). Verify-charts enumerates chart pages from `design_spec.md §VII` (authoritative deck plan) and uses the marker to feed `svg_position_calculator.py`.

> Do NOT run `svg_position_calculator.py` during the initial draft. The calculator calibrates already-generated SVGs against their declared plot areas; running it before the SVG exists has nothing to compare against.

---

## 6. Image Handling

Handle images by their status in the Design Spec's Image Resource List. Status enum and lifecycle: [`svg-image-embedding.md`](svg-image-embedding.md).

| Status | Source | Handling |
|--------|--------|----------|
| **Existing** | User-provided | Reference images directly from `../images/` directory |
| **Formula SVG** | LaTeX-rendered (`formula_*.svg`) | ⛔ IRON RULE — ALL formulas (including simple sub/super like 10², H₂O) MUST be embedded via `<image href="../images/formula_XXX.svg" .../>` by default. Generate with `latex_to_svg.py "<expr>" -o <project>/images/formula_inline_<NNN>.svg` (counter from 901). Tier A (`baseline-shift`) is a narrow exception — see [`shared-standards.md §4.1`](shared-standards.md). Use `short_alias` from `image_asset_table.md` for lookup |
| **Generated** | Generated by Image_Generator | Reference images directly from `../images/` directory |
| **Sourced** | Web-acquired by Image_Searcher | Reference from `../images/`. **Read [`image_sources.json`](image-searcher.md) to decide attribution** — see §6.1 below. |
| **Needs-Manual** | Acquisition failed and file is absent | Use dashed border placeholder unless the expected file exists |
| **Placeholder** | Not yet prepared | Use dashed border placeholder |

**Reference syntax**: see [`svg-image-embedding.md`](svg-image-embedding.md).

**Formula SVG lookup**: read `notes/formula_asset_table.md` before placing any rendered formula. Use the table's `SVG href` as the `<image href="../images/...">` value, add `data-formula-id="<id>"`, and follow `Recommended display` / `Scale note`.

**Formula sizing**: short / inline formulas (`inline-or-callout`, `formula-compact`) must stay near text or callout scale unless the formula is explicitly the page's main object.

**Placeholder**: Dashed border `<rect stroke-dasharray="8,4" .../>` + description text

**Legacy `no-crop` tag**: older specs may append ` | no-crop`; treat it as `Display mode: complete`. Untagged content-bearing images also default to complete display.

**Completeness-first override**: the current default is `complete`, not crop. For Existing, MinerU-extracted, source-document, chart, screenshot, product, and evidence images, size the container from the image's native ratio (from `analyze_images.py`, manifest dimensions, or file dimensions) and use `preserveAspectRatio="xMidYMid meet"`. Treat legacy `no-crop` tags as an explicit restatement of this default.

**Scale integrity enforcement**: do not render a large, content-bearing source image as a tiny inset just to protect surrounding text blocks. If the current composition makes chart labels, figure annotations, screenshot UI, or other core details hard to read at slide size, reallocate the page so the image becomes dominant. If the page still cannot fit both image and text at a readable size without narrative changes, surface a warning that the page needs re-planning instead of silently miniaturizing the image.

| Display mode | When allowed | Executor action |
|---|---|---|
| `complete` | Default for all content-bearing images | Preserve the full image; resize/reposition the container around the native ratio |
| `crop-ok` | Only when the image row is a decorative background / atmosphere / full-bleed cover and edge loss does not remove information | Use `preserveAspectRatio="xMidYMid slice"` and add overlays as needed |

**Forbidden**: do not crop source charts, screenshots, paper figures, dense diagrams, product photos, or user-provided evidence images just to fill a fixed box. Change the page composition instead.

**⛔ No Stretching**: `preserveAspectRatio="none"` is FORBIDDEN on content-bearing images. It distorts originals and misrepresents source data. The only exception is a full-bleed background at `x="0" y="0"` covering the entire canvas. `svg_quality_checker.py` flags violations as blocking errors — you must fix them before proceeding.

### 6.1 Inline Attribution for Sourced Images (web path)

Whenever the slide uses an image with `Status: Sourced`, look up the corresponding entry in `project/images/image_sources.json` and act on `license_tier`:

| `license_tier` | Action on this slide |
|---|---|
| `no-attribution` | Embed the `<image>` element only. **No credit element needed.** |
| `attribution-required` | Embed the `<image>` element **plus** a small inline `<text>` credit element per the visual spec in [image-searcher.md §7](./image-searcher.md). |

The credit text is **not** rendered by post-processing or export — it must be present in the SVG you produce. The shape of the credit element (size, position, color, multi-image source line, hero gradient overlay) is specified in [image-searcher.md §7](./image-searcher.md). Do not invent a different style.

Use `attribution_text` from the manifest entry as the **starting point**, then compress for the small-text constraint (drop URL, drop filename, keep "via Provider / License"). For CC0/PD images that landed in the `attribution-required` tier only because of upstream metadata quirks (rare), credits are still safe to render.

`svg_quality_checker.py` treats missing CC BY / CC BY-SA inline attribution as an **error**. Fix the offending SVG before post-processing.

**The manifest is the single source of truth for credits.** Do not duplicate license info into speaker notes or any other artifact.

---

## 7. Font Usage

Source of truth: `spec_lock.md typography`. Use `font_family` as default; override per role with `title_family` / `body_family` / `emphasis_family` / `code_family` if declared.

If `spec_lock.md` is absent, consult [`strategist.md`](strategist.md) §g — do not invent a stack.

**Hard rule**: every SVG `font-family` stack MUST end with a pre-installed family (Microsoft YaHei / SimHei / SimSun / Arial / Calibri / Segoe UI / Times New Roman / Georgia / Consolas / Courier New / Impact / Arial Black). PPTX has no runtime fallback — missing fonts degrade to Calibri. **⛔ 楷体 (`KaiTi`) is BANNED — do not use in any font stack, any role, any page.**

**CJK-first metric-consistency rule**: when the primary font is CJK, write it FIRST in the SVG `font-family` stack (e.g. `font-family="'Microsoft YaHei', sans-serif"`). The converter uses the first CJK font for BOTH `<a:latin>` and `<a:ea>` DrawingML slots, ensuring Latin and CJK characters share identical metrics. Do NOT place a Latin font before the CJK font unless you intentionally want different rendering for Latin characters (this causes visible size mismatch between Latin and CJK in mixed text).

---

## 8. Speaker Notes Generation Framework

### Task 1. Generate Complete Speaker Notes Document

After all SVG pages are finalized, enter Logic Construction Phase and write the full notes to `notes/total.md`. Batch-writing (not per-page) lets transitions plan coherently.

**Pure spoken narration**: notes are read aloud verbatim by `notes_to_audio.py` (TTS). Write only what should be spoken. No visible markers, no labeled meta-lines, no enumerated key-point lists, no duration annotations — anything you write outside the heading will be vocalized.

**Per-page structure**: `# <number>_<page_title>` heading (the `#` heading line is the only thing stripped before TTS), pages separated by `---`. Body is 2–5 natural sentences carrying the page's core message. Page-to-page transitions live inside the opening sentence as natural prose ("接下来……" / "Having framed X, let's turn to Y") — no bracketed `[过渡]` / `[Transition]` tags.

**Concrete examples** — same shape applies to any language; just write naturally in that language.

中文 deck：

```
# 02_市场格局

在明确了行业背景之后，我们来看具体的市场格局。当前线上零售集中度持续上升，前三大平台合计份额已经达到百分之六十八，腰部玩家正在被快速挤压，留给新进入者的窗口期不超过十八个月。这意味着我们的策略必须聚焦，而不是铺开。
```

英文 deck：

```
# 02_market_landscape

Having framed the industry backdrop, let's look at the actual market landscape. Online retail concentration keeps rising — the top three platforms now hold sixty-eight percent of combined share, mid-tier players are being squeezed fast, and the window for new entrants is under eighteen months. This means our strategy has to focus, not spread.
```

> 日本語 / 한국어 / 其他语言：照搬同样的结构，用对应语言自然书写即可。

**Number readability**: TTS reads digits and symbols literally. Prefer fully-spelled forms in the language being spoken when literal pronunciation would be awkward (e.g. Chinese "百分之六十八" reads better than "68%"; "1-2分钟" reads as "一减二分钟"). Plain integers and percentages in English are fine as-is.

**Common mistakes to avoid**:
- Leaving any bracketed stage marker (`[过渡]` / `[Transition]` / `[Pause]` / `[Data]` / `[Scan Room]` / `[Interactive]` / `[Benchmark]` etc.) in the text — they will be read aloud literally.
- Adding `要点：① …` / `Key points: (1) …` / `时长：2分钟` / `Duration: 2 minutes` / `Flex: …` lines — TTS will speak "要点 一 …".
- Mixing languages within one deck's notes.

### Task 2. Split Into Per-Page Note Files

Auto-split `notes/total.md` into per-page files in `notes/`.

**Naming**: match SVG names (`01_cover.svg` → `notes/01_cover.md`); `slide01.md` also supported (legacy).

---

## 9. Next Steps After Completion

> **Auto-continuation**: After Visual Construction Phase (all SVG pages) and Logic Construction Phase (all notes) are complete, the Executor proceeds directly to the post-processing pipeline.

**Post-processing & Export** (same canonical pipeline as [shared-standards.md §5](shared-standards.md)):

```bash
# 1. Split speaker notes
python3 scripts/total_md_split.py <project_path>

# 2. SVG post-processing (auto-embed icons, images, etc.)
python3 scripts/finalize_svg.py <project_path>

# 3. Export PPTX
python3 scripts/svg_to_pptx.py <project_path>
# Output (default-flow mode):
#   exports/<project_name>_<timestamp>.pptx           ← native pptx (canonical output)
#   backup/<timestamp>/svg_output/                    ← Executor SVG source backup (always written)
#
# Add --svg-snapshot to additionally emit:
#   exports/<project_name>_<timestamp>_svg.pptx      ← SVG snapshot pptx (sibling of native pptx)
```
