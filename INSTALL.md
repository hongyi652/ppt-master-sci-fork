# 安装指南 / Installation Guide

本文档详细说明 ppt-master-sci-fork 的所有依赖及安装步骤。

---

## 快速安装（必需）

```bash
# 1. 克隆仓库
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 复制环境变量模板
cp .env.example .env
# Windows: copy .env.example .env
```

---

## 依赖总览

| 组件 | 必要性 | 功能 | 安装方式 |
|------|:------:|------|----------|
| **Python 3.10+** | ✅ 必需 | 核心运行时 | [python.org](https://www.python.org/downloads/) |
| **pip 包** | ✅ 必需 | 所有 Python 库 | `pip install -r requirements.txt` |
| **LaTeX + dvisvgm** | ⚠️ 公式必需 | LaTeX 公式渲染为 SVG | 见下方详细说明 |
| **MinerU API Token** | ⚠️ 科研 PDF 必需 | 云端复杂文档解析 | [mineru.net](https://mineru.net/) |
| **AI 图片 API Key** | ❌ 可选 | AI 生成配图 | `.env` 配置 |
| **CairoSVG** | ❌ 可选 | 更高质量 PNG 后备 | `pip install cairosvg` |
| **Pandoc** | ❌ 可选 | 旧格式文档转换 | [pandoc.org](https://pandoc.org/installing.html) |

---

## 一、Python 环境

### Windows

1. 从 [python.org](https://www.python.org/downloads/) 下载 Python 3.10+ 安装包
2. **务必勾选 "Add python.exe to PATH"**
3. 打开 PowerShell 验证：
   ```powershell
   python --version
   pip --version
   ```

### macOS

```bash
brew install python
```

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

---

## 二、Python 依赖包

```bash
pip install -r requirements.txt
```

`requirements.txt` 会自动安装以下核心库：

| 包名 | 用途 |
|------|------|
| `python-pptx` | SVG → PPTX 转换 |
| `PyMuPDF (fitz)` | PDF 解析 |
| `Pillow` | 图片处理 |
| `numpy` | 图片数值计算 |
| `requests` | HTTP 请求 |
| `beautifulsoup4` | HTML 解析 |
| `flask` | Web UI 服务 |
| `edge-tts` | 语音旁白生成 |
| `openai` | OpenAI API 调用 |
| `google-genai` | Gemini API 调用 |
| `mammoth` | DOCX 转换 |
| `openpyxl` | Excel 解析 |
| `svglib` / `reportlab` | SVG → PNG 后备 |

验证安装成功：

```bash
python -c "import pptx; import fitz; import flask; print('All OK')"
```

---

## 三、LaTeX → SVG 公式渲染（重点）

本 fork 的核心增强功能之一。用于将科研论文中的数学公式渲染为矢量 SVG，嵌入 PPT 后无损缩放。

### 工作原理

```
源文档（PDF）
    ↓ MinerU 解析
Markdown（含 LaTeX 公式）
    ↓ extract_formulas.py
formula_manifest.json（公式清单）
    ↓ latex_to_svg.py
SVG 公式文件（images/formula_*.svg）
    ↓ Executor 引用
嵌入最终 PPTX（矢量图形，无损缩放）
```

### 必需工具

| 工具 | 说明 | 验证命令 |
|------|------|----------|
| `latex` 或 `xelatex` 或 `pdflatex` | TeX 编译器 | `latex --version` |
| `dvisvgm` | DVI/PDF → SVG 转换器 | `dvisvgm --version` |

两者由同一个 TeX 发行版提供。

### Windows 安装（MiKTeX，推荐）

1. 下载 MiKTeX：**[miktex.org/download](https://miktex.org/download)**
2. 运行安装程序：
   - 选择 **"Install for anyone who uses this computer"**
   - ⚠️ 设置 **"Install missing packages on-the-fly"** 为 **Yes**（自动下载缺少的 LaTeX 包）
3. 安装完成后**重启 PowerShell**
4. 验证：
   ```powershell
   latex --version
   dvisvgm --version
   ```

> **常见问题**：如果 `latex` 命令找不到，检查 MiKTeX 的安装路径是否在 PATH 中。默认路径通常是 `C:\Users\<用户名>\AppData\Local\Programs\MiKTeX\miktex\bin\x64\`

### macOS 安装

```bash
# 方案 A：MacTeX（完整版，约 4GB）
brew install --cask mactex

# 方案 B：BasicTeX（精简版，约 100MB）+ 手动安装包
brew install --cask basictex
sudo tlmgr update --self
sudo tlmgr install amsmath amssymb standalone preview mathtools
```

### Linux 安装

```bash
# Ubuntu / Debian
sudo apt install texlive-base texlive-latex-extra texlive-fonts-recommended dvisvgm

# Arch Linux
sudo pacman -S texlive-core texlive-latexextra
```

### 验证 LaTeX → SVG 完整流程

```bash
python skills/ppt-master/scripts/latex_to_svg.py "E=mc^2" -o test_formula.svg
```

成功时会生成 `test_formula.svg` 文件。用浏览器打开可以看到渲染后的公式。测试后删除：

```bash
# Windows
del test_formula.svg
# macOS/Linux
rm test_formula.svg
```

### 批量公式渲染

```bash
# 1. 从 Markdown 中提取公式
python skills/ppt-master/scripts/extract_formulas.py <markdown文件> -o <项目路径>/images/formula_manifest.json

# 2. 批量渲染为 SVG
python skills/ppt-master/scripts/latex_to_svg.py --manifest <项目路径>/images/formula_manifest.json
```

### 常见 LaTeX 错误排查

| 错误信息 | 原因 | 解决 |
|----------|------|------|
| `No TeX compiler found` | PATH 中没有 latex/xelatex/pdflatex | 重装 MiKTeX 或手动添加 PATH |
| `dvisvgm not found` | dvisvgm 不在 PATH | MiKTeX 通常自带，检查安装 |
| `Package xxx not found` | 缺少 LaTeX 宏包 | MiKTeX 设置为自动安装；或 `tlmgr install xxx` |
| `Font xxx not found` | 缺少字体 | `tlmgr install collection-fontsrecommended` |

---

## 四、MinerU 文档解析

MinerU 擅长解析复杂科研 PDF（多栏布局、表格、公式混排等）。

### 获取 API Token

1. 注册：[mineru.net](https://mineru.net/)
2. 登录后进入控制台 → API 管理
3. 复制 API Token

### 配置

编辑项目根目录的 `.env` 文件：

```ini
MINERU_API_TOKEN=your-token-here
MINERU_API_BASE_URL=https://mineru.net/api/v4
```

> ⚠️ **安全提醒**：`.env` 文件已在 `.gitignore` 中排除，不会被提交到 Git。请勿将 Token 写入任何会被提交的文件。

### 验证

启动 Web UI：

```bash
python app.py
```

上传一份 PDF，如果看到 MinerU 解析进度 — 配置成功。

---

## 五、AI 图片生成（可选）

在 `.env` 中配置任一后端即可启用 AI 配图：

```ini
# 推荐：OpenAI gpt-image-2
IMAGE_BACKEND=openai
OPENAI_API_KEY=sk-your-key
OPENAI_MODEL=gpt-image-2
```

支持的后端：`openai` / `gemini` / `minimax` / `qwen` / `zhipu` / `volcengine`

详见 `.env.example` 中的完整配置示例。

---

## 六、Web UI 启动

```bash
python app.py
```

默认访问 `http://localhost:5000`。

---

## 完整验证脚本

一键检查所有依赖是否就绪：

```bash
python -c "
import sys
print(f'Python: {sys.version}')

# 核心依赖
try:
    import pptx; print(f'python-pptx: {pptx.__version__} ✓')
except: print('python-pptx: ✗ (pip install python-pptx)')

try:
    import fitz; print(f'PyMuPDF: {fitz.version_bind} ✓')
except: print('PyMuPDF: ✗ (pip install PyMuPDF)')

try:
    import flask; print(f'Flask: {flask.__version__} ✓')
except: print('Flask: ✗ (pip install flask)')

try:
    from PIL import Image; import PIL; print(f'Pillow: {PIL.__version__} ✓')
except: print('Pillow: ✗ (pip install Pillow)')

# LaTeX
import shutil
latex = shutil.which('latex') or shutil.which('xelatex') or shutil.which('pdflatex')
print(f'LaTeX compiler: {\"✓ (\" + latex + \")\" if latex else \"✗ — 安装 MiKTeX 或 TeX Live\"}')

dvisvgm = shutil.which('dvisvgm')
print(f'dvisvgm: {\"✓ (\" + dvisvgm + \")\" if dvisvgm else \"✗ — 安装 MiKTeX 或 TeX Live\"}')

print()
print('核心依赖检查完成。LaTeX 相关标记为 ✗ 表示公式功能不可用，但不影响基础 PPT 生成。')
"
```
