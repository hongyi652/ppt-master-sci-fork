#!/usr/bin/env python3
"""
PPT Master - LaTeX SVG GUI

Run a small local browser interface for converting one LaTeX formula at a time
to standalone SVG. The GUI reuses latex_to_svg.py and does not change the PPT
generation workflow.

Usage:
    python3 scripts/latex_svg_gui.py [--port 8765] [--output-dir exports/latex_svg]

Examples:
    python3 scripts/latex_svg_gui.py
    python3 scripts/latex_svg_gui.py --open --output-dir projects/demo/images
    python3 scripts/latex_svg_gui.py --check-deps

Dependencies:
    Standard library, plus latex/xelatex/pdflatex and dvisvgm on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from latex_to_svg import (  # noqa: E402
    DEFAULT_BORDER_PT,
    _find_dvisvgm,
    _find_tex_compiler,
    _parse_svg_dimensions,
    annotate_formula_svg,
    compile_formula_to_svg,
)


DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 128 * 1024
MAX_FORMULA_CHARS = 8000
DEFAULT_OUTPUT_DIR = Path("exports") / "latex_svg"
FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


HTML_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LaTeX to SVG</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f8;
      --panel: #ffffff;
      --ink: #202329;
      --muted: #69717d;
      --line: #d9dee5;
      --soft: #edf1f4;
      --accent: #087f7a;
      --accent-strong: #05615e;
      --danger: #b42318;
      --warn: #9a6700;
      --ok: #1f7a3a;
      --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--ink);
      font-family: var(--sans);
      letter-spacing: 0;
    }

    button,
    input,
    textarea {
      font: inherit;
    }

    .app {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }

    .topbar {
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }

    .topbar-inner {
      width: min(1280px, calc(100vw - 32px));
      min-height: 68px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }

    .brand {
      display: flex;
      flex-direction: column;
      gap: 3px;
      min-width: 0;
    }

    .brand h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      font-weight: 720;
    }

    .brand span {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
    }

    .status-strip {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .badge {
      min-height: 28px;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--soft);
      padding: 4px 8px;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }

    .badge.ok {
      border-color: #acd4b7;
      background: #eef9f1;
      color: var(--ok);
    }

    .badge.error {
      border-color: #f0b8b2;
      background: #fff2f0;
      color: var(--danger);
    }

    main {
      width: min(1280px, calc(100vw - 32px));
      margin: 24px auto;
      display: grid;
      grid-template-columns: minmax(340px, 460px) minmax(0, 1fr);
      gap: 20px;
      align-items: stretch;
    }

    .panel {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }

    .controls {
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }

    .section {
      padding: 18px;
      border-bottom: 1px solid var(--line);
    }

    .section:last-child {
      border-bottom: 0;
    }

    label {
      display: block;
      margin: 0 0 8px;
      color: #3b414a;
      font-size: 13px;
      font-weight: 650;
    }

    textarea {
      width: 100%;
      min-height: 238px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      color: var(--ink);
      background: #fbfcfd;
      font-family: var(--mono);
      font-size: 14px;
      line-height: 1.55;
      outline: none;
    }

    textarea:focus,
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(8, 127, 122, 0.16);
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }

    .full {
      grid-column: 1 / -1;
    }

    input[type="text"],
    input[type="number"] {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      color: var(--ink);
      background: #fbfcfd;
      outline: none;
    }

    .segmented {
      display: grid;
      grid-template-columns: 1fr 1fr;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: #fbfcfd;
    }

    .segment {
      min-height: 40px;
      display: grid;
      place-items: center;
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }

    .segment + .segment {
      border-left: 1px solid var(--line);
    }

    .segment input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }

    .segment:has(input:checked) {
      background: #e8f5f3;
      color: var(--accent-strong);
    }

    .checkline {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 40px;
      color: var(--muted);
      font-size: 13px;
    }

    .checkline input {
      width: 16px;
      height: 16px;
      accent-color: var(--accent);
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }

    .button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 9px 13px;
      cursor: pointer;
      font-weight: 680;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }

    .button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }

    .button.primary:hover {
      background: var(--accent-strong);
    }

    .button[disabled],
    .button.disabled {
      cursor: not-allowed;
      opacity: 0.52;
      pointer-events: none;
    }

    .preview {
      display: grid;
      grid-template-rows: auto minmax(420px, 1fr) auto;
      min-height: 620px;
      overflow: hidden;
    }

    .preview-head,
    .result-row {
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
    }

    .result-row {
      border-top: 1px solid var(--line);
      border-bottom: 0;
      justify-content: flex-start;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
    }

    .preview-head h2 {
      margin: 0;
      font-size: 15px;
      line-height: 1.25;
    }

    .canvas {
      min-width: 0;
      min-height: 420px;
      display: grid;
      place-items: center;
      padding: 28px;
      background:
        linear-gradient(90deg, rgba(32, 35, 41, 0.05) 1px, transparent 1px),
        linear-gradient(rgba(32, 35, 41, 0.05) 1px, transparent 1px),
        #fafbfc;
      background-size: 24px 24px;
      overflow: auto;
    }

    .empty {
      width: min(420px, 100%);
      min-height: 132px;
      display: grid;
      place-items: center;
      border: 1px dashed #b8c0ca;
      border-radius: 8px;
      color: var(--muted);
      text-align: center;
      padding: 20px;
      background: rgba(255, 255, 255, 0.82);
    }

    .canvas img {
      display: none;
      max-width: min(720px, 100%);
      max-height: 420px;
      padding: 18px;
      border: 1px solid #ccd3db;
      border-radius: 6px;
      background: #fff;
    }

    .canvas.has-output img {
      display: block;
    }

    .canvas.has-output .empty {
      display: none;
    }

    .log {
      min-height: 24px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      word-break: break-word;
    }

    .log.error {
      color: var(--danger);
    }

    .log.ok {
      color: var(--ok);
    }

    code {
      font-family: var(--mono);
      font-size: 12px;
      color: #343942;
    }

    @media (max-width: 860px) {
      .topbar-inner {
        min-height: 96px;
        align-items: flex-start;
        flex-direction: column;
        justify-content: center;
      }

      .status-strip {
        justify-content: flex-start;
      }

      main {
        grid-template-columns: 1fr;
      }

      .preview {
        min-height: 520px;
      }

      .grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <h1>LaTeX to SVG</h1>
          <span>Local renderer for PPT Master formula assets</span>
        </div>
        <div class="status-strip" id="statusStrip">
          <span class="badge" id="texBadge">TeX checking</span>
          <span class="badge" id="svgBadge">dvisvgm checking</span>
        </div>
      </div>
    </header>

    <main>
      <section class="panel controls">
        <div class="section">
          <label for="formula">Formula</label>
          <textarea id="formula" spellcheck="false">E=mc^2</textarea>
        </div>

        <div class="section">
          <div class="grid">
            <div>
              <label>Mode</label>
              <div class="segmented">
                <label class="segment">
                  <input type="radio" name="mode" value="display" checked>
                  Display
                </label>
                <label class="segment">
                  <input type="radio" name="mode" value="inline">
                  Inline
                </label>
              </div>
            </div>
            <div>
              <label for="border">Border pt</label>
              <input id="border" type="number" min="0" max="30" step="1" value="2">
            </div>
            <div class="full">
              <label for="outputDir">Output directory</label>
              <input id="outputDir" type="text" value="">
            </div>
            <div>
              <label for="filename">File name</label>
              <input id="filename" type="text" value="">
            </div>
            <label class="checkline">
              <input id="overwrite" type="checkbox">
              Overwrite existing file
            </label>
          </div>
        </div>

        <div class="section">
          <div class="actions">
            <button class="button primary" id="renderButton" type="button">Render SVG</button>
            <button class="button" id="copyButton" type="button" disabled>Copy SVG</button>
            <a class="button disabled" id="downloadButton" href="#" download>Download</a>
          </div>
          <div class="log" id="log"></div>
        </div>
      </section>

      <section class="panel preview">
        <div class="preview-head">
          <h2>Preview</h2>
          <span class="badge" id="dimensionBadge">No output</span>
        </div>
        <div class="canvas" id="canvas">
          <div class="empty">No SVG rendered</div>
          <img id="previewImage" alt="Rendered SVG preview">
        </div>
        <div class="result-row" id="resultRow">
          <span>Path: <code id="pathLabel">-</code></span>
          <span>Time: <code id="timeLabel">-</code></span>
        </div>
      </section>
    </main>
  </div>

  <script>
    const formula = document.getElementById("formula");
    const border = document.getElementById("border");
    const outputDir = document.getElementById("outputDir");
    const filename = document.getElementById("filename");
    const overwrite = document.getElementById("overwrite");
    const renderButton = document.getElementById("renderButton");
    const copyButton = document.getElementById("copyButton");
    const downloadButton = document.getElementById("downloadButton");
    const canvas = document.getElementById("canvas");
    const previewImage = document.getElementById("previewImage");
    const dimensionBadge = document.getElementById("dimensionBadge");
    const pathLabel = document.getElementById("pathLabel");
    const timeLabel = document.getElementById("timeLabel");
    const log = document.getElementById("log");
    const texBadge = document.getElementById("texBadge");
    const svgBadge = document.getElementById("svgBadge");

    let currentSvg = "";
    let currentBlobUrl = "";

    function modeValue() {
      return document.querySelector("input[name='mode']:checked").value;
    }

    function setLog(message, kind = "") {
      log.textContent = message || "";
      log.className = kind ? `log ${kind}` : "log";
    }

    function setBusy(isBusy) {
      renderButton.disabled = isBusy;
      renderButton.textContent = isBusy ? "Rendering" : "Render SVG";
    }

    function setBadge(el, label, ok) {
      el.textContent = label;
      el.className = ok ? "badge ok" : "badge error";
    }

    async function refreshStatus() {
      try {
        const resp = await fetch("/api/status");
        const data = await resp.json();
        outputDir.value = data.default_output_dir || "";
        setBadge(texBadge, data.tex.available ? `TeX: ${data.tex.command}` : "TeX missing",
          data.tex.available);
        setBadge(svgBadge,
          data.dvisvgm.available ? `dvisvgm: ${data.dvisvgm.command}` : "dvisvgm missing",
          data.dvisvgm.available);
      } catch (err) {
        setBadge(texBadge, "TeX unknown", false);
        setBadge(svgBadge, "dvisvgm unknown", false);
      }
    }

    async function renderFormula() {
      const payload = {
        formula: formula.value,
        mode: modeValue(),
        border: Number(border.value || 2),
        output_dir: outputDir.value,
        filename: filename.value,
        overwrite: overwrite.checked,
      };

      setBusy(true);
      setLog("");

      try {
        const resp = await fetch("/api/render", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || !data.ok) {
          throw new Error(data.error || "Render failed");
        }

        currentSvg = data.svg;
        if (currentBlobUrl) {
          URL.revokeObjectURL(currentBlobUrl);
        }
        currentBlobUrl = URL.createObjectURL(new Blob([data.svg], { type: "image/svg+xml" }));
        previewImage.src = currentBlobUrl;
        canvas.classList.add("has-output");
        copyButton.disabled = false;
        downloadButton.classList.remove("disabled");
        downloadButton.href = data.download_url;
        downloadButton.download = data.filename;
        dimensionBadge.textContent = `${data.width || "?"} x ${data.height || "?"}`;
        dimensionBadge.className = "badge ok";
        pathLabel.textContent = data.output_path;
        timeLabel.textContent = `${data.duration_ms} ms`;
        setLog("SVG rendered", "ok");
      } catch (err) {
        setLog(err.message, "error");
      } finally {
        setBusy(false);
      }
    }

    async function copySvg() {
      if (!currentSvg) {
        return;
      }
      await navigator.clipboard.writeText(currentSvg);
      setLog("SVG copied", "ok");
    }

    renderButton.addEventListener("click", renderFormula);
    copyButton.addEventListener("click", copySvg);
    refreshStatus();
  </script>
</body>
</html>
"""


@dataclass
class RenderRecord:
    """Track one generated file for safe download by token."""

    path: Path
    created_at: float


class LatexSvgHTTPServer(ThreadingHTTPServer):
    """HTTP server with app-specific shared state."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        base_dir: Path,
        output_dir: Path,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        self.base_dir = base_dir
        self.output_dir = output_dir
        self.records: dict[str, RenderRecord] = {}


class LatexSvgHandler(BaseHTTPRequestHandler):
    """Serve the HTML app and conversion API."""

    server: LatexSvgHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[latex-svg-gui] {self.address_string()} - {fmt % args}", file=sys.stderr)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route in {"/", "/index.html"}:
            self._send_text(HTML_PAGE, "text/html; charset=utf-8")
            return
        if route == "/api/status":
            self._send_json(build_status(self.server.output_dir))
            return
        if route.startswith("/download/"):
            self._handle_download(route.removeprefix("/download/"))
            return
        self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/api/render":
            self._handle_render()
            return
        self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _handle_render(self) -> None:
        started = time.perf_counter()
        try:
            payload = self._read_json()
            request = parse_render_request(payload, self.server.base_dir, self.server.output_dir)
            result_path = compile_formula_to_svg(
                request["formula"],
                request["output_path"],
                display=request["display"],
                border_pt=request["border"],
            )
            annotate_formula_svg(
                result_path,
                formula_id=result_path.stem,
                latex=request["formula"],
                display=request["display"],
            )
            width, height = _parse_svg_dimensions(result_path)
            svg_text = result_path.read_text(encoding="utf-8", errors="replace")
            token = hashlib.sha1(
                f"{result_path}|{time.time_ns()}".encode("utf-8")
            ).hexdigest()[:20]
            self.server.records[token] = RenderRecord(path=result_path, created_at=time.time())
            self._send_json(
                {
                    "ok": True,
                    "svg": svg_text,
                    "filename": result_path.name,
                    "output_path": str(result_path),
                    "download_url": f"/download/{token}",
                    "width": width,
                    "height": height,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                }
            )
        except ValueError as exc:
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _handle_download(self, token: str) -> None:
        record = self.server.records.get(token)
        if record is None or not record.path.is_file():
            self._send_json({"ok": False, "error": "File not found"}, HTTPStatus.NOT_FOUND)
            return
        data = record.path.read_bytes()
        quoted_name = urllib.parse.quote(record.path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted_name}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length <= 0:
            raise ValueError("Request body is empty.")
        if length > MAX_REQUEST_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _send_text(
        self,
        text: str,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def build_status(default_output_dir: Path) -> dict[str, Any]:
    """Return TeX tool availability for the UI."""
    tex = _detect_tool(_find_tex_compiler)
    dvisvgm = _detect_tool(_find_dvisvgm)
    return {
        "ok": bool(tex["available"] and dvisvgm["available"]),
        "default_output_dir": str(default_output_dir),
        "tex": tex,
        "dvisvgm": dvisvgm,
    }


def parse_render_request(
    payload: dict[str, Any],
    base_dir: Path,
    default_output_dir: Path,
) -> dict[str, Any]:
    """Validate the render request and return normalized values."""
    formula = str(payload.get("formula", "")).strip()
    if not formula:
        raise ValueError("Formula is required.")
    if len(formula) > MAX_FORMULA_CHARS:
        raise ValueError(f"Formula is too long; limit is {MAX_FORMULA_CHARS} characters.")

    mode = str(payload.get("mode", "display")).strip().lower()
    if mode not in {"display", "inline"}:
        raise ValueError("Mode must be display or inline.")

    try:
        border = int(payload.get("border", DEFAULT_BORDER_PT))
    except (TypeError, ValueError) as exc:
        raise ValueError("Border must be an integer.") from exc
    if border < 0 or border > 30:
        raise ValueError("Border must be between 0 and 30.")

    output_dir = _resolve_output_dir(
        str(payload.get("output_dir") or ""),
        base_dir,
        default_output_dir,
    )
    filename = _sanitize_filename(str(payload.get("filename") or ""), formula)
    output_path = output_dir / filename
    if not bool(payload.get("overwrite", False)):
        output_path = _dedupe_path(output_path)

    return {
        "formula": formula,
        "display": mode == "display",
        "border": border,
        "output_path": output_path,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Run a local GUI for converting LaTeX formulas to SVG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Default: 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind. Default: {DEFAULT_PORT}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Default SVG output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the GUI in the system browser after startup.",
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check TeX/dvisvgm availability and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    base_dir = Path.cwd().resolve()
    output_dir = _resolve_output_dir(args.output_dir, base_dir, base_dir / DEFAULT_OUTPUT_DIR)

    if args.check_deps:
        status = build_status(output_dir)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status["ok"] else 1

    try:
        server = create_server(args.host, args.port, base_dir, output_dir)
    except OSError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    host, port = server.server_address[:2]
    url = f"http://{_display_host(str(host))}:{port}/"
    print(f"[OK] LaTeX SVG GUI running at {url}", file=sys.stderr)
    print(f"[OK] Default output directory: {output_dir}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)

    if args.open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] Stopping LaTeX SVG GUI.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def create_server(
    host: str,
    preferred_port: int,
    base_dir: Path,
    output_dir: Path,
) -> LatexSvgHTTPServer:
    """Bind a server, trying nearby ports when the preferred one is busy."""
    if preferred_port <= 0 or preferred_port > 65535:
        raise OSError("Port must be between 1 and 65535.")

    last_error: OSError | None = None
    for port in range(preferred_port, min(preferred_port + 20, 65536)):
        try:
            return LatexSvgHTTPServer(
                (host, port),
                LatexSvgHandler,
                base_dir=base_dir,
                output_dir=output_dir,
            )
        except OSError as exc:
            last_error = exc
            continue
    raise OSError(f"Could not bind {host}:{preferred_port}: {last_error}")


def _detect_tool(finder: Any) -> dict[str, Any]:
    """Run one tool finder and normalize the result."""
    try:
        command = finder()
        return {"available": True, "command": command, "error": ""}
    except RuntimeError as exc:
        return {"available": False, "command": "", "error": str(exc)}


def _resolve_output_dir(value: str, base_dir: Path, default_output_dir: Path) -> Path:
    """Resolve an output directory relative to the launch directory."""
    raw = value.strip()
    path = Path(raw).expanduser() if raw else default_output_dir
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _sanitize_filename(value: str, formula: str) -> str:
    """Return a safe SVG file name."""
    raw = value.strip()
    if not raw:
        digest = hashlib.sha1(formula.encode("utf-8")).hexdigest()[:8]
        raw = f"formula_{time.strftime('%Y%m%d_%H%M%S')}_{digest}.svg"
    clean = FILENAME_RE.sub("_", raw).strip("._")
    if not clean:
        clean = "formula.svg"
    if not clean.lower().endswith(".svg"):
        clean += ".svg"
    return clean


def _dedupe_path(path: Path) -> Path:
    """Return a non-existing sibling path by appending a numeric suffix."""
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    digest = hashlib.sha1(f"{path}|{time.time_ns()}".encode("utf-8")).hexdigest()[:8]
    return path.with_name(f"{path.stem}_{digest}{path.suffix}")


def _display_host(host: str) -> str:
    """Return a browser-friendly host label."""
    if host in {"", "0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


if __name__ == "__main__":
    raise SystemExit(main())
