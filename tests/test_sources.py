from pathlib import Path

import pytest
from pydantic import ValidationError

from ingest.sources import load_sources

VALID_YAML = """
sources:
  - code: CD 109
    title: Highway link design
    version: "2.0.0"
    revision_date: 2021-03-01
    url: https://www.standardsforhighways.co.uk/search/example-cd109
    filename: CD109.pdf
  - code: CG 501
    title: Design of highway drainage systems
    version: "2.1.0"
    revision_date: 2022-07-01
    url: https://www.standardsforhighways.co.uk/search/example-cg501
    filename: CG501.pdf
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text)
    return path


def test_loads_valid_sources(tmp_path):
    sources = load_sources(write(tmp_path, VALID_YAML))

    assert [s.code for s in sources] == ["CD 109", "CG 501"]
    assert sources[0].doc_id == "CD109"
    assert sources[0].revision_date.year == 2021


def test_rejects_placeholder_version(tmp_path):
    text = VALID_YAML.replace('"2.0.0"', "TODO")

    with pytest.raises(ValidationError, match="TODO"):
        load_sources(write(tmp_path, text))


def test_rejects_duplicate_codes(tmp_path):
    text = VALID_YAML.replace("code: CG 501", "code: CD 109")

    with pytest.raises(ValueError, match="Duplicate"):
        load_sources(write(tmp_path, text))


def test_rejects_malformed_code(tmp_path):
    text = VALID_YAML.replace("code: CD 109", "code: highway link")

    with pytest.raises(ValidationError):
        load_sources(write(tmp_path, text))


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_sources(tmp_path / "nope.yaml")


def test_rejects_code_outside_gg101_scheme(tmp_path):
    text = VALID_YAML.replace("code: CD 109", "code: XD 109")

    with pytest.raises(ValidationError, match="discipline letter"):
        load_sources(write(tmp_path, text))


def test_normalises_code_without_space(tmp_path):
    sources = load_sources(write(tmp_path, VALID_YAML.replace("code: CD 109", "code: CD109")))

    assert sources[0].code == "CD 109"


def test_committed_sources_file_is_valid():
    # Guards the real data/sources.yaml in CI: no TODO versions or malformed codes on main.
    sources = load_sources()

    assert {s.code for s in sources} >= {"GG 101", "CD 109"}


def test_text_figures_default_to_none_and_are_read_as_strings(tmp_path):
    from ingest.sources import load_sources

    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  - {code: CG 501, title: Drainage, version: Version 2.2.0, revision_date: 2025-09-30,\n"
        "     url: 'https://example.org', filename: cg501.pdf, text_figures: ['2.5']}\n"
        "  - {code: CD 109, title: Link design, version: Revision 1, revision_date: 2020-03-01,\n"
        "     url: 'https://example.org', filename: cd109.pdf}\n"
    )

    cg501, cd109 = load_sources(path)

    assert cg501.text_figures == ("2.5",) and cd109.text_figures == ()


@pytest.mark.parametrize(
    ("entry", "message"),
    [("[2.5]", "valid string"), ("['Fig 2']", "text_figures")],
    ids=["unquoted-yaml-float", "unreadable-number"],
)
def test_bad_text_figures_fail_at_load_time(tmp_path, entry, message):
    from ingest.sources import load_sources

    path = tmp_path / "sources.yaml"
    path.write_text(
        "sources:\n"
        "  - {code: CG 501, title: Drainage, version: Version 2.2.0, revision_date: 2025-09-30,\n"
        f"     url: 'https://example.org', filename: cg501.pdf, text_figures: {entry}}}\n"
    )

    with pytest.raises(ValueError, match=message):
        load_sources(path)
