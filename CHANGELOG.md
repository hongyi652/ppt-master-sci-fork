# Changelog

All notable changes to this project will be documented in this file.

This project is forked from [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) (snapshot: 2026-05-22, upstream v2.8.0).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **MinerU document parsing** — integrated [MinerU](https://github.com/opendatalab/MinerU) as the primary document parsing engine for scientific PDFs with complex layouts, tables, and formulas
- **SVG formula rendering** — LaTeX formulas are now rendered as SVG and used directly as PPT planning assets, avoiding rasterization quality loss
- **GUI progress indicator** — added PPT generation progress display in the web UI
- **Generation time display** — show elapsed time during PPT generation
- **Delete project history** — added ability to delete projects from the history list in the web UI

### Fixed

- **Text overlap issue** — resolved a bug where multiple characters/words could stack on top of each other in generated slides
- **Image display truncation** — fixed images not displaying fully in certain layouts
- **Formula stretching** — fixed formulas being incorrectly stretched/distorted when placed in slides

### Changed

- **Optimized call logic** — improved the internal pipeline invocation flow for better reliability

## [Upstream Base] — 2026-05-22

Based on [hugohe3/ppt-master v2.8.0](https://github.com/hugohe3/ppt-master/releases/tag/v2.8.0).
