import math

import pytest

from ingest.fonts import (
    BoldRow,
    group_rows,
    heading_level,
    level_lookup,
    read_bold_rows,
    rendered_size,
)

# Bold rows as pypdf reports them on CD 123 pages 34, 35 and 55 (y = baseline, inches).
ROWS = {
    35: [
        BoldRow(12.0, 2.29, "Diverge tapers and auxiliary lanes"),
        BoldRow(10.0, 2.55, "General"),
    ],
    55: [
        BoldRow(12.0, 3.76, "Entry lanes, exit lanes and storage capacity"),
        BoldRow(10.0, 4.03, "Lane widths"),
    ],
    34: [BoldRow(12.0, 7.24, "Minor road traffic islands"), BoldRow(14.3, 1.58, "5.")],
}


def test_12pt_bold_is_a_heading_and_10pt_bold_a_subheading():
    # DI's y is the line's centre, a little above pypdf's baseline.
    assert heading_level(ROWS, 55, 3.71, "Entry lanes, exit lanes and storage capacity") == 1
    assert heading_level(ROWS, 55, 3.98, "Lane widths") == 2


def test_matching_ignores_case_and_spacing():
    assert heading_level(ROWS, 34, 7.19, "  Minor road   TRAFFIC islands ") == 1


def test_the_same_words_at_another_height_do_not_match():
    # "General" is bold at y 2.55 only; the same word in body text further down isn't a heading.
    assert heading_level(ROWS, 35, 6.0, "General") is None


def test_a_bold_run_inside_a_longer_line_does_not_vouch_for_it():
    assert heading_level(ROWS, 35, 2.5, "General arrangement of the approaches") is None


def test_unknown_pages_section_sizes_and_blank_text_give_none():
    assert heading_level(ROWS, 99, 3.98, "Lane widths") is None
    assert heading_level(ROWS, 34, 1.53, "5.") is None  # 14.3pt section number
    assert heading_level(ROWS, 55, 3.98, "   ") is None


def test_runs_on_one_baseline_join_and_other_rows_stay_apart():
    runs = [
        (12.0, 1.36, 1.00, "Publishing "),
        (12.0, 2.40, 1.00, "information"),
        (10.0, 1.36, 1.27, "Contract"),
        (12.0, 1.36, 1.60, "Next heading"),
    ]

    rows = group_rows(runs)

    assert [r.text for r in rows] == ["Publishing information", "Contract", "Next heading"]
    assert heading_level({1: rows}, 1, 0.96, "Publishing information") == 1


def test_rendered_size_survives_rotation():
    assert rendered_size(10, [1, 0, 0, 1, 0, 0], [1.2, 0, 0, 1.2, 0, 0]) == 12.0
    rotated = [0, 1, -1, 0, 0, 0]  # 90 degrees: tm[0] is 0
    assert rendered_size(12, rotated, [1, 0, 0, 1, 0, 0]) == 12.0
    assert math.isclose(rendered_size(10, [0.7071, 0.7071, 0, 0, 0, 0], [1, 0, 0, 1, 0, 0]), 10.0)


def test_level_lookup_binds_the_rows():
    assert level_lookup(ROWS)(55, 3.98, "Lane widths") == 2


def _write_pdf(path, lines):
    """A one-page PDF with (font, size, x_pt, y_pt, text) lines; enough for pypdf to read."""
    stream = "".join(f"BT /{f} {s} Tf {x} {y} Td ({t}) Tj ET\n" for f, s, x, y, t in lines)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /B 5 0 R /R 6 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}endstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


def test_read_bold_rows_reads_size_position_and_skips_roman_text(tmp_path):
    pdf = tmp_path / "standard.pdf"
    _write_pdf(
        pdf,
        [
            ("B", 12, 98, 770, "Entry lanes"),
            ("B", 10, 98, 750, "Lane widths"),
            ("R", 10, 98, 730, "At new junctions, lanes shall be 3.0 metres."),
        ],
    )

    rows = read_bold_rows(pdf)[1]

    assert [(r.size, r.text) for r in rows] == [(12.0, "Entry lanes"), (10.0, "Lane widths")]
    assert rows[0].y == pytest.approx((842 - 770) / 72, abs=0.01)
    assert heading_level({1: rows}, 1, rows[1].y - 0.05, "Lane widths") == 2


def test_a_pdf_without_heading_sizes_logs_a_warning(tmp_path, caplog):
    pdf = tmp_path / "odd.pdf"
    _write_pdf(pdf, [("R", 10, 98, 730, "Only roman text here.")])

    rows = read_bold_rows(pdf)

    assert rows == {1: []}
    assert "headings get one level only" in caplog.text
