# Windows Installation Guide

This guide walks you through installing PPT Master on Windows step by step. Follow along and you'll have a working setup in under 10 minutes.

---

## Step 1 — Install Python (Required)

Python is the only hard requirement.

1. Go to **[python.org/downloads](https://www.python.org/downloads/)** and download the latest **Python 3.10+** installer.

2. **⚠️ CRITICAL: Check "Add python.exe to PATH"** during installation — this is the single most common mistake on Windows. Skipping this will break every step that follows.

   ![Python installer — check Add to PATH](assets/windows-python-path.png)

3. After installation, open **PowerShell** (search "PowerShell" in Start menu) and verify:

   ```powershell
   python --version
   ```

   You should see `Python 3.12.x` or similar. If you see "Python was not found" or it opens the Microsoft Store, see [Troubleshooting](#python-was-not-found-or-opens-microsoft-store) below.

> **💡 Tip**: Python installed via Anaconda or Miniconda works too — just make sure `python --version` shows 3.10+.

---

## Step 2 — Download the Project

**Option A — Download ZIP** (easiest):

1. Go to [github.com/hongyi652/ppt-master-sci-fork](https://github.com/hongyi652/ppt-master-sci-fork)
2. Click the green **Code** button → **Download ZIP**
3. Unzip to `C:\Users\YourName\ppt-master-sci-fork`

**Option B — Git Clone** (requires [Git](https://git-scm.com/downloads)):

```powershell
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork
```

---

## Step 3 — Install Dependencies

```powershell
cd C:\Users\YourName\ppt-master-sci-fork   # ← adjust to your actual path
pip install -r requirements.txt
```

> If `pip` is not recognized, try `python -m pip install -r requirements.txt`.

Wait for it to finish. You should see `Successfully installed ...` at the end.

---

## Step 4 — Verify Your Setup

```powershell
python -c "import pptx; import fitz; print('All core dependencies OK')"
```

✅ Output: `All core dependencies OK` → you're good.

❌ Error → see [Troubleshooting](#troubleshooting) below.

---

## Step 5 — Run a Minimal Example

Open your AI editor (Cursor, VS Code + Copilot, etc.), open the `ppt-master` folder, and type in the chat:

```
Please create a simple 3-page test PPT with a cover, one content page, and a closing page. Topic: "Hello World".
```

If a `.pptx` file appears in `exports/` that opens in PowerPoint — **you're done.**

---

## Step 6 — LaTeX to SVG (Scientific/Academic Use)

If your documents contain mathematical formulas (common in scientific papers), PPT Master can render LaTeX formulas as high-quality SVG and embed them directly into slides. This requires a TeX distribution.

### Install MiKTeX (Recommended for Windows)

1. Download the MiKTeX installer from **[miktex.org/download](https://miktex.org/download)**
2. Run the installer, choose **"Install for anyone who uses this computer"** (recommended)
3. During setup, set **"Install missing packages on-the-fly"** to **Yes** — this avoids having to manually install LaTeX packages later
4. After installation, open PowerShell and verify:

```powershell
latex --version
dvisvgm --version
```

Both should return version info. If not, restart PowerShell or check PATH.

> **Alternative**: You can also use [TeX Live](https://tug.org/texlive/) (larger download, ~4GB full install). MiKTeX is smaller and installs packages on demand.

### How it works in the pipeline

```
LaTeX formula in source PDF
    ↓ (extract_formulas.py)
formula_manifest.json
    ↓ (latex_to_svg.py)
SVG files in images/
    ↓ (used by Executor)
Embedded in final PPTX as vector graphics
```

### Verify LaTeX → SVG works

```powershell
cd C:\Users\YourName\ppt-master
python skills/ppt-master/scripts/latex_to_svg.py "E=mc^2" -o test_formula.svg
```

If `test_formula.svg` is generated — you're good. Delete it after testing.

---

## Step 7 — MinerU Document Parsing (Optional)

MinerU is a cloud-based document parsing service that excels at extracting content from complex scientific PDFs (with tables, multi-column layouts, formulas, etc.).

### Get your API Token

1. Register at **[mineru.net](https://mineru.net/)**
2. Log in and go to your dashboard → API section
3. Copy your API Token

### Configure

Create or edit `.env` file in the project root:

```ini
MINERU_API_TOKEN=your-token-here
MINERU_API_BASE_URL=https://mineru.net/api/v4
```

> **Security**: Never commit `.env` to Git. It's already in `.gitignore`.

### Verify

After setting the token, start the Web UI (`python app.py`) and try uploading a PDF — if MinerU parsing is triggered successfully, the setup is correct.

---

## Step 8 — AI Image Generation (Optional)

If you want AI-generated images in your slides, configure one of the supported backends in `.env`:

```ini
# Example: OpenAI gpt-image-2 (recommended)
IMAGE_BACKEND=openai
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-image-2
```

See `.env.example` for all available backends (Gemini, MiniMax, Qwen, Zhipu, Volcengine, etc.).

---

## Dependency Summary

| Component | Required? | What it does | Install |
|-----------|:---------:|--------------|---------|
| **Python 3.10+** | ✅ Yes | Core runtime | [python.org](https://www.python.org/downloads/) |
| **pip packages** | ✅ Yes | All Python libraries | `pip install -r requirements.txt` |
| **MiKTeX / TeX Live** | ⚠️ For formulas | LaTeX→SVG rendering | [miktex.org](https://miktex.org/download) |
| **MinerU API Token** | ⚠️ For sci PDFs | Cloud document parsing | [mineru.net](https://mineru.net/) |
| **Image API Key** | ❌ Optional | AI image generation | `.env` config |
| **CairoSVG + GTK3** | ❌ Optional | Higher quality PNG fallback | `pip install cairosvg` |
| **Pandoc** | ❌ Optional | Legacy doc formats only | [pandoc.org](https://pandoc.org/installing.html) |

---

## Troubleshooting

### `python` was not found or opens Microsoft Store

**Cause**: Python isn't in your system PATH.

**Fix 1** — Re-run the Python installer → **Modify** → check **"Add Python to environment variables"**.

**Fix 2** — Manually add to PATH:
1. Run `where python` in PowerShell first to find the actual path (e.g. `C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe`)
2. Search "Environment Variables" in Start menu
3. Find `Path` → **Edit** → add the **directory** from step 1 and its `Scripts` subfolder:
   ```
   C:\Users\YourName\AppData\Local\Programs\Python\Python312
   C:\Users\YourName\AppData\Local\Programs\Python\Python312\Scripts
   ```
4. Click OK, then **restart PowerShell**

**Fix 3** — Try `python3` or `py` instead.

### `pip install` fails with permission errors

```powershell
pip install --user -r requirements.txt
```

Or run PowerShell as Administrator.

### `pip install` fails due to network issues

```powershell
pip install -r requirements.txt --proxy http://your-proxy:port
```

### `ModuleNotFoundError`

`pip` installed to a different Python. Use `python -m pip install -r requirements.txt` to match.

### `import fitz` fails

1. Upgrade pip: `python -m pip install --upgrade pip`
2. Pre-built wheel: `pip install PyMuPDF --only-binary :all:`
3. Still failing → install [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)

### PowerShell says "running scripts is disabled"

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## Still stuck?

- 📖 [FAQ](./faq.md)
- 🐛 [GitHub Issues](https://github.com/hongyi652/ppt-master-sci-fork/issues) — include your Python version, Windows version, and full error message
