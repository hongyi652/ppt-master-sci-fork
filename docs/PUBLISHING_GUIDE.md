# 开源发布操作指南

本文档提供将本项目发布到 GitHub 的完整操作步骤。

---

## 一、发布前检查清单

- [x] LICENSE 文件 — 保留原作者版权，添加自己的版权声明
- [x] README.md / README_CN.md — 明确标注 fork 来源和修改内容
- [x] CHANGELOG.md — 记录你的修改
- [x] CONTRIBUTING.md — 更新仓库地址
- [x] SECURITY.md — 更新联系方式
- [x] CODE_OF_CONDUCT.md — 更新联系方式
- [x] .gitignore — 确保不会提交敏感文件（.env、API keys）
- [x] INSTALL.md — 完整的安装依赖说明

### 敏感文件检查

发布前**务必**确认以下文件/目录**不会**被提交：

```
.env              ← API 密钥（含 MINERU_API_TOKEN）
LOCAL_SETUP_CN.md ← 本地路径信息
uploads/          ← 用户上传的文件（含论文 PDF）
exports/          ← 生成的 PPTX
projects/         ← 工作中的项目（除 README.md）
tmp_mineru_output/ ← 临时解析输出
tmp_mineru_probe/  ← 临时探测输出
svg_qc_output.txt ← 质检输出
```

---

## 二、发布步骤

当前工作区已有 Git 历史，且你准备新开公开仓库 `https://github.com/hongyi652/ppt-master-sci-fork`。
由于历史中已追踪了 `uploads/`、`tmp_mineru_probe/` 等含用户数据的文件，公开发布前建议清理，或直接重建一份干净历史。

### 步骤 1：从 Git 索引中移除不应追踪的文件

```powershell
cd "c:\Users\87745\Desktop\idea\ppt-master"

# 移除已追踪的敏感/临时文件（本地文件不删除，仅从 Git 索引移除）
git rm -r --cached uploads/
git rm -r --cached tmp_mineru_probe/
git rm --cached svg_qc_output.txt
git rm --cached LOCAL_SETUP_CN.md
```

### 步骤 1.5：将远程指向新仓库

如果当前 `origin` 仍指向旧仓库，改成新仓库即可：

```powershell
git remote set-url origin https://github.com/hongyi652/ppt-master-sci-fork.git
git remote -v
```

### 步骤 2：确认状态

```powershell
git status
```

应该看到大量 "deleted" 文件（Git 不再追踪），以及你修改过的文档。

### 步骤 3：提交清理

```powershell
git add .
git status  # 最终确认：不应有 .env、uploads 内容等

git commit -m "chore: clean tracked user data, update docs for public release

- Remove uploads/, tmp_mineru_probe/, svg_qc_output.txt from tracking
- Add INSTALL.md with full dependency guide (incl. LaTeX→SVG)
- Add CHANGELOG.md documenting fork modifications
- Update README/CONTRIBUTING/SECURITY with fork attribution
- Update .gitignore for comprehensive exclusion"
```

### 步骤 4：推送

```powershell
git push -u origin main
```

> **注意**：虽然文件从当前索引移除了，但 Git 历史中仍存在。如果 uploads/ 中有隐私敏感内容（如未公开论文），建议使用方案 B（下方）重建干净历史。

### （可选）方案 B：完全干净的历史

如果你不想让 Git 历史中保留任何用户文件痕迹：

```powershell
cd "c:\Users\87745\Desktop\idea\ppt-master"

# 删除旧的 .git 目录
Remove-Item -Recurse -Force .git

# 重新初始化
git init
git add .
git status  # 最终确认

git commit -m "feat: initial release — fork of hugohe3/ppt-master (2026-05-22)

Based on upstream v2.8.0. Key additions:
- MinerU document parsing for scientific PDFs
- SVG formula rendering as PPT assets
- GUI progress display and generation time
- Fixed text overlap, image truncation, formula stretching"

git remote add origin https://github.com/hongyi652/ppt-master-sci-fork.git
git branch -M main
git push -f origin main
```

---

## 三、发布后配置（GitHub 网页操作）

### 3.1 仓库设置

1. 进入仓库 **Settings** → **General**
2. Description: `AI generates natively editable PPTX — fork with MinerU parsing & SVG formula support`
3. 确认 "Default branch" 是 `main`
4. 可选：启用 **Issues**

### 3.2 添加 Topics

进入仓库主页 → 点击描述旁的 ⚙️ → 添加 topics：
```
ppt, powerpoint, pptx, ai-agent, presentation, slides, latex, svg, mineru, scientific-document
```

### 3.3 创建首个 Release

1. 进入 **Releases** → **Create a new release**
2. Tag: `v1.0.0`
3. Title: `v1.0.0 — Initial Fork Release`
4. Description 填入 CHANGELOG.md 中的内容
5. 点击 **Publish release**

---

## 四、持续维护规范

### 版本号规范（Semantic Versioning）

```
v主版本.次版本.修订号

v1.0.0 → 首次发布
v1.0.1 → bug 修复
v1.1.0 → 新增功能（向后兼容）
v2.0.0 → 破坏性变更
```

### 提交信息规范（Conventional Commits）

```
feat: 新功能
fix: 修复 bug
docs: 文档更新
style: 代码格式（不影响逻辑）
refactor: 代码重构
perf: 性能优化
chore: 构建/工具变更
```

示例：
```
feat: add MinerU PDF parsing support
fix: resolve text overlap in slide generation
docs: update installation guide for Windows
```

### 同步上游更新

如果需要从上游 hugohe3/ppt-master 拉取更新：

```powershell
# 添加上游远程（仅需一次）
git remote add upstream https://github.com/hugohe3/ppt-master.git

# 拉取上游更新
git fetch upstream

# 合并上游 main 到你的分支
git merge upstream/main

# 解决冲突后推送
git push origin main
```

---

## 五、MIT 许可证的义务

MIT 许可证非常宽松，你的主要义务是：

1. **保留版权声明** — 保留原作者 Hugo He 的 copyright 声明（已在 LICENSE 中做到）
2. **保留许可证文本** — LICENSE 文件需要完整保留
3. **你的权利** — 可以自由修改、分发、商用、再许可
