# PPT Master Sci Fork — 面向论文、PDF 与技术文档的可原生编辑 PPTX 生成工具

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/hongyi652/ppt-master-sci-fork.svg)](https://github.com/hongyi652/ppt-master-sci-fork/stargazers)
[![Upstream](https://img.shields.io/badge/upstream-hugohe3%2Fppt--master-green.svg)](https://github.com/hugohe3/ppt-master)

[English](./README.md) | 中文

> 本项目是 [PPT Master](https://github.com/hugohe3/ppt-master) 的一个面向科研/学术场景的 fork，重点增强了 **MinerU 文档解析** 与 **SVG 公式支持**，更适合处理论文、公式密集型 PDF 和技术文档。

<p align="center">
  <a href="./examples/"><strong>示例</strong></a> ·
  <a href="./docs/faq.md"><strong>FAQ</strong></a> ·
  <a href="./CHANGELOG.md"><strong>更新日志</strong></a> ·
  <a href="https://github.com/hongyi652/ppt-master-sci-fork/issues"><strong>Issues</strong></a>
</p>

---

## 为什么有这个 fork？

这个仓库基于 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) 创建，但针对**科研、教学、学术报告、技术沟通**等场景做了更明确的增强。

相比上游项目，这个 fork 更关注：

- **基于 MinerU 的文档解析**：更适合学术 PDF、技术文档、结构化材料
- **SVG 公式支持**：让数学公式在生成后的幻灯片中显示更清晰
- 更适合处理 **公式密集型**、**论文风格**、**技术报告风格** 的输入材料
- 针对 **研究汇报 / 教学课件 / 技术总结** 场景进行工作流优化

如果你的输入材料包含论文、公式、图表、技术说明或高密度结构化内容，这个 fork 会比通用版本更适合作为起点。

> **Fork 说明**：本项目 fork 自 [hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)。原项目作者为 [Hugo He](https://github.com/hugohe3)，采用 MIT License。当前 fork 在尽量保持兼容的前提下，增加了面向科学文档的增强能力。fork 的变更可见 [CHANGELOG.md](./CHANGELOG.md)。

---

## 它能做什么？

你可以输入 PDF、DOCX、URL 或 Markdown 文本，生成一个**可在 PowerPoint 中原生编辑的 `.pptx` 文件**，其中包含：

- 真实文本框
- 真实矢量图形
- 真实图表
- 可逐个元素编辑的幻灯片内容

这不是“每页一张图片”的伪 PPT。输出结果的目标是：**生成后仍然可以在 PowerPoint 中继续编辑**。

---

## 适合哪些场景？

- 论文解读 / 论文汇报
- 技术报告
- 学术演示文稿
- 公式较多的 PDF
- 结构化长文档
- 科学 / 工程类沟通材料

## 当前限制

- 输出质量仍然高度依赖底层 AI 模型能力
- 公式效果取决于源文档质量与解析质量
- 扫描版 PDF 如果噪声较多，可能仍需额外清洗
- 某些版式仍可能需要多轮提示词调整或人工微调

---

## 30 秒快速开始

```bash
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork
pip install -r requirements.txt
```

然后把你的源文件放进 `projects/` 目录，并对 AI 这样说：

```text
Please create a PPT from projects/demo/sources/paper.pdf
```

生成出的 `.pptx` 会保存到 `exports/` 目录。

---

## 快速开始

### 1. 环境要求

你只需要 **Python 3.10+**。

| 依赖 | 是否必需 | 用途 |
|------|:--------:|------|
| [Python](https://www.python.org/downloads/) 3.10+ | ✅ 是 | 核心运行环境 |

安装依赖：

```bash
pip install -r requirements.txt
```

<details open>
<summary><strong>Windows</strong> —— 建议先看专门安装指南</summary>

Windows 用户建议参考：

**📖 [Windows Installation Guide](./docs/windows-installation.md)**

简化步骤：
1. 从 [python.org](https://www.python.org/downloads/) 安装 Python
2. 安装时勾选“Add to PATH”
3. 执行：

```bash
pip install -r requirements.txt
```
</details>

<details>
<summary><strong>macOS / Linux</strong> —— 安装即可使用</summary>

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
<summary><strong>可选补充依赖</strong> —— 仅某些旧格式可能需要</summary>

对于部分较旧或较少见的文档格式，比如 `.doc`、`.odt`、`.rtf`、`.tex`、`.rst`、`.org`、`.typ`，你可能还需要安装 **Pandoc**。

```bash
# macOS
brew install pandoc

# Ubuntu / Debian
sudo apt install pandoc
```
</details>

### 2. 获取仓库

**方式 A — 下载 ZIP**

直接从以下地址下载：

- https://github.com/hongyi652/ppt-master-sci-fork

下载后解压即可。

**方式 B — Git clone**

```bash
git clone https://github.com/hongyi652/ppt-master-sci-fork.git
cd ppt-master-sci-fork
```

### 3. 选择一个 AI Agent / IDE

PPT Master 最适合运行在具有以下能力的工具中：

- 能读写文件
- 能执行命令
- 能进行多轮对话
- 能根据上下文持续完成工作流

例如：

- VS Code + GitHub Copilot
- Claude Code
- Cursor
- Cline
- Continue
- 其他支持 agent 工作流的 IDE / CLI 工具

> **模型建议**：对于技术文档、论文总结、公式较多的内容，更强的模型通常会带来更好的结构化结果、版面组织与摘要质量。

### 4. 准备源材料

把你的文件放进 `projects/` 目录，例如：

```text
projects/demo/sources/paper.pdf
projects/demo/sources/report.docx
projects/demo/sources/notes.md
```

你也可以不提供文件，而是直接把文本内容粘贴到 AI 聊天窗口。

### 5. 让 AI 生成幻灯片

示例提示词：

```text
Please create a PPT from projects/demo/sources/paper.pdf
```

```text
Please turn projects/demo/sources/report.docx into a clean academic presentation.
```

```text
Please summarize this paper into an 8-slide presentation with a technical but readable style.
```

### 6. 获取输出结果

生成后的 PowerPoint 文件保存在：

```text
exports/<name>_<timestamp>.pptx
```

输出结果目标是：**在 PowerPoint 中继续可编辑**，而不仅仅是可预览。

---

## 科学文档支持

这个 fork 尤其面向技术与学术输入材料。

### MinerU 文档解析

MinerU 用于增强对文档型输入的抽取与理解，尤其适用于：

- 论文风格 PDF
- 结构化学术文本
- 含有章节、表格、公式邻近内容的材料
- 使用通用解析流程时较难提取干净结构的技术文档

### SVG 公式支持

该 fork 增强了基于 SVG 的公式处理能力，使数学公式在生成后的幻灯片中显示得更清晰。

特别适合：

- 数学或公式较多的报告
- 科学演示文稿
- 教学课件
- 研究总结类幻灯片

> 注意：SVG 公式支持主要提升的是**显示质量与清晰度**。至于公式是否能完全保持原始结构或达到完全可编辑，仍取决于源文档质量和具体生成路径。

---

## 工作原理

PPT Master 本质上是一个与 AI agent / 编码环境配合使用的工作流工具层。

典型流程如下：

1. 你提供源材料
2. AI 读取并整理内容结构
3. 项目生成幻灯片内容和 SVG 资源
4. 管线导出原生可编辑的 `.pptx`

它最适合在具备以下能力的环境中运行：

- 读写文件
- 执行命令
- 多轮交互
- 持续推进任务

---

## 示例

参见：

- [examples/](./examples/)
- [examples/README.md](./examples/README.md)

如果你希望更清楚地展示这个 fork 的差异化能力，建议后续补充以下类型的示例：

- 论文总结
- 技术白皮书
- 公式密集型讲义
- 科研报告

---

## 文档

建议先看：

- [Windows Installation](./docs/windows-installation.md)
- [FAQ](./docs/faq.md)
- [Templates Guide](./docs/templates-guide.md)
- [Technical Design](./docs/technical-design.md)

更多文档：

| 文档 | 说明 |
|------|------|
| [Why PPT Master](./docs/why-ppt-master.md) | 与其他 AI 演示工具的对比 |
| [SKILL.md](./skills/ppt-master/SKILL.md) | 核心工作流与规则 |
| [Canvas Formats](./skills/ppt-master/references/canvas-formats.md) | 支持的输出格式 |
| [Animations & Transitions](./skills/ppt-master/references/animations.md) | 动画与转场支持 |
| [Audio Narration & Video Export](./docs/audio-narration.md) | 语音旁白与视频导出 |
| [Scripts & Tools](./skills/ppt-master/scripts/README.md) | 脚本与工具说明 |
| [Examples](./examples/README.md) | 示例项目 |
| [FAQ](./docs/faq.md) | 常见问题与排障 |

---

## 与上游项目的关系

本项目基于：

- **上游仓库**：[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)

这个 fork 的目标是在保留原项目优势的同时，进一步扩展对**科学 / 学术文档场景**的支持。

如果你需要的是一个更通用的演示生成项目，可以查看上游仓库。  
如果你主要处理论文、公式、技术文档，那么这个 fork 可能更适合你。

---

## 贡献

欢迎贡献。

请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)。

提交 issue 或 PR 时，如果能顺便说明问题属于以下哪类，会更有助于沟通：

- 与上游兼容相关
- fork 特有的科学文档解析问题
- 公式渲染问题
- 文档到幻灯片的工作流问题
- 安装或环境问题

---

## 许可证

[MIT](./LICENSE)

原始项目版权归 Hugo He 所有  
fork 修改部分版权归 hongyi 所有

---

## 致谢

- **原始项目**：[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master) by [Hugo He](https://github.com/hugohe3)
- [MinerU](https://github.com/opendatalab/MinerU) — 文档解析支持
- [SVG Repo](https://www.svgrepo.com/)
- [Tabler Icons](https://github.com/tabler/tabler-icons)
- [Simple Icons](https://github.com/simple-icons/simple-icons)

---

## 联系方式

- **Bug 反馈 / 功能建议**：[GitHub Issues](https://github.com/hongyi652/ppt-master-sci-fork/issues)
- **邮箱**：[877454565@qq.com](mailto:877454565@qq.com)

---

[⬆ 返回顶部](#ppt-master-sci-fork--面向论文pdf-与技术文档的可原生编辑-pptx-生成工具)
