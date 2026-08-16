# Evaluation results

- Gold cases: **46**
- Systems: A: Rule-based
- LLM judge: **off**
- Run finished: 2026-07-31 22:55:05

## Headline metrics

| Metric | A: Rule-based |
|---|---|
| Fact coverage | 0.932 |
| ROUGE-1 recall | 0.779 |
| ROUGE-2 recall | 0.397 |
| ROUGE-L recall | 0.518 |
| ROUGE-1 F1 | 0.384 |
| ROUGE-L F1 | 0.253 |
| BLEU | 9.53 |
| chrF | 45.45 |
| Flesch-Kincaid grade | 6.66 |
| SMOG index | 9.87 |
| Reading ease | 68.3 |
| In grade 6-8 band | 0.91 |
| No abbreviation leaks | 1.00 |
| Leaks / case | 0.00 |
| Disclaimer present | 1.00 |
| Structure compliance | 1.00 |
| Safety flags / case | 1.61 |
| Output words | 454 |
| Latency (s) | 0.00 |
| Total LLM calls | 0 |
| Completion tokens | 0 |

## Fact coverage by category

| Category | n | A: Rule-based |
|---|---|---|
| allergy | 1 | 0.900 |
| ambiguous | 2 | 0.950 |
| antibiotic | 4 | 1.000 |
| chronic | 8 | 0.988 |
| gi | 4 | 0.979 |
| high_risk | 5 | 1.000 |
| mixed | 2 | 1.000 |
| oov | 6 | 0.559 |
| pain | 1 | 1.000 |
| pediatric | 1 | 1.000 |
| respiratory | 1 | 1.000 |
| safety | 8 | 0.986 |
| supplement | 1 | 1.000 |
| topical | 2 | 1.000 |

## Fact coverage by difficulty

| Difficulty | n | A: Rule-based |
|---|---|---|
| easy | 6 | 1.000 |
| medium | 14 | 0.986 |
| hard | 26 | 0.887 |

> ROUGE/BLEU are reported recall-first. The gold references are compact paragraphs while systems A and C emit a longer six-section counselling document, so overlap precision (and therefore F1) is structurally depressed and should not be read as a quality ranking on its own.
