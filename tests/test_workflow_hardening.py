from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "ppt-master" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import preflight_check  # noqa: E402
from project_manager import ProjectManager  # noqa: E402
from svg_finalize.align_embed_images import align_and_embed_images_in_svg  # noqa: E402
from svg_quality_checker import SVGQualityChecker  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover - covered by skip guards
    Image = None


class PreflightCheckTests(unittest.TestCase):
    def test_python_pptx_import_name_uses_pptx_module(self) -> None:
        required = dict(preflight_check.REQUIRED_PACKAGES)

        self.assertEqual(required["python-pptx"], "pptx")
        result_by_name = {
            message.split()[0]: (passed, message)
            for passed, message in preflight_check._check_required_packages()
        }
        self.assertTrue(result_by_name["python-pptx"][0], result_by_name["python-pptx"][1])

    def test_output_dirs_are_not_created_without_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)

            results = preflight_check._check_output_dirs(project_dir)

            self.assertFalse((project_dir / "svg_output").exists())
            self.assertTrue(any(not passed and "missing" in message for passed, message in results))

            fixed_results = preflight_check._check_output_dirs(project_dir, fix=True)
            self.assertTrue((project_dir / "svg_output").is_dir())
            self.assertTrue(all(passed for passed, _ in fixed_results))


class ProjectManagerReportTests(unittest.TestCase):
    def test_import_sources_writes_stage_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "project"
            project_dir.mkdir()
            source = root / "source.md"
            source.write_text("# Source\n\nPlain content.\n", encoding="utf-8")

            with contextlib.redirect_stderr(io.StringIO()):
                summary = ProjectManager().import_sources(str(project_dir), [str(source)])
            report_path = project_dir / "notes" / "import_sources_report.json"

            self.assertTrue(report_path.is_file())
            self.assertTrue(summary["markdown"])
            report = report_path.read_text(encoding="utf-8")
            self.assertIn('"stages"', report)
            self.assertIn('"archive_markdown"', report)
            self.assertIn('"sync_formulas"', report)


@unittest.skipIf(Image is None, "Pillow is required for image fixture generation")
class ImageFinalizeTests(unittest.TestCase):
    def test_png_and_formula_svg_embed_but_missing_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            Image.new("RGB", (4, 4), color="red").save(work_dir / "tiny.png")
            (work_dir / "formula.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
                '<path d="M0 0H10V6H0Z"/></svg>',
                encoding="utf-8",
            )
            svg_path = work_dir / "slide.svg"
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
                'viewBox="0 0 1280 720">'
                '<image href="tiny.png" x="0" y="0" width="4" height="4"/>'
                '<image href="formula.svg" x="10" y="0" width="10" height="6"/>'
                '<image href="missing.png" x="20" y="0" width="4" height="4"/>'
                '</svg>',
                encoding="utf-8",
            )

            summary = align_and_embed_images_in_svg(svg_path)
            content = svg_path.read_text(encoding="utf-8")

            self.assertEqual(summary.processed, 2)
            self.assertEqual(summary.errors, 1)
            self.assertIn("data:image/png;base64", content)
            self.assertIn("data:image/svg+xml;base64", content)
            self.assertIn("missing.png", content)


@unittest.skipIf(Image is None, "Pillow is required for image resolution checks")
class SVGQualityCheckerTests(unittest.TestCase):
    def test_meet_mode_uses_fitted_size_for_blurry_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            Image.new("RGB", (400, 100), color="blue").save(work_dir / "wide.png")
            svg_path = work_dir / "slide.svg"
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
                'viewBox="0 0 1280 720">'
                '<image href="wide.png" x="0" y="0" width="400" height="400" '
                'preserveAspectRatio="xMidYMid meet"/>'
                '</svg>',
                encoding="utf-8",
            )

            result = SVGQualityChecker(template_mode=True).check_file(str(svg_path))

            self.assertFalse(
                any("may appear blurry" in warning for warning in result["warnings"]),
                result["warnings"],
            )

    def test_actual_upscale_still_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_dir = Path(tmp)
            Image.new("RGB", (100, 50), color="blue").save(work_dir / "small.png")
            svg_path = work_dir / "slide.svg"
            svg_path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
                'viewBox="0 0 1280 720">'
                '<image href="small.png" x="0" y="0" width="400" height="100" '
                'preserveAspectRatio="xMidYMid meet"/>'
                '</svg>',
                encoding="utf-8",
            )

            result = SVGQualityChecker(template_mode=True).check_file(str(svg_path))

            self.assertTrue(
                any("may appear blurry" in warning for warning in result["warnings"]),
                result["warnings"],
            )


if __name__ == "__main__":
    unittest.main()
