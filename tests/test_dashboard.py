"""Dashboard generation does not require live data."""

from pathlib import Path

from natgas.dashboard import generate


def test_generate_writes_html_and_json(tmp_path: Path) -> None:
    path = generate(tmp_path)
    assert path.exists()
    html = path.read_text()
    assert "US natural gas storage forecast" in html
    assert (tmp_path / "data.json").exists()
    assert (tmp_path / ".nojekyll").exists()
