# Charts and equations: Phase 4 calculation tools

Survey of CD 109 Revision 1, CD 123 Version 2.1.0 and CG 501 Version 2.2.0 (22 Oct 2026).
Every figure caption was checked (about 110 figures), plus figure regions without a caption. The
candidate pages were rendered and read by eye.

**Why tools:** search indexes clause text and figure captions only. The charts themselves are
pictures, so the model knows "Figure 7.5.4" exists but can't read its curve, and would have to
guess a value. A tool computes the answer in code and cites the clause, following the project
rule that the model never does arithmetic.

**Result:** 7 charts and 6 equations can become tools, split into three tiers by difficulty.
CG 501 Figure 2.5 is a decision flowchart, and the plan is to make its text searchable instead
of building a tool. The other ~100 figures are layout drawings (junctions, road markings,
signs), whose dimensions are already stated in the clause text.

## Build status (2026-10-25)

| # | Tool | Status |
|---|---|---|
| 1–5 | CD 109 Eq 2.2a/b, 2.8.1, 2.8.2, 4.2, 4.13 | **Built** (`agent/tools/equations.py`), wired into the agent as tools |
| 6 | CG 501 flow path length / gradient | Waiting on Ben: OK to cite the derived formulas? |
| 7 | CD 109 Fig 9.23N2 overtaking sections | Waiting on Ben: boundary sides, and what applies above V²/R = 20 |
| 8–10 | CG 501 Fig 7.5.4, 7.5.1; CD 109 Fig 2.1 | Waiting on Ben's digitising; the chart data format and interpolation are built with the first one |
| flagship | Design speed chain (1 + 3 + 10 + Table 2.3) | After tool 10 |
| 11–12 | CD 123 Fig 2.3.1; Fig 9.23N2 inset | Waiting on Ben's tier 3 decision |
| 13 | CG 501 Fig 2.5 flowchart | **Done** as a searchable figure chunk (no tool needed) |

## Tier 1: formulas (exact, no digitising)

| # | Source | Calculates | Inputs | Requirement it serves | Notes |
|---|---|---|---|---|---|
| 1 | CD 109 Eq 2.2a / 2.2b | Alignment constraint Ac | carriageway type, bendiness B (°/km), VISI (m, single only) | cl. 2.2 (shall) | Dual: `Ac = 6.6 + B/10`. Single: `Ac = 12 − VISI/60 + 2B/45`. Bendiness is measured over ≥ 2 km (Glossary); worked example in the NOTE: 180° over 3 km = 60°/km |
| 2 | CD 109 Eq 2.8.1 | Harmonic mean visibility (new roads) | sight distances V1..Vn (m) | cl. 2.8.1 (should) | `VISI = n / Σ(1/Vi)`. Measured every 50 m or 100 m over ≥ 2 km (cl. 2.6, 2.6.1) |
| 3 | CD 109 Eq 2.8.2 | VISI, empirical (existing roads) | average verge width VW (m), B (°/km) | cl. 2.8.2 (should) | `log10 VISI = 2.46 + VW/25 − B/400`. Valid up to VISI = 720 m (NOTE 1): refuse above it |
| 4 | CD 109 Eq 4.2 | Superelevation S (%) | design speed V (kph), radius R (m), rural/urban | cl. 4.2 (shall), 4.3, 4.4 | `S = V² / (2.828 R)`. Cap 7% rural (4.3; except existing roads and connector loops) and 5% urban (4.4). Thresholds by V²/R from Table 2.10 (cl. 4.1, 4.1.1) |
| 5 | CD 109 Eq 4.13 | Basic transition length L (m) | V (kph), q (m/s³), R (m) | cl. 4.12, 4.13 (shall) | `L = V³ / (46.7 q R)`. q ≤ 0.6 (4.14 shall), ≤ 0.3 (4.14.1 should). Use L if L < √(24R), else √(24R) (4.15, 4.15.1) |
| 6 | CG 501 Fig 7.5.3N2a / N2b | Flow path length (m) and gradient (%) | longitudinal gradient S (%), crossfall C (%), paved width W (m) | cl. 7.5.3 (may) | `G = √(S² + C²)`, `L = W·√(1 + (S/C)²)`. **Checked against the charts** for W = 14.6 m (4 × 3.65 m): S 4, C 0.5 gives L = 117.7 m (chart ≈ 118); S 4, C 3.5 gives G = 5.3% (chart 5.3). CG 501 doesn't print the formulas, so Ben to confirm they're acceptable to cite |
| 7 | CD 109 Fig 9.23N2 (main chart) | Overtaking section A–D for a curve | design speed V (kph), radius R (m) | cl. 9.7, 9.13, 9.22, 9.23 (shall), 9.24.1 (should) | Bands are V²/R boundaries printed on the chart: A < 1.25 ≤ B < 3.53 ≤ C < 10 ≤ D ≤ 20. Ben to confirm which side each boundary falls and what applies above 20 |

## Tier 2: digitised curves (moderate)

The curve points are read off the chart once by Ben (ground truth, like the eval questions).
The tool interpolates linearly between them, refuses inputs outside the chart, and flags results
close to the line as marginal, sending them for review.

| # | Source | Answers | Inputs | Requirement | Digitising |
|---|---|---|---|---|---|
| 8 | CG 501 Fig 7.5.4 | Is a localised flow path below the minimum desirable line? | flow path length (0–35 m), flow path gradient (%) | cl. 7.5.4 (should avoid) | One line with 8 plotted markers at 0, 5, …, 35 m. About 5 minutes |
| 9 | CG 501 Fig 7.5.1 | Is the longitudinal gradient / crossfall combination below the minimum desirable line? | number of 3.65 m lanes (4, 5 or 6), longitudinal gradient (0–4%), crossfall (%) | cl. 7.5.1 (should avoid) | Three lines with markers at 0, 1, 2, 3, 4%. Refuse other lane counts. About 10 minutes |
| 10 | CD 109 Fig 2.1 | Design speed (e.g. 100A) for a rural road | Ac (from tool 1), layout constraint Lc | cl. 2.1, 2.4 (shall) | Eight straight dashed lines (Lc 0–35) across Ac 0–20, so two end points each, plus the band edges. Test case from NOTE 2: Ac 12, Lc 15 gives **100A**. Needs Lc from Table 2.3 (road type × verge width × access frequency H/M/L), which is already a table chunk. Single carriageways with Ac < 6.6 are flagged (box on the chart) |

Tools 1, 3 and 10, together with Table 2.3, chain into the **flagship tool**: "what's the design
speed of this rural road?" It goes from bendiness, verge width and road type to a design speed
band, then on to Table 2.10's stopping sight distance and radii for that band.

## Tier 3: harder, candidates to drop

| # | Source | Why it's harder | Proposal |
|---|---|---|---|
| 11 | CD 123 Fig 2.3.1 | Regions (polygons) on major vs minor road AADT, giving simple / ghost island / single lane dualling / other. Most vertices sit on grid lines, but some edges are sloped and the chart is titled "approximate" | Build it: return the region plus "approximate, based on flows only (cl. 2.3.1)", and flag points near a boundary. cl. 2.3.1 and 2.3.2 are "should" |
| 12 | CD 109 Fig 9.23N2 (verge-width inset) | Diagonal line of verge width (3.5 / 6.5 / 10.65 m) against V²/R within section B. Only the 10.65 m at V²/R = 3.53 point is stated in text (NOTE to cl. 9.24) | Ben to say how engineers read it. Drop if it's ambiguous |
| 13 | CG 501 Fig 2.5 | Runoff destination flowchart: yes/no judgement questions ("is it reasonably practicable?"), not numbers | **No tool. Done:** indexed as figure chunk `CG501-Figure-2.5`, citable as `[CG 501 Figure 2.5]` and linked from the clauses that cite it. Other flowcharts are added by listing them in `text_figures` in `data/sources.yaml` |

## Issues found in the standards (for Ben)

- CD 109 cl. 9.24 and 6.9 cite **"Figure 9.24N2"**. The chart with V²/R = 3.53 is printed as
  **Figure 9.23N2** (p63); there is no Figure 9.24N2.
- CD 123 Table 5.18b, first row: "≤ 450, > 4% gradient", where every other row says "≥"
  (noted in PR #15).

## How a tool will look (to settle in Phase 4)

- **Digitised data** goes in `data/charts/<id>.yaml`, written by Ben, committed and attributed
  (OGL v3.0). It holds the source figure, the clauses it serves, axes with units, curves as
  points, which side of the line fails, and the reading tolerance.

  ```yaml
  id: CG501-Figure-7.5.4
  clauses: [CG 501 cl. 7.5.4]
  x: {name: flow path length, unit: m, min: 0, max: 35}
  y: {name: flow path gradient, unit: "%"}
  curves:
    minimum desirable: [[0, 0.15], [5, 0.37], [10, 0.62]]  # ... read with WebPlotDigitizer
  fails: below
  tolerance: 0.05  # within this of the line -> "marginal", sent for review
  ```
- **Tools** are typed functions with Pydantic arguments, like `check_limit`. Each returns the
  value, the clauses it relied on, and a `marginal` / `out_of_range` flag that feeds Phase 5
  escalation.
- **Tests:**
  - every digitised point must round-trip;
  - the worked examples printed in the standards (Fig 2.1 NOTE 2; the bendiness NOTE) must
    pass;
  - the tier 1 formulas must reproduce the chart points within reading tolerance.
- **Digitising** (Ben): [WebPlotDigitizer](https://automeris.io) is free and runs in the
  browser. Screenshot the figure (the images are ~300 dpi), set the axes from two known ticks,
  then click the markers. The CG 501 charts plot markers at whole-number values, so the points
  can be read straight off.

## Scaling to the whole DMRB

The survey above was done by hand for three standards. Across all of the DMRB, the approach is
to automate finding the charts, prioritise them by demand, and add each chart as data rather
than code.

1. **Find candidates automatically at ingest.** Document Intelligence already returns figure
   regions and captions. A scan flags likely charts: axis-like tick numbers and unit labels
   inside the figure, plus "Equation N" captions. On these three standards it flagged 9 of
   about 110 figures, including all 7 real charts. The candidate pages are rendered to images
   for a person, or a vision model, to classify as chart / formula / layout drawing / flowchart.
   That goes into an inventory file per document.
2. **Prioritise by value, not completeness.** Rank candidates by how many clauses cite them
   and how strongly ("shall" above "should"). Add real demand from the agent's own traces: when
   a question needs a figure that has no tool, the agent says so and escalates, and that event
   is logged. The most-asked charts get built first.
3. **Add each chart as data, not code.** Formulas stay as small tested functions; there are few
   of them, and the standards print worked examples to test against. Each chart becomes a
   digitised YAML file with provenance: document, version, figure, who digitised it and when.
   One generic `check_chart(chart_id, inputs)` tool interpolates any of them. Adding a chart
   needs no new code, and tests check that every digitised point round-trips.
4. **Never guess from a caption.** If no tool exists for a figure, the answer names the figure
   and goes to an engineer. That's the same escalation path as a possible departure.
5. **Keep it current.** Chart files are keyed to the document version in `sources.yaml`. When
   a revision is ingested (the footer check already detects a version change), any chart whose
   version no longer matches fails a test until someone re-checks it.

The trade-off to state in an interview: people stay in the loop for classifying and digitising
charts, because they're ground truth, like the eval answers. That human work is spread over
time and directed at the charts people actually ask about, rather than done up front for all
of the DMRB.
