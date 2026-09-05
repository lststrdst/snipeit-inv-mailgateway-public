import runpy
from pathlib import Path

import pytest

CHECKER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/check_public_anonymization.py")
)
SIGNATURE = CHECKER["PUBLIC_AUTHOR_SIGNATURE"]


def test_exact_public_author_signature_is_allowed_in_root_readme(tmp_path):
    assert CHECKER["findings"](tmp_path, tmp_path / "README.md", f"# Проект\n\n{SIGNATURE}\n") == []


@pytest.mark.parametrize("filename", ["docs/README.md", "CHANGELOG.md", "report.txt"])
def test_public_author_signature_is_not_allowed_in_other_files(tmp_path, filename):
    assert CHECKER["findings"](tmp_path, tmp_path / filename, SIGNATURE)


@pytest.mark.parametrize("template", ["Автор: {}", "{} — разработчик", "`{}`"])
def test_author_exception_requires_the_exact_signature_line(tmp_path, template):
    assert CHECKER["findings"](tmp_path, tmp_path / "README.md", template.format(SIGNATURE))


def test_author_signature_does_not_hide_other_blocked_literals(tmp_path):
    for literal in CHECKER["BLOCKED_LITERALS"]:
        text = f"{SIGNATURE}\n{literal}\n"
        assert CHECKER["findings"](tmp_path, tmp_path / "README.md", text)


@pytest.mark.parametrize("suffix", [".htm", ".html"])
def test_html_examples_are_checked_for_internal_identifiers(tmp_path, monkeypatch, suffix):
    assert suffix in CHECKER["TEXT_SUFFIXES"]
    literal = sorted(CHECKER["BLOCKED_LITERALS"])[0]
    (tmp_path / f"example{suffix}").write_text(f"<p>{literal}</p>", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["check_public_anonymization.py", str(tmp_path)])
    assert CHECKER["main"]() == 1
