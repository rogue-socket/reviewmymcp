# Scoring Calibration Notes

Date: 2026-05-31

## Fixture Set

Calibration used the replay reports in `audits/2026-05-31/fixtures-replay/`:

| Server | Tool count | Call count | Notable grades |
| --- | ---: | ---: | --- |
| everything | 15 | 48 | accuracy/discoverability/reliability B |
| filesystem | 14 | 53 | security C; efficiency/accuracy/composability/reliability B |
| github | 26 | 43 | efficiency/conformance C, discoverability D; accuracy/reliability/compliance B |
| playwright | 23 | 31 | efficiency C, accuracy D, discoverability C, security B |
| web_search | 1 | 35 | reliability B from silent empty-success behavior |

## Rationale

The current severity weights remain:

| Severity | Weight |
| --- | ---: |
| critical | 25 |
| high | 10 |
| medium | 4 |
| low | 1 |
| info | 0 |

These weights produce the expected broad shape on the fixture set:

- Well-contained servers with a few medium findings land in A/B instead of failing the dimension.
- Servers with repeated tool-surface or security issues fall into C/D in the affected dimensions.
- Clean single-tool behavior is not over-penalized by one non-critical finding.
- Critical and repeated-high hard caps still dominate when safety defects are severe.

No weight change is recommended from this calibration pass. The main gaps found by cross-checking were missing checks, not bad severity math; those checks are tracked separately and should be recalibrated with fresh fixture reports after they land.

## Follow-Up

Re-run this calibration after adding more real-server fixtures, especially servers with known critical defects and known-good production deployments. Weight changes should be made only when the same misgrading pattern appears across multiple fixtures.
