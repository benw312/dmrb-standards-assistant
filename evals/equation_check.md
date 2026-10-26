# Equation check: the CD 109 tools against the standard

Claude, 2026-10-26: a first check for Ben to confirm, not a sign-off. Two parts.

## 1. Formulas and constants against the printed equations

Each equation was read off the rendered CD 109 Revision 1 page and compared with
`agent/tools/equations.py`.

| Equation | Page | Printed | Code | Match |
|---|---|---|---|---|
| 2.2a (dual) | 10 | Ac = 6.6 + B/10 | `AC_DUAL_BASE 6.6`, `AC_DUAL_DIVISOR 10` | Yes |
| 2.2b (single) | 10 | Ac = 12 − VISI/60 + 2B/45 | `12`, `60`, `2/45` | Yes |
| Bendiness NOTE | 10 | 180° over 3 km = 60°/km | tool gives 60.0 | Yes |
| 2.8.1 | 13 | VISI = n / (1/V1 + … + 1/Vn) | harmonic mean | Yes |
| 2.8.2 | 13 | log10 VISI = 2.46 + VW/25 − B/400; valid to 720 m (NOTE 1) | `2.46`, `25`, `400`, 720 m warning | Yes |
| 4.2 | 20 | S = V² / 2.828R; 7% rural (4.3, except existing roads / connector loops), 5% urban (4.4) | `2.828`, caps and exceptions | Yes |
| 4.13 | 22 | L = V³ / 46.7qR; q ≤ 0.6 (4.14 shall), ≤ 0.3 (4.14.1 should); L vs √(24R) (4.15, 4.15.1) | `46.7`, q warnings, exact √(24R) comparison | Yes |

Table 2.10's radii are checked against the indexed table by `tests/test_design_speed_table.py`
(passes locally, where the chunks exist).

## 2. Independent re-implementation

A separate float implementation, written from the printed formulas, compared with the tools on
6,041 cases: 3,000 random superelevation cases and 3,000 transition cases across every design
speed, radius 30–4,000 m and q of 0.3 / 0.45 / 0.6, plus grids for Equation 2.8.2, both Ac
equations and a 40-reading harmonic mean. **0 mismatches** (to the displayed precision).

## Found: questions for Ben

1. **Superelevation below 2.5% just under the 2.5% radius.** The tool follows the clauses:
   2.5% from Table 2.10's "minimum R with 2.5%" up to "minimum R with adverse camber"
   (cl. 4.1), and Equation 4.2 below it (cl. 4.2). But for three speeds Equation 4.2 gives
   less than 2.5% just below that radius, so a *tighter* curve gets *less* superelevation:

   | Design speed | Radii | Equation 4.2 |
   |---|---|---|
   | 100 kph | 1414–1439 m | down to 2.46% |
   | 70 kph | 693–719 m | down to 2.41% |
   | 50 kph | 354–359 m | down to 2.46% |

   The table's radii are rounded; cl. 4.2's own shorthand says V²/R > 7, where Equation 4.2 is
   2.48%. Should the tool floor Equation 4.2 at 2.5%, or report it as printed?
2. **VISI readings, both directions.** cl. 2.6.1 says measure in both directions; the tool
   warns below 20 readings in total. 2 km at 100 m is 20 intervals per direction, so the
   trigger might be 40.
3. **Typical VISI values** (cl. 2.8.3: 700 / 500 / 300 m for preliminary route analysis) aren't
   offered by any tool. Add them as an option to `empirical_visibility`, or leave to the model
   to cite?
