# PPT Master Sci Fork — AI generates natively editable PPTX from papers, PDFs, and technical documents

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/hongyi652/ppt-master-sci-fork.svg)](https://github.com/hongyi652/ppt-master-sci-fork/stargazers)
[![Upstream](https://img.shields.io/badge/upstream-hugohe3%2Fppt--master-green.svg)](https://github.com/hugohe3/ppt-master)

English | [中文](./README_CN.md)

> A scientific/academic fork of [PPT Master](https://github.com/hugohe3/ppt-master), enhanced with **MinerU document parsing** and **SVG formula support** for paper-style, formula-heavy, and technical documents.

<p align="center">
  <a href="./examples/"><strong>Examples</strong></a> ·
  <a href="./docs/faq.md"><strong>FAQ</strong></a> ·
  <a href="./CHANGELOG.md"><strong>Changelog</strong></a> ·
  <a href="https://github.com/hongyi652/ppt-master-sci-fork/issues"><strong>Issues</strong></a>
</p>

---

## Why this fork?

This repository is a fork of [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master), adapted for **scientific and academic presentation workflows**.

Compared with the upstream project, this fork focuses more on:

- **MinerU-based parsing** for academic PDFs and structured technical documents
- **SVG formula rendering** for clearer math expressions in generated slides
- Better handling of **formula-heavy**, **paper-style**, and **report-style** source materials
- Workflow improvements for **research**, **teaching**, and **technical communication**

If your source material includes papers, equations, diagrams, or dense technical content, this fork is intended to be a better starting point.

> **Fork Notice**: This project is forked from [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master). The original project is created by [Hugo He](https://github.com/hugohe3) and licensed under the MIT License. This fork keeps compatibility where practical while adding scientific-document-oriented enhancements. See [CHANGELOG.md](./CHANGELOG.md) for fork-specific changes.

---

## What it does

Drop in a PDF, DOCX, URL, or Markdown file, and generate a **natively editable PowerPoint (`.pptx`)** with:

- real text boxes
- real vector shapes
- real charts
- editable slide elements in PowerPoint

This is **not** image-per-slide export. The output is intended to remain editable after generation.

---

## Best for

- Research papers
- Technical reports
- Academic presentations
- Formula-heavy PDFs
- Structured long-form documents
- Scientific or engineering communication materials

## Current limitations

- Output quality still depends heavily on the underlying AI model
- Formula fidelity depends on source quality and parsing quality
- Scanned PDFs may need cleanup if the source is noisy
- Some layouts still require iterative prompting or manual refinement

---

## 30-Second Quick Start

```bash
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork
pip install -r requirements.txt
```

Then place your source file in `projects/` and ask your AI agent something like:

```text
Please create a PPT from projects/demo/sources/paper.pdf
```

The output `.pptx` will be generated into `exports/`.

---

## Quick Start

### 1. Prerequisites

You only need **Python 3.10+**.

| Dependency | Required? | What it does |
|------------|:---------:|--------------|
| [Python](https://www.python.org/downloads/) 3.10+ | ✅ Yes | Core runtime |

Install dependencies:

```bash
pip install -r requirements.txt
```

<details open>
<summary><strong>Windows</strong> — see the dedicated step-by-step guide</summary>

Windows users can follow:

**📖 [Windows Installation Guide](./docs/windows-installation.md)**

Quick version: install Python from [python.org](https://www.python.org/downloads/), make sure Python is added to PATH, then run:

```bash
pip install -r requirements.txt
```
</details>

<details>
<summary><strong>macOS / Linux</strong> — install and go</summary>

```bash
# macOS
brew install python
pip install -r requirements.txt

# Ubuntu / Debian
sudo apt install python3 python3-pip
pip install -r requirements.txt
```
</details>

<details>
<summary><strong>Optional fallback dependency</strong> — only for some legacy formats</summary>

**Pandoc** may be useful for some older or less common document formats such as `.doc`, `.odt`, `.rtf`, `.tex`, `.rst`, `.org`, or `.typ`.

```bash
# macOS
brew install pandoc

# Ubuntu / Debian
sudo apt install pandoc
```
</details>

### 2. Get the repository

**Option A — Download ZIP**

Download this repository from:

- https://github.com/hongyi652/ppt-master-sci-fork

Then unzip it locally.

**Option B — Git clone**

```bash
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork
```

### 3. Pick an agent

PPT Master works best in tools that can:

- read and write files
- execute commands
- handle multi-turn instructions

Examples include:

- GitHub Copilot in VS Code
- Claude Code
- Cursor
- Cline
- Continue
- other agent-style IDE or CLI tools

> **Model note**: Better models usually produce better layout, structure, and summarization quality, especially for technical and formula-heavy inputs.

### 4. Prepare source materials

Put your files into the `projects/` directory, for example:

```text
projects/demo/sources/paper.pdf
projects/demo/sources/report.docx
projects/demo/sources/notes.md
```

You can also paste content directly into your AI chat instead of referencing a file.

### 5. Ask your AI agent to generate slides

Examples:

```text
Please create a PPT from projects/demo/sources/paper.pdf
```

```text
Please turn projects/demo/sources/report.docx into a clean academic presentation.
```

```text
Please summarize this paper into an 8-slide presentation with a technical but readable style.
```

### 6. Get the output

Generated PowerPoint files are saved to:

```text
exports/<name>_<timestamp>.pptx
```

The result is intended to be **editable in PowerPoint**, not just visually previewable.

---

## Scientific document support

This fork is especially aimed at technical and academic inputs.

### MinerU-based parsing

MinerU is used to improve extraction from document-like inputs, especially when dealing with:

- paper-style PDFs
- structured academic text
- sections, tables, and formula-adjacent content
- technical documents that are harder to parse cleanly with generic pipelines

### SVG formula support

Formula rendering support is enhanced through SVG-based handling so that mathematical expressions can appear more clearly in generated slides.

This is especially useful for:

- math-heavy reports
- scientific presentations
- lecture materials
- research summaries

> Note: SVG formula support improves clarity and display quality, but exact editability and fidelity may still vary depending on the original source and the generation path.

---

## How it works

PPT Master is a workflow/tooling layer used together with an AI coding or agent environment.

Typical workflow:

1. You provide source materials
2. The AI reads and structures the content
3. The project generates slide content and SVG assets
4. The pipeline exports a native editable `.pptx`

It works best in tools that can:

- read and write files
- execute commands
- handle multi-turn instructions

---

## Examples

See:

- [examples/](./examples/)
- [examples/README.md](./examples/README.md)

If you want to showcase this fork more clearly, consider adding examples specifically based on:

- research papers
- technical whitepapers
- formula-heavy lecture notes
- scientific reports

---

## Documentation

Start here:

- [Windows Installation](./docs/windows-installation.md)
- [FAQ](./docs/faq.md)
- [Templates Guide](./docs/templates-guide.md)
- [Technical Design](./docs/technical-design.md)

More docs:

| Document | Description |
|----------|-------------|
| [Why PPT Master](./docs/why-ppt-master.md) | Comparison with other AI presentation tools |
| [SKILL.md](./skills/ppt-master/SKILL.md) | Core workflow and rules |
| [Canvas Formats](./skills/ppt-master/references/canvas-formats.md) | Supported output formats |
| [Animations & Transitions](./skills/ppt-master/references/animations.md) | Animation support |
| [Audio Narration & Video Export](./docs/audio-narration.md) | TTS and video export |
| [Scripts & Tools](./skills/ppt-master/scripts/README.md) | Scripts and commands |
| [Examples](./examples/README.md) | Example projects |
| [FAQ](./docs/faq.md) | Troubleshooting and common questions |

---

## Relationship to upstream

This project is based on:

- **Upstream**: [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)

This fork aims to preserve the strengths of the original project while extending it for scientific and academic use cases.

If you are looking for the original general-purpose project, please see the upstream repository.  
If you are specifically working with papers, formulas, and technical documents, this fork may be a better fit.

---

## Contributing

Contributions are welcome.

Please see [CONTRIBUTING.md](./CONTRIBUTING.md) for contribution guidelines.

When opening issues or pull requests, it helps to specify whether the topic is:

- upstream-compatible behavior
- fork-specific scientific parsing behavior
- formula rendering
- document-to-slide workflow
- installation or environment issues

---

## License

[MIT](./LICENSE)

Original work copyright (c) 2025–2026 Hugo He  
Fork modifications copyright (c) 2026 hongyi

---

## Acknowledgments

- **Original Project**: [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) by [Hugo He](https://github.com/hugohe3)
- [MinerU](https://github.com/opendatalab/MinerU) — document parsing support
- [SVG Repo](https://www.svgrepo.com/)
- [Tabler Icons](https://github.com/tabler/tabler-icons)
- [Simple Icons](https://github.com/simple-icons/simple-icons)

---

## Contact

- **Bug reports / feature requests**: [GitHub Issues](https://github.com/hongyi652/ppt-master-sci-fork/issues)
- **Email**: [877454565@qq.com](mailto:877454565@qq.com)

---

[⬆ Back to Top](#ppt-master-sci-fork--ai-generates-natively-editable-pptx-from-papers-pdfs-and-technical-documents)
