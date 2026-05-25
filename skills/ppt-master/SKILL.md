---
name: ppt-master
description: >
  AI-driven multi-format SVG content generation system. Converts source documents
  (PDF/DOCX/URL/Markdown) into high-quality SVG pages and exports to PPTX through
  multi-role collaboration. Use when user asks to "create PPT", "make presentation",
  "生成PPT", "做PPT", "制作演示文稿", or mentions "ppt-master".
---

# PPT Master Skill

> AI-driven multi-format SVG content generation system. Converts source documents into high-quality SVG pages through multi-role collaboration and exports to PPTX.

## Python Command Detection (MANDATORY — run before any script)

All command examples below use `python3` as a placeholder. On Windows, `python3` often points to a Microsoft Store stub that silently fails (exit code 49). **Before the first script call in every session**, detect the working Python command:

```bash
# Try in order — use the first one that prints a version:
python3 --version      # Linux / macOS / some Windows
python --version       # Some Windows installs
py -3 --version        # Windows Python Launcher (most reliable on Windows)
```

Store the result as `PYTHON` and use `${PYTHON}` for all subsequent commands. Example:
```bash
PYTHON="py -3"   # or python3, or python — whichever worked
${PYTHON} ${SKILL_DIR}/scripts/preflight_check.py <project_path>
```

`preflight_check.py` also auto-detects the correct command and prints `PYTHON_CMD=<cmd>` — use that value for the rest of the session.

**Core Pipeline**: `Source Document → Create Project → [Template] → Strategist → [Image_Generator] → Executor Live Preview → Quality Check → Post-processing → Export`

> [!CAUTION]
> ## 🚨 Global Execution Discipline (MANDATORY)
>
> **This workflow is a strict serial pipeline. The following rules have the highest priority — violating any one of them constitutes execution failure:**
>
> 1. **SERIAL EXECUTION** — Steps MUST be executed in order; the output of each step is the input for the next. Non-BLOCKING adjacent steps may proceed continuously once prerequisites are met, without waiting for the user to say "continue"
> 2. **BLOCKING = HARD STOP** — Steps marked ⛔ BLOCKING require a full stop. Exception: the timeout-enabled decision gates defined in Step 0 and Step 4 may wait up to 120 seconds, then auto-apply the currently recommended option(s) if the user stays silent. Outside those gates, the AI MUST wait for an explicit user response before proceeding and MUST NOT make any decisions on behalf of the user
> 3. **NO CROSS-PHASE BUNDLING** — Cross-phase bundling is FORBIDDEN. (Note: the Eight Confirmations in Step 4 are ⛔ BLOCKING — the AI MUST present recommendations and wait for explicit user confirmation, or for the 120-second timeout fallback to accept the recommended bundle, before proceeding. Once the bundle is confirmed or the timeout fallback is applied, all subsequent non-BLOCKING steps — design spec output, SVG generation, speaker notes, and post-processing — may proceed automatically without further user confirmation)
> 4. **GATE BEFORE ENTRY** — Each Step has prerequisites (🚧 GATE) listed at the top; these MUST be verified before starting that Step
> 5. **NO SPECULATIVE EXECUTION** — "Pre-preparing" content for subsequent Steps is FORBIDDEN (e.g., writing SVG code during the Strategist phase)
> 6. **NO SUB-AGENT SVG GENERATION** — Executor Step 6 SVG generation is context-dependent and MUST be completed by the current main agent end-to-end. Delegating page SVG generation to sub-agents is FORBIDDEN
> 7. **SEQUENTIAL PAGE GENERATION ONLY** — In Executor Step 6, after the global design context is confirmed, SVG pages MUST be generated sequentially page by page in one continuous pass. Grouped page batches (for example, 5 pages at a time) are FORBIDDEN
> 8. **SPEC_LOCK RE-READ PER PAGE** — Before generating each SVG page, Executor MUST `read_file <project_path>/spec_lock.md`. All colors / fonts / icons / images MUST come from this file — no values from memory or invented on the fly. Executor MUST also look up the current page's `page_rhythm` (`anchor` / `dense` / `breathing`), `page_layouts` (which template SVG to inherit, if any), and `page_charts` (which chart template to adapt, if any). Empty / absent entries are intentional Strategist signals — see executor-base.md §2.1. This rule exists to resist context-compression drift on long decks and to break the uniform "every page is a card grid" default
> 9. **SVG MUST BE HAND-WRITTEN, NOT SCRIPT-GENERATED** — Every SVG page is written by the main agent directly, one page at a time (see rules 6 and 7). Writing or running a Python / Node / shell script that produces the SVG files in batch — looping over pages, templating from data, or emitting them via a generator — is FORBIDDEN, including under "save tokens", "quick draft", or "user is in a hurry" pretexts. The script-generation path was tried on a feature branch and abandoned: cross-page visual consistency depends on per-page authoring with full upstream context, which a generator script cannot reproduce
> 10. **⛔ IRON RULE — NO PLAIN-TEXT FORMULAS / SVG-FIRST** — Raw formula-like patterns (`a_1`, `x^2`, `a/b`, `√x`, bare `²`) in `<text>` / `<tspan>` without proper rendering are a **blocking error**. **Placing the base and exponent/subscript in separate `<text>` elements to fake sub/superscripts is FORBIDDEN.** Full rules in [`shared-standards.md §4.1`](references/shared-standards.md).
>    - **DEFAULT: Tier B — SVG image** — ALL mathematical formulas, including simple sub/superscripts like 10², H₂O, Tₑ, MUST be rendered as SVG images via `latex_to_svg.py` and embedded as `<image>`. This is the **only** approach the AI should use unless Tier A conditions are explicitly met.
>      ```
>      ${PYTHON} ${SKILL_DIR}/scripts/latex_to_svg.py "10^{2}" -o <project_path>/images/formula_inline_<NNN>.svg --source-file <page_or_note_file> --line-number <N> --context "<surrounding text>"
>      ```
>      Then embed: `<image href="../images/formula_inline_<NNN>.svg" .../>`. Counter `<NNN>` from 901.
>      When the formula is generated ad hoc during page execution, pass `--source-file`, `--line-number`, and `--context` whenever that information is available so the SVG and `formula_manifest.json` carry the same semantic metadata as manifest-rendered formulas.
>    - **EXCEPTION ONLY: Tier A — baseline-shift** — permitted ONLY when ALL of: (a) the expression is a single sub/superscript of 1–2 characters on a single base, (b) it appears **inline in a prose sentence** where an `<image>` element would break text flow (e.g. "扩散系数 D 的单位为 m²/s"), (c) no other formula on the same page uses Tier A (one Tier A per page maximum to keep things consistent). If in doubt, use Tier B.
>    - **Inline super/sub = one text frame** — for inline prose/unit cases like `m^-3`, `cm^-3`, `H₂O`, `Tₑ`, Tier A still means a **single** parent `<text>` / one PowerPoint text frame with an inline `<tspan baseline-shift>`. Never fake this with separate tiny `<text>` elements. This does **not** relax the Tier B default; it only bans the split-text-box workaround.
>    - **Per-page mandatory pre-scan**: before writing each page, scan ALL planned text for formula-like tokens. For each one, call `latex_to_svg.py` to generate the SVG **before** writing the page SVG. Raw patterns without `<image>` → blocking error. **Never split a formula across multiple `<text>` elements.** `svg_quality_checker.py` detects violations as **errors**.
>    - **⛔ FORBIDDEN — formula avoidance by text substitution**: when the quality checker flags a formula violation, the ONLY acceptable fix is to **generate an SVG image** via `latex_to_svg.py`. Replacing the formula with plain-text descriptions (e.g. `φ_burst` → "破裂填充比"), removing mathematical notation (e.g. `x/y` → "x与y"), or substituting an equation with a vague label (e.g. `P = C₁ε^C₂ / (1+C₃ε^C₄)` → "四参数 S 型曲线") is **strictly forbidden**. These workarounds destroy the scientific meaning of the slide content.
> 11. **⛔ IRON RULE — NEVER MODIFY OR DELETE USER SOURCE FILES** — User-provided original documents (PDF, DOCX, PPTX, XLSX, etc.) MUST **never** be deleted, moved, or modified. `import-sources` always **copies** original documents into `sources/` regardless of the `--move` flag. Only generated intermediate files (Step 1 Markdown output, `_files/` companion dirs) may be moved. If the original file disappears after the workflow, that is a critical bug.
> 12. **⛔ IRON RULE — NEVER OVERWRITE EXISTING PROJECTS** — When creating a new project, if a project directory with the same name already exists, `project_manager.py init` automatically appends `_2`, `_3`, etc. Never reuse, overwrite, or continue work in an existing project directory when the user asks to create a **new** project. Each `init` call produces a fresh, independent project.
> 13. **⛔ IRON RULE — NO IMAGE STRETCHING / DISTORTION** — When embedding source images (paper figures, screenshots, charts, diagrams, product photos) into SVG pages, the image MUST preserve its original aspect ratio. Use `preserveAspectRatio="xMidYMid meet"` (default). **`preserveAspectRatio="none"` is FORBIDDEN for all content-bearing images** — it stretches the image to fill the container, distorting charts, text, and details. If the image's native ratio does not match the allocated container, resize the container to match the image — never stretch the image to match the container. `svg_quality_checker.py` flags `preserveAspectRatio="none"` on non-background images as an **error**.
> 14. **⛔ IRON RULE — QUALITY CHECKER ERRORS ARE BLOCKING** — When `svg_quality_checker.py` reports **errors** (not warnings), the Executor MUST fix every error before proceeding to Step 7. Errors are never "non-critical" — skipping formula violations, XML issues, or spec drift and proceeding to export defeats the quality gate. The ONLY acceptable response to an error is: fix the SVG, re-run the checker, confirm 0 errors. Proceeding with errors present constitutes execution failure.
> 15. **⛔ IRON RULE — FORMULA ERRORS MUST BE FIXED BY SVG RENDERING, NEVER BY REMOVAL** — When the quality checker flags a plain-text formula violation, the executor MUST: (1) run `latex_to_svg.py` to render the formula as SVG, (2) embed via `<image>`. **Deleting the mathematical symbol and rewording the sentence** (e.g. `v_⊥²/(v²B) 较大` → "低速粒子", `v_∥ 足够大` → "平行速度分量足够大") is a **forbidden shortcut** — it destroys scientific meaning. If the LaTeX cannot compile, fix the source expression; never remove the formula. When the checker flags a fake inline super/sub split (`m` + `-3`, `H` + `2`, etc.), the only acceptable fixes are: merge it back into **one** `<text>` with inline `baseline-shift` for the narrow Tier A case, or convert it to Tier B SVG. Never keep separate boxes and just tweak their positions.
> 16. **⛔ IRON RULE — ONE SENTENCE = ONE `<text>` ELEMENT** — When a sentence needs mixed styling (bold, color, size for emphasis), it MUST stay in a **single `<text>` element** with `<tspan>` children for inline formatting. **Never split a sentence into multiple adjacent `<text>` elements** to apply different styles — this creates separate text frames in PowerPoint with fragile spacing that drifts on resize. Example: `<text>实现<tspan fill="#1A73E8" font-weight="bold">10倍</tspan>效率提升</text>`. `svg_quality_checker.py` detects 3+ same-line `<text>` splits as a warning.
> 17. **⛔ IRON RULE — NO PAGE TRANSITIONS UNLESS THE USER EXPLICITLY ASKS** — Slide-to-slide PPT transition effects are **off by default**. Unless the user clearly asks for page transitions / 切换动画 / 过场动画, export with `-t none` (or leave `-t` unset, which defaults to `none`). Do **not** add fade/push/wipe/split transitions on your own. If the user asks for in-slide object animation but says nothing about page transitions, keep page transition at `none` and only tune object animation.

> [!IMPORTANT]
> ## 🌐 Language & Communication Rule
>
> - **Response language**: match the user's input and source materials. Explicit user override (e.g., "请用英文回答") takes precedence.
> - **Template format**: `design_spec.md` MUST follow its original English template structure (section headings, field names) regardless of conversation language. Content values may be in the user's language.
> - **Deck text language is explicit — ASK FIRST**: Strategist MUST proactively ask the user which language the PPT copy should use **before** presenting the Eight Confirmations bundle (e.g. Chinese / English / bilingual). This is the very first question in the pipeline. Do not infer from chat language alone; do not skip it.
> - **Default deck language**: when the user has not specified a deck-copy language, Strategist MUST recommend `zh-CN` as the default option. Switch to another language only on explicit user choice or when the source task clearly requires it.
> - **Timed fallback for the language gate**: the language question is timeout-enabled. After presenting the options and the recommendation, wait up to 120 seconds. If the user does not answer, lock the recommended language (`zh-CN` unless another recommendation is explicitly justified by the source task) and continue. When continuing, explicitly state that the timeout fallback was applied.
> - **Light background by default**: unless the user **explicitly** requests a dark background (e.g. "用深色背景" / "dark background" / "dark tech style with dark bg"), every deck MUST use a light (white or near-white) page background. Merely choosing a "dark tech" visual style does NOT automatically trigger a dark background — the background stays light unless the user says otherwise. Record this in `design_spec.md §II` and `spec_lock.md`.

> [!IMPORTANT]
> ## 🔌 Compatibility With Generic Coding Skills
>
> - `ppt-master` is a repository-specific workflow, not a general application scaffold
> - Do NOT create `.worktrees/`, `tests/`, branch workflows, or generic engineering structure by default
> - On conflict with a generic coding skill, follow this skill unless the user explicitly says otherwise

## Main Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `${SKILL_DIR}/scripts/source_to_md/mineru_to_md.py` | PDF/file parsing to Markdown + image assets via MinerU |
| `${SKILL_DIR}/scripts/source_to_md/doc_to_md.py` | Documents to Markdown — native Python for DOCX/HTML/EPUB/IPYNB, pandoc fallback for legacy formats (.doc/.odt/.rtf/.tex/.rst/.org/.typ) |
| `${SKILL_DIR}/scripts/source_to_md/excel_to_md.py` | Excel workbooks to Markdown — supports .xlsx/.xlsm; legacy .xls should be resaved as .xlsx |
| `${SKILL_DIR}/scripts/source_to_md/ppt_to_md.py` | PowerPoint to Markdown |
| `${SKILL_DIR}/scripts/source_to_md/web_to_md.py` | Web page to Markdown (supports WeChat via `curl_cffi`) |
| `${SKILL_DIR}/scripts/project_manager.py` | Project init / validate / manage |
| `${SKILL_DIR}/scripts/analyze_images.py` | Image analysis |
| `${SKILL_DIR}/scripts/image_gen.py` | AI image generation (multi-provider) |
| `${SKILL_DIR}/scripts/extract_formulas.py` | Extract LaTeX formulas from MinerU Markdown into formula_manifest.json |
| `${SKILL_DIR}/scripts/latex_to_svg.py` | Convert LaTeX formulas to SVG (single or manifest batch mode) |
| `${SKILL_DIR}/scripts/svg_quality_checker.py` | SVG quality check |
| `${SKILL_DIR}/scripts/total_md_split.py` | Speaker notes splitting |
| `${SKILL_DIR}/scripts/finalize_svg.py` | SVG post-processing (unified entry) |
| `${SKILL_DIR}/scripts/svg_to_pptx.py` | Export to PPTX |
| `${SKILL_DIR}/scripts/update_spec.py` | Propagate a `spec_lock.md` color / font_family change across all generated SVGs |

For complete tool documentation, see `${SKILL_DIR}/scripts/README.md`.

## Template Index

| Index | Path | Purpose |
|-------|------|---------|
| Layout templates | `${SKILL_DIR}/templates/layouts/layouts_index.json` | Query available page layout templates |
| Brand presets | `${SKILL_DIR}/templates/brands/brands_index.json` | Query available brand identity presets (color / typography / logo / voice) |
| Visualization templates | `${SKILL_DIR}/templates/charts/charts_index.json` | Query available visualization SVG templates (charts, infographics, diagrams, frameworks) |
| Icon library | `${SKILL_DIR}/templates/icons/` | See `${SKILL_DIR}/templates/icons/README.md`; search icons on demand with `ls templates/icons/<library>/ \| grep <keyword>` |

## Standalone Workflows

| Workflow | Path | Purpose |
|----------|------|---------|
| `topic-research` | `workflows/topic-research.md` | Pre-pipeline — gather web sources when the user supplies only a topic with no source files |
| `create-template` | `workflows/create-template.md` | Standalone layout template creation workflow |
| `create-brand` | `workflows/create-brand.md` | Standalone brand-only template creation (identity preset; no SVG page roster) |
| `resume-execute` | `workflows/resume-execute.md` | Phase B entry — resume execution in a fresh chat after Phase A (Step 1–5) completed in another session (split mode) |
| `verify-charts` | `workflows/verify-charts.md` | Chart coordinate calibration — run after SVG generation if the deck contains data charts |
| `customize-animations` | `workflows/customize-animations.md` | Object-level PPTX animation customization — run only when the user explicitly asks to tune animation order/effects/timing |
| `live-preview` | `workflows/live-preview.md` | Browser-based live preview — auto-started during generation and re-enterable any time the user mentions "live preview", "preview", "看效果", or wants to click/select a slide element |
| `visual-review` | `workflows/visual-review.md` | Per-page rubric-based visual self-check — run only when the user explicitly asks for a visual re-pass on the generated SVGs (between Executor and post-processing). Opt-in only; never invoked by the main pipeline. |

---

## Workflow

### Step 0: Execution Mode Selection ⛔ BLOCKING

Before starting the pipeline, ask the user:

> **执行模式选择 / Execution Mode**:
> 1. **一键执行模式 (One-click)** — 全流程自动执行，中间只在八次确认（Step 4）暂停等待用户确认，其余步骤（文件读写、脚本运行、SVG 生成、后处理、导出）全部自动完成，不再逐一请求权限。
> 2. **逐步确认模式 (Step-by-step)** — 每个文件操作和命令执行都向用户请求权限后再执行（默认行为）。

**Recommendation**: explicitly recommend one mode before waiting.

| Runtime condition | Recommended mode |
|---|---|
| Host already has auto-approve / pre-granted tool permissions enabled | One-click |
| Auto-approve is unavailable, disabled, or unknown | Step-by-step |

**Timeout policy**: this question is timeout-enabled. After presenting both options and the recommendation, wait up to 120 seconds. If the user does not choose, continue with the recommended mode and explicitly state that the timeout fallback was applied.

**If the user chooses one-click mode**:

For **Claude Code** (VS Code extension): ensure the project has `.claude/settings.json` with tool permissions pre-configured (see repo root for the shipped file). If the file is missing, create it from the template. This file allows Claude Code to execute bash commands, read/write/edit project files without per-action approval.

For **GitHub Copilot Agent**: instruct the user to enable auto-approve in VS Code settings: `Settings → search "agent auto" → enable "Chat > Agent: Auto Approve"`, or selectively approve tool categories (terminal, edit, etc.) when the first prompt appears by clicking "Always allow".

After confirming the mode, proceed to Step 1.

### Step 1: Source Content Processing

🚧 **GATE**: User has provided source material (PDF / DOCX / EPUB / URL / Markdown file / text description / conversation content — any form is acceptable).

> **No source content?** When the user supplies only a topic name or requirements without any file or substantive description, run the [`topic-research`](workflows/topic-research.md) workflow first, then return here with its products as input.

> **Project-bound conversion only**: when the user provides source files (PDF / DOCX / XLSX / PPTX / EPUB / HTML / URL), do **not** run a standalone converter on the original source path. Initialize the project first, then use `project_manager.py import-sources` so every derived Markdown file, `_files/` directory, zip, and conversion report stays under the target `project/` tree instead of beside the user's original document.

For source intake, use this rule:

| User Provides | Step 1 action |
|---------------|---------------|
| PDF / DOCX / XLSX / PPTX / EPUB / HTML / LaTeX / RST / Office file | Confirm the source is present, then defer conversion to Step 2 `project_manager.py import-sources <project_path> <source_files...> --move` |
| Web link / WeChat / high-security site | Initialize the project first, then import the URL through Step 2 `project_manager.py import-sources <project_path> <URL>` so fetched Markdown lands inside `project/sources/` |
| CSV / TSV | Read directly as plain-text table source, or import into the project if a persisted copy is needed |
| Markdown | Read directly |

> **⛔ Intermediate-file location rule**: generated Markdown, companion `_files/` directories, MinerU zip archives, conversion reports, and any other conversion intermediates must live inside the corresponding `project/` directory. Writing them beside the user's original PDF / DOCX / PPTX / XLSX file is a workflow violation.

> **Office vector assets (EMF/WMF) from DOCX/PPTX sources**:
> `doc_to_md.py` / `ppt_to_md.py` extract embedded Office vector images (.emf/.wmf)
> alongside bitmap images. After `import-sources`, these land in `images/`
> together with `image_manifest.json` and are first-class assets in §VIII Image Resource List.
>
> **Do NOT convert EMF/WMF to PNG.** The PPT Master pipeline preserves them as external
> references (`finalize_svg.py` skips them) and `svg_to_pptx.py` embeds them as
> PPTX-native media via `image/x-emf` / `image/x-wmf` MIME — PowerPoint renders them at full vector fidelity.
> Converting via LibreOffice/Inkscape introduces CJK font substitution drift and
> rasterization loss; the original EMF/WMF is always higher fidelity than the converted PNG.
>
> Browser-based live preview cannot render EMF (will show blank) — this is expected;
> the PPTX output is the source of truth.

**✅ Checkpoint — Confirm source content is ready, proceed to Step 2.**

> **LaTeX formula extraction (conditional)**:
> When source material was parsed by MinerU (or any converter that preserves LaTeX notation), the resulting Markdown may contain `$...$` and `$$...$$` formulas. After conversion, run:
>
> ```bash
> ${PYTHON} ${SKILL_DIR}/scripts/extract_formulas.py <markdown_file> -o <project_path>/images/formula_manifest.json
> ```
>
> This produces `formula_manifest.json` listing every extracted formula. In Step 4, the Strategist reviews the manifest and sets `"render": true` for formulas that should appear as SVG graphics in the presentation. In Step 5, the rendering runs:
>
> ```bash
> ${PYTHON} ${SKILL_DIR}/scripts/latex_to_svg.py --manifest <project_path>/images/formula_manifest.json
> ```
>
> Generated formula SVGs land in `images/` as `formula_*.svg` and are referenced like any other image asset: `<image href="../images/formula_001_xxx.svg" .../>`.
> Requires `latex` and `dvisvgm` on PATH (provided by MiKTeX or TeX Live).
> The formula manifest is the semantic source of truth: rendered SVGs receive non-visual `<title>` / `<desc>` metadata, and `stabilize_image_assets.py` writes `notes/formula_asset_table.md` with `SVG href`, LaTeX, source context, and scale guidance.
> Executor MUST use that table before placing formula SVGs and include `data-formula-id="<id>"` on the slide `<image>`.
> Short / inline formulas marked `inline-or-callout` or `formula-compact` must remain near text/callout scale; do not enlarge them into hero-size visuals unless the page outline explicitly makes the formula the main object.
>
> **Source asset inventory (mandatory)**:
> Before Step 4, treat every extracted original asset in `<project_path>/images/` as a first-class planning input: source figures, screenshots, charts, MinerU/PDF-extracted images, EMF/WMF office vectors, and rendered `formula_*.svg` outputs. Add them to §VIII Image Resource List with source/context tags before outline decisions are locked.
>
> **Hard rule — source-first visuals**: when the source already contains a figure, chart, screenshot, or formula that carries the point, prefer that original asset over re-drawing or re-imagining it.
>
> **Hard rule — no fabricated charts**: do not create bar / line / pie / radar / KPI charts unless the source provides concrete underlying data or an existing chart that can be faithfully adapted. If the source has no usable numbers, use the original figure, a table, equations, or text/diagram layouts instead.

---

### Step 2: Project Initialization

🚧 **GATE**: Step 1 complete; source content is ready (Markdown file, user-provided text, or requirements described in conversation are all valid).

```bash
${PYTHON} ${SKILL_DIR}/scripts/project_manager.py init <project_name> --format <format>
```

Format options: `ppt169` (default), `ppt43`, `xhs`, `story`, etc. For the full format list, see `references/canvas-formats.md`.

Import source content (choose based on the situation):

| Situation | Action |
|-----------|--------|
| Has source files (PDF/MD/etc.) | `${PYTHON} ${SKILL_DIR}/scripts/project_manager.py import-sources <project_path> <source_files...> --move` |
| User provided text directly in conversation | No import needed — content is already in conversation context; subsequent steps can reference it directly |

> ⚠️ **Source document protection**: `import-sources` **always copies** original documents (PDF, DOCX, PPTX, XLSX, etc.) — even with `--move`. Only generated intermediate files (Step 1's Markdown output, `_files/` dirs) are moved. The user's original file is **never deleted or modified**.
>
> ⚠️ **Intermediate output location**: use `import-sources` as the default entry point for file-based sources. It writes converted Markdown, `_files/` asset directories, and reports under the project tree. Do **not** run standalone converters on the original source path unless you also pass an explicit `-o` inside `<project_path>/sources/`.
>
> ⚠️ **Project name collision**: if a project directory with the same name already exists, `init` automatically appends an incrementing suffix (`_2`, `_3`, ...) to create a **new** project. It **never overwrites or reuses** an existing project directory.

**Live Preview Early Startup (Mandatory)**: immediately after the project directory exists, launch the browser editor and keep it running through the whole workflow:
```bash
${PYTHON} ${SKILL_DIR}/scripts/start_live_preview.py <project_path>
```
- Open it right after Step 2 succeeds, not later. The wrapper starts `svg_editor/server.py` in the background, waits for readiness, prints `LIVE_PREVIEW_URL=...`, opens the browser locally, and exits so the workflow can continue.
- Default URL is `http://127.0.0.1:5050`; if `5050` is unavailable, use `--port <other>` and report the **actual** URL that was printed.
- `svg_output/` may still be empty at this stage. That is intentional: the UI should already be open and show deck-generation progress before the first SVG appears.
- Do not wait for user confirmation after startup.

**✅ Checkpoint — Confirm project structure created successfully, `sources/` contains all source files, converted materials are ready, and live preview is already open. Proceed to Step 3.**

---

### Step 3: Template Option

🚧 **GATE**: Step 2 complete; project directory structure is ready.

**Default — free design.** Proceed directly to Step 4. Do NOT query `layouts_index.json` unless triggered. Do NOT ask the user. Do NOT proactively suggest, hint at, or fuzzy-match any template based on content, slug-like words, or vague style descriptions.

**Template flow triggers ONLY on an explicit template directory path** supplied by the user in their initial message. The trigger rule is mechanical, not interpretive:

| User input contains | Step 3 action |
|---|---|
| An explicit path to a template directory (e.g. `skills/ppt-master/templates/layouts/academic_defense/`, `projects/foo/template/`, or any other absolute / relative path that resolves to a directory containing `design_spec.md` and one or more page SVGs) | Copy that directory's SVGs + `design_spec.md` + assets into the project, advance |
| Anything else — including bare template names ("用 academic_defense 模板"), style descriptions ("麦肯锡风格" / "Google style"), brand mentions ("招商银行风格"), vague intent ("想用个模板"), or silence | Skip Step 3, free design |

There is no slug matching, no name lookup, no fuzzy resolution. A template name without a path does not trigger — the user must give a path the AI can `cd` into.

The path may live anywhere — `skills/ppt-master/templates/layouts/<name>/` (the built-in library), `projects/<other_project>/template/` (reusing a previous project's templates), or any other location. Location is irrelevant; what matters is that the user named the path.

```bash
TEMPLATE_DIR=<user-supplied path>
cp ${TEMPLATE_DIR}/*.svg <project_path>/templates/
cp ${TEMPLATE_DIR}/design_spec.md <project_path>/templates/
cp ${TEMPLATE_DIR}/*.png <project_path>/images/ 2>/dev/null || true
cp ${TEMPLATE_DIR}/*.jpg <project_path>/images/ 2>/dev/null || true
```

> Style descriptions ("麦肯锡风格" / "Keynote 风" / "极简风" / etc.) never trigger Step 3. They flow naturally into Strategist's Eight Confirmations as part of the user's input — Strategist uses them as a style brief when proposing color / typography / tone in confirmations e and g.

> Bare template names ("academic_defense", "招商银行") do NOT trigger Step 3 even if a folder by that name exists in the library. The user must give a path. AI must not "helpfully" resolve a name to a path.

> "What templates exist?" is out-of-band Q&A — answer by listing entries from `layouts_index.json` together with their paths. Listing alone does not advance the pipeline; the user still has to send a path to trigger the Step 3 copy.

> To create a new template, read `workflows/create-template.md`.

**Brand triggering follows the same explicit-path rule as layout templates.** A brand is structurally a layout template minus its SVG page roster — its `design_spec.md` declares `kind: brand` in YAML frontmatter and lives under `templates/brands/<id>/`. `brands_index.json` is discovery-only, same as `layouts_index.json` — listing brands never triggers Step 3.

| User input contains | Step 3 brand action |
|---|---|
| An explicit path to a brand directory (e.g. `skills/ppt-master/templates/brands/acme/`, or any path that resolves to a directory whose `design_spec.md` declares `kind: brand`) | Copy `design_spec.md` + logo files + any present asset subdirectories into `<project_path>/templates/` |
| Bare brand names ("use acme brand", "用 acme 品牌"), brand mentions without a path, or silence | Skip — same mechanical rule as layout templates: bare names never trigger |

```bash
BRAND_DIR=<user-supplied brand path>
cp ${BRAND_DIR}/design_spec.md <project_path>/templates/
cp ${BRAND_DIR}/*.svg <project_path>/templates/ 2>/dev/null || true     # brand logo SVG files
cp ${BRAND_DIR}/*.png <project_path>/templates/ 2>/dev/null || true     # brand logo raster files
[ -d ${BRAND_DIR}/images ] && cp -r ${BRAND_DIR}/images <project_path>/templates/
[ -d ${BRAND_DIR}/illustrations ] && cp -r ${BRAND_DIR}/illustrations <project_path>/templates/
[ -d ${BRAND_DIR}/icons ] && cp -r ${BRAND_DIR}/icons <project_path>/templates/
```

> Brand and layout outputs share `<project_path>/templates/` because they are the same kind of artifact — a reference bundle that Strategist treats as truth. Downstream code never needs to distinguish them.

> "What brands exist?" is out-of-band Q&A — answer by listing entries from `brands_index.json` together with their paths. Listing alone does not advance the pipeline; the user still has to send a path to trigger the Step 3 copy.

> To create a new brand, read `workflows/create-brand.md`.

#### Brand + layout combined input

A brand path and a layout template path may both be supplied in the same message. When both are present, Step 3 **fuses them into a single `design_spec.md`** inside `<project_path>/templates/` instead of leaving two specs side by side. Field-level precedence is fixed (no per-deck prompting):

| Field group | Source |
|---|---|
| Color (primary / secondary / accents / text / bg) | **brand** |
| Typography (font family) | **brand** |
| Logo | **brand** (if absent, fall back to layout's logo) |
| Voice & tone | **brand** |
| Icon style preference | **brand** |
| Canvas (size / viewBox / margins) | **layout** |
| Page roster + signature visual elements (top bar / underline / decorative motifs) | **layout** |
| Font-size hierarchy (H1 / H2 / body / data / label) | **layout** |
| Spacing, grid, layout patterns | **layout** |
| SVG technical constraints | **layout** |
| Placeholder set | **layout** |

Action: AI reads `${LAYOUT_DIR}/design_spec.md` and `${BRAND_DIR}/design_spec.md`, composes one fused `design_spec.md` using the table above, writes it to `<project_path>/templates/design_spec.md`. SVG page files come from `${LAYOUT_DIR}`; brand logos and asset subdirectories from `${BRAND_DIR}`. The fused spec carries a one-line `> Fused from: layout=<layout_id>, brand=<brand_id>` provenance note under its H1.

**Conflict gates** — clarify with the user only in these two cases:

1. **Brand has no logo, layout has one.** Ask: "your brand has no bundled logo; use the layout's logo, or leave the deck logo-less?"
2. **Layout is itself a branded template (e.g. `招商银行`, `重庆大学`, `中汽研_*`, `中国电建_*`) and the supplied brand is different.** Ask: "this layout carries `<layout's own brand>` identity, which conflicts with the `<supplied brand>` you provided — confirm you want brand identity from `<supplied brand>` and only the page structure from `<layout>`?"

If neither gate trips, fusion proceeds silently and Step 3 advances.

**✅ Checkpoint — Default path proceeds to Step 4 without user interaction. If the user's input contains an explicit template directory path and/or an explicit brand directory path, those directories are copied (or fused) into `<project_path>/templates/` before advancing.**

---

### Step 4: Strategist Phase (MANDATORY — cannot be skipped)

🚧 **GATE**: Step 3 complete; default free-design path taken, or (if triggered) template files copied into the project.

First, read the role definition:
```
Read references/strategist.md
```

> ⚠️ **Mandatory gate**: before writing `design_spec.md`, Strategist MUST `read_file templates/design_spec_reference.md` and follow its full I–XI section structure. See `strategist.md` Section 1.

**Eight Confirmations** (full template: `templates/design_spec_reference.md`):

**Language timeout policy**: before presenting the bundled eight items, ask the deck-copy language first, recommend a concrete default (`zh-CN` unless the source clearly justifies another language), and wait up to 120 seconds. If the user does not answer, lock the recommended language and continue to the bundle. Explicitly state when this timeout fallback is applied.

⛔ **BLOCKING (120-second fallback enabled)**: present the Eight Confirmations as a single bundled recommendation set and wait for explicit user confirmation or modification before outputting Design Specification & Content Outline. If the user does not respond within 120 seconds, treat the full recommended set as accepted, explicitly say that the timeout fallback was applied, and proceed. This is the single core confirmation point — once confirmed or auto-accepted by timeout, all subsequent steps proceed automatically.

1. Canvas format
2. Page count range
3. Target audience + deck text language
4. Style objective
5. Color scheme
6. Icon usage approach
7. Typography plan
8. Image usage approach

**Mandatory — split-mode note** (not a ninth confirmation): after listing the eight confirmation details, you MUST append exactly one short line (rendered in the user's language, prefixed with 💡) about generation mode. Pick the variant by qualitative read of Phase A signals — recommended page count, source-material bulk, whether `topic-research` ran with substantial web-fetch accumulation:

| Signal read | Line content |
|---|---|
| Heavy (long page count / bulky sources / heavy web-fetch accumulation) | State estimated page count and large source size; recommend switching to [split mode](workflows/resume-execute.md) after Step 5 — stop this chat, open a fresh window and input `继续生成 projects/<project_name>` to enter Phase B (SVG generation + export); no response or "continue" = default continuous mode. |
| Normal (default) | State scale is moderate, default continuous mode generates in one go; if mid-way window switch is desired, input `继续生成 projects/<project_name>` after Step 5 to switch to [split mode](workflows/resume-execute.md). |

This line is required output every run — the user must always see the mode choice exists. Whether to act on it is the user's call.

If the user provided images, run analysis **before outputting the design spec**:
```bash
${PYTHON} ${SKILL_DIR}/scripts/analyze_images.py <project_path>/images
```

> ⚠️ **Image scale iron rule**: when the deck uses source-document images (paper figures, screenshots, source charts, dense diagrams, evidence photos, product photos), Strategist MUST `read_file references/image-layout-spec.md` before locking §VIII and the outline. Plan from native dimensions and readable complete-display size, not from a text-first placeholder box. Large, information-bearing source images may own the page or dominant zone; they must not be shrunk into a small supporting tile just to preserve extra bullets. If image + text cannot both remain readable, reduce text density or split the material across more pages.

> ⚠️ **Image handling**: NEVER directly read / open / view image files (`.jpg`, `.png`, etc.). All image info comes from `analyze_images.py` output or the Design Spec's Image Resource List.

**Output**:
- `<project_path>/design_spec.md` — human-readable design narrative
- `<project_path>/spec_lock.md` — machine-readable execution contract (skeleton: `templates/spec_lock_reference.md`); Executor re-reads before every page

**✅ Checkpoint — Phase deliverables complete, auto-proceed to next step**:
```markdown
## ✅ Strategist Phase Complete
- [x] Eight Confirmations completed (user confirmed or 120-second timeout fallback applied)
- [x] Split-mode note appended below the eight items (heavy or normal variant)
- [x] Design Specification & Content Outline generated
- [x] Execution lock (spec_lock.md) generated
- [ ] **Next**: Auto-proceed to [Image_Generator / Executor] phase
```

---

### Step 5: Image Acquisition Phase (Conditional)

🚧 **GATE**: Step 4 complete; Design Specification & Content Outline generated and user confirmed.

> **Trigger**: At least one row in the resource list has `Acquire Via: ai` and/or `Acquire Via: web`. If every row is `user` or `placeholder`, skip to Step 6.

**Always load the common framework**:

```
Read references/image-base.md
```

Then **lazy-load the path-specific reference** for each row that actually needs it:

| Acquire Via | Load reference (only if any such row exists) | Run |
|---|---|---|
| `ai` | `references/image-generator.md` | `${PYTHON} ${SKILL_DIR}/scripts/image_gen.py --manifest <project_path>/images/image_prompts.json` |
| `web` | `references/image-searcher.md` | `${PYTHON} ${SKILL_DIR}/scripts/image_search.py ...` |
| `user` / `placeholder` | (skip) | (skip) |

A deck with only `ai` rows never loads `image-searcher.md`; a deck with only `web` rows never loads `image-generator.md`. A mixed deck loads both, processes each row through its own path, and writes both `image_prompts.json` and `image_sources.json`.

> ⚠️ **In-pipeline ai path MUST use manifest mode** — even when only 1 ai row exists. Write `images/image_prompts.json` first, then run `image_gen.py --manifest`, then `image_gen.py --render-md` to produce the `image_prompts.md` sidecar. The positional form (`image_gen.py "prompt" ...`) is reserved for **out-of-pipeline one-off testing / single-image fixups** — it skips manifest + sidecar, leaving no audit trail.

Workflow:

1. Extract all rows with `Status: Pending` and `Acquire Via ∈ {ai, web}` from the design spec
2. Generate prompts (ai rows) and/or run search (web rows) per [image-base.md](references/image-base.md) §2 dispatch table
3. Verify every row reaches a terminal status: `Generated` (ai success), `Sourced` (web success), or `Needs-Manual`
4. **Formula rendering (conditional)**: If `formula_manifest.json` exists in `images/` and contains entries with `"render": true`, run `${PYTHON} ${SKILL_DIR}/scripts/latex_to_svg.py --manifest <project_path>/images/formula_manifest.json` to generate formula SVGs, then run `${PYTHON} ${SKILL_DIR}/scripts/stabilize_image_assets.py <project_path>` so `notes/formula_asset_table.md` reflects the rendered formulas and short-formula scale caps

**✅ Checkpoint — Confirm acquisition attempted for every row**:
```markdown
## ✅ Image Acquisition Phase Complete
- [x] image_prompts.json created (when any ai rows processed)
- [x] image_prompts.md sidecar rendered (when any ai rows processed)
- [x] image_sources.json created (when any web rows processed)
- [x] formula_manifest.json rendered (when LaTeX formulas were extracted in Step 1 and Strategist marked render=true)
- [x] formula_asset_table.md refreshed (when formula SVGs were rendered)
- [x] Each row: status is `Generated` / `Sourced` / `Needs-Manual` (no `Pending` remaining)
```

**Default — auto-proceed to Step 6.** Only when the user's Step 4 response explicitly opted into split mode (in reply to the optional hint), output the Phase A hand-off below and stop this conversation:

  ```markdown
  ## ✅ Phase A Complete
  - [x] Spec: `design_spec.md`, `spec_lock.md`
  - [x] Resources: `sources/`, `images/`, `templates/`
  - [ ] **Next**: open a fresh chat window and input `继续生成 projects/<project_name>` to enter Phase B via the [`resume-execute`](workflows/resume-execute.md) workflow.
  ```

> On acquisition failure, do NOT halt — follow the Failure Handling rule in [image-base.md](references/image-base.md) §5: retry once, then mark the row `Needs-Manual`, report to user, and continue to the checkpoint above.

---

### Step 6: Executor Phase

🚧 **GATE**: Step 4 (and Step 5 if triggered) complete; all prerequisite deliverables are ready.

Read the role definition based on the selected style:
```
Read references/executor-base.md          # REQUIRED: common guidelines
Read references/shared-standards.md       # REQUIRED: SVG/PPT technical constraints
Read references/executor-general.md       # General flexible style
Read references/executor-consultant.md    # Consulting style
Read references/executor-consultant-top.md # Top consulting style (MBB level)
```

> Only read executor-base + shared-standards + one style file.

**Design Parameter Confirmation (Mandatory)**: before the first SVG, output key design parameters from the spec (canvas dimensions, color scheme, font plan, body font size). See executor-base.md §2.

**Live Preview Continuation (Mandatory)**: ensure the browser editor from Step 2 is still running before the first SVG, and keep it running continuously through Executor + Step 7 export:
```bash
${PYTHON} ${SKILL_DIR}/scripts/start_live_preview.py <project_path>
```
- If Step 2 already opened the preview, this command should simply reuse it; otherwise start it now. The wrapper prints `LIVE_PREVIEW_URL=...` and the editor should be at `http://127.0.0.1:5050` unless another port had to be chosen.
- If another preview is already running for a different project, switch the preview to the current project first; always report the **actual** URL the server ended up using instead of assuming `5050`.
- Do not run `svg_editor/server.py` directly in the foreground during the workflow. Use `start_live_preview.py` so the long-running server stays alive in the background while SVG generation continues. Do not wait for user confirmation after startup.
- **Service must keep running** until one of: (a) the user clicks **Exit preview** in the browser, or (b) the user explicitly asks in chat to stop it. Generation continues even if the user closes the editor.
- **Do NOT read or apply submitted annotations during generation.** Users may annotate at any time, but Executor proceeds without touching them. The window to apply annotations opens only after Step 7 completes — see [`workflows/live-preview.md`](workflows/live-preview.md).
- UI button semantics and editor details: see [`workflows/live-preview.md`](workflows/live-preview.md) Notes.

**Pre-generation Batch Read (Mandatory)**: before the first SVG, batch-read every distinct layout SVG referenced in `spec_lock.page_layouts` and every distinct chart SVG referenced in `spec_lock.page_charts` (plus any §VII backup charts). One read per file, up front — do not re-read these during page generation. See executor-base.md §1.0.

**Per-page spec_lock re-read (Mandatory)**: before **each** SVG page, `read_file <project_path>/spec_lock.md` and use only its colors / fonts / icons / images, plus the per-page `page_rhythm` / `page_layouts` / `page_charts` lookups (resolves to template SVGs already loaded in the batch read above). Resists context-compression drift on long decks. See executor-base.md §2.1.

> ⚠️ **Main-agent only**: SVG generation MUST stay in the current main agent — page design depends on full upstream context. Do NOT delegate to sub-agents.
> ⚠️ **Generation rhythm**: generate pages sequentially, one at a time, in the same continuous context. Do NOT batch (e.g., 5 per group).

**Visual Construction Phase**: generate SVG pages sequentially, one at a time, in one continuous pass → `<project_path>/svg_output/`

**Quality Check Gate (Mandatory)** — after all SVGs, BEFORE annotation handling and speaker notes:
```bash
${PYTHON} ${SKILL_DIR}/scripts/svg_quality_checker.py <project_path>
```
- Any `error` (banned SVG features, viewBox mismatch, spec_lock drift, etc.) MUST be fixed before proceeding — return to Visual Construction, regenerate that page, re-run check.
- `warning` entries (low-res image, non-PPT-safe font tail, etc.): fix when straightforward, otherwise acknowledge and release.
- Run against `svg_output/` (not after `finalize_svg.py` — finalize rewrites SVG and masks violations).

**Logic Construction Phase**: generate speaker notes → `<project_path>/notes/total.md`

**✅ Checkpoint — Confirm all SVGs and notes are fully generated and quality-checked. Proceed directly to Step 7 post-processing**:
```markdown
## ✅ Executor Phase Complete
- [x] Live preview started and kept available at the reported URL
- [x] All SVGs generated to svg_output/
- [x] svg_quality_checker.py passed (0 errors)
- [x] Speaker notes generated at notes/total.md
```

> **Chart pages?** If this deck contains data charts (bar / line / pie / radar / etc.), run the standalone [`verify-charts`](workflows/verify-charts.md) workflow before Step 7 to calibrate coordinates. AI models routinely introduce 10–50 px errors when mapping data to pixel positions; verify-charts eliminates that class of error. Skip if no chart pages.

> **Visual self-check (opt-in)?** If the user explicitly asked for a per-page visual re-pass on the SVGs ("跑一下视觉自检 / 视觉回看", "visual review", "check pages visually", etc.), run the standalone [`visual-review`](workflows/visual-review.md) workflow before Step 7. Do NOT run it by default and do NOT recommend it based on inferred model capability or deck size — trigger is user request only.

---

### Step 7: Post-processing & Export

🚧 **GATE**: Step 6 complete; all SVGs generated to `svg_output/`; speaker notes `notes/total.md` generated.

🚧 **Image readiness GATE** (when Step 5 left ai rows in `Needs-Manual`): every expected file must exist at `project/images/<filename>` before running 7.1.

> If files are missing: PAUSE, list the missing filenames, point the user to `images/image_prompts.md` (each `### Image N:` block is paste-ready for ChatGPT / Gemini / Midjourney; auto-generated from `image_prompts.json`) and the required placement `project/images/<filename>`. Resume Step 7.1 only after all expected files are in place. `finalize_svg.py` and `svg_to_pptx.py` do not detect missing files at this layer — proceeding with gaps produces a deck with broken image references.

> ⚠️ Run the three sub-steps **one at a time** — each must complete successfully before the next.
> ❌ **NEVER** combine them into a single code block or shell invocation.

Canonical three-command pipeline (mirrors `references/shared-standards.md` §5):

**Step 7.1** — Split speaker notes:
```bash
${PYTHON} ${SKILL_DIR}/scripts/total_md_split.py <project_path>
```

**Step 7.2** — SVG post-processing (icon embedding / image crop & embed / text flattening / rounded rect to path):
```bash
${PYTHON} ${SKILL_DIR}/scripts/finalize_svg.py <project_path>
```

**Step 7.3** — Export PPTX (embeds speaker notes by default):
```bash
${PYTHON} ${SKILL_DIR}/scripts/svg_to_pptx.py <project_path>
# Output (default-flow mode):
#   exports/<project_name>_<timestamp>.pptx           ← native pptx (canonical output, reads svg_output/)
#   backup/<timestamp>/svg_output/                    ← Executor SVG source backup (always written)
#   PPTX_OUTPUT_DIR=<absolute exports directory>      printed after successful export
#   PPTX_OUTPUT_FILE=<absolute canonical pptx path>   printed after successful export
#   PPTX_OPENED=<absolute canonical pptx path>        printed after auto-open succeeds
#
# Add --svg-snapshot to additionally emit the SVG-image preview pptx alongside the native pptx:
#   exports/<project_name>_<timestamp>_svg.pptx      ← SVG preview pptx (reads svg_final/)
# The primary PPTX opens automatically after successful export; pass --no-open to suppress.
```

> The native pptx consumes `svg_output/` directly so the converter can preserve
> high-fidelity primitives (icon `<use>` placeholders, image `preserveAspectRatio`
> → `srcRect`, rounded rect `rx/ry` → `prstGeom roundRect`). The `svg_output/`
> snapshot in `backup/<timestamp>/` is always written so the project can be
> re-exported from frozen SVG sources without re-running the LLM. The SVG-rendered
> preview pptx is opt-in via `--svg-snapshot` — live preview already provides the
> SVG visual reference, so it's only needed when you want a self-contained file
> to share. Pass `-s output` or `-s final` to force a single source if you need it.

> **Paragraph editability vs line fidelity** — by default every dy-stacked line is
> its own PowerPoint text frame, preserving exact SVG layout. Add `--merge-paragraphs`
> only when the user explicitly asks for an editable / wrap-friendly export (e.g.
> "I want to edit the abstract as one block", "make text boxes resizable / reflow"):
> mergeable paragraph blocks collapse into one editable text frame with multiple
> `<a:p>`, at the cost of PowerPoint re-wrapping inside each box. Default off keeps
> pixel-fidelity; turn it on per the user's request, not on your own judgement.

**Optional animation flags** (page transitions default off; per-element entrance animations keep their global defaults unless the user asks otherwise):
- `-t <effect>` — page transition. Default `none`. Options: `none` / `fade` / `push` / `wipe` / `split` / `strips` / `cover` / `random`. Only enable when the user explicitly asks for slide transitions.
- `-a <effect>` — per-element entrance animation. Default `auto` (map effect from group id: chart→wipe, card-/step-/pillar-→fly, title/takeaway→fade; image-like ids `hero` / `figure-` / `image` / `img-` / `kpi` cycle a richer pool — zoom / dissolve / circle / box / diamond / wheel — so multiple images vary across the deck). Pass `none` to disable, a specific effect like `fade`, or `mixed` for the legacy 16-effect cycle. Requires top-level `<g id="...">` groups (already required by Executor).
- `--animation-trigger {on-click,with-previous,after-previous}` — Start mode (matches PowerPoint's animation-pane Start dropdown). Default `after-previous` (click-free cascade; pace via `--animation-stagger`). Use `on-click` for presenter-paced reveals, or `with-previous` for all-at-once.
- `--animation-config <path>` — optional object-level sidecar. Default: `<project_path>/animations.json` when present.
- `--auto-advance <seconds>` — kiosk-style auto-play.

**Optional custom animations** (only when the user asks to tune animation order/effects/timing for specific objects):

Run the standalone [`customize-animations`](workflows/customize-animations.md) workflow. Default export has **no page transition**; global entrance animation may still be present. Do not create `animations.json` unless object-level customization was requested.

**Optional recorded narration** (only when the user asks for narrated/video export):

Run the standalone [`generate-audio`](workflows/generate-audio.md) workflow. The AI picks a narration backend (`edge` by default, or a configured cloud provider such as ElevenLabs / MiniMax / Qwen / CosyVoice for high-quality or cloned voices), asks the user once (backend + voice + rate/settings + embed-or-not, all with recommended values), then executes `notes_to_audio.py` and (if chosen) re-exports the PPTX with `--recorded-narration audio`.

Do NOT call `notes_to_audio.py` directly without going through the workflow — `--voice` / `--voice-id` is required and the workflow produces the locale/provider-aware recommendation that makes the choice meaningful.

Full effect list, anchor logic, and limits: [`references/animations.md`](references/animations.md).

> ❌ **NEVER** substitute `cp` for `finalize_svg.py` — finalize performs multiple critical processing steps
> ❌ **NEVER** force `-s output` for the legacy/preview pptx (PowerPoint's internal SVG parser drops icons and rounded corners). The default auto-split already gives native the high-fidelity source it needs without touching legacy.
> ❌ **NEVER** use `--only` (it suppresses one of the two output files)

> **Post-export annotation window**: the preview service from Step 6 typically remains running after export. If the user submitted annotations in the browser (during Executor or after export) and now asks to apply them — they may quote the browser prompt (`Annotations saved. ... apply my annotations`), say "apply my annotations" / "应用注解" / equivalent — run [`live-preview`](workflows/live-preview.md) Step 2 to apply and re-export. Annotations submitted during generation are also handled here, not earlier.

> **Preview not running?** Any time the user mentions "live preview", "preview", "看效果", or wants to select/click a slide element and the service is not running, run [`live-preview`](workflows/live-preview.md) Step 1 to start it. If the service is already running, just point them at the URL — do not restart.

---

## Role Switching Protocol

Before switching roles, **MUST first read** the corresponding reference file. Output marker:

```markdown
## [Role Switch: <Role Name>]
📖 Reading role definition: references/<filename>.md
📋 Current task: <brief description>
```

---

## Reference Resources

| Resource | Path |
|----------|------|
| Shared technical constraints | `references/shared-standards.md` |
| Canvas format specification | `references/canvas-formats.md` |
| Image-text layout patterns (Primary structures + Modifier layers — combine freely) | `references/image-layout-patterns.md` |
| Image layout sizing (math for side-by-side container dimensions) | `references/image-layout-spec.md` |
| SVG image embedding | `references/svg-image-embedding.md` |
| Icon library | `templates/icons/README.md` |

---

## Notes

- Local preview: `${PYTHON} -m http.server -d <project_path>/svg_final 8000`
- **Troubleshooting**: on generation issues (layout overflow, export errors, blank images, etc.), check `docs/faq.md` for known solutions
