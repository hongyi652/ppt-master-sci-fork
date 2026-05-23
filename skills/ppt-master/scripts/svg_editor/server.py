#!/usr/bin/env python3
"""
PPT Master - SVG Editor Server

Flask backend for the SVG annotation editor.
Serves the web UI and provides API endpoints for reading/writing SVG annotations.

Usage:
    python3 scripts/svg_editor/server.py <project_dir>

Examples:
    python3 scripts/svg_editor/server.py projects/my-project
    python3 scripts/svg_editor/server.py projects/my-project --port 8080
    python3 scripts/svg_editor/server.py projects/my-project --live

Dependencies:
    flask>=3.0.0
"""

import argparse
import atexit
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

logger = logging.getLogger('svg_editor')

# Per-project lock file. Lives at <project_path>/.live_preview.lock and
# matches the *.lock entry already in the repo .gitignore.
LOCK_FILE_NAME = '.live_preview.lock'
GLOBAL_LOCK_FILE = Path.home() / '.ppt-master' / 'live_preview_global.lock'

# Local — sys.path injection for sibling module (code-style.md §3)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_FINALIZE_DIR = _SCRIPTS_DIR.parent / 'svg_finalize'
if str(_FINALIZE_DIR) not in sys.path:
    sys.path.insert(0, str(_FINALIZE_DIR))

from annotations import (  # noqa: E402
    assign_temp_ids,
    parse_annotations,
    set_annotation,
    remove_annotation,
)
from embed_icons import (  # noqa: E402
    parse_use_element,
    resolve_icon_path,
    extract_paths_from_icon,
    generate_icon_group,
)

_ICONS_DIR = _SCRIPTS_DIR.parent.parent / 'templates' / 'icons'
_USE_ICON_PATTERN = re.compile(r'<use\s+[^>]*data-icon="[^"]*"[^>]*/>')

# Per-path mtime caches: key = absolute path str, value = (mtime, payload).
# Entry is evicted/replaced when the file's mtime changes, so stale data
# cannot leak. Locks guard concurrent access under Flask's threaded server.
_SLIDE_CACHE_LOCK = threading.Lock()
_SLIDE_CACHE: dict = {}  # path -> (mtime, (content, warnings))

_LIST_CACHE_LOCK = threading.Lock()
_LIST_CACHE: dict = {}  # path -> (mtime, annotation_count_on_disk)

_PAGE_REF_RE = re.compile(r'^\s*-\s*P(\d+)\s*:', re.MULTILINE)
_PAGE_COUNT_RE = re.compile(r'\|\s*\*\*Page Count\*\*\s*\|\s*([^|\r\n]+)\|', re.IGNORECASE)


def _cache_get(cache: dict, lock: threading.Lock, path: str, mtime: float):
    with lock:
        entry = cache.get(path)
        if entry is None or entry[0] != mtime:
            return None
        return entry[1]


def _cache_put(cache: dict, lock: threading.Lock, path: str, mtime: float, value) -> None:
    with lock:
        cache[path] = (mtime, value)


def _preview_url(port: int) -> str:
    """Return the canonical local preview URL for *port*."""
    return f'http://127.0.0.1:{port}/?preview={int(time.time())}'


def _process_alive(pid: int) -> bool:
    """Return True if a process with this pid is reachable.

    ``os.kill(pid, 0)`` succeeds when the process exists even without
    permission to signal it; ``PermissionError`` therefore still counts
    as alive (a real lock holder owned by another user). ``ESRCH`` /
    other ``OSError`` means the pid is gone.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(lock_file: Path) -> Optional[dict]:
    try:
        data = json.loads(lock_file.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _claim_lock(lock_file: Path, payload: dict) -> Optional[dict]:
    """Try to claim a preview lock file.

    Returns ``None`` on success. If another live process already holds the
    slot, returns the existing lock dict (caller surfaces it as an error).
    A stale lock (pointing at a dead pid) is silently overwritten.
    """
    existing = _read_lock(lock_file)
    if existing and _process_alive(int(existing.get('pid', 0))):
        return existing
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        json.dumps(payload),
        encoding='utf-8',
    )
    return None


def _write_lock(lock_file: Path, payload: dict) -> None:
    """Write a lock payload unconditionally."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(payload), encoding='utf-8')


def _release_lock(lock_file: Path) -> None:
    """Best-effort cleanup: only delete the lock if it still names *us*."""
    try:
        current = _read_lock(lock_file)
        if current and int(current.get('pid', 0)) == os.getpid():
            lock_file.unlink(missing_ok=True)
    except OSError:
        pass


def _pick_available_port(preferred_port: int, *, host: str = '127.0.0.1', attempts: int = 32) -> int:
    """Return the first bindable port starting at ``preferred_port``."""
    for port in range(preferred_port, preferred_port + max(1, attempts)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f'No free port found in range {preferred_port}-{preferred_port + max(1, attempts) - 1}.'
    )


def _request_preview_shutdown(port: int, reason: str, *, timeout: float = 2.0) -> bool:
    """Ask a running preview server on *port* to shut down."""
    payload = json.dumps({'reason': reason}).encode('utf-8')
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/shutdown',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= getattr(response, 'status', 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_for_process_exit(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    """Wait briefly for *pid* to exit."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.1)
    return not _process_alive(pid)


def _find_latest_pptx(exports_dir: Path) -> Optional[Path]:
    """Return the newest exported PPTX in ``exports_dir`` if present."""
    latest: Optional[Path] = None
    latest_mtime = float('-inf')
    for candidate in exports_dir.glob('*.pptx'):
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest_mtime:
            latest = candidate
            latest_mtime = mtime
    return latest


def _read_text(path: Path) -> str:
    """Read a UTF-8 text file best-effort, returning an empty string on failure."""
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def _safe_mtime(path: Path) -> float:
    """Return the file mtime or 0 when the file is missing/unreadable."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _count_files(directory: Path, pattern: str) -> int:
    """Count matching files in *directory*."""
    if not directory.exists():
        return 0
    return sum(1 for path in directory.glob(pattern) if path.is_file())


def _newest_file_mtime(directory: Path, pattern: str) -> float:
    """Return the newest mtime among matching files in *directory*."""
    newest = 0.0
    if not directory.exists():
        return newest
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        newest = max(newest, _safe_mtime(path))
    return newest


def _markdown_sections(text: str) -> dict[str, str]:
    """Split a simple markdown file by second-level headings."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith('## '):
            current = line[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {name: '\n'.join(lines) for name, lines in sections.items()}


def _infer_total_pages(project_path: Path) -> int:
    """Infer total deck pages from spec_lock/design_spec and generated artifacts."""
    candidates: list[int] = []

    spec_lock_path = project_path / 'spec_lock.md'
    spec_lock_text = _read_text(spec_lock_path)
    if spec_lock_text:
        sections = _markdown_sections(spec_lock_text)
        for section_name in ('page_rhythm', 'page_layouts', 'page_charts'):
            section_body = sections.get(section_name, '')
            if not section_body:
                continue
            page_refs = [int(match) for match in _PAGE_REF_RE.findall(section_body)]
            if page_refs:
                candidates.append(max(page_refs))

    design_spec_path = project_path / 'design_spec.md'
    design_spec_text = _read_text(design_spec_path)
    if design_spec_text:
        page_count_match = _PAGE_COUNT_RE.search(design_spec_text)
        if page_count_match:
            numeric_bits = [int(part) for part in re.findall(r'\d+', page_count_match.group(1))]
            if numeric_bits:
                candidates.append(max(numeric_bits))

    notes_dir = project_path / 'notes'
    split_note_count = _count_files(notes_dir, '[0-9][0-9]_*.md')
    if split_note_count:
        candidates.append(split_note_count)

    svg_output_dir = project_path / 'svg_output'
    svg_output_count = _count_files(svg_output_dir, '*.svg')
    if svg_output_count:
        candidates.append(svg_output_count)

    svg_final_dir = project_path / 'svg_final'
    svg_final_count = _count_files(svg_final_dir, '*.svg')
    if svg_final_count:
        candidates.append(svg_final_count)

    return max(candidates, default=0)


def _build_progress_payload(project_path: Path, exports_dir: Path) -> dict[str, object]:
    """Summarize current PPT generation progress for the live-preview UI."""
    sources_dir = project_path / 'sources'
    images_dir = project_path / 'images'
    notes_dir = project_path / 'notes'
    svg_output_dir = project_path / 'svg_output'
    svg_final_dir = project_path / 'svg_final'
    design_spec_path = project_path / 'design_spec.md'
    spec_lock_path = project_path / 'spec_lock.md'

    total_pages = _infer_total_pages(project_path)
    source_count = sum(1 for path in sources_dir.iterdir() if path.is_file()) if sources_dir.exists() else 0
    slides_generated = _count_files(svg_output_dir, '*.svg')
    slides_finalized = _count_files(svg_final_dir, '*.svg')
    split_note_count = _count_files(notes_dir, '[0-9][0-9]_*.md')
    notes_ready = (notes_dir / 'total.md').is_file()
    design_spec_ready = design_spec_path.is_file()
    spec_lock_ready = spec_lock_path.is_file()
    has_image_phase = any(
        (images_dir / name).is_file()
        for name in ('image_prompts.json', 'image_sources.json', 'formula_manifest.json')
    )

    latest_export = _find_latest_pptx(exports_dir) if exports_dir.exists() else None
    export_count = _count_files(exports_dir, '*.pptx')
    latest_export_name = latest_export.name if latest_export else ''
    notes_mtime = _safe_mtime(notes_dir / 'total.md')
    newest_notes_split_mtime = _newest_file_mtime(notes_dir, '[0-9][0-9]_*.md')
    newest_svg_output_mtime = _newest_file_mtime(svg_output_dir, '*.svg')
    newest_svg_final_mtime = _newest_file_mtime(svg_final_dir, '*.svg')
    latest_export_mtime = _safe_mtime(latest_export) if latest_export else 0.0
    latest_work_mtime = max(
        _safe_mtime(design_spec_path),
        _safe_mtime(spec_lock_path),
        notes_mtime,
        newest_notes_split_mtime,
        newest_svg_output_mtime,
        newest_svg_final_mtime,
    )
    slides_complete = slides_generated > 0 and (total_pages == 0 or slides_generated >= total_pages)
    notes_current = notes_ready and slides_complete and notes_mtime >= newest_svg_output_mtime
    final_current = (
        slides_finalized > 0
        and notes_current
        and newest_svg_final_mtime >= max(notes_mtime, newest_svg_output_mtime)
    )
    export_is_current = (
        latest_export is not None
        and final_current
        and latest_export_mtime >= max(newest_svg_final_mtime, latest_work_mtime)
    )

    if export_is_current:
        stage = 'completed'
        percent = 100
    elif slides_finalized > 0 and newest_svg_final_mtime >= newest_svg_output_mtime:
        denom = max(total_pages or slides_generated or slides_finalized, 1)
        finalized_ratio = min(1.0, slides_finalized / denom)
        stage = 'exporting_pptx' if slides_finalized >= denom else 'finalizing_svg'
        percent = min(99, round(90 + finalized_ratio * 8))
    elif notes_current:
        stage = 'finalizing_svg'
        percent = 88
    elif slides_generated > 0:
        if slides_complete:
            stage = 'generating_notes'
            percent = 82
        else:
            denom = max(total_pages or slides_generated, 1)
            stage = 'generating_slides'
            percent = min(80, round(20 + min(slides_generated, denom) / denom * 60))
    elif has_image_phase:
        stage = 'acquiring_assets'
        percent = 24 if spec_lock_ready or design_spec_ready else 16
    elif spec_lock_ready or design_spec_ready:
        stage = 'planning_deck'
        percent = 18 if spec_lock_ready else 12
    elif source_count > 0:
        stage = 'preparing_sources'
        percent = 5
    else:
        stage = 'initializing'
        percent = 0

    return {
        'project_name': project_path.name,
        'project_path': str(project_path),
        'stage': stage,
        'percent': percent,
        'total_pages': total_pages,
        'source_count': source_count,
        'slides_generated': slides_generated,
        'slides_finalized': slides_finalized,
        'split_note_count': split_note_count,
        'notes_ready': notes_ready,
        'design_spec_ready': design_spec_ready,
        'spec_lock_ready': spec_lock_ready,
        'exports_count': export_count,
        'latest_export_name': latest_export_name,
    }


def _open_in_file_manager(folder: Path, *, select: Optional[Path] = None) -> None:
    """Open *folder* in the platform file manager, selecting *select* when possible."""
    folder = folder.resolve()
    if select is not None:
        select = select.resolve()

    if sys.platform.startswith('win'):
        if select is not None:
            subprocess.Popen(['explorer.exe', '/select,', str(select)])
        else:
            subprocess.Popen(['explorer.exe', str(folder)])
        return

    if sys.platform == 'darwin':
        cmd = ['open', '-R', str(select)] if select is not None else ['open', str(folder)]
        subprocess.Popen(cmd)
        return

    subprocess.Popen(['xdg-open', str(folder)])


def _inline_icons(content: str) -> tuple[str, list[dict]]:
    """Replace <use data-icon="..."/> with rendered <g> for browser preview.

    Returns (rewritten_content, warnings). Each warning is
    ``{"icon": <name>, "reason": <str>}`` so the frontend can surface
    "icon X not found" to the user instead of silently dropping it.
    """
    warnings: list[dict] = []
    matches = list(_USE_ICON_PATTERN.finditer(content))
    if not matches:
        return content, warnings
    new_content = content
    for match in reversed(matches):
        use_str = match.group(0)
        icon_name: str = ''
        try:
            attrs = parse_use_element(use_str)
            icon_name = str(attrs.get('icon') or '')
            if not icon_name:
                warnings.append({'icon': '', 'reason': 'missing data-icon attribute'})
                continue
            icon_path, _ = resolve_icon_path(icon_name, _ICONS_DIR)
            color = str(attrs.get('fill', '#000000'))
            elements, style, base_size = extract_paths_from_icon(icon_path, color)
        except Exception as exc:
            warnings.append({'icon': icon_name, 'reason': f'{type(exc).__name__}: {exc}'})
            logger.warning('icon inline failed: name=%r reason=%s', icon_name, exc)
            continue
        if not elements:
            warnings.append({'icon': icon_name, 'reason': 'no renderable paths in icon'})
            continue
        replacement = generate_icon_group(attrs, elements, style, base_size)
        id_match = re.search(r'\bid="([^"]+)"', use_str)
        if id_match:
            replacement = replacement.replace(
                '<g ', f'<g id="{id_match.group(1)}" data-icon="{icon_name}" ', 1,
            )
        new_content = new_content[:match.start()] + replacement + new_content[match.end():]
    return new_content, warnings


def create_app(
    project_dir: str,
    idle_timeout: int = 900,
    live: bool = False,
    lock_files: tuple[Path, ...] = (),
) -> Flask:
    """Create and configure the Flask app for a given project directory."""
    project_path = Path(project_dir).resolve()
    svg_dir = project_path / 'svg_output'
    images_dir = project_path / 'images'
    assets_dir = project_path / 'assets'
    exports_dir = project_path / 'exports'

    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.config['PROJECT_PATH'] = project_path
    app.config['PROJECT_NAME'] = project_path.name
    app.config['SVG_DIR'] = svg_dir
    app.config['EXPORTS_DIR'] = exports_dir
    app.config['LIVE_MODE'] = live
    app.config['LOCK_FILES'] = tuple(lock_files)

    # In-memory annotation store: {filename: {element_id: annotation_text}}
    app.config['ANNOTATIONS'] = {}

    # Idle timeout: auto-shutdown if no one connects within idle_timeout seconds
    app.config['LAST_REQUEST_TIME'] = time.time()

    @app.before_request
    def _update_activity():
        app.config['LAST_REQUEST_TIME'] = time.time()

    @app.after_request
    def _disable_response_cache(response):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    def _exit_with_lock_release(code: int = 0) -> None:
        for lf in app.config.get('LOCK_FILES', ()):
            _release_lock(lf)
        # os._exit: atexit handlers do not run; we just released the lock
        # manually above so a clean restart still finds the slot free.
        os._exit(code)

    def _idle_watchdog():
        if idle_timeout <= 0:
            return
        while True:
            time.sleep(10)
            elapsed = time.time() - app.config['LAST_REQUEST_TIME']
            if elapsed > idle_timeout:
                logger.info('idle for %ds, shutting down', idle_timeout)
                # Flask dev server has no clean shutdown; data is safe
                # because idle timeout only fires when no requests are in flight.
                _exit_with_lock_release(0)

    watchdog = threading.Thread(target=_idle_watchdog, daemon=True)
    watchdog.start()

    @app.route('/api/shutdown', methods=['POST'])
    def shutdown():
        data = request.get_json(silent=True) or {}
        reason = data.get('reason') or 'shutdown'

        def _stop():
            time.sleep(0.5)  # Let HTTP response flush before killing the process
            logger.info('shutting down (%s)', reason)
            # os._exit: save-all already wrote to disk; 0.5s delay ensures response is sent.
            _exit_with_lock_release(0)
        threading.Thread(target=_stop, daemon=True).start()
        return jsonify({'status': 'ok'})

    @app.route('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.route('/api/config')
    def get_config():
        latest_pptx = _find_latest_pptx(exports_dir) if exports_dir.exists() else None
        return jsonify({
            'live': app.config['LIVE_MODE'],
            'project_name': app.config['PROJECT_NAME'],
            'project_path': str(project_path),
            'exports_dir': str(exports_dir),
            'latest_export_name': latest_pptx.name if latest_pptx else '',
        })

    @app.route('/api/progress')
    def get_progress():
        return jsonify(_build_progress_payload(project_path, exports_dir))

    @app.route('/api/open-export-folder', methods=['POST'])
    def open_export_folder():
        exports_dir.mkdir(parents=True, exist_ok=True)
        latest_pptx = _find_latest_pptx(exports_dir)
        try:
            _open_in_file_manager(exports_dir, select=latest_pptx)
        except Exception as exc:
            logger.warning('open export folder failed: %s', exc)
            return jsonify({'error': f'Failed to open export folder: {exc}'}), 500
        return jsonify({
            'status': 'ok',
            'exports_dir': str(exports_dir),
            'selected_file': latest_pptx.name if latest_pptx else '',
        })

    @app.route('/images/<path:filename>')
    def serve_image(filename: str):
        """Serve images referenced by SVGs as `../images/*.png`.

        Resolution against an absolute images_dir + relative_to() check is the
        authoritative path-traversal guard.
        """
        if not images_dir.exists():
            return jsonify({'error': 'images directory not found'}), 404
        target = (images_dir / filename).resolve()
        try:
            target.relative_to(images_dir.resolve())
        except ValueError:
            return jsonify({'error': 'invalid path'}), 400
        if not target.exists() or not target.is_file():
            return jsonify({'error': 'not found'}), 404
        return send_from_directory(str(images_dir), filename)

    @app.route('/assets/<path:filename>')
    def serve_asset(filename: str):
        """Serve media extracted by pptx_to_svg.py as `../assets/*`."""
        if not assets_dir.exists():
            return jsonify({'error': 'assets directory not found'}), 404
        target = (assets_dir / filename).resolve()
        try:
            target.relative_to(assets_dir.resolve())
        except ValueError:
            return jsonify({'error': 'invalid path'}), 400
        if not target.exists() or not target.is_file():
            return jsonify({'error': 'not found'}), 404
        return send_from_directory(str(assets_dir), filename)

    @app.route('/api/slides')
    def get_slides():
        svg_dir = app.config['SVG_DIR']
        if not svg_dir.exists():
            return jsonify({
                'project_name': app.config['PROJECT_NAME'],
                'project_path': str(project_path),
                'slides': [],
            })

        annotations = app.config['ANNOTATIONS']
        slides = []
        for svg_file in sorted(svg_dir.glob('*.svg')):
            path_str = str(svg_file)
            try:
                mtime = svg_file.stat().st_mtime
            except OSError as exc:
                logger.warning('stat failed: %s: %s', path_str, exc)
                continue

            ok = True
            error_msg: Optional[str] = None
            disk_count = _cache_get(_LIST_CACHE, _LIST_CACHE_LOCK, path_str, mtime)
            if disk_count is None:
                try:
                    tree = ET.parse(path_str)
                    disk_count = len(parse_annotations(tree.getroot()))
                except ET.ParseError as exc:
                    ok = False
                    error_msg = f'XML parse error: {exc}'
                    disk_count = 0
                    logger.warning('slide parse failed: %s: %s', svg_file.name, exc)
                _cache_put(_LIST_CACHE, _LIST_CACHE_LOCK, path_str, mtime, disk_count)

            mem_count = len(annotations.get(svg_file.name, {}))
            annotation_count = max(disk_count, mem_count)

            slides.append({
                'name': svg_file.name,
                'annotated': annotation_count > 0,
                'annotation_count': annotation_count,
                'ok': ok,
                'error': error_msg,
                'mtime': mtime,
            })

        return jsonify({
            'project_name': app.config['PROJECT_NAME'],
            'project_path': str(project_path),
            'slides': slides,
        })

    def _safe_svg_path(name: str):
        """Validate slide name and return safe path. Returns None if invalid.

        The early string checks reject obvious bad inputs; the resolve()+startswith()
        check is the authoritative path traversal guard.
        """
        if '/' in name or '\\' in name or '..' in name:
            return None
        svg_file = (svg_dir / name).resolve()
        if not str(svg_file).startswith(str(svg_dir.resolve())):
            return None
        return svg_file

    @app.route('/api/slide/<name>')
    def get_slide(name: str):
        svg_file = _safe_svg_path(name)
        if svg_file is None:
            return jsonify({'error': 'Invalid slide name'}), 400
        if not svg_file.exists():
            return jsonify({'error': 'Slide not found'}), 404

        path_str = str(svg_file)
        try:
            mtime = svg_file.stat().st_mtime
        except OSError as exc:
            logger.warning('stat failed: %s: %s', path_str, exc)
            return jsonify({'error': f'Failed to stat SVG: {exc}'}), 500

        cached = _cache_get(_SLIDE_CACHE, _SLIDE_CACHE_LOCK, path_str, mtime)
        if cached is not None:
            content, warnings, disk_annotations, id_to_tag = cached
        else:
            try:
                tree = ET.parse(path_str)
                root = tree.getroot()
            except ET.ParseError as exc:
                logger.warning('slide parse failed: %s: %s', name, exc)
                return jsonify({'error': f'Failed to parse SVG: {exc}'}), 500

            assign_temp_ids(root)
            disk_annotations = parse_annotations(root)
            id_to_tag: dict[str, str] = {}
            for elem in root.iter():
                eid = elem.get('id')
                if eid:
                    tag = elem.tag
                    if '}' in tag:
                        tag = tag.split('}', 1)[1]
                    id_to_tag[eid] = tag
            content = ET.tostring(root, encoding='unicode', xml_declaration=False)
            content, warnings = _inline_icons(content)
            _cache_put(
                _SLIDE_CACHE, _SLIDE_CACHE_LOCK, path_str, mtime,
                (content, warnings, disk_annotations, id_to_tag),
            )

        mem_annotations = app.config['ANNOTATIONS'].get(name, {})
        merged: dict[str, str] = {}
        for ann in disk_annotations:
            merged[ann['element_id']] = ann['annotation']
        merged.update(mem_annotations)

        annotations_list = [
            {
                'element_id': eid,
                'tag': id_to_tag.get(eid, ''),
                'annotation': ann_text,
            }
            for eid, ann_text in merged.items()
        ]

        return jsonify({
            'name': name,
            'content': content,
            'annotations': annotations_list,
            'warnings': warnings,
            'mtime': mtime,
        })

    @app.route('/api/slide/<name>/annotate', methods=['POST'])
    def post_annotate(name: str):
        data = request.get_json()
        if not data or 'element_id' not in data or 'annotation' not in data:
            return jsonify({'error': 'Missing element_id or annotation'}), 400

        element_id = data['element_id']
        annotation = data['annotation']

        if not isinstance(element_id, str) or not isinstance(annotation, str):
            return jsonify({'error': 'element_id and annotation must be strings'}), 400

        if len(element_id) > 200:
            return jsonify({'error': 'element_id too long (max 200 chars)'}), 400

        if len(annotation) > 10000:
            return jsonify({'error': 'Annotation too long (max 10000 chars)'}), 400

        if name not in app.config['ANNOTATIONS']:
            app.config['ANNOTATIONS'][name] = {}

        app.config['ANNOTATIONS'][name][element_id] = annotation

        return jsonify({
            'status': 'ok',
            'annotations_count': len(app.config['ANNOTATIONS'][name]),
        })

    @app.route('/api/slide/<name>/annotate/<element_id>', methods=['DELETE'])
    def delete_annotate(name: str, element_id: str):
        annotations = app.config['ANNOTATIONS']
        # Ensure the file key exists so save-all knows to rewrite this file
        # even if no new annotations were added (pure delete path).
        if name not in annotations:
            annotations[name] = {}
        if element_id in annotations[name]:
            del annotations[name][element_id]

        return jsonify({
            'status': 'ok',
            'annotations_count': len(annotations.get(name, {})),
        })

    @app.route('/api/save-all', methods=['POST'])
    def save_all():
        annotations = app.config['ANNOTATIONS']
        svg_dir = app.config['SVG_DIR']
        modified = []

        for filename, anns in annotations.items():
            # anns may be empty when the user deleted all annotations — still
            # need to write so the on-disk data-edit-* attributes are cleared.

            svg_file = _safe_svg_path(filename)
            if svg_file is None or not svg_file.exists():
                continue

            try:
                tree = ET.parse(str(svg_file))
                root = tree.getroot()
            except ET.ParseError:
                continue

            assign_temp_ids(root)

            # Clear all existing annotations from the file before writing current state
            for elem in root.iter():
                elem.attrib.pop('data-edit-target', None)
                elem.attrib.pop('data-edit-annotation', None)

            for element_id, annotation_text in anns.items():
                set_annotation(root, element_id, annotation_text)

            # Strip transient _edit_N ids from elements that are NOT user-annotated.
            # Only annotated elements need to keep their id so the AI can locate them
            # via check_annotations.py; the rest are pollution.
            annotated_ids = set(anns.keys())
            for elem in root.iter():
                eid = elem.get('id', '')
                if eid.startswith('_edit_') and eid not in annotated_ids:
                    elem.attrib.pop('id', None)

            tree.write(str(svg_file), encoding='UTF-8', xml_declaration=True)
            modified.append(filename)

        app.config['ANNOTATIONS'] = {}

        return jsonify({'status': 'ok', 'files_modified': modified})

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='PPT Master SVG Editor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('project_dir', help='Path to project directory (contains svg_output/)')
    parser.add_argument('--port', type=int, default=5050, help='Port to listen on (default: 5050)')
    parser.add_argument('--no-browser', action='store_true', help='Do not auto-open browser')
    parser.add_argument(
        '--live',
        action='store_true',
        help='Run as Executor live preview: allow empty svg_output/ and keep serving after annotation submit',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=None,
        help='Idle timeout in seconds (default: 900; live mode default: 7200; 0 = disabled)',
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [%(levelname)s] svg_editor: %(message)s',
        datefmt='%H:%M:%S',
    )

    project_path = Path(args.project_dir).resolve()
    svg_output = project_path / 'svg_output'
    if not svg_output.exists():
        if args.live:
            svg_output.mkdir(parents=True, exist_ok=True)
        else:
            logger.error('%s does not exist', svg_output)
            return 1
    elif not svg_output.is_dir():
        logger.error('%s is not a directory', svg_output)
        return 1

    global_existing = _read_lock(GLOBAL_LOCK_FILE)
    if global_existing and _process_alive(int(global_existing.get('pid', 0))):
        existing_pid = int(global_existing.get('pid', 0))
        existing_port = int(global_existing.get('port', args.port))
        existing_project = str(global_existing.get('project_path') or '')
        try:
            same_project = Path(existing_project).resolve() == project_path
        except OSError:
            same_project = False

        if same_project:
            existing_url = _preview_url(existing_port)
            logger.info(
                'live preview already running for this project at %s (pid=%s)',
                existing_url,
                existing_pid,
            )
            if not args.no_browser:
                webbrowser.open(existing_url)
            return 0

        logger.info(
            'preview already running for another project (%s on port %s); switching to %s',
            existing_project or '(unknown project)',
            existing_port,
            project_path,
        )
        _request_preview_shutdown(existing_port, 'switch-project')
        if _wait_for_process_exit(existing_pid, timeout_seconds=6.0):
            _release_lock(GLOBAL_LOCK_FILE)
        else:
            logger.warning(
                'previous preview (pid=%s, port=%s) did not exit cleanly; falling back to a new port',
                existing_pid,
                existing_port,
            )

    try:
        actual_port = _pick_available_port(args.port)
    except RuntimeError as exc:
        logger.error('%s', exc)
        return 1
    if actual_port != args.port:
        logger.warning('port %s is busy; using %s instead', args.port, _preview_url(actual_port))
        args.port = actual_port

    # Per-project mutual exclusion. The major driver of orphaned servers is
    # --live mode (which used to disable idle timeout entirely) combined with
    # silent restarts; refusing duplicate launches catches the accumulation
    # at its source. Stale locks (dead pid) are overwritten by _claim_lock.
    lock_file = project_path / LOCK_FILE_NAME
    payload = {
        'pid': os.getpid(),
        'port': args.port,
        'project_path': str(project_path),
    }
    existing = _claim_lock(lock_file, payload)
    if existing:
        existing_pid = existing.get('pid', '?')
        existing_port = existing.get('port', '?')
        existing_url = _preview_url(existing_port)
        logger.info(
            'live preview is already running for this project at %s (pid=%s)',
            existing_url,
            existing_pid,
        )
        if not args.no_browser:
            webbrowser.open(existing_url)
        return 0
    existing_global = _claim_lock(GLOBAL_LOCK_FILE, payload)
    if existing_global is not None:
        _write_lock(GLOBAL_LOCK_FILE, payload)
    # atexit covers normal interpreter shutdown (Ctrl+C / SystemExit);
    # /api/shutdown and idle timeout call _release_lock directly before
    # os._exit since atexit handlers do not run on os._exit.
    atexit.register(_release_lock, lock_file)
    atexit.register(_release_lock, GLOBAL_LOCK_FILE)

    # SIGTERM would otherwise terminate without running atexit, leaving a
    # stale lock that future launches have to recover from. Translate it
    # into SystemExit so the atexit handler above runs. SIGINT (Ctrl+C) is
    # already handled by werkzeug's reloader-free shutdown path.
    def _on_sigterm(signum: int, _frame) -> None:
        logger.info('received signal %s, exiting', signum)
        sys.exit(0)
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        # ValueError: not in main thread; OSError: unsupported on platform.
        pass

    idle_timeout = args.timeout
    if idle_timeout is None:
        # Long but finite default for --live so a forgotten preview eventually
        # dies. Set --timeout 0 to keep the historical never-expire behavior.
        idle_timeout = 7200 if args.live else 900

    app = create_app(
        str(project_path),
        idle_timeout=idle_timeout,
        live=args.live,
        lock_files=(lock_file, GLOBAL_LOCK_FILE),
    )

    url = _preview_url(args.port)
    if not args.no_browser:
        webbrowser.open(url)

    mode = "live preview (auto-startup)" if args.live else "live preview"
    svg_count = len(list(svg_output.glob('*.svg')))
    logger.info('running at %s (%s)', url, mode)
    logger.info('project: %s', project_path)
    logger.info('svg_output: %s (%d slides)', svg_output, svg_count)
    logger.info('idle timeout: %ds (0 = disabled)', idle_timeout)
    app.run(host='127.0.0.1', port=args.port, debug=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
