#!/usr/bin/env python3
"""PPT Master local helper server for compatibility APIs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import signal
import shlex
import socket
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context
from PIL import Image

from simple_ppt_builder import build_presentation


BASE_DIR = Path(__file__).resolve().parent
SKILL_DIR = BASE_DIR / "skills" / "ppt-master"
SCRIPTS_DIR = SKILL_DIR / "scripts"
PROJECTS_DIR = BASE_DIR / "projects"
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(exist_ok=True)
PROJECTS_DIR.mkdir(exist_ok=True)
TOOL_UPLOAD_DIR = UPLOAD_DIR / "toolbox"
TOOL_UPLOAD_DIR.mkdir(exist_ok=True)
TEMPLATE_IMPORTS_DIR = PROJECTS_DIR / "_template_imports"
TEMPLATE_IMPORTS_DIR.mkdir(exist_ok=True)

PYTHON = "py"
PYTHON_ARGS = ["-3.11"]
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xlsm", ".xls",
    ".epub", ".html", ".htm",
    ".md", ".markdown", ".txt",
    ".csv", ".tsv",
    ".odt", ".rtf", ".tex", ".rst",
}
TEXT_SOURCE_EXTENSIONS = {".md", ".markdown", ".txt", ".csv", ".tsv"}
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
ASSET_TAG_PATTERN = re.compile(r"[A-Za-z0-9_+\-]{3,}|[\u4e00-\u9fff]{2,}")
FIGURE_CAPTION_PATTERN = re.compile(r"^\s*((?:figure|fig\.?|图)\s*[A-Za-z]?\d+(?:\.\d+)?[A-Za-z]?)\s*[:：.\-、]?\s*(.+)?$", re.I)
SUPPORTED_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".wmf", ".emf",
}
FORMULA_MANIFEST_FILENAME = "formula_manifest.json"
FORMULA_RENDER_REPORT_JSON = "formula_render_report.json"
FORMULA_RENDER_REPORT_MD = "formula_render_report.md"
ASSET_MATCH_REPORT_JSON = "asset_match_diagnostics.json"
ASSET_MATCH_REPORT_MD = "asset_match_diagnostics.md"
MINERU_DEFAULT_BASE_URL = "https://mineru.net/api/v4"
MINERU_TERMINAL_STATES = {"done", "failed"}
MINERU_POLL_INTERVAL_SECONDS = 2.0
MINERU_POLL_TIMEOUT_SECONDS = 300.0
START_LIVE_PREVIEW_SCRIPT = SCRIPTS_DIR / "start_live_preview.py"
LIVE_PREVIEW_LOCK_FILE = ".live_preview.lock"
LAYOUTS_INDEX_PATH = SKILL_DIR / "templates" / "layouts" / "layouts_index.json"
BRANDS_INDEX_PATH = SKILL_DIR / "templates" / "brands" / "brands_index.json"
CHARTS_INDEX_PATH = SKILL_DIR / "templates" / "charts" / "charts_index.json"
LIVE_PREVIEW_LOCK = threading.Lock()
LIVE_PREVIEW_PROCESSES: dict[str, dict[str, Any]] = {}
TOOL_TIMEOUT_SHORT = 300
TOOL_TIMEOUT_LONG = 1800


def _load_script_module(module_name: str, module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_EXTRACT_FORMULAS_MODULE = _load_script_module(
    "ppt_master_extract_formulas",
    SCRIPTS_DIR / "extract_formulas.py",
)
extract_formulas_from_markdown = _EXTRACT_FORMULAS_MODULE.extract_formulas_from_markdown
build_formula_manifest = _EXTRACT_FORMULAS_MODULE.build_manifest
save_formula_manifest = _EXTRACT_FORMULAS_MODULE.save_manifest

_LATEX_TO_SVG_MODULE = _load_script_module(
    "ppt_master_latex_to_svg",
    SCRIPTS_DIR / "latex_to_svg.py",
)
process_formula_manifest = _LATEX_TO_SVG_MODULE.process_manifest
load_formula_manifest_entries = _LATEX_TO_SVG_MODULE.load_manifest
save_formula_manifest_entries = _LATEX_TO_SVG_MODULE.save_manifest

TOOL_ACTIONS = [
    {
        "key": "project_info",
        "label": "查看项目信息",
        "category": "项目",
        "requires_project": True,
        "description": "运行 project_manager info，查看项目结构、画布、目录情况。",
        "extra_args_placeholder": "",
    },
    {
        "key": "validate_project",
        "label": "验证项目结构",
        "category": "项目",
        "requires_project": True,
        "description": "运行 project_manager validate，检查项目目录是否符合 PPT Master 规范。",
        "extra_args_placeholder": "",
    },
    {
        "key": "analyze_images",
        "label": "分析项目图片",
        "category": "图像",
        "requires_project": True,
        "description": "运行 analyze_images.py，对项目 images/ 做批量分析。",
        "extra_args_placeholder": "",
    },
    {
        "key": "generate_single_image",
        "label": "AI 生成单图",
        "category": "图像",
        "requires_project": True,
        "description": "运行 image_gen.py，把单张 AI 图片输出到当前项目 images/。",
        "primary_input_label": "提示词",
        "primary_input_placeholder": "例如：聚变反应堆剖面图，工程蓝图风格，中文科普海报",
        "extra_args_placeholder": "--backend qwen --aspect_ratio 16:9 --image_size 1K",
    },
    {
        "key": "search_web_image",
        "label": "检索网页图片",
        "category": "图像",
        "requires_project": True,
        "description": "运行 image_search.py，从开放图库检索图片并写入项目 images/。",
        "primary_input_label": "检索词",
        "primary_input_placeholder": "例如：fusion reactor control room editorial photography",
        "secondary_input_label": "输出文件名",
        "secondary_input_placeholder": "例如：cover_reference.jpg",
        "extra_args_placeholder": "--provider openverse --orientation landscape",
    },
    {
        "key": "generate_manifest_images",
        "label": "批量生成 manifest 图片",
        "category": "图像",
        "requires_project": True,
        "description": "运行 image_gen.py --manifest，按 image_prompts.json 批量生成图片。",
        "extra_args_placeholder": "--backend openai",
    },
    {
        "key": "render_image_prompt_markdown",
        "label": "导出图片提示词 Markdown",
        "category": "图像",
        "requires_project": True,
        "description": "运行 image_gen.py --render-md，把 image_prompts.json 渲染成可审阅 Markdown。",
        "extra_args_placeholder": "",
    },
    {
        "key": "start_live_preview",
        "label": "启动 Live Preview",
        "category": "预览",
        "requires_project": True,
        "description": "运行 start_live_preview.py，优先绑定 5050 端口并返回实际预览地址。",
        "extra_args_placeholder": "--timeout 7200 或 --port 5051",
    },
    {
        "key": "stop_live_preview",
        "label": "关闭 Live Preview",
        "category": "预览",
        "requires_project": True,
        "description": "关闭当前项目的 Live Preview 进程。",
        "extra_args_placeholder": "",
    },
    {
        "key": "render_visual_review",
        "label": "渲染 Visual Review PNG",
        "category": "预览",
        "requires_project": True,
        "description": "运行 visual_review.py，把 svg_output/ 渲染为 .preview PNG；rubric 复查仍需 agent/人工。",
        "extra_args_placeholder": "--pages 02 03",
    },
    {
        "key": "split_notes",
        "label": "拆分讲稿 notes",
        "category": "SVG 流水线",
        "requires_project": True,
        "description": "运行 total_md_split.py，把 total.md 切成逐页 notes/*.md。",
        "extra_args_placeholder": "",
    },
    {
        "key": "finalize_svg",
        "label": "后处理 SVG",
        "category": "SVG 流水线",
        "requires_project": True,
        "description": "运行 finalize_svg.py，执行图标嵌入、图像修正、扁平化等后处理。",
        "extra_args_placeholder": "",
    },
    {
        "key": "quality_check",
        "label": "检查 SVG 质量",
        "category": "SVG 流水线",
        "requires_project": True,
        "description": "运行 svg_quality_checker.py，对项目 SVG 做技术合规检查。",
        "extra_args_placeholder": "--format ppt169",
    },
    {
        "key": "export_pptx",
        "label": "原生导出 PPTX",
        "category": "SVG 流水线",
        "requires_project": True,
        "description": "运行 svg_to_pptx.py，从 svg_final/svg_output 导出原生可编辑 PPTX。",
        "extra_args_placeholder": "--merge-paragraphs --animation mixed --animation-duration 0.8",
    },
    {
        "key": "animation_scaffold",
        "label": "生成动画配置骨架",
        "category": "动画",
        "requires_project": True,
        "description": "运行 animation_config.py scaffold，生成 animations.json 骨架。",
        "extra_args_placeholder": "--force",
    },
    {
        "key": "animation_validate",
        "label": "校验动画配置",
        "category": "动画",
        "requires_project": True,
        "description": "运行 animation_config.py validate，检查 animations.json 是否引用了有效对象。",
        "extra_args_placeholder": "",
    },
    {
        "key": "generate_audio",
        "label": "生成逐页旁白音频",
        "category": "音频",
        "requires_project": True,
        "description": "运行 notes_to_audio.py，根据 notes/*.md 生成逐页旁白音频。",
        "extra_args_placeholder": "--voice zh-CN-XiaoxiaoNeural 或 --provider qwen --voice-id Cherry",
    },
    {
        "key": "update_spec",
        "label": "批量更新 spec_lock",
        "category": "维护",
        "requires_project": True,
        "description": "运行 update_spec.py，把 colors.* 或 typography.font_family 的修改传播到 spec_lock.md 和 svg_output/*.svg。",
        "primary_input_label": "变更表达式",
        "primary_input_placeholder": "例如：colors.primary=#0066AA 或 typography.font_family=\"Microsoft YaHei\", Arial, sans-serif",
        "extra_args_placeholder": "",
    },
]

AGENT_ONLY_FEATURES = [
    {
        "name": "Strategist 八项确认 + design_spec 生成",
        "reason": "这是仓库定义的阻塞式对话阶段，不是脚本入口。",
    },
    {
        "name": "Executor 顺序写 SVG 页面",
        "reason": "仓库明确要求主 agent 逐页手写 SVG，不能伪装成批处理 API。",
    },
    {
        "name": "topic-research",
        "reason": "属于独立 workflow，需要 agent 做资料搜集与取舍，不是现成脚本。",
    },
    {
        "name": "create-brand / create-template 完整创作流程",
        "reason": "可提供库浏览与 PPTX 参考导入，但真正产出 brand/template 仍是 workflow。",
    },
    {
        "name": "resume-execute / verify-charts 的人工判读部分",
        "reason": "依赖上下文与视觉判断，不能在 GUI 里安全自动化。",
    },
]

app = Flask(__name__, template_folder="templates_web", static_folder="static_web")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def _allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _resolve_env_path() -> Path:
    candidates = [
        Path.cwd() / ".env",
        BASE_DIR / ".env",
        Path.home() / ".ppt-master" / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _strip_inline_env_comment(value: str) -> str:
    stripped = value.lstrip()
    if stripped.startswith(("'", '"')):
        quote = stripped[0]
        end = stripped.find(quote, 1)
        if end == -1:
            return value
        head_length = len(value) - len(stripped) + end + 1
        head = value[:head_length]
        tail = value[head_length:]
        hash_pos = tail.find("#")
        if hash_pos == -1:
            return value
        return head + tail[:hash_pos]
    hash_pos = value.find("#")
    if hash_pos == -1:
        return value
    return value[:hash_pos]


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_named_env_keys(keys: tuple[str, ...]) -> Path | None:
    env_path = _resolve_env_path()
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in keys:
            continue
        cleaned = _strip_env_quotes(_strip_inline_env_comment(value).strip())
        os.environ.setdefault(key, cleaned)
    return env_path


def _shorten_component(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    keep = max_length - 9
    shortened = value[:keep].rstrip("_-. ")
    if not shortened:
        shortened = value[:keep]
    return f"{shortened}_{digest}"


def _shorten_filename(filename: str, max_stem_length: int = 88) -> str:
    path = Path(filename)
    return f"{_shorten_component(path.stem, max_stem_length)}{path.suffix}"


def _sanitize_project_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    cleaned = cleaned or "presentation"
    return _shorten_component(cleaned, 48)


def _run_script(
    script_path: Path,
    args: list[str],
    cwd: Path | None = None,
    *,
    timeout: int = TOOL_TIMEOUT_SHORT,
) -> dict[str, object]:
    cmd = [PYTHON, *PYTHON_ARGS, str(script_path), *args]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    started_at = time.monotonic()
    resolved_cwd = cwd or BASE_DIR
    command_text = subprocess.list2cmdline(cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(resolved_cwd),
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration_seconds = round(time.monotonic() - started_at, 2)
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nCommand timed out after {timeout}s.",
            "command": command_text,
            "cwd": str(resolved_cwd),
            "duration_seconds": duration_seconds,
        }
    duration_seconds = round(time.monotonic() - started_at, 2)
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": command_text,
        "cwd": str(resolved_cwd),
        "duration_seconds": duration_seconds,
    }


def _detect_converter(filename: str) -> Path | None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return SCRIPTS_DIR / "source_to_md" / "mineru_to_md.py"
    if suffix in {".docx", ".doc", ".odt", ".rtf", ".epub", ".html", ".htm", ".tex", ".rst"}:
        return SCRIPTS_DIR / "source_to_md" / "doc_to_md.py"
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return SCRIPTS_DIR / "source_to_md" / "excel_to_md.py"
    if suffix in {".pptx", ".ppt"}:
        return SCRIPTS_DIR / "source_to_md" / "ppt_to_md.py"
    return None


def _extract_existing_path(text: str, suffix: str) -> Path | None:
    for raw_line in text.splitlines():
        line = raw_line.strip().strip('"')
        if not line.lower().endswith(suffix):
            continue
        candidate = Path(line)
        if candidate.exists():
            return candidate
    return None


def _find_newest_markdown(search_dir: Path, known_paths: set[Path]) -> Path | None:
    candidates = [path for path in search_dir.glob("*.md") if path.resolve() not in known_paths]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _companion_asset_dir(path: Path) -> Path | None:
    candidate = path.with_name(f"{path.stem}_files")
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _move_path(source: Path, destination: Path) -> Path:
    destination = _ensure_unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    return destination


def _move_tree(source: Path, destination: Path) -> Path:
    destination = _ensure_unique_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    return destination


def _rewrite_asset_refs(markdown_path: Path, old_dir_name: str, new_dir_name: str) -> None:
    if old_dir_name == new_dir_name:
        return
    content = markdown_path.read_text(encoding="utf-8", errors="ignore")
    updated = content.replace(f"{old_dir_name}/", f"{new_dir_name}/")
    if updated != content:
        markdown_path.write_text(updated, encoding="utf-8")


def _propagate_gui_image_assets(asset_dir: Path, project_path: Path, namespace: str) -> int:
    images_dir = project_path / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for source_file in sorted(asset_dir.iterdir()):
        if not source_file.is_file():
            continue
        if source_file.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        shutil.copy2(source_file, images_dir / f"{namespace}__{source_file.name}")
        copied += 1
    return copied


def _archive_gui_sources(project_path: Path, source_paths: list[Path]) -> dict[str, list[str]]:
    sources_dir = project_path / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "archived": [],
        "markdown": [],
        "assets": [],
        "notes": [],
    }

    seen: set[Path] = set()
    for source_path in source_paths:
        try:
            resolved = source_path.resolve()
        except FileNotFoundError:
            summary["notes"].append(f"{source_path}: path not found")
            continue
        if resolved in seen:
            continue
        seen.add(resolved)

        asset_dir = _companion_asset_dir(source_path)
        archived_path = _move_path(source_path, sources_dir / _shorten_filename(source_path.name))
        summary["archived"].append(str(archived_path))
        if archived_path.suffix.lower() in {".md", ".markdown", ".txt", ".csv", ".tsv"}:
            summary["markdown"].append(str(archived_path))

        if asset_dir is not None:
            imported_asset_dir = _move_tree(asset_dir, sources_dir / f"{archived_path.stem}_files")
            _rewrite_asset_refs(archived_path, asset_dir.name, imported_asset_dir.name)
            summary["assets"].append(str(imported_asset_dir))
            copied = _propagate_gui_image_assets(imported_asset_dir, project_path, archived_path.stem)
            if copied:
                summary["notes"].append(
                    f"{archived_path.name}: copied {copied} image asset(s) to images/"
                )

    formula_sync = _sync_project_formula_assets(project_path)
    if formula_sync.get("total"):
        summary["notes"].append(
            "公式同步："
            f"共 {formula_sync.get('total', 0)} 条，"
            f"成功 {formula_sync.get('rendered', 0)} 条，"
            f"失败 {formula_sync.get('failed', 0)} 条，"
            f"待渲染 {formula_sync.get('pending', 0)} 条。"
        )
    elif formula_sync.get("removed"):
        summary["notes"].append("公式同步：当前项目未检测到 LaTeX 公式，已清理旧公式产物。")

    return summary


def _get_mineru_api_token() -> str:
    _load_named_env_keys(("MINERU_API_TOKEN", "MINERU_API_KEY", "MINERU_TOKEN"))
    token = (
        os.environ.get("MINERU_API_TOKEN")
        or os.environ.get("MINERU_API_KEY")
        or os.environ.get("MINERU_TOKEN")
        or ""
    ).strip()
    if token:
        return token
    raise RuntimeError(
        "未配置 MinerU API Token。请在 .env 或环境变量中设置 MINERU_API_TOKEN。"
    )


def _get_mineru_base_url() -> str:
    _load_named_env_keys(("MINERU_API_BASE_URL", "MINERU_BASE_URL"))
    base_url = (
        os.environ.get("MINERU_API_BASE_URL")
        or os.environ.get("MINERU_BASE_URL")
        or MINERU_DEFAULT_BASE_URL
    ).strip()
    return base_url.rstrip("/") or MINERU_DEFAULT_BASE_URL


def _mineru_auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _mineru_api_error(payload: dict[str, object], fallback: str) -> RuntimeError:
    message = str(payload.get("msg") or fallback).strip() or fallback
    code = payload.get("code")
    if code not in {None, ""}:
        message = f"{message} (code={code})"
    return RuntimeError(message)


def _rewrite_markdown_asset_refs(content: str, old_dir_name: str, new_dir_name: str) -> str:
    updated = content
    for prefix in (old_dir_name, f"./{old_dir_name}"):
        updated = updated.replace(f"]({prefix}/", f"]({new_dir_name}/")
        updated = updated.replace(f'src="{prefix}/', f'src="{new_dir_name}/')
        updated = updated.replace(f"src='{prefix}/", f"src='{new_dir_name}/")
    return updated


def _normalize_mineru_bundle(extracted_dir: Path, workspace_dir: Path, bundle_prefix: str) -> tuple[Path, int]:
    markdown_source = extracted_dir / "full.md"
    if not markdown_source.exists():
        markdown_candidates = sorted(extracted_dir.glob("*.md"))
        if not markdown_candidates:
            raise RuntimeError("MinerU 结果中缺少 Markdown 文件。")
        markdown_source = markdown_candidates[0]

    markdown_path = workspace_dir / f"{bundle_prefix}.md"
    content = _read_text_file(markdown_source)
    image_count = 0

    images_dir = extracted_dir / "images"
    if images_dir.exists() and images_dir.is_dir():
        asset_dir = workspace_dir / f"{markdown_path.stem}_files"
        shutil.copytree(images_dir, asset_dir)
        image_count = sum(1 for path in asset_dir.rglob("*") if path.is_file())
        content = _rewrite_markdown_asset_refs(content, "images", asset_dir.name)

    markdown_path.write_text(content, encoding="utf-8")
    return markdown_path, image_count


def _convert_pdf_with_mineru(upload_path: Path, original_name: str) -> tuple[Path, dict[str, object]]:
    token = _get_mineru_api_token()
    base_url = _get_mineru_base_url()
    bundle_prefix = f"mineru_{uuid.uuid4().hex[:8]}"
    workspace_dir = _ensure_unique_path(upload_path.parent / bundle_prefix)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    create_payload = {
        "enable_formula": True,
        "enable_table": True,
        "is_ocr": False,
        "files": [{
            "name": Path(original_name).name,
            "data_id": uuid.uuid4().hex,
            "page_ranges": "",
        }],
    }

    try:
        create_response = requests.post(
            f"{base_url}/file-urls/batch",
            headers=_mineru_auth_headers(token),
            json=create_payload,
            timeout=60,
        )
        create_response.raise_for_status()
        create_payload_json = create_response.json()
        if create_payload_json.get("code") != 0:
            raise _mineru_api_error(create_payload_json, "MinerU 初始化失败。")

        create_data = create_payload_json.get("data") or {}
        batch_id = str(create_data.get("batch_id") or "").strip()
        file_urls = list(create_data.get("file_urls") or [])
        upload_url = str(file_urls[0] or "").strip() if file_urls else ""
        if not batch_id or not upload_url:
            raise RuntimeError("MinerU 未返回有效的上传地址或 batch_id。")

        with upload_path.open("rb") as file_handle:
            upload_response = requests.put(upload_url, data=file_handle, timeout=300)
        upload_response.raise_for_status()

        extract_results: list[dict[str, object]] = []
        deadline = time.monotonic() + MINERU_POLL_TIMEOUT_SECONDS
        while True:
            batch_response = requests.get(
                f"{base_url}/extract-results/batch/{batch_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            batch_response.raise_for_status()
            batch_payload = batch_response.json()
            if batch_payload.get("code") != 0:
                raise _mineru_api_error(batch_payload, "MinerU 结果轮询失败。")
            extract_results = list((batch_payload.get("data") or {}).get("extract_result") or [])
            if extract_results and all(str(item.get("state") or "") in MINERU_TERMINAL_STATES for item in extract_results):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("MinerU 解析超时，请稍后重试。")
            time.sleep(MINERU_POLL_INTERVAL_SECONDS)

        result_item = extract_results[0] if extract_results else {}
        if str(result_item.get("state") or "") != "done":
            raise RuntimeError(str(result_item.get("err_msg") or "MinerU 解析失败。"))

        zip_url = str(result_item.get("full_zip_url") or result_item.get("full_zip_link") or "").strip()
        if not zip_url:
            raise RuntimeError("MinerU 解析完成，但未返回结果压缩包地址。")

        zip_path = workspace_dir / "mineru_result.zip"
        extract_dir = workspace_dir / "_extract"
        with requests.get(zip_url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with zip_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)

        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)

        markdown_path, image_count = _normalize_mineru_bundle(extract_dir, workspace_dir, bundle_prefix)
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)

        stdout_lines = [
            "MinerU 云解析成功",
            f"batch_id: {batch_id}",
            f"markdown: {markdown_path}",
            f"images: {image_count}",
        ]
        return markdown_path, {"returncode": 0, "stdout": "\n".join(stdout_lines), "stderr": ""}
    except requests.RequestException as exc:
        message = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            body = response.text[:240].strip()
            if body:
                message = f"{message}: {body}"
        raise RuntimeError(f"MinerU 云解析失败：{message}") from exc


def _convert_uploaded_file(
    upload_path: Path,
    original_name: str,
) -> tuple[Path, dict[str, object]]:
    if Path(original_name).suffix.lower() == ".pdf":
        known_paths = {path.resolve() for path in upload_path.parent.glob("*.md")}
        result = _run_script(
            SCRIPTS_DIR / "source_to_md" / "mineru_to_md.py",
            [str(upload_path)],
            timeout=TOOL_TIMEOUT_LONG,
        )
        if result["returncode"] != 0:
            return upload_path, result

        markdown_path = _extract_existing_path(str(result["stdout"]), ".md")
        if markdown_path is None:
            markdown_path = _find_newest_markdown(upload_path.parent, known_paths)
        if markdown_path is None:
            result["returncode"] = 1
            result["stderr"] = f"{result['stderr']}\n未找到 MinerU 转换后的 Markdown 文件。"
            return upload_path, result
        return markdown_path, result

    converter = _detect_converter(original_name)
    if not converter:
        return upload_path, {"returncode": 0, "stdout": "无需转换，直接使用原文件", "stderr": ""}

    known_paths = {path.resolve() for path in upload_path.parent.glob("*.md")}
    result = _run_script(converter, [str(upload_path)])
    if result["returncode"] != 0:
        return upload_path, result

    markdown_path = _extract_existing_path(str(result["stdout"]), ".md")
    if markdown_path is None:
        markdown_path = _find_newest_markdown(upload_path.parent, known_paths)
    if markdown_path is None:
        result["returncode"] = 1
        result["stderr"] = f"{result['stderr']}\n未找到转换后的 Markdown 文件。"
        return upload_path, result
    return markdown_path, result


def _extract_project_path(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        if "Project created:" in line:
            project_path = Path(line.split("Project created:", 1)[1].strip())
            if not project_path.is_absolute():
                project_path = (BASE_DIR / project_path).resolve()
            return project_path
    return None


def _create_project(project_name: str, canvas_format: str) -> tuple[Path | None, dict[str, object]]:
    base_name = _sanitize_project_name(project_name)
    candidates = [
        base_name,
        f"{base_name}_{datetime.now():%H%M%S}",
        f"{base_name}_{uuid.uuid4().hex[:6]}",
    ]
    last_result: dict[str, object] | None = None
    for candidate in candidates:
        result = _run_script(SCRIPTS_DIR / "project_manager.py", ["init", candidate, "--format", canvas_format])
        project_path = _extract_project_path(str(result["stdout"]))
        if result["returncode"] == 0 and project_path is not None:
            return project_path, result
        last_result = result
        combined_output = f"{result['stdout']}\n{result['stderr']}"
        if "already exists" not in combined_output:
            break
    return None, last_result or {"returncode": 1, "stdout": "", "stderr": "项目创建失败"}


def _sanitize_tool_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _split_cli_args(raw_args: str) -> list[str]:
    value = raw_args.strip()
    if not value:
        return []
    try:
        return shlex.split(value, posix=True)
    except ValueError as exc:
        raise ValueError(f"附加参数格式错误：{exc}") from exc


def _strip_option(args: list[str], option_names: set[str]) -> list[str]:
    filtered: list[str] = []
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg in option_names:
            has_value = index + 1 < len(args) and not args[index + 1].startswith("-")
            skip_next = has_value
            continue
        if any(arg.startswith(f"{name}=") for name in option_names):
            continue
        filtered.append(arg)
    return filtered


def _extract_option_value(args: list[str], option_name: str) -> str | None:
    for index, arg in enumerate(args):
        if arg == option_name and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith(f"{option_name}="):
            return arg.split("=", 1)[1]
    return None


def _read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _windows_open_process(pid: int):
    import ctypes

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    access = process_query_limited_information | synchronize
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(access, False, pid)
    return ctypes, kernel32, handle


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            ctypes, kernel32, handle = _windows_open_process(pid)
            if not handle:
                error = ctypes.get_last_error()
                # Access denied still implies the process exists.
                return error == 5
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except SystemError:
        return False
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        try:
            ctypes, kernel32, handle = _windows_open_process(pid)
            if not handle:
                return
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
            return
        except Exception:
            return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return


def _external_preview_record(project_path: Path) -> dict[str, Any] | None:
    lock_path = project_path / LIVE_PREVIEW_LOCK_FILE
    if not lock_path.exists():
        return None
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    try:
        pid = int(payload.get("pid") or 0)
        port = int(payload.get("port") or 0)
    except (TypeError, ValueError):
        return None

    if not _pid_alive(pid):
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    url = f"http://127.0.0.1:{port}"
    for _ in range(10):
        if _probe_live_preview(url):
            return {
                "process": None,
                "pid": pid,
                "port": port,
                "url": url,
                "external": True,
            }
        time.sleep(0.2)

    return {
        "process": None,
        "pid": pid,
        "port": port,
        "url": url,
        "external": True,
    }


def _preview_record(project_name: str, project_path: Path | None = None) -> dict[str, Any] | None:
    with LIVE_PREVIEW_LOCK:
        record = LIVE_PREVIEW_PROCESSES.get(project_name)
        if record is None:
            pass
        else:
            process = record.get("process")
            if process is None:
                url = str(record.get("url") or "")
                if url and _probe_live_preview(url):
                    return record
            elif process.poll() is None:
                return record
            LIVE_PREVIEW_PROCESSES.pop(project_name, None)

    if project_path is None:
        return None

    external = _external_preview_record(project_path)
    if external is None:
        return None
    with LIVE_PREVIEW_LOCK:
        LIVE_PREVIEW_PROCESSES[project_name] = external
    return external


def _probe_live_preview(url: str) -> bool:
    try:
        response = requests.get(f"{url.rstrip('/')}/api/slides", timeout=2)
        return response.ok
    except Exception:
        return False


def _parse_command_kv_output(text: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()
    return payload


def _preview_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url.rstrip("/")


def _fetch_preview_json(base_url: str, endpoint: str) -> dict[str, Any]:
    response = requests.get(f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}", timeout=3)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _start_live_preview(project_path: Path, extra_args: list[str]) -> dict[str, Any]:
    existing = _preview_record(project_path.name, project_path)
    if existing is not None:
        return {
            "project_name": project_path.name,
            "live_preview_url": existing["url"],
            "port": existing["port"],
            "already_running": True,
        }

    port_text = _extract_option_value(extra_args, "--port")
    if port_text:
        try:
            preferred_port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"预览端口无效：{port_text}") from exc
    else:
        preferred_port = 5050

    preview_args = _strip_option(extra_args, {"--port"})
    if "--no-browser" not in preview_args:
        preview_args = ["--no-browser", *preview_args]

    result = _run_script(
        START_LIVE_PREVIEW_SCRIPT,
        [str(project_path), "--port", str(preferred_port), *preview_args],
        timeout=max(30, TOOL_TIMEOUT_SHORT),
    )
    output = _parse_command_kv_output(str(result.get("stdout") or ""))
    live_preview_url = str(output.get("LIVE_PREVIEW_URL") or "").strip()
    if result["returncode"] != 0 or not live_preview_url:
        details = str(result.get("stderr") or "").strip() or str(result.get("stdout") or "").strip()
        raise RuntimeError(details or "Live Preview 启动失败，请检查 5050 端口占用或项目日志。")

    record = _preview_record(project_path.name, project_path)
    base_url = _preview_base_url(live_preview_url)
    parsed_url = urlparse(base_url)
    actual_port = int(parsed_url.port or preferred_port)
    if record is None:
        pid_text = str(output.get("LIVE_PREVIEW_PID") or "").strip()
        pid = int(pid_text) if pid_text.isdigit() else 0
        record = {
            "process": None,
            "pid": pid,
            "port": actual_port,
            "url": base_url,
            "external": True,
        }
        with LIVE_PREVIEW_LOCK:
            LIVE_PREVIEW_PROCESSES[project_path.name] = record

    return {
        "project_name": project_path.name,
        "live_preview_url": live_preview_url,
        "port": int(record.get("port") or actual_port),
        "already_running": output.get("LIVE_PREVIEW_STATUS") == "reused",
    }


def _stop_live_preview(project_name: str) -> dict[str, Any]:
    project_path = PROJECTS_DIR / project_name
    record = _preview_record(project_name, project_path if project_path.exists() else None)
    if record is None:
        return {
            "project_name": project_name,
            "stopped": False,
            "message": "当前项目没有正在运行的 Live Preview。",
        }

    url = str(record["url"])
    process = record.get("process")
    pid = int(record.get("pid") or 0)
    try:
        requests.post(f"{url.rstrip('/')}/api/shutdown", json={"reason": "toolbox stop"}, timeout=2)
    except Exception:
        pass

    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    elif pid and _pid_alive(pid):
        _terminate_pid(pid)

    with LIVE_PREVIEW_LOCK:
        LIVE_PREVIEW_PROCESSES.pop(project_name, None)
    return {
        "project_name": project_name,
        "stopped": True,
        "message": "Live Preview 已关闭。",
    }


def _resolve_within(root: Path, relative_path: str) -> Path | None:
    resolved_root = root.resolve()
    candidate = (resolved_root / unquote(relative_path)).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate


def _recent_files(root: Path, patterns: list[str], limit: int = 8) -> list[Path]:
    if not root.exists():
        return []
    by_path: dict[Path, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                by_path[path.resolve()] = path
    return sorted(by_path.values(), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _project_artifact(project_path: Path, file_path: Path) -> dict[str, str]:
    relative = file_path.relative_to(project_path).as_posix()
    return {
        "name": file_path.name,
        "relative_path": relative,
        "url": f"/api/projects/{quote(project_path.name)}/files/{quote(relative)}",
    }


def _template_import_artifact(import_name: str, import_path: Path, file_path: Path) -> dict[str, str]:
    relative = file_path.relative_to(import_path).as_posix()
    return {
        "name": file_path.name,
        "relative_path": relative,
        "url": f"/api/template-imports/{quote(import_name)}/files/{quote(relative)}",
    }


def _tool_definition(action: str) -> dict[str, Any]:
    for item in TOOL_ACTIONS:
        if item["key"] == action:
            return item
    raise ValueError(f"未知工具动作：{action}")


def _catalog_entries(raw_entries: dict[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key, value in sorted(raw_entries.items()):
        if not isinstance(value, dict):
            continue
        entries.append({
            "id": key,
            "summary": str(value.get("summary", "")).strip(),
            "keywords": list(value.get("keywords") or []),
            "primary_color": str(value.get("primary_color", "")).strip(),
            "path": (base_dir / key).as_posix(),
        })
    return entries


def _toolbox_catalog() -> dict[str, Any]:
    layouts = _catalog_entries(_read_json_file(LAYOUTS_INDEX_PATH), SKILL_DIR / "templates" / "layouts")
    brands = _catalog_entries(_read_json_file(BRANDS_INDEX_PATH), SKILL_DIR / "templates" / "brands")
    charts_payload = _read_json_file(CHARTS_INDEX_PATH)
    chart_entries = charts_payload.get("charts") or {}
    charts = [
        {
            "id": key,
            "summary": str(value.get("summary", "")).strip(),
            "path": (SKILL_DIR / "templates" / "charts" / f"{key}.svg").as_posix(),
        }
        for key, value in sorted(chart_entries.items())
        if isinstance(value, dict)
    ]
    return {
        "actions": TOOL_ACTIONS,
        "agent_only_features": AGENT_ONLY_FEATURES,
        "layouts": layouts,
        "brands": brands,
        "charts": charts,
        "chart_meta": charts_payload.get("meta") or {},
        "projects": _list_projects(),
    }


def _tool_result(
    *,
    project_path: Path,
    action: str,
    result: dict[str, Any],
    artifacts: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = _tool_definition(action)
    payload = {
        "success": result.get("returncode") == 0,
        "project_name": project_path.name,
        "action": action,
        "label": definition["label"],
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "returncode": int(result.get("returncode") or 0),
        "artifacts": artifacts or [],
        "command": str(result.get("command") or ""),
        "cwd": str(result.get("cwd") or ""),
        "duration_seconds": result.get("duration_seconds"),
    }
    if extra:
        payload.update(extra)
    return payload


def _run_project_tool(project_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    if not action:
        raise ValueError("缺少工具动作。")

    primary_input = str(payload.get("primary_input") or "").strip()
    secondary_input = str(payload.get("secondary_input") or "").strip()
    extra_args = _split_cli_args(str(payload.get("extra_args") or ""))

    if action == "start_live_preview":
        preview = _start_live_preview(project_path, extra_args)
        return {
            "success": True,
            "project_name": project_path.name,
            "action": action,
            "label": _tool_definition(action)["label"],
            "stdout": preview["live_preview_url"],
            "stderr": "",
            "returncode": 0,
            "artifacts": [],
            "live_preview_url": preview["live_preview_url"],
            "already_running": preview["already_running"],
        }

    if action == "stop_live_preview":
        preview = _stop_live_preview(project_path.name)
        return {
            "success": True,
            "project_name": project_path.name,
            "action": action,
            "label": _tool_definition(action)["label"],
            "stdout": preview["message"],
            "stderr": "",
            "returncode": 0,
            "artifacts": [],
        }

    script_path: Path
    script_args: list[str]
    timeout = TOOL_TIMEOUT_SHORT
    artifacts: list[dict[str, str]] = []
    extra: dict[str, Any] = {}

    if action == "project_info":
        script_path = SCRIPTS_DIR / "project_manager.py"
        script_args = ["info", str(project_path), *extra_args]
    elif action == "validate_project":
        script_path = SCRIPTS_DIR / "project_manager.py"
        script_args = ["validate", str(project_path), *extra_args]
    elif action == "analyze_images":
        script_path = SCRIPTS_DIR / "analyze_images.py"
        script_args = [str(project_path / "images"), *extra_args]
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path, ["image_analysis.csv"])
        ]
    elif action == "generate_single_image":
        if not primary_input:
            raise ValueError("请填写图片提示词。")
        script_path = SCRIPTS_DIR / "image_gen.py"
        script_args = [primary_input, "-o", str(project_path / "images"), *extra_args]
        timeout = TOOL_TIMEOUT_LONG
    elif action == "search_web_image":
        if not primary_input:
            raise ValueError("请填写图片检索词。")
        filename = _sanitize_tool_filename(secondary_input, "reference.jpg")
        script_path = SCRIPTS_DIR / "image_search.py"
        script_args = [primary_input, "--filename", filename, "-o", str(project_path / "images"), *extra_args]
        timeout = TOOL_TIMEOUT_LONG
    elif action == "generate_manifest_images":
        manifest_path = project_path / "images" / "image_prompts.json"
        if not manifest_path.exists():
            raise ValueError(f"缺少 manifest 文件：{manifest_path}")
        script_path = SCRIPTS_DIR / "image_gen.py"
        script_args = ["--manifest", str(manifest_path), *extra_args]
        timeout = TOOL_TIMEOUT_LONG
    elif action == "render_image_prompt_markdown":
        manifest_path = project_path / "images" / "image_prompts.json"
        if not manifest_path.exists():
            raise ValueError(f"缺少 manifest 文件：{manifest_path}")
        script_path = SCRIPTS_DIR / "image_gen.py"
        script_args = ["--render-md", str(manifest_path), *extra_args]
    elif action == "render_visual_review":
        preview = _start_live_preview(project_path, [])
        script_path = SCRIPTS_DIR / "visual_review.py"
        script_args = [str(project_path), "--server-url", preview["live_preview_url"], *extra_args]
        timeout = TOOL_TIMEOUT_LONG
        extra["live_preview_url"] = preview["live_preview_url"]
    elif action == "split_notes":
        script_path = SCRIPTS_DIR / "total_md_split.py"
        script_args = [str(project_path), *extra_args]
    elif action == "finalize_svg":
        script_path = SCRIPTS_DIR / "finalize_svg.py"
        script_args = [str(project_path), *extra_args]
    elif action == "quality_check":
        script_path = SCRIPTS_DIR / "svg_quality_checker.py"
        script_args = [str(project_path), *extra_args]
    elif action == "export_pptx":
        script_path = SCRIPTS_DIR / "svg_to_pptx.py"
        script_args = [str(project_path), *extra_args]
        timeout = TOOL_TIMEOUT_LONG
    elif action == "animation_scaffold":
        script_path = SCRIPTS_DIR / "animation_config.py"
        script_args = ["scaffold", str(project_path), *extra_args]
    elif action == "animation_validate":
        script_path = SCRIPTS_DIR / "animation_config.py"
        script_args = ["validate", str(project_path), *extra_args]
    elif action == "generate_audio":
        script_path = SCRIPTS_DIR / "notes_to_audio.py"
        script_args = [str(project_path), *extra_args]
        timeout = TOOL_TIMEOUT_LONG
    elif action == "update_spec":
        if not primary_input:
            raise ValueError("请填写要传播的 spec 变更表达式。")
        script_path = SCRIPTS_DIR / "update_spec.py"
        script_args = [str(project_path), primary_input, *extra_args]
    else:
        raise ValueError(f"暂不支持的工具动作：{action}")

    result = _run_script(script_path, script_args, timeout=timeout)

    if action in {"generate_single_image", "search_web_image", "generate_manifest_images"}:
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / "images", ["*.png", "*.jpg", "*.jpeg", "*.webp", "image_sources.json"], limit=10)
        ]
    elif action == "render_image_prompt_markdown":
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / "images", ["*.md", "image_prompts.json"], limit=10)
        ]
    elif action == "render_visual_review":
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / ".preview", ["*.png"], limit=12)
        ]
    elif action == "split_notes":
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / "notes", ["*.md"], limit=12)
        ]
    elif action == "export_pptx":
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / "exports", ["*.pptx"], limit=4)
        ]
    elif action in {"animation_scaffold", "animation_validate"}:
        animation_config = project_path / "animations.json"
        if animation_config.exists():
            artifacts = [_project_artifact(project_path, animation_config)]
    elif action == "generate_audio":
        artifacts = [
            _project_artifact(project_path, file_path)
            for file_path in _recent_files(project_path / "audio", ["*.mp3", "*.m4a", "*.wav"], limit=12)
        ]
    elif action == "update_spec":
        spec_lock = project_path / "spec_lock.md"
        if spec_lock.exists():
            artifacts = [_project_artifact(project_path, spec_lock)]

    return _tool_result(project_path=project_path, action=action, result=result, artifacts=artifacts, extra=extra)


def _read_source_text(project_path: Path) -> str:
    return _read_source_bundle(project_path)["text"]


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _image_orientation(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "unknown"
    if width >= height * 1.1:
        return "landscape"
    if height >= width * 1.1:
        return "portrait"
    return "square"


def _measure_svg_dimensions(image_path: Path) -> dict[str, object]:
    try:
        text = image_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    width_match = re.search(r'\bwidth=["\']([0-9.]+)(?:px|pt)?["\']', text)
    height_match = re.search(r'\bheight=["\']([0-9.]+)(?:px|pt)?["\']', text)
    if width_match and height_match:
        width = float(width_match.group(1))
        height = float(height_match.group(1))
    else:
        view_box = re.search(
            r'\bviewBox=["\'][-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)["\']',
            text,
        )
        if not view_box:
            return {}
        width = float(view_box.group(1))
        height = float(view_box.group(2))

    if width <= 0 or height <= 0:
        return {}
    return {
        "width": int(round(width)),
        "height": int(round(height)),
        "aspect_ratio": round(width / height, 3),
        "orientation": _image_orientation(int(round(width)), int(round(height))),
    }


def _measure_image_dimensions(image_path: Path) -> dict[str, object]:
    if not image_path.exists() or not image_path.is_file():
        return {}
    if image_path.suffix.lower() == ".svg":
        return _measure_svg_dimensions(image_path)
    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except Exception:
        return {}
    if width <= 0 or height <= 0:
        return {}
    return {
        "width": int(width),
        "height": int(height),
        "aspect_ratio": round(width / height, 3),
        "orientation": _image_orientation(width, height),
    }


def _normalize_markdown_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    target = re.split(r"\s+(?=['\"])", target, maxsplit=1)[0]
    return target.strip()


def _clean_context_line(text: str) -> str:
    cleaned = MARKDOWN_IMAGE_PATTERN.sub("", text)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _extract_heading_context(content: str, start: int, max_items: int = 2) -> list[str]:
    headings: list[str] = []
    for line in reversed(content[:start].splitlines()):
        match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if not match:
            continue
        heading = _clean_context_line(match.group(2))
        if not heading or heading in headings:
            continue
        headings.append(heading)
        if len(headings) >= max_items:
            break
    headings.reverse()
    return headings


def _normalize_figure_ref_key(raw_label: str) -> str:
    cleaned = str(raw_label or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"(?i)\bfigure\b|\bfig\.?\b|图", "", cleaned)
    cleaned = re.sub(r"[^A-Za-z0-9.]+", "", cleaned).lower()
    if not cleaned:
        return ""
    return f"fig{cleaned}"


def _extract_figure_metadata(content: str, start: int, end: int, alt: str) -> dict[str, str]:
    before_lines = content[:start].splitlines()
    after_lines = content[end:].splitlines()
    headings = _extract_heading_context(content, start)
    nearby_lines: list[str] = []

    for line in after_lines[:4]:
        cleaned = _clean_context_line(line)
        if cleaned:
            nearby_lines.append(cleaned)
    for line in reversed(before_lines[-2:]):
        cleaned = _clean_context_line(line)
        if cleaned:
            nearby_lines.append(cleaned)

    figure_label = ""
    figure_caption = ""
    for candidate in nearby_lines:
        match = FIGURE_CAPTION_PATTERN.match(candidate)
        if not match:
            continue
        figure_label = match.group(1).strip()
        figure_caption = str(match.group(2) or "").strip()
        break

    if not figure_label and alt:
        alt_match = FIGURE_CAPTION_PATTERN.match(alt)
        if alt_match:
            figure_label = alt_match.group(1).strip()
            figure_caption = str(alt_match.group(2) or "").strip()

    if not figure_caption and alt:
        figure_caption = alt.strip()[:180]

    section_heading = headings[-1] if headings else ""
    section_heading_path = " > ".join(headings)
    return {
        "figure_label": figure_label,
        "figure_ref_key": _normalize_figure_ref_key(figure_label),
        "figure_caption": figure_caption,
        "section_heading": section_heading,
        "section_heading_path": section_heading_path,
    }


def _extract_image_context(content: str, start: int, end: int) -> str:
    before_lines = content[:start].splitlines()
    after_lines = content[end:].splitlines()
    context_parts: list[str] = list(_extract_heading_context(content, start))

    for line in reversed(before_lines):
        if re.match(r"^(#{1,6})\s+", line.strip()):
            continue
        cleaned = _clean_context_line(line)
        if cleaned:
            context_parts.append(cleaned)
            break

    for line in after_lines:
        if re.match(r"^(#{1,6})\s+", line.strip()):
            continue
        cleaned = _clean_context_line(line)
        if cleaned:
            context_parts.append(cleaned)
            break

    merged: list[str] = []
    seen: set[str] = set()
    for part in context_parts:
        if not part or part in seen:
            continue
        seen.add(part)
        merged.append(part)
    return " | ".join(merged[:3])[:180]


def _extract_asset_tags(*parts: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for raw_tag in ASSET_TAG_PATTERN.findall(part or ""):
            tag = raw_tag.strip().lower()
            if len(tag) < 2 or tag.isdigit() or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
    return tags[:16]


def _merge_asset_text(*parts: str, max_length: int) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = re.sub(r"\s+", " ", str(part or "")).strip(" |")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        merged.append(cleaned)
    return " | ".join(merged)[:max_length]


def _merge_asset_tag_lists(*tag_lists: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for tag_list in tag_lists:
        for raw_tag in tag_list:
            tag = str(raw_tag or "").strip().lower()
            if not tag or tag in seen:
                continue
            seen.add(tag)
            merged.append(tag)
    return merged[:16]


def _is_blank_asset_value(value: Any) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _prefer_source_file(current: str, incoming: str) -> str:
    current = str(current or "").strip()
    incoming = str(incoming or "").strip()
    if not current:
        return incoming
    if not incoming:
        return current
    if current == "images/" and incoming != "images/":
        return incoming
    return current


def _load_asset_manifest_items(manifest_path: Path) -> list[dict[str, Any]]:
    if not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _asset_manifest_match_keys(asset_path: Path, images_dir: Path) -> set[str]:
    keys = {
        asset_path.name,
        asset_path.relative_to(images_dir).as_posix(),
    }
    return {key.replace("\\", "/") for key in keys if key}


def _match_asset_manifest_item(
    asset_path: Path,
    images_dir: Path,
    manifest_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    match_keys = _asset_manifest_match_keys(asset_path, images_dir)
    for item in manifest_items:
        candidate_values: set[str] = set()
        for key in ("filename", "original_filename", "source_target"):
            raw_value = str(item.get(key) or "").strip()
            if not raw_value:
                continue
            normalized = raw_value.replace("\\", "/")
            candidate_values.add(normalized)
            candidate_values.add(Path(normalized).name)
        if match_keys & candidate_values:
            return item
    return None


def _flatten_asset_metadata_value(value: Any) -> list[str]:
    if _is_blank_asset_value(value):
        return []
    if isinstance(value, dict):
        parts: list[str] = []
        for nested in value.values():
            parts.extend(_flatten_asset_metadata_value(nested))
        return parts
    if isinstance(value, list):
        parts: list[str] = []
        for nested in value:
            parts.extend(_flatten_asset_metadata_value(nested))
        return parts
    return [str(value)]


def _asset_metadata_bits(item: dict[str, Any] | None, *keys: str) -> list[str]:
    if not item:
        return []

    bits: list[str] = []
    for key in keys:
        values = [
            value.strip()
            for value in _flatten_asset_metadata_value(item.get(key))
            if value and value.strip()
        ]
        if not values:
            continue
        bits.append(f"{key}: {' / '.join(values[:3])}")
    return bits


def _asset_metadata_values(item: dict[str, Any] | None, *keys: str) -> list[str]:
    if not item:
        return []

    values: list[str] = []
    for key in keys:
        for value in _flatten_asset_metadata_value(item.get(key)):
            cleaned = value.strip()
            if cleaned:
                values.append(cleaned)
    return values


def _infer_asset_type(label: str, context: str, asset_path: Path | None, *, is_formula: bool = False) -> str:
    if is_formula:
        return "formula"

    stem = asset_path.stem.lower() if asset_path is not None else ""
    hint = f"{label} {context} {stem}".lower()
    if re.search(r"chart|graph|plot|curve|trend|figure|fig\.|图|表|曲线|趋势", hint):
        return "chart"
    if re.search(r"diagram|schematic|workflow|pipeline|architecture|framework|流程|架构|原理|示意", hint):
        return "diagram"
    if asset_path is not None and asset_path.suffix.lower() in {".svg", ".emf", ".wmf"}:
        return "diagram"
    return "image"


def _append_source_asset(
    source_assets: list[dict[str, Any]],
    *,
    asset_id: str,
    label: str,
    asset_path: Path | None,
    source_file: str,
    reference: str,
    context: str,
    asset_type: str,
    extra: dict[str, Any] | None = None,
    tag_parts: list[str] | None = None,
) -> None:
    path_text = str(asset_path) if asset_path is not None else ""
    tags = _extract_asset_tags(*(tag_parts or []), label, context, reference, Path(path_text).stem if path_text else "")
    if path_text:
        for existing in source_assets:
            if str(existing.get("path") or "") != path_text:
                continue
            existing["tags"] = _merge_asset_tag_lists(list(existing.get("tags") or []), tags)
            existing["context"] = _merge_asset_text(str(existing.get("context") or ""), context, max_length=240)
            existing["reference"] = _merge_asset_text(str(existing.get("reference") or ""), reference, max_length=240)
            existing["source_file"] = _prefer_source_file(str(existing.get("source_file") or ""), source_file)
            if str(existing.get("asset_type") or "image") == "image" and asset_type != "image":
                existing["asset_type"] = asset_type
            if extra:
                for key, value in extra.items():
                    if _is_blank_asset_value(value):
                        continue
                    existing[key] = value
            return
    source_assets.append({
        "id": asset_id,
        "alt": label[:120],
        "path": path_text,
        "source_file": source_file,
        "reference": reference,
        "context": context,
        "asset_type": asset_type,
        "tags": tags,
        **(_measure_image_dimensions(asset_path) if asset_path is not None and asset_path.exists() else {}),
        **(extra or {}),
    })


def _collect_project_image_assets(project_path: Path, source_assets: list[dict[str, Any]]) -> None:
    images_dir = project_path / "images"
    if not images_dir.exists():
        return

    image_manifest_items = _load_asset_manifest_items(images_dir / "image_manifest.json")
    image_source_items = _load_asset_manifest_items(images_dir / "image_sources.json")

    supported_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".emf", ".wmf"}
    ignored_names = {
        FORMULA_MANIFEST_FILENAME,
        "image_manifest.json",
        "image_sources.json",
        "image_prompts.json",
    }

    for asset_path in sorted(images_dir.rglob("*")):
        if not asset_path.is_file():
            continue
        if any(part == ".cache" for part in asset_path.parts):
            continue
        if asset_path.name in ignored_names:
            continue
        if asset_path.suffix.lower() not in supported_suffixes:
            continue
        if asset_path.name.startswith("formula_"):
            continue

        manifest_item = _match_asset_manifest_item(asset_path, images_dir, image_manifest_items)
        source_item = _match_asset_manifest_item(asset_path, images_dir, image_source_items)

        label = asset_path.stem.replace("_", " ").replace("-", " ").strip() or asset_path.name
        manifest_bits = _asset_metadata_bits(
            manifest_item,
            "source_kind",
            "source_file",
            "original_filename",
            "asset_kind",
            "source_namespace",
            "content_type",
        )
        source_bits = _asset_metadata_bits(
            source_item,
            "purpose",
            "slide",
            "provider",
            "title",
            "author",
            "license_name",
            "license_tier",
        )
        context = _merge_asset_text(
            f"Imported project image from {asset_path.parent.relative_to(project_path)}",
            " | ".join(manifest_bits[:3]),
            " | ".join(source_bits[:4]),
            max_length=240,
        )
        reference = _merge_asset_text(
            asset_path.name,
            " | ".join(_asset_metadata_bits(manifest_item, "source_target", "usage_count")),
            " | ".join(_asset_metadata_bits(source_item, "search_query", "source_page_url", "attribution_text")),
            max_length=240,
        )
        tag_parts = [
            *_asset_metadata_values(
                manifest_item,
                "source_kind",
                "source_file",
                "original_filename",
                "asset_kind",
                "source_namespace",
                "content_type",
                "source_target",
            ),
            *_asset_metadata_values(
                source_item,
                "purpose",
                "slide",
                "provider",
                "title",
                "author",
                "license_name",
                "license_tier",
                "search_query",
                "source_page_url",
                "attribution_text",
            ),
        ]
        asset_type = _infer_asset_type(label, context, asset_path)
        extra: dict[str, Any] = {
            "source_kind": str(manifest_item.get("source_kind") or "") if manifest_item else "",
            "original_filename": str(manifest_item.get("original_filename") or "") if manifest_item else "",
            "usage_count": manifest_item.get("usage_count") if manifest_item else None,
            "source_namespace": str(manifest_item.get("source_namespace") or "") if manifest_item else "",
            "candidate_material": bool(manifest_item.get("candidate_material", False)) if manifest_item else False,
            "selected_for_deck": bool(manifest_item.get("selected_for_deck", False)) if manifest_item else False,
            "provider": str(source_item.get("provider") or "") if source_item else "",
            "purpose": str(source_item.get("purpose") or "") if source_item else "",
            "search_query": str(source_item.get("search_query") or "") if source_item else "",
            "source_title": str(source_item.get("title") or "") if source_item else "",
            "source_author": str(source_item.get("author") or "") if source_item else "",
            "license_name": str(source_item.get("license_name") or "") if source_item else "",
            "license_tier": str(source_item.get("license_tier") or "") if source_item else "",
            "source_page_url": str(source_item.get("source_page_url") or "") if source_item else "",
        }
        _append_source_asset(
            source_assets,
            asset_id=f"IMG_{len(source_assets) + 1:03d}",
            label=label,
            asset_path=asset_path,
            source_file="images/",
            reference=reference,
            context=context,
            asset_type=asset_type,
            extra=extra,
            tag_parts=tag_parts,
        )


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _formula_report_paths(project_path: Path) -> tuple[Path, Path]:
    notes_dir = project_path / "notes"
    return notes_dir / FORMULA_RENDER_REPORT_JSON, notes_dir / FORMULA_RENDER_REPORT_MD


def _relative_project_path(project_path: Path, target_path: Path | None) -> str:
    if target_path is None:
        return ""
    try:
        return target_path.resolve().relative_to(project_path.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def _project_file_url(project_name: str, relative_path: str) -> str:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if not normalized:
        return ""
    return f"/api/projects/{quote(project_name)}/files/{quote(normalized, safe='/')}"


def _asset_family(asset_type: str) -> str:
    return "formula" if str(asset_type or "").strip().lower() == "formula" else "image"


def _normalize_asset_status(raw_status: str, *, asset_family: str, has_file: bool) -> str:
    status = str(raw_status or "").strip().lower()
    if asset_family == "formula":
        if status == "error":
            status = "failed"
        if status == "rendered" and not has_file:
            return "missing"
        if status in {"rendered", "failed", "pending", "missing"}:
            return status
        return "rendered" if has_file else "pending"
    return "rendered" if has_file else "missing"


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for raw_item in value:
            cleaned = str(raw_item or "").strip()
            if cleaned:
                items.append(cleaned)
        return items
    return []


def _load_formula_assets(project_path: Path) -> list[dict[str, Any]]:
    manifest_path = project_path / "images" / FORMULA_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return []

    try:
        entries = load_formula_manifest_entries(manifest_path)
    except Exception:
        return []

    formula_assets: list[dict[str, Any]] = []
    for entry in entries:
        extra = getattr(entry, "extra", {}) or {}
        raw_context = str(getattr(entry, "context", "") or "").strip()
        raw_latex = str(getattr(entry, "latex", "") or "").strip()
        raw_source_file = str(extra.get("source_file") or "").strip()
        svg_path_text = str(getattr(entry, "svg_path", "") or "").strip()
        resolved_path = (manifest_path.parent / svg_path_text).resolve() if svg_path_text else None
        if resolved_path is not None and not resolved_path.exists():
            resolved_path = None
        label = raw_context or raw_latex[:80] or f"公式 {getattr(entry, 'id', '')}"
        tags = _coerce_string_list(extra.get("tags"))
        if not tags:
            tags = _extract_asset_tags(raw_latex, raw_context, raw_source_file)

        extra_payload: dict[str, Any] = {
            "latex": raw_latex,
            "display": bool(getattr(entry, "display", True)),
            "status": str(getattr(entry, "status", "pending") or "pending"),
            "error": str(getattr(entry, "error", "") or "").strip(),
            "source_kind": str(extra.get("source_kind") or "parsed_markdown"),
            "line_number": extra.get("line_number"),
            "candidate_material": bool(extra.get("candidate_material", False)),
            "selected_for_deck": bool(extra.get("selected_for_deck", False)),
            "tags": tags,
        }
        _append_source_asset(
            formula_assets,
            asset_id=f"FORM_{getattr(entry, 'id', '')}",
            label=label,
            asset_path=resolved_path,
            source_file=raw_source_file,
            reference=raw_latex,
            context=raw_context,
            asset_type="formula",
            extra=extra_payload,
            tag_parts=[*tags, raw_latex, raw_context, raw_source_file],
        )
    return formula_assets


def _clear_formula_artifacts(project_path: Path) -> list[str]:
    images_dir = project_path / "images"
    manifest_path = images_dir / FORMULA_MANIFEST_FILENAME
    report_json_path, report_md_path = _formula_report_paths(project_path)
    removed: list[str] = []
    for path in [manifest_path, report_json_path, report_md_path, *sorted(images_dir.glob("formula_*.svg"))]:
        if not path.exists() or not path.is_file():
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def _project_text_sources(project_path: Path) -> list[tuple[Path, str]]:
    sources_dir = project_path / "sources"
    text_sources: list[tuple[Path, str]] = []
    if not sources_dir.exists():
        return text_sources

    for source_path in sorted(sources_dir.iterdir()):
        if not source_path.is_file() or source_path.suffix.lower() not in TEXT_SOURCE_EXTENSIONS:
            continue
        text_sources.append((source_path, _read_text_file(source_path)))
    return text_sources


def _formula_sources_from_text_sources(text_sources: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    return [
        (source_path, content)
        for source_path, content in text_sources
        if "$" in content or "\\begin{" in content
    ]


def _formula_assets_need_refresh(
    project_path: Path,
    text_sources: list[tuple[Path, str]],
    formula_sources: list[tuple[Path, str]],
) -> bool:
    manifest_path = project_path / "images" / FORMULA_MANIFEST_FILENAME
    report_json_path, report_md_path = _formula_report_paths(project_path)
    formula_svgs = list((project_path / "images").glob("formula_*.svg"))
    artifact_mtime = max(
        [_safe_mtime(manifest_path), _safe_mtime(report_json_path), _safe_mtime(report_md_path), *(_safe_mtime(path) for path in formula_svgs)],
        default=0.0,
    )

    if not formula_sources:
        return artifact_mtime > 0

    newest_source_mtime = max((_safe_mtime(source_path) for source_path, _content in text_sources), default=0.0)
    if artifact_mtime <= 0:
        return True
    return newest_source_mtime > artifact_mtime


def _sync_project_formula_assets(
    project_path: Path,
    *,
    text_sources: list[tuple[Path, str]] | None = None,
) -> dict[str, Any]:
    resolved_text_sources = text_sources if text_sources is not None else _project_text_sources(project_path)
    formula_sources = _formula_sources_from_text_sources(resolved_text_sources)
    removed = _clear_formula_artifacts(project_path)
    if not formula_sources:
        return {
            "total": 0,
            "rendered": 0,
            "failed": 0,
            "pending": 0,
            "missing": 0,
            "removed": removed,
            "updated": bool(removed),
        }

    formula_assets = _build_formula_assets(project_path, formula_sources)
    report = _build_formula_render_report(project_path, formula_assets)
    report_paths = _write_formula_render_report(project_path, report)
    summary = dict(report.get("summary") or {})
    summary.update({
        "removed": removed,
        "updated": True,
        "report_paths": report_paths,
    })
    return summary


def _persist_asset_planning_state(project_path: Path, source_assets: list[dict[str, Any]]) -> None:
    formula_manifest_path = project_path / "images" / FORMULA_MANIFEST_FILENAME
    image_manifest_path = project_path / "images" / "image_manifest.json"

    formula_state_by_id: dict[str, dict[str, bool]] = {}
    image_state_by_filename: dict[str, dict[str, bool]] = {}
    for raw_asset in source_assets:
        candidate_material = bool(raw_asset.get("candidate_material", False))
        selected_for_deck = bool(raw_asset.get("selected_for_deck", False))
        asset_type = str(raw_asset.get("asset_type") or "image").strip().lower()
        path_text = str(raw_asset.get("path") or "").strip()
        if asset_type == "formula":
            asset_id = str(raw_asset.get("id") or "").strip()
            if asset_id.startswith("FORM_"):
                asset_id = asset_id[5:]
            if asset_id:
                formula_state_by_id[asset_id] = {
                    "candidate_material": candidate_material,
                    "selected_for_deck": selected_for_deck,
                }
            continue

        if not path_text:
            continue
        asset_path = Path(path_text)
        filename = asset_path.name
        if filename:
            image_state_by_filename[filename] = {
                "candidate_material": candidate_material,
                "selected_for_deck": selected_for_deck,
            }

    if formula_manifest_path.is_file():
        try:
            payload = json.loads(formula_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            formulas = payload.get("formulas")
            changed = False
            if isinstance(formulas, list):
                for formula in formulas:
                    if not isinstance(formula, dict):
                        continue
                    state = formula_state_by_id.get(str(formula.get("id") or ""), {
                        "candidate_material": False,
                        "selected_for_deck": False,
                    })
                    for key, value in state.items():
                        if bool(formula.get(key, False)) != value:
                            formula[key] = value
                            changed = True
                if changed:
                    formula_manifest_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

    image_manifest_items: list[dict[str, Any]] = []
    if image_manifest_path.is_file():
        try:
            loaded = json.loads(image_manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                image_manifest_items = [item for item in loaded if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            image_manifest_items = []

    manifest_by_filename = {
        str(item.get("filename") or ""): item
        for item in image_manifest_items
        if str(item.get("filename") or "").strip()
    }
    changed = False
    for filename, state in image_state_by_filename.items():
        item = manifest_by_filename.get(filename)
        if item is None:
            item = {"filename": filename}
            image_manifest_items.append(item)
            manifest_by_filename[filename] = item
            changed = True
        for key, value in state.items():
            if bool(item.get(key, False)) != value:
                item[key] = value
                changed = True
    if changed:
        image_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        image_manifest_path.write_text(
            json.dumps(image_manifest_items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _build_formula_render_report(project_path: Path, formula_assets: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "total": len(formula_assets),
        "rendered": 0,
        "failed": 0,
        "pending": 0,
        "missing": 0,
    }
    failed_items: list[dict[str, Any]] = []

    for asset in formula_assets:
        asset_path = Path(str(asset.get("path") or "")).resolve() if asset.get("path") else None
        has_file = bool(asset_path and asset_path.exists())
        status = _normalize_asset_status(
            str(asset.get("status") or ""),
            asset_family="formula",
            has_file=has_file,
        )
        summary[status] += 1

        error_text = str(asset.get("render_error") or asset.get("error") or "").strip()
        if status in {"failed", "missing"}:
            failed_items.append({
                "id": str(asset.get("id") or ""),
                "title": str(asset.get("alt") or ""),
                "latex": str(asset.get("latex") or "")[:240],
                "source_file": str(asset.get("source_file") or ""),
                "status": status,
                "error": error_text or ("公式 SVG 文件缺失" if status == "missing" else ""),
            })

    return {
        "project_name": project_path.name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "failed_items": failed_items,
    }


def _render_formula_render_report_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    failed_items = list(report.get("failed_items") or [])
    lines = [
        "# 公式渲染报告",
        "",
        f"- 项目：{report.get('project_name') or ''}",
        f"- 生成时间：{report.get('generated_at') or ''}",
        "",
        "## 汇总",
        "",
        f"- 总公式数：{summary.get('total', 0)}",
        f"- 渲染成功：{summary.get('rendered', 0)}",
        f"- 渲染失败：{summary.get('failed', 0)}",
        f"- 等待渲染：{summary.get('pending', 0)}",
        f"- 结果缺失：{summary.get('missing', 0)}",
        "",
        "## 失败清单",
        "",
    ]
    if not failed_items:
        lines.append("- 当前没有失败项。")
        return "\n".join(lines)

    for item in failed_items:
        lines.append(f"### {item.get('id') or '未命名公式'}")
        if item.get("title"):
            lines.append(f"- 标题：{item['title']}")
        if item.get("source_file"):
            lines.append(f"- 来源文件：{item['source_file']}")
        if item.get("status"):
            lines.append(f"- 状态：{item['status']}")
        if item.get("latex"):
            lines.append(f"- LaTeX：`{item['latex']}`")
        lines.append(f"- 错误：{item.get('error') or '未知错误'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_formula_render_report(project_path: Path, report: dict[str, Any]) -> dict[str, str]:
    summary = report.get("summary") or {}
    if int(summary.get("total") or 0) <= 0:
        return {}

    json_path, markdown_path = _formula_report_paths(project_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_formula_render_report_markdown(report), encoding="utf-8")

    return {
        "json": json_path.relative_to(project_path).as_posix(),
        "markdown": markdown_path.relative_to(project_path).as_posix(),
    }


def _count_project_image_assets(project_path: Path) -> tuple[int, float]:
    images_dir = project_path / "images"
    if not images_dir.exists():
        return 0, 0.0

    count = 0
    last_refresh_ts = _safe_mtime(images_dir)
    supported_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".emf", ".wmf"}
    ignored_names = {
        FORMULA_MANIFEST_FILENAME,
        FORMULA_RENDER_REPORT_JSON,
        FORMULA_RENDER_REPORT_MD,
        "image_manifest.json",
        "image_sources.json",
        "image_prompts.json",
    }
    for asset_path in images_dir.rglob("*"):
        if not asset_path.is_file():
            continue
        if any(part == ".cache" for part in asset_path.parts):
            continue
        last_refresh_ts = max(last_refresh_ts, _safe_mtime(asset_path))
        if asset_path.name in ignored_names:
            continue
        if asset_path.suffix.lower() not in supported_suffixes:
            continue
        if asset_path.name.startswith("formula_"):
            continue
        count += 1
    return count, last_refresh_ts


def _formula_asset_summary(project_path: Path) -> dict[str, Any]:
    manifest_path = project_path / "images" / FORMULA_MANIFEST_FILENAME
    summary = {
        "total": 0,
        "rendered": 0,
        "failed": 0,
        "pending": 0,
        "missing": 0,
        "candidate_material": 0,
        "selected_for_deck": 0,
    }
    last_refresh_ts = _safe_mtime(manifest_path)
    if not manifest_path.is_file():
        return {"summary": summary, "last_refresh_ts": last_refresh_ts}

    try:
        entries = load_formula_manifest_entries(manifest_path)
    except Exception:
        return {"summary": summary, "last_refresh_ts": last_refresh_ts}

    for entry in entries:
        extra = getattr(entry, "extra", {}) or {}
        svg_path_text = str(getattr(entry, "svg_path", "") or "").strip()
        resolved_path = (manifest_path.parent / svg_path_text).resolve() if svg_path_text else None
        has_file = bool(resolved_path and resolved_path.exists())
        status = _normalize_asset_status(
            str(getattr(entry, "status", "") or ""),
            asset_family="formula",
            has_file=has_file,
        )
        summary["total"] += 1
        summary[status] += 1
        if bool(extra.get("candidate_material", False)):
            summary["candidate_material"] += 1
        if bool(extra.get("selected_for_deck", False)):
            summary["selected_for_deck"] += 1
        if has_file and resolved_path is not None:
            last_refresh_ts = max(last_refresh_ts, _safe_mtime(resolved_path))

    report_json_path, report_md_path = _formula_report_paths(project_path)
    last_refresh_ts = max(last_refresh_ts, _safe_mtime(report_json_path), _safe_mtime(report_md_path))
    return {"summary": summary, "last_refresh_ts": last_refresh_ts}


def _project_asset_summary(project_path: Path) -> dict[str, Any]:
    image_count, image_refresh_ts = _count_project_image_assets(project_path)
    image_manifest_items = _load_asset_manifest_items(project_path / "images" / "image_manifest.json")
    image_candidate_count = sum(1 for item in image_manifest_items if bool(item.get("candidate_material", False)))
    image_selected_count = sum(1 for item in image_manifest_items if bool(item.get("selected_for_deck", False)))
    formula_summary_payload = _formula_asset_summary(project_path)
    formula_summary = dict(formula_summary_payload.get("summary") or {})
    formula_candidate_count = int(formula_summary.get("candidate_material") or 0)
    formula_selected_count = int(formula_summary.get("selected_for_deck") or 0)
    report_json_path, report_md_path = _formula_report_paths(project_path)
    last_refresh_ts = max(
        image_refresh_ts,
        float(formula_summary_payload.get("last_refresh_ts") or 0.0),
        _safe_mtime(report_json_path),
        _safe_mtime(report_md_path),
    )
    return {
        "asset_count": image_count + int(formula_summary.get("total") or 0),
        "image_count": image_count,
        "formula_count": int(formula_summary.get("total") or 0),
        "formula_rendered_count": int(formula_summary.get("rendered") or 0),
        "formula_failed_count": int(formula_summary.get("failed") or 0),
        "formula_pending_count": int(formula_summary.get("pending") or 0),
        "formula_missing_count": int(formula_summary.get("missing") or 0),
        "image_candidate_count": image_candidate_count,
        "image_selected_count": image_selected_count,
        "formula_candidate_count": formula_candidate_count,
        "formula_selected_count": formula_selected_count,
        "candidate_material_count": image_candidate_count + formula_candidate_count,
        "selected_for_deck_count": image_selected_count + formula_selected_count,
        "report_ready": report_json_path.is_file() and report_md_path.is_file(),
        "last_asset_refresh_at": _format_timestamp(last_refresh_ts),
        "last_asset_refresh_ts": last_refresh_ts,
    }


def _serialize_project_asset(project_path: Path, raw_asset: dict[str, Any]) -> dict[str, Any]:
    path_text = str(raw_asset.get("path") or "").strip()
    asset_path = Path(path_text) if path_text else None
    if asset_path is not None and not asset_path.exists():
        asset_path = None
    relative_path = _relative_project_path(project_path, asset_path)
    has_file = bool(relative_path and asset_path is not None and asset_path.exists())
    asset_type = str(raw_asset.get("asset_type") or "image") or "image"
    asset_family = _asset_family(asset_type)
    status = _normalize_asset_status(
        str(raw_asset.get("status") or ""),
        asset_family=asset_family,
        has_file=has_file,
    )
    tags = _merge_asset_tag_lists(_coerce_string_list(raw_asset.get("tags")))
    title = str(raw_asset.get("alt") or raw_asset.get("id") or "未命名素材").strip()[:120]
    description = _merge_asset_text(
        str(raw_asset.get("figure_caption") or ""),
        str(raw_asset.get("source_title") or ""),
        str(raw_asset.get("context") or ""),
        max_length=240,
    )
    preview_url = _project_file_url(project_path.name, relative_path) if has_file else ""
    return {
        "id": str(raw_asset.get("id") or ""),
        "asset_type": asset_type,
        "asset_family": asset_family,
        "status": status,
        "title": title,
        "description": description,
        "source_file": str(raw_asset.get("source_file") or ""),
        "source_kind": str(raw_asset.get("source_kind") or ""),
        "original_filename": str(raw_asset.get("original_filename") or ""),
        "relative_path": relative_path,
        "preview_url": preview_url,
        "thumbnail_url": preview_url,
        "download_url": preview_url,
        "tags": tags,
        "context": str(raw_asset.get("context") or ""),
        "reference": str(raw_asset.get("reference") or ""),
        "figure_label": str(raw_asset.get("figure_label") or ""),
        "figure_caption": str(raw_asset.get("figure_caption") or ""),
        "section_heading": str(raw_asset.get("section_heading") or ""),
        "width": raw_asset.get("width"),
        "height": raw_asset.get("height"),
        "orientation": str(raw_asset.get("orientation") or ""),
        "latex": str(raw_asset.get("latex") or ""),
        "display": raw_asset.get("display"),
        "error": str(raw_asset.get("render_error") or raw_asset.get("error") or ""),
        "candidate_material": bool(raw_asset.get("candidate_material", False)),
        "selected_for_deck": bool(raw_asset.get("selected_for_deck", False)),
        "provider": str(raw_asset.get("provider") or ""),
        "purpose": str(raw_asset.get("purpose") or ""),
        "search_query": str(raw_asset.get("search_query") or ""),
        "source_page_url": str(raw_asset.get("source_page_url") or ""),
        "line_number": raw_asset.get("line_number"),
        "report_anchor": str(raw_asset.get("id") or ""),
    }


def _filter_project_assets(
    items: list[dict[str, Any]],
    *,
    asset_type: str,
    status: str,
    keyword: str,
    source_file: str,
) -> list[dict[str, Any]]:
    normalized_type = asset_type.strip().lower()
    normalized_status = status.strip().lower()
    normalized_keyword = keyword.strip().lower()
    normalized_source_file = source_file.strip().lower()

    filtered: list[dict[str, Any]] = []
    for item in items:
        if normalized_type and normalized_type != "all" and str(item.get("asset_family") or "") != normalized_type:
            continue
        if normalized_status and normalized_status != "all" and str(item.get("status") or "") != normalized_status:
            continue
        if normalized_source_file and str(item.get("source_file") or "").strip().lower() != normalized_source_file:
            continue
        if normalized_keyword:
            haystack = " ".join([
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                str(item.get("source_file") or ""),
                str(item.get("context") or ""),
                str(item.get("latex") or ""),
                str(item.get("reference") or ""),
                str(item.get("section_heading") or ""),
                " ".join(item.get("tags") or []),
            ]).lower()
            if normalized_keyword not in haystack:
                continue
        filtered.append(item)
    return filtered


def _project_assets_payload(
    project_path: Path,
    *,
    asset_type: str = "all",
    status: str = "all",
    keyword: str = "",
    source_file: str = "",
    limit: int = 0,
    offset: int = 0,
) -> dict[str, Any]:
    image_assets: list[dict[str, Any]] = []
    _collect_project_image_assets(project_path, image_assets)
    formula_assets = _load_formula_assets(project_path)
    formula_report = _build_formula_render_report(project_path, formula_assets)
    report_paths = _write_formula_render_report(project_path, formula_report)

    items = [_serialize_project_asset(project_path, asset) for asset in [*image_assets, *formula_assets]]
    items.sort(key=lambda item: (item.get("asset_family") != "formula", item.get("source_file") or "", item.get("id") or ""))
    filtered_items = _filter_project_assets(
        items,
        asset_type=asset_type,
        status=status,
        keyword=keyword,
        source_file=source_file,
    )
    total_items = len(filtered_items)
    if offset > 0:
        filtered_items = filtered_items[offset:]
    if limit > 0:
        filtered_items = filtered_items[:limit]

    filters = {
        "source_files": sorted({str(item.get("source_file") or "") for item in items if str(item.get("source_file") or "").strip()}),
        "tags": sorted({tag for item in items for tag in (item.get("tags") or [])})[:40],
        "statuses": sorted({str(item.get("status") or "") for item in items if str(item.get("status") or "").strip()}),
        "types": sorted({str(item.get("asset_family") or "") for item in items if str(item.get("asset_family") or "").strip()}),
    }
    summary = {
        **_project_asset_summary(project_path),
        "formula_rendered_count": int((formula_report.get("summary") or {}).get("rendered") or 0),
        "formula_failed_count": int((formula_report.get("summary") or {}).get("failed") or 0),
        "formula_pending_count": int((formula_report.get("summary") or {}).get("pending") or 0),
        "formula_missing_count": int((formula_report.get("summary") or {}).get("missing") or 0),
        "report_ready": bool(report_paths),
    }

    reports: dict[str, str] = {}
    if report_paths.get("json"):
        reports["formula_render_report_json"] = _project_file_url(project_path.name, report_paths["json"])
    if report_paths.get("markdown"):
        reports["formula_render_report_md"] = _project_file_url(project_path.name, report_paths["markdown"])

    return {
        "success": True,
        "project_name": project_path.name,
        "summary": summary,
        "reports": reports,
        "formula_report": formula_report,
        "filters": filters,
        "query": {
            "type": asset_type,
            "status": status,
            "q": keyword,
            "source_file": source_file,
            "limit": limit,
            "offset": offset,
        },
        "total_items": total_items,
        "returned_items": len(filtered_items),
        "items": filtered_items,
    }


def _image_extension_from_content_type(content_type: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
        "image/x-emf": ".emf",
        "image/x-wmf": ".wmf",
    }
    return mapping.get(content_type.lower(), ".img")


def _download_remote_image(url: str, cache_dir: Path) -> Path | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and not content_type.startswith("image/"):
        return None

    if not suffix:
        suffix = _image_extension_from_content_type(content_type)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    output_path = cache_dir / f"{digest}{suffix or '.img'}"
    if not output_path.exists():
        output_path.write_bytes(response.content)
    return output_path


def _resolve_markdown_image(markdown_path: Path, raw_target: str, project_path: Path) -> Path | None:
    target = _normalize_markdown_target(raw_target)
    if not target or target.startswith("data:"):
        return None

    if re.match(r"^[A-Za-z]:[\\/]", target):
        candidate = Path(target)
        return candidate if candidate.exists() else None

    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"}:
        try:
            return _download_remote_image(target, project_path / "sources" / "_remote_assets")
        except requests.RequestException:
            return None

    relative_target = unquote(target.split("#", 1)[0])
    candidate = (markdown_path.parent / relative_target).resolve()
    if candidate.exists():
        return candidate
    return None


def _extract_markdown_images(project_path: Path, markdown_path: Path, content: str, source_images: list[dict[str, Any]]) -> str:
    def replace_image(match: re.Match[str]) -> str:
        raw_target = match.group(2)
        resolved = _resolve_markdown_image(markdown_path, raw_target, project_path)

        alt = re.sub(r"\s+", " ", match.group(1)).strip()
        figure_meta = _extract_figure_metadata(content, match.start(), match.end(), alt)
        label = (
            alt
            or str(figure_meta.get("figure_caption") or "").strip()
            or Path(urlparse(_normalize_markdown_target(raw_target)).path).name
            or "source image"
        )
        image_id = f"IMG_{len(source_images) + 1:03d}"
        context = _merge_asset_text(
            _extract_image_context(content, match.start(), match.end()),
            figure_meta.get("figure_caption") or "",
            figure_meta.get("section_heading_path") or "",
            max_length=240,
        )

        if resolved is not None and resolved.exists():
            _append_source_asset(
                source_images,
                asset_id=image_id,
                label=label,
                asset_path=resolved,
                source_file=str(markdown_path.name),
                reference=_normalize_markdown_target(raw_target),
                context=context,
                asset_type=_infer_asset_type(label, context, resolved),
                extra={
                    "source_kind": "markdown_asset",
                    **{key: value for key, value in figure_meta.items() if value},
                },
                tag_parts=[
                    alt,
                    figure_meta.get("figure_label") or "",
                    figure_meta.get("figure_ref_key") or "",
                    figure_meta.get("figure_caption") or "",
                    figure_meta.get("section_heading") or "",
                    figure_meta.get("section_heading_path") or "",
                ],
            )
            return f"[Source Image {image_id}: {label}]"

        return f"[Image reference: {label}]"

    return MARKDOWN_IMAGE_PATTERN.sub(replace_image, content)


def _build_formula_assets(project_path: Path, formula_sources: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    if not formula_sources:
        return []

    extracted_formulas: list[Any] = []
    for source_path, content in formula_sources:
        extracted_formulas.extend(
            extract_formulas_from_markdown(
                content,
                source_file=source_path.name,
            )
        )

    if not extracted_formulas:
        return []

    manifest = build_formula_manifest(extracted_formulas, source_file="")
    formulas = list(manifest.get("formulas") or [])
    for index, formula in enumerate(formulas, start=1):
        short_hash = hashlib.md5(str(formula.get("latex") or "").encode("utf-8")).hexdigest()[:6]
        formula["id"] = f"{index:03d}_{short_hash}"
        formula["render"] = True
        formula["status"] = "pending"
        formula["tags"] = formula.get("tags") or _extract_asset_tags(
            str(formula.get("latex") or ""),
            str(formula.get("context") or ""),
            str(formula.get("source_file") or ""),
        )
        formula["candidate_material"] = bool(formula.get("candidate_material", False))
        formula["selected_for_deck"] = bool(formula.get("selected_for_deck", False))

    manifest_path = project_path / "images" / FORMULA_MANIFEST_FILENAME
    save_formula_manifest(manifest, manifest_path)

    render_error = ""
    try:
        process_formula_manifest(manifest_path)
    except Exception as exc:
        render_error = str(exc).strip() or "Formula rendering failed."
        try:
            entries = load_formula_manifest_entries(manifest_path)
            for entry in entries:
                if str(getattr(entry, "status", "pending") or "pending").strip().lower() == "rendered":
                    continue
                entry.status = "failed"
                if not str(getattr(entry, "error", "") or "").strip():
                    entry.error = render_error
            save_formula_manifest_entries(manifest_path, entries)
        except Exception:
            pass

    formula_assets: list[dict[str, Any]] = []
    try:
        entries = load_formula_manifest_entries(manifest_path)
    except Exception:
        return formula_assets

    for entry in entries:
        raw_context = str(getattr(entry, "context", "") or "").strip()
        raw_latex = str(getattr(entry, "latex", "") or "").strip()
        raw_source_file = str((getattr(entry, "extra", {}) or {}).get("source_file") or "").strip()
        label = raw_context or raw_latex[:80] or f"公式 {getattr(entry, 'id', '')}"
        svg_path_text = str(getattr(entry, "svg_path", "") or "").strip()
        resolved_path = (manifest_path.parent / svg_path_text).resolve() if svg_path_text else None
        if resolved_path is not None and not resolved_path.exists():
            resolved_path = None
        extra: dict[str, Any] = {
            "latex": raw_latex,
            "display": bool(getattr(entry, "display", True)),
            "status": str(getattr(entry, "status", "pending") or "pending"),
        }
        entry_error = str(getattr(entry, "error", "") or "").strip()
        if entry_error:
            extra["render_error"] = entry_error
        elif render_error:
            extra["render_error"] = render_error
        _append_source_asset(
            formula_assets,
            asset_id=f"FORM_{getattr(entry, 'id', '')}",
            label=label,
            asset_path=resolved_path,
            source_file=raw_source_file,
            reference=raw_latex,
            context=raw_context,
            asset_type="formula",
            extra=extra,
        )

    return formula_assets


def _read_source_bundle(project_path: Path) -> dict[str, object]:
    sources_dir = project_path / "sources"
    parts: list[str] = []
    source_images: list[dict[str, Any]] = []
    text_sources: list[tuple[Path, str]] = []
    if not sources_dir.exists():
        return {"text": "", "images": source_images}
    for source in sorted(sources_dir.iterdir()):
        if source.is_file() and source.suffix.lower() in TEXT_SOURCE_EXTENSIONS:
            content = _read_text_file(source)
            text_sources.append((source, content))
            if "![" in content:
                content = _extract_markdown_images(project_path, source, content, source_images)
            if content.strip():
                parts.append(content.strip())
    _collect_project_image_assets(project_path, source_images)
    formula_sources = _formula_sources_from_text_sources(text_sources)
    if _formula_assets_need_refresh(project_path, text_sources, formula_sources):
        _sync_project_formula_assets(project_path, text_sources=text_sources)
    source_images.extend(_load_formula_assets(project_path))
    return {
        "text": "\n\n".join(parts).strip(),
        "images": source_images,
    }


def _read_preview(project_path: Path) -> str:
    return str(_read_source_bundle(project_path)["text"])[:5000]


def _detect_canvas_format(project_path: Path) -> str:
    readme_path = project_path / "README.md"
    if readme_path.exists():
        readme = readme_path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"Canvas format:\s*([A-Za-z0-9_\-]+)", readme)
        if match:
            return match.group(1)
    return "ppt169"


def _format_timestamp(timestamp: float) -> str | None:
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return None


def _project_created_at(project_path: Path) -> float:
    try:
        return float(project_path.stat().st_ctime)
    except OSError:
        return 0.0


def _render_asset_match_report_markdown(diagnostics: list[dict[str, Any]]) -> str:
    lines = ["# 图文匹配诊断报告", ""]
    if not diagnostics:
        lines.append("暂无图文匹配诊断数据。")
        return "\n".join(lines) + "\n"

    for index, section in enumerate(diagnostics, start=1):
        lines.append(f"## {index}. {section.get('section_title') or '未命名章节'}")
        lead = str(section.get("lead") or "").strip()
        if lead:
            lines.append(f"- Lead: {lead}")
        figure_refs = list(section.get("section_figure_refs") or [])
        lines.append(f"- 显式图号引用: {', '.join(figure_refs) if figure_refs else '（无）'}")
        lines.append(f"- 目标配图容量: {int(section.get('capacity') or 0)}")

        selected_assets = list(section.get("selected_assets") or [])
        lines.append("- 已选素材:")
        if not selected_assets:
            lines.append("  - （无）")
        else:
            for candidate in selected_assets:
                lines.append(
                    "  - [{decision}] {asset_id} {asset_label} | {asset_type} | score={score}".format(
                        decision=str(candidate.get("decision") or "selected"),
                        asset_id=str(candidate.get("asset_id") or "NO_ID"),
                        asset_label=str(candidate.get("asset_label") or "asset"),
                        asset_type=str(candidate.get("asset_type") or "image"),
                        score=str(candidate.get("score") or 0),
                    )
                )
                if candidate.get("figure_label"):
                    lines.append(f"    - 图号: {candidate['figure_label']}")
                if candidate.get("figure_caption"):
                    lines.append(f"    - 图注: {candidate['figure_caption']}")
                reasons = list(candidate.get("reasons") or [])
                if reasons:
                    lines.append(f"    - 原因: {'; '.join(reasons)}")

        top_candidates = list(section.get("top_candidates") or [])
        lines.append(f"- Top 候选 ({len(top_candidates)}):")
        if not top_candidates:
            lines.append("  - （无）")
        else:
            for rank, candidate in enumerate(top_candidates, start=1):
                lines.append(
                    "  {rank}. [{decision}] {asset_id} {asset_label} | {asset_type} | score={score} | explicit={explicit}".format(
                        rank=rank,
                        decision=str(candidate.get("decision") or "unknown"),
                        asset_id=str(candidate.get("asset_id") or "NO_ID"),
                        asset_label=str(candidate.get("asset_label") or "asset"),
                        asset_type=str(candidate.get("asset_type") or "image"),
                        score=str(candidate.get("score") or 0),
                        explicit=str(bool(candidate.get("explicit_binding"))).lower(),
                    )
                )
                if candidate.get("figure_label"):
                    lines.append(f"     - 图号: {candidate['figure_label']}")
                if candidate.get("figure_caption"):
                    lines.append(f"     - 图注: {candidate['figure_caption']}")
                reasons = list(candidate.get("reasons") or [])
                if reasons:
                    lines.append(f"     - 原因: {'; '.join(reasons)}")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_asset_match_reports(project_path: Path, diagnostics: list[dict[str, Any]]) -> list[Path]:
    if not diagnostics:
        return []

    notes_dir = project_path / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    json_path = notes_dir / ASSET_MATCH_REPORT_JSON
    markdown_path = notes_dir / ASSET_MATCH_REPORT_MD

    json_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_asset_match_report_markdown(diagnostics), encoding="utf-8")
    return [json_path, markdown_path]


def _project_updated_at(project_path: Path) -> float:
    timestamps: list[float] = []
    scan_paths = [
        project_path,
        project_path / "sources",
        project_path / "exports",
        project_path / "svg_output",
        project_path / "svg_final",
        project_path / "notes",
    ]
    for current_path in scan_paths:
        if not current_path.exists():
            continue
        try:
            timestamps.append(float(current_path.stat().st_mtime))
        except OSError:
            continue
        if not current_path.is_dir():
            continue
        for child in current_path.iterdir():
            try:
                timestamps.append(float(child.stat().st_mtime))
            except OSError:
                continue
    return max(timestamps, default=0.0)


def _generate_ppt(project_path: Path, canvas_format: str | None = None) -> tuple[Path, dict[str, object], str]:
    source_bundle = _read_source_bundle(project_path)
    source_text = str(source_bundle["text"])
    if not source_text:
        raise ValueError("项目中没有可用于生成 PPT 的文本素材。")
    resolved_canvas = canvas_format or _detect_canvas_format(project_path)
    export_name = f"{project_path.name}_gui_{datetime.now():%H%M%S}.pptx"
    export_path = project_path / "exports" / export_name
    result = build_presentation(
        source_text,
        project_path.name,
        export_path,
        resolved_canvas,
        source_images=list(source_bundle["images"]),
    )
    _persist_asset_planning_state(project_path, list(source_bundle["images"]))
    diagnostics = list(result.get("asset_match_diagnostics") or [])
    report_paths = _write_asset_match_reports(project_path, diagnostics)
    result["asset_match_report_paths"] = [str(path) for path in report_paths]
    return export_path, result, source_text[:5000]


def _project_to_json(project_path: Path) -> dict[str, object]:
    exports = sorted((project_path / "exports").glob("*.pptx"), key=lambda path: path.stat().st_mtime, reverse=True)
    sources_dir = project_path / "sources"
    source_paths = [file for file in sorted(sources_dir.iterdir()) if file.is_file()] if sources_dir.exists() else []
    source_files = [file.name for file in source_paths]
    source_exts = sorted({file.suffix.lower().lstrip(".") for file in source_paths if file.suffix})
    preview_record = _preview_record(project_path.name, project_path)
    created_at = _project_created_at(project_path)
    updated_at = _project_updated_at(project_path)
    return {
        "name": project_path.name,
        "path": str(project_path),
        "canvas_format": _detect_canvas_format(project_path),
        "exports": [file.name for file in exports],
        "export_count": len(exports),
        "has_exports": bool(exports),
        "latest_export": exports[0].name if exports else None,
        "latest_export_path": str(exports[0]) if exports else None,
        "source_files": source_files,
        "source_exts": source_exts,
        "source_count": len(source_files),
        "created_at": _format_timestamp(created_at),
        "created_at_ts": created_at,
        "updated_at": _format_timestamp(updated_at),
        "updated_at_ts": updated_at,
        "has_preview": preview_record is not None,
        "live_preview_url": preview_record["url"] if preview_record else None,
        "live_preview_port": int(preview_record["port"]) if preview_record else None,
        "asset_summary": _project_asset_summary(project_path),
    }


def _is_user_project_dir(project_path: Path) -> bool:
    if not project_path.is_dir() or project_path.name.startswith(("_", ".")):
        return False
    try:
        project_path.resolve().relative_to(PROJECTS_DIR.resolve())
    except ValueError:
        return False
    except OSError:
        return False
    return True


def _list_projects() -> list[dict[str, object]]:
    projects = [
        _project_to_json(project_path)
        for project_path in sorted(PROJECTS_DIR.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
        if _is_user_project_dir(project_path)
    ]
    return projects


def _delete_project(project_name: str) -> dict[str, Any]:
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        raise FileNotFoundError(project_name)
    if not _is_user_project_dir(project_path):
        raise ValueError("系统项目目录不允许删除。")

    _stop_live_preview(project_name)
    shutil.rmtree(project_path)
    with LIVE_PREVIEW_LOCK:
        LIVE_PREVIEW_PROCESSES.pop(project_name, None)

    return {
        "success": True,
        "project_name": project_name,
        "deleted": True,
        "message": f"项目 {project_name} 已删除。",
        "projects": _list_projects(),
    }


def _success_payload(
    project_path: Path,
    steps: list[dict[str, object]],
    preview: str,
    export_path: Path,
    deck_meta: dict[str, object],
    source_parser: str | None = None,
) -> dict[str, object]:
    source_bundle = _read_source_bundle(project_path)
    source_images = list(source_bundle.get("images") or [])
    sources_dir = project_path / "sources"
    exports_dir = project_path / "exports"
    notes_dir = project_path / "notes"
    text_sources = sorted(
        [path for path in sources_dir.iterdir() if path.is_file() and path.suffix.lower() in TEXT_SOURCE_EXTENSIONS],
        key=lambda path: path.name,
    ) if sources_dir.exists() else []
    original_sources = sorted(
        [path for path in sources_dir.iterdir() if path.is_file() and path.suffix.lower() not in TEXT_SOURCE_EXTENSIONS],
        key=lambda path: path.name,
    ) if sources_dir.exists() else []
    asset_dirs = sorted([path for path in sources_dir.iterdir() if path.is_dir()], key=lambda path: path.name) if sources_dir.exists() else []

    def location_entry(label: str, target_path: Path) -> dict[str, object]:
        entry: dict[str, object] = {
            "label": label,
            "path": str(target_path),
            "kind": "directory" if target_path.is_dir() else "file",
        }
        try:
            relative = target_path.relative_to(project_path).as_posix()
        except ValueError:
            return entry
        entry["relative_path"] = relative
        if target_path.is_file():
            entry["url"] = f"/api/projects/{quote(project_path.name)}/files/{quote(relative)}"
        return entry

    asset_match_diagnostics = list(deck_meta.get("asset_match_diagnostics") or [])
    report_paths = [
        Path(str(raw_path))
        for raw_path in deck_meta.get("asset_match_report_paths") or []
        if Path(str(raw_path)).exists()
    ]
    formula_report_paths = [
        path
        for path in _formula_report_paths(project_path)
        if path.exists()
    ]
    artifacts = [_project_artifact(project_path, export_path)]
    artifacts.extend(_project_artifact(project_path, path) for path in text_sources[:4])
    artifacts.extend(_project_artifact(project_path, path) for path in report_paths)
    artifacts.extend(_project_artifact(project_path, path) for path in formula_report_paths)

    file_locations = [
        location_entry("项目目录", project_path),
        location_entry("sources 目录", sources_dir),
        location_entry("exports 目录", exports_dir),
        location_entry("PPTX 输出文件", export_path),
    ]
    if notes_dir.exists():
        file_locations.append(location_entry("notes 目录", notes_dir))
    for index, path in enumerate(text_sources[:6], start=1):
        file_locations.append(location_entry(f"解析后文本 {index}", path))
    for index, path in enumerate(original_sources[:4], start=1):
        file_locations.append(location_entry(f"原始源文件 {index}", path))
    for index, path in enumerate(asset_dirs[:4], start=1):
        file_locations.append(location_entry(f"素材图片目录 {index}", path))
    for path in report_paths:
        label = "图文匹配诊断报告" if path.suffix.lower() == ".md" else "图文匹配诊断数据"
        file_locations.append(location_entry(label, path))
    for path in formula_report_paths:
        label = "公式渲染报告" if path.suffix.lower() == ".md" else "公式渲染数据"
        file_locations.append(location_entry(label, path))

    config_used = {
        "canvas_format": _detect_canvas_format(project_path),
        "content_language": "简体中文",
        "source_parser": source_parser or "已归档本地素材",
    }

    image_items: list[dict[str, object]] = []
    known_image_size_count = 0
    for raw_image in source_images[:18]:
        width = raw_image.get("width")
        height = raw_image.get("height")
        if width and height:
            known_image_size_count += 1
        image_items.append({
            "id": raw_image.get("id"),
            "alt": raw_image.get("alt"),
            "source_file": raw_image.get("source_file"),
            "asset_type": raw_image.get("asset_type"),
            "source_kind": raw_image.get("source_kind"),
            "tags": raw_image.get("tags") or [],
            "width": width,
            "height": height,
            "orientation": raw_image.get("orientation"),
            "context": raw_image.get("context"),
            "figure_label": raw_image.get("figure_label"),
            "figure_caption": raw_image.get("figure_caption"),
            "section_heading": raw_image.get("section_heading"),
        })

    return {
        "success": True,
        "project_name": project_path.name,
        "project_path": str(project_path),
        "steps": steps,
        "source_preview": preview,
        "download_url": f"/api/projects/{project_path.name}/exports/{export_path.name}",
        "export_name": export_path.name,
        "slide_count": deck_meta.get("slide_count"),
        "deck_title": deck_meta.get("title"),
        "source_image_count": len(source_images),
        "source_formula_count": sum(1 for raw_image in source_images if raw_image.get("asset_type") == "formula"),
        "placed_image_count": deck_meta.get("placed_image_count"),
        "source_parser": source_parser,
        "artifacts": artifacts,
        "file_locations": file_locations,
        "config_used": config_used,
        "source_images": {
            "count": len(source_images),
            "known_size_count": known_image_size_count,
            "items": image_items,
            "truncated": len(source_images) > len(image_items),
        },
        "asset_match_diagnostics": {
            "count": len(asset_match_diagnostics),
            "items": asset_match_diagnostics[:6],
            "truncated": len(asset_match_diagnostics) > 6,
            "report_files": [path.name for path in report_paths],
        },
    }


def _stream_event(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _stream_step_event(steps: list[dict[str, object]]) -> dict[str, Any]:
    return {
        "type": "step",
        "steps": steps,
        "step": steps[-1] if steps else None,
    }


def _stream_error_event(error: str, steps: list[dict[str, object]]) -> dict[str, Any]:
    return {
        "type": "error",
        "error": error,
        "steps": steps,
    }


def _stream_result_event(payload: dict[str, object]) -> dict[str, Any]:
    return {
        "type": "result",
        "data": payload,
    }


@app.route("/")
def index():
    return render_template("upload.html", projects=_list_projects())


@app.route("/upload")
def upload_page():
    return render_template("upload.html", projects=_list_projects())


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "没有选择文件"}), 400
    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "没有选择文件"}), 400
    if not _allowed_file(uploaded_file.filename):
        return jsonify({"error": f"不支持的文件格式: {Path(uploaded_file.filename).suffix}"}), 400

    project_name = request.form.get("project_name", "").strip() or Path(uploaded_file.filename).stem
    canvas_format = request.form.get("canvas_format", "ppt169").strip() or "ppt169"
    source_parser = None
    if Path(uploaded_file.filename).suffix.lower() == ".pdf":
        source_parser = "MinerU 云解析"

    safe_filename = f"{uuid.uuid4().hex[:8]}_{Path(uploaded_file.filename).name}"
    upload_path = UPLOAD_DIR / safe_filename
    uploaded_file.save(str(upload_path))

    steps: list[dict[str, object]] = []
    converted_path, convert_result = _convert_uploaded_file(upload_path, uploaded_file.filename)
    steps.append({
        "step": "转换素材",
        "success": convert_result["returncode"] == 0,
        "output": convert_result["stdout"],
        "error": convert_result["stderr"],
        "command": convert_result.get("command"),
        "cwd": convert_result.get("cwd"),
        "duration_seconds": convert_result.get("duration_seconds"),
    })
    if convert_result["returncode"] != 0:
        return jsonify({"error": "文件转换失败", "steps": steps}), 500

    project_path, init_result = _create_project(project_name, canvas_format)
    steps.append({
        "step": "创建项目",
        "success": init_result["returncode"] == 0 and project_path is not None,
        "output": init_result["stdout"],
        "error": init_result["stderr"],
        "command": init_result.get("command"),
        "cwd": init_result.get("cwd"),
        "duration_seconds": init_result.get("duration_seconds"),
    })
    if project_path is None:
        return jsonify({"error": "项目创建失败", "steps": steps}), 500

    import_paths = [converted_path]
    if converted_path.resolve() != upload_path.resolve():
        import_paths.append(upload_path)

    try:
        import_summary = _archive_gui_sources(project_path, import_paths)
        import_output_parts = [f"[OK] Archived sources into: {project_path}"]
        if import_summary["archived"]:
            import_output_parts.append("\nArchived files:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["archived"])
        if import_summary["markdown"]:
            import_output_parts.append("\nNormalized markdown:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["markdown"])
        if import_summary["assets"]:
            import_output_parts.append("\nAsset directories:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["assets"])
        if import_summary["notes"]:
            import_output_parts.append("\nNotes:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["notes"])
        import_result = {"returncode": 0, "stdout": "\n".join(import_output_parts), "stderr": ""}
    except Exception as exc:
        import_result = {"returncode": 1, "stdout": "", "stderr": str(exc)}

    steps.append({
        "step": "导入素材",
        "success": import_result["returncode"] == 0,
        "output": import_result["stdout"],
        "error": import_result["stderr"],
    })
    if import_result["returncode"] != 0:
        return jsonify({"error": "导入素材失败", "steps": steps}), 500

    try:
        export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
    except Exception as exc:
        steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
        return jsonify({"error": str(exc), "steps": steps}), 500

    steps.append({
        "step": "生成 PPT",
        "success": True,
        "output": f"已生成 {export_path.name}",
        "error": "",
    })
    return jsonify(_success_payload(project_path, steps, preview, export_path, deck_meta, source_parser=source_parser))


@app.route("/api/upload/stream", methods=["POST"])
def upload_file_stream():
    if "file" not in request.files:
        return jsonify({"error": "没有选择文件"}), 400
    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "没有选择文件"}), 400
    if not _allowed_file(uploaded_file.filename):
        return jsonify({"error": f"不支持的文件格式: {Path(uploaded_file.filename).suffix}"}), 400

    project_name = request.form.get("project_name", "").strip() or Path(uploaded_file.filename).stem
    canvas_format = request.form.get("canvas_format", "ppt169").strip() or "ppt169"
    source_parser = None
    if Path(uploaded_file.filename).suffix.lower() == ".pdf":
        source_parser = "MinerU 云解析"

    safe_filename = f"{uuid.uuid4().hex[:8]}_{Path(uploaded_file.filename).name}"
    upload_path = UPLOAD_DIR / safe_filename
    uploaded_file.save(str(upload_path))

    @stream_with_context
    def generate() -> Any:
        steps: list[dict[str, object]] = []

        converted_path, convert_result = _convert_uploaded_file(upload_path, uploaded_file.filename)
        steps.append({
            "step": "转换素材",
            "success": convert_result["returncode"] == 0,
            "output": convert_result["stdout"],
            "error": convert_result["stderr"],
            "command": convert_result.get("command"),
            "cwd": convert_result.get("cwd"),
            "duration_seconds": convert_result.get("duration_seconds"),
        })
        yield _stream_event(_stream_step_event(steps))
        if convert_result["returncode"] != 0:
            yield _stream_event(_stream_error_event("文件转换失败", steps))
            return

        project_path, init_result = _create_project(project_name, canvas_format)
        steps.append({
            "step": "创建项目",
            "success": init_result["returncode"] == 0 and project_path is not None,
            "output": init_result["stdout"],
            "error": init_result["stderr"],
            "command": init_result.get("command"),
            "cwd": init_result.get("cwd"),
            "duration_seconds": init_result.get("duration_seconds"),
        })
        yield _stream_event(_stream_step_event(steps))
        if project_path is None:
            yield _stream_event(_stream_error_event("项目创建失败", steps))
            return

        import_paths = [converted_path]
        if converted_path.resolve() != upload_path.resolve():
            import_paths.append(upload_path)

        try:
            import_summary = _archive_gui_sources(project_path, import_paths)
            import_output_parts = [f"[OK] Archived sources into: {project_path}"]
            if import_summary["archived"]:
                import_output_parts.append("\nArchived files:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["archived"])
            if import_summary["markdown"]:
                import_output_parts.append("\nNormalized markdown:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["markdown"])
            if import_summary["assets"]:
                import_output_parts.append("\nAsset directories:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["assets"])
            if import_summary["notes"]:
                import_output_parts.append("\nNotes:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["notes"])
            import_result = {"returncode": 0, "stdout": "\n".join(import_output_parts), "stderr": ""}
        except Exception as exc:
            import_result = {"returncode": 1, "stdout": "", "stderr": str(exc)}

        steps.append({
            "step": "导入素材",
            "success": import_result["returncode"] == 0,
            "output": import_result["stdout"],
            "error": import_result["stderr"],
        })
        yield _stream_event(_stream_step_event(steps))
        if import_result["returncode"] != 0:
            yield _stream_event(_stream_error_event("导入素材失败", steps))
            return

        try:
            export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
        except Exception as exc:
            steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
            yield _stream_event(_stream_step_event(steps))
            yield _stream_event(_stream_error_event(str(exc), steps))
            return

        steps.append({
            "step": "生成 PPT",
            "success": True,
            "output": f"已生成 {export_path.name}",
            "error": "",
        })
        yield _stream_event(_stream_step_event(steps))

        payload = _success_payload(project_path, steps, preview, export_path, deck_meta, source_parser=source_parser)
        yield _stream_event(_stream_result_event(payload))

    return Response(generate(), mimetype="application/x-ndjson")


@app.route("/api/upload-url", methods=["POST"])
def upload_url():
    data = request.get_json(force=True)
    url = data.get("url", "").strip()
    project_name = data.get("project_name", "").strip()
    canvas_format = data.get("canvas_format", "ppt169").strip() or "ppt169"
    if not url:
        return jsonify({"error": "请输入 URL"}), 400

    steps: list[dict[str, object]] = []
    convert_result = _run_script(SCRIPTS_DIR / "source_to_md" / "web_to_md.py", [url])
    markdown_path = _extract_existing_path(str(convert_result["stdout"]), ".md")
    steps.append({
        "step": "抓取网页",
        "success": convert_result["returncode"] == 0 and markdown_path is not None,
        "output": convert_result["stdout"],
        "error": convert_result["stderr"],
        "command": convert_result.get("command"),
        "cwd": convert_result.get("cwd"),
        "duration_seconds": convert_result.get("duration_seconds"),
    })
    if convert_result["returncode"] != 0 or markdown_path is None:
        return jsonify({"error": "网页抓取失败", "steps": steps}), 500

    if not project_name:
        project_name = urlparse(url).netloc.replace(".", "_") or "web_page"

    project_path, init_result = _create_project(project_name, canvas_format)
    steps.append({
        "step": "创建项目",
        "success": init_result["returncode"] == 0 and project_path is not None,
        "output": init_result["stdout"],
        "error": init_result["stderr"],
        "command": init_result.get("command"),
        "cwd": init_result.get("cwd"),
        "duration_seconds": init_result.get("duration_seconds"),
    })
    if project_path is None:
        return jsonify({"error": "项目创建失败", "steps": steps}), 500

    try:
        import_summary = _archive_gui_sources(project_path, [markdown_path])
        import_output_parts = [f"[OK] Archived sources into: {project_path}"]
        if import_summary["archived"]:
            import_output_parts.append("\nArchived files:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["archived"])
        if import_summary["markdown"]:
            import_output_parts.append("\nNormalized markdown:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["markdown"])
        if import_summary["assets"]:
            import_output_parts.append("\nAsset directories:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["assets"])
        if import_summary["notes"]:
            import_output_parts.append("\nNotes:")
            import_output_parts.extend(f"  - {item}" for item in import_summary["notes"])
        import_result = {"returncode": 0, "stdout": "\n".join(import_output_parts), "stderr": ""}
    except Exception as exc:
        import_result = {"returncode": 1, "stdout": "", "stderr": str(exc)}

    steps.append({
        "step": "导入素材",
        "success": import_result["returncode"] == 0,
        "output": import_result["stdout"],
        "error": import_result["stderr"],
    })
    if import_result["returncode"] != 0:
        return jsonify({"error": "导入素材失败", "steps": steps}), 500

    try:
        export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
    except Exception as exc:
        steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
        return jsonify({"error": str(exc), "steps": steps}), 500

    steps.append({
        "step": "生成 PPT",
        "success": True,
        "output": f"已生成 {export_path.name}",
        "error": "",
    })
    return jsonify(_success_payload(project_path, steps, preview, export_path, deck_meta, source_parser="网页抓取 Markdown"))


@app.route("/api/upload-url/stream", methods=["POST"])
def upload_url_stream():
    data = request.get_json(force=True)
    url = data.get("url", "").strip()
    project_name = data.get("project_name", "").strip()
    canvas_format = data.get("canvas_format", "ppt169").strip() or "ppt169"
    if not url:
        return jsonify({"error": "请输入 URL"}), 400

    @stream_with_context
    def generate() -> Any:
        steps: list[dict[str, object]] = []

        convert_result = _run_script(SCRIPTS_DIR / "source_to_md" / "web_to_md.py", [url])
        markdown_path = _extract_existing_path(str(convert_result["stdout"]), ".md")
        steps.append({
            "step": "抓取网页",
            "success": convert_result["returncode"] == 0 and markdown_path is not None,
            "output": convert_result["stdout"],
            "error": convert_result["stderr"],
            "command": convert_result.get("command"),
            "cwd": convert_result.get("cwd"),
            "duration_seconds": convert_result.get("duration_seconds"),
        })
        yield _stream_event(_stream_step_event(steps))
        if convert_result["returncode"] != 0 or markdown_path is None:
            yield _stream_event(_stream_error_event("网页抓取失败", steps))
            return

        resolved_project_name = project_name or (urlparse(url).netloc.replace(".", "_") or "web_page")
        project_path, init_result = _create_project(resolved_project_name, canvas_format)
        steps.append({
            "step": "创建项目",
            "success": init_result["returncode"] == 0 and project_path is not None,
            "output": init_result["stdout"],
            "error": init_result["stderr"],
            "command": init_result.get("command"),
            "cwd": init_result.get("cwd"),
            "duration_seconds": init_result.get("duration_seconds"),
        })
        yield _stream_event(_stream_step_event(steps))
        if project_path is None:
            yield _stream_event(_stream_error_event("项目创建失败", steps))
            return

        try:
            import_summary = _archive_gui_sources(project_path, [markdown_path])
            import_output_parts = [f"[OK] Archived sources into: {project_path}"]
            if import_summary["archived"]:
                import_output_parts.append("\nArchived files:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["archived"])
            if import_summary["markdown"]:
                import_output_parts.append("\nNormalized markdown:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["markdown"])
            if import_summary["assets"]:
                import_output_parts.append("\nAsset directories:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["assets"])
            if import_summary["notes"]:
                import_output_parts.append("\nNotes:")
                import_output_parts.extend(f"  - {item}" for item in import_summary["notes"])
            import_result = {"returncode": 0, "stdout": "\n".join(import_output_parts), "stderr": ""}
        except Exception as exc:
            import_result = {"returncode": 1, "stdout": "", "stderr": str(exc)}

        steps.append({
            "step": "导入素材",
            "success": import_result["returncode"] == 0,
            "output": import_result["stdout"],
            "error": import_result["stderr"],
        })
        yield _stream_event(_stream_step_event(steps))
        if import_result["returncode"] != 0:
            yield _stream_event(_stream_error_event("导入素材失败", steps))
            return

        try:
            export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
        except Exception as exc:
            steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
            yield _stream_event(_stream_step_event(steps))
            yield _stream_event(_stream_error_event(str(exc), steps))
            return

        steps.append({
            "step": "生成 PPT",
            "success": True,
            "output": f"已生成 {export_path.name}",
            "error": "",
        })
        yield _stream_event(_stream_step_event(steps))
        payload = _success_payload(
            project_path,
            steps,
            preview,
            export_path,
            deck_meta,
            source_parser="网页抓取 Markdown",
        )
        yield _stream_event(_stream_result_event(payload))

    return Response(generate(), mimetype="application/x-ndjson")


@app.route("/api/generate-text", methods=["POST"])
def generate_text():
    data = request.get_json(force=True)
    content = data.get("content", "").strip()
    project_name = data.get("project_name", "").strip() or "text_presentation"
    canvas_format = data.get("canvas_format", "ppt169").strip() or "ppt169"
    if not content:
        return jsonify({"error": "请输入正文内容"}), 400

    steps: list[dict[str, object]] = []
    project_path, init_result = _create_project(project_name, canvas_format)
    steps.append({
        "step": "创建项目",
        "success": init_result["returncode"] == 0 and project_path is not None,
        "output": init_result["stdout"],
        "error": init_result["stderr"],
    })
    if project_path is None:
        return jsonify({"error": "项目创建失败", "steps": steps}), 500

    source_path = project_path / "sources" / "manual_input.md"
    source_path.write_text(content, encoding="utf-8")
    steps.append({
        "step": "写入素材",
        "success": True,
        "output": f"已写入 {source_path.name}",
        "error": "",
    })

    try:
        export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
    except Exception as exc:
        steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
        return jsonify({"error": str(exc), "steps": steps}), 500

    steps.append({
        "step": "生成 PPT",
        "success": True,
        "output": f"已生成 {export_path.name}",
        "error": "",
    })
    return jsonify(_success_payload(project_path, steps, preview, export_path, deck_meta, source_parser="手动输入文本"))


@app.route("/api/generate-text/stream", methods=["POST"])
def generate_text_stream():
    data = request.get_json(force=True)
    content = data.get("content", "").strip()
    project_name = data.get("project_name", "").strip() or "text_presentation"
    canvas_format = data.get("canvas_format", "ppt169").strip() or "ppt169"
    if not content:
        return jsonify({"error": "请输入正文内容"}), 400

    @stream_with_context
    def generate() -> Any:
        steps: list[dict[str, object]] = []

        project_path, init_result = _create_project(project_name, canvas_format)
        steps.append({
            "step": "创建项目",
            "success": init_result["returncode"] == 0 and project_path is not None,
            "output": init_result["stdout"],
            "error": init_result["stderr"],
            "command": init_result.get("command"),
            "cwd": init_result.get("cwd"),
            "duration_seconds": init_result.get("duration_seconds"),
        })
        yield _stream_event(_stream_step_event(steps))
        if project_path is None:
            yield _stream_event(_stream_error_event("项目创建失败", steps))
            return

        source_path = project_path / "sources" / "manual_input.md"
        source_path.write_text(content, encoding="utf-8")
        steps.append({
            "step": "写入素材",
            "success": True,
            "output": f"已写入 {source_path.name}",
            "error": "",
        })
        yield _stream_event(_stream_step_event(steps))

        try:
            export_path, deck_meta, preview = _generate_ppt(project_path, canvas_format)
        except Exception as exc:
            steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
            yield _stream_event(_stream_step_event(steps))
            yield _stream_event(_stream_error_event(str(exc), steps))
            return

        steps.append({
            "step": "生成 PPT",
            "success": True,
            "output": f"已生成 {export_path.name}",
            "error": "",
        })
        yield _stream_event(_stream_step_event(steps))
        payload = _success_payload(
            project_path,
            steps,
            preview,
            export_path,
            deck_meta,
            source_parser="手动输入文本",
        )
        yield _stream_event(_stream_result_event(payload))

    return Response(generate(), mimetype="application/x-ndjson")


@app.route("/api/toolbox/catalog")
def toolbox_catalog():
    catalog = _toolbox_catalog()
    return jsonify(catalog)


@app.route("/api/projects/<project_name>/tool", methods=["POST"])
def run_project_tool(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"error": "项目不存在"}), 404

    payload = request.get_json(force=True) or {}
    try:
        result = _run_project_tool(project_path, payload)
    except Exception as exc:
        return jsonify({
            "success": False,
            "project_name": project_name,
            "action": str(payload.get("action") or ""),
            "error": str(exc),
        }), 400
    return jsonify(result), (200 if result.get("success") else 500)


@app.route("/api/toolbox/template-import", methods=["POST"])
def import_template_pptx():
    if "file" not in request.files:
        return jsonify({"error": "没有选择 PPTX 模板文件"}), 400

    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "没有选择 PPTX 模板文件"}), 400
    if Path(uploaded_file.filename).suffix.lower() != ".pptx":
        return jsonify({"error": "模板导入仅支持 .pptx 文件"}), 400

    extra_args = _split_cli_args(request.form.get("extra_args", ""))
    extra_args = _strip_option(extra_args, {"-o", "--output"})
    output_name = request.form.get("output_name", "").strip() or Path(uploaded_file.filename).stem
    import_name = f"{_sanitize_project_name(output_name)}_{datetime.now():%H%M%S}"
    upload_path = TOOL_UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{Path(uploaded_file.filename).name}"
    output_dir = TEMPLATE_IMPORTS_DIR / import_name
    uploaded_file.save(str(upload_path))

    try:
        result = _run_script(
            SCRIPTS_DIR / "pptx_template_import.py",
            [str(upload_path), "-o", str(output_dir), *extra_args],
            timeout=TOOL_TIMEOUT_LONG,
        )
    finally:
        try:
            upload_path.unlink(missing_ok=True)
        except Exception:
            pass

    artifacts: list[dict[str, str]] = []
    if output_dir.exists():
        artifact_paths = []
        for relative in ("summary.md", "manifest.json"):
            candidate = output_dir / relative
            if candidate.exists() and candidate.is_file():
                artifact_paths.append(candidate)
        artifact_paths.extend(_recent_files(output_dir / "svg", ["*.svg"], limit=6))
        artifacts = [
            _template_import_artifact(import_name, output_dir, file_path)
            for file_path in artifact_paths
        ]

    payload = {
        "success": result["returncode"] == 0,
        "import_name": import_name,
        "output_path": str(output_dir),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode", 1),
        "artifacts": artifacts,
        "command": result.get("command", ""),
        "cwd": result.get("cwd", ""),
        "duration_seconds": result.get("duration_seconds"),
    }
    return jsonify(payload), (200 if payload["success"] else 500)


@app.route("/api/projects")
def list_projects():
    return jsonify(_list_projects())


@app.route("/api/projects/<project_name>/assets")
def get_project_assets(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"success": False, "error": "项目不存在"}), 404
    if not _is_user_project_dir(project_path):
        return jsonify({"success": False, "error": "系统项目目录不提供 GUI 管理。"}), 400

    raw_limit = str(request.args.get("limit") or "").strip()
    raw_offset = str(request.args.get("offset") or "").strip()
    try:
        limit = max(int(raw_limit), 0) if raw_limit else 0
        offset = max(int(raw_offset), 0) if raw_offset else 0
    except ValueError:
        return jsonify({"success": False, "error": "limit / offset 必须是非负整数。"}), 400

    payload = _project_assets_payload(
        project_path,
        asset_type=str(request.args.get("type") or "all"),
        status=str(request.args.get("status") or "all"),
        keyword=str(request.args.get("q") or ""),
        source_file=str(request.args.get("source_file") or ""),
        limit=limit,
        offset=offset,
    )
    return jsonify(payload)


@app.route("/api/projects/<project_name>/preview/start", methods=["POST"])
def start_project_preview(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"success": False, "error": "项目不存在"}), 404
    if not _is_user_project_dir(project_path):
        return jsonify({"success": False, "error": "系统项目目录不提供 GUI 管理。"}), 400

    payload = request.get_json(silent=True) or {}
    extra_args: list[str] = []
    port = str(payload.get("port") or "").strip()
    if port:
        extra_args.extend(["--port", port])

    try:
        preview = _start_live_preview(project_path, extra_args)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({
        "success": True,
        "message": "Live Preview 已启动。",
        "preview": preview,
        "project": _project_to_json(project_path),
    })


@app.route("/api/projects/<project_name>/preview/stop", methods=["POST"])
def stop_project_preview(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"success": False, "error": "项目不存在"}), 404
    if not _is_user_project_dir(project_path):
        return jsonify({"success": False, "error": "系统项目目录不提供 GUI 管理。"}), 400

    preview = _stop_live_preview(project_name)
    return jsonify({
        "success": True,
        "message": str(preview.get("message") or "Live Preview 已关闭。"),
        "preview": preview,
        "project": _project_to_json(project_path),
    })


@app.route("/api/projects/<project_name>/preview/state")
def project_preview_state(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"success": False, "error": "项目不存在"}), 404
    if not _is_user_project_dir(project_path):
        return jsonify({"success": False, "error": "系统项目目录不提供 GUI 管理。"}), 400

    record = _preview_record(project_name, project_path)
    if record is None:
        return jsonify({
            "success": True,
            "project_name": project_name,
            "running": False,
            "live_preview_url": "",
            "port": None,
            "config": {},
            "progress": {},
            "slides": [],
            "preview_error": "",
        })

    base_url = str(record.get("url") or "").rstrip("/")
    config: dict[str, Any] = {}
    progress: dict[str, Any] = {}
    slides_payload: dict[str, Any] = {}
    errors: list[str] = []

    for endpoint, target in (("/api/config", "config"), ("/api/progress", "progress"), ("/api/slides", "slides")):
        try:
            payload = _fetch_preview_json(base_url, endpoint)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            continue
        if target == "config":
            config = payload
        elif target == "progress":
            progress = payload
        else:
            slides_payload = payload

    return jsonify({
        "success": True,
        "project_name": project_name,
        "running": True,
        "live_preview_url": base_url,
        "port": int(record.get("port") or 0) or None,
        "config": config,
        "progress": progress,
        "slides": list(slides_payload.get("slides") or []),
        "preview_error": " | ".join(errors),
    })


@app.route("/api/projects/<project_name>", methods=["DELETE"])
def delete_project(project_name: str):
    try:
        payload = _delete_project(project_name)
    except FileNotFoundError:
        return jsonify({"success": False, "error": "项目不存在"}), 404
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"success": False, "error": f"删除项目失败：{exc}"}), 500
    return jsonify(payload)


@app.route("/api/projects/<project_name>/generate", methods=["POST"])
def regenerate_project(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"error": "项目不存在"}), 404
    steps: list[dict[str, object]] = []
    try:
        export_path, deck_meta, preview = _generate_ppt(project_path)
    except Exception as exc:
        steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
        return jsonify({"error": str(exc), "steps": steps}), 500
    steps.append({
        "step": "生成 PPT",
        "success": True,
        "output": f"已生成 {export_path.name}",
        "error": "",
    })
    return jsonify(_success_payload(project_path, steps, preview, export_path, deck_meta))


@app.route("/api/projects/<project_name>/generate/stream", methods=["POST"])
def regenerate_project_stream(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"error": "项目不存在"}), 404

    @stream_with_context
    def generate() -> Any:
        steps: list[dict[str, object]] = []
        try:
            export_path, deck_meta, preview = _generate_ppt(project_path)
        except Exception as exc:
            steps.append({"step": "生成 PPT", "success": False, "output": "", "error": str(exc)})
            yield _stream_event(_stream_step_event(steps))
            yield _stream_event(_stream_error_event(str(exc), steps))
            return

        steps.append({
            "step": "生成 PPT",
            "success": True,
            "output": f"已生成 {export_path.name}",
            "error": "",
        })
        yield _stream_event(_stream_step_event(steps))
        result_payload = _success_payload(project_path, steps, preview, export_path, deck_meta)
        yield _stream_event(_stream_result_event(result_payload))

    return Response(generate(), mimetype="application/x-ndjson")


@app.route("/api/projects/<project_name>/sources")
def get_project_sources(project_name: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"error": "项目不存在"}), 404
    sources_dir = project_path / "sources"
    sources: list[dict[str, str]] = []
    if sources_dir.exists():
        for source in sorted(sources_dir.iterdir()):
            if not source.is_file():
                continue
            try:
                content = source.read_text(encoding="utf-8")
            except Exception:
                content = "(二进制文件)"
            sources.append({"name": source.name, "content": content})
    return jsonify(sources)


@app.route("/api/projects/<project_name>/files/<path:relative_path>")
def download_project_file(project_name: str, relative_path: str):
    project_path = PROJECTS_DIR / project_name
    if not project_path.exists():
        return jsonify({"error": "项目不存在"}), 404
    file_path = _resolve_within(project_path, relative_path)
    if file_path is None or not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return send_from_directory(str(file_path.parent), file_path.name, as_attachment=False)


@app.route("/api/template-imports/<import_name>/files/<path:relative_path>")
def download_template_import_file(import_name: str, relative_path: str):
    import_path = TEMPLATE_IMPORTS_DIR / import_name
    if not import_path.exists():
        return jsonify({"error": "模板导入结果不存在"}), 404
    file_path = _resolve_within(import_path, relative_path)
    if file_path is None or not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "文件不存在"}), 404
    return send_from_directory(str(file_path.parent), file_path.name, as_attachment=False)


@app.route("/api/projects/<project_name>/exports/<filename>")
def download_export(project_name: str, filename: str):
    safe_name = Path(filename).name
    export_dir = PROJECTS_DIR / project_name / "exports"
    if not (export_dir / safe_name).exists():
        return jsonify({"error": "文件不存在"}), 404
    return send_from_directory(str(export_dir), safe_name, as_attachment=True)


if __name__ == "__main__":
    print("=" * 60)
    print("  PPT Master Project Manager")
    print("  http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True)
