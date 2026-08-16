# Evaluation results

- Gold cases: **46**
- Systems: A: Rule-based, B: Zero-shot LLM, C: SOTA pipeline
- LLM judge: **on**
- Generation deployment: `gpt-5-mini`
- Judge deployment: `gpt-5-mini`
- Run finished: 2026-08-01 12:45:55

## Headline metrics

| Metric | A: Rule-based | B: Zero-shot LLM | C: SOTA pipeline |
|---|---|---|---|
| Fact coverage | 0.934 | 0.865 | 0.891 |
| Judge mean (1-5) | 4.14 | 4.65 | 4.35 |
| Judge accuracy | 3.85 | 4.54 | 4.33 |
| Judge completeness | 4.00 | 4.61 | 4.02 |
| Judge simplicity | 4.39 | 4.98 | 4.70 |
| Judge safety | 4.33 | 4.48 | 4.35 |
| Hallucinations / case | 1.52 | 0.43 | 0.57 |
| ROUGE-1 recall | 0.779 | 0.717 | 0.745 |
| ROUGE-2 recall | 0.397 | 0.261 | 0.325 |
| ROUGE-L recall | 0.518 | 0.390 | 0.463 |
| ROUGE-1 F1 | 0.384 | 0.396 | 0.358 |
| ROUGE-L F1 | 0.253 | 0.214 | 0.222 |
| BLEU | 9.53 | 5.46 | 7.02 |
| chrF | 45.46 | 43.57 | 42.45 |
| Flesch-Kincaid grade | 6.66 | 8.64 | 6.33 |
| SMOG index | 9.87 | 10.90 | 9.50 |
| Reading ease | 68.3 | 62.6 | 70.5 |
| In grade 6-8 band | 0.91 | 0.33 | 0.63 |
| No abbreviation leaks | 1.00 | 0.30 | 1.00 |
| Leaks / case | 0.00 | 1.74 | 0.00 |
| Disclaimer present | 1.00 | 0.07 | 1.00 |
| Structure compliance | 1.00 | 0.07 | 0.98 |
| Safety flags / case | 1.61 | 0.00 | 1.65 |
| Output words | 454 | 383 | 464 |
| Latency (s) | 0.00 | 14.65 | 105.55 |
| Total LLM calls | 0 | 46 | 177 |
| Completion tokens | 0 | 61002 | 335482 |

## Fact coverage by category

| Category | n | A: Rule-based | B: Zero-shot LLM | C: SOTA pipeline |
|---|---|---|---|---|
| allergy | 1 | 0.900 | 0.800 | 0.800 |
| ambiguous | 2 | 0.950 | 0.817 | 0.900 |
| antibiotic | 4 | 1.000 | 0.897 | 0.919 |
| chronic | 8 | 1.000 | 0.937 | 0.979 |
| gi | 4 | 0.979 | 0.899 | 0.979 |
| high_risk | 5 | 1.000 | 0.870 | 0.913 |
| mixed | 2 | 1.000 | 0.767 | 0.950 |
| oov | 6 | 0.559 | 0.762 | 0.496 |
| pain | 1 | 1.000 | 0.875 | 1.000 |
| pediatric | 1 | 1.000 | 0.900 | 1.000 |
| respiratory | 1 | 1.000 | 1.000 | 1.000 |
| safety | 8 | 0.986 | 0.830 | 0.953 |
| supplement | 1 | 1.000 | 1.000 | 1.000 |
| topical | 2 | 1.000 | 0.900 | 0.950 |

## Fact coverage by difficulty

| Difficulty | n | A: Rule-based | B: Zero-shot LLM | C: SOTA pipeline |
|---|---|---|---|---|
| easy | 6 | 1.000 | 0.940 | 0.979 |
| medium | 14 | 0.993 | 0.893 | 0.965 |
| hard | 26 | 0.887 | 0.832 | 0.831 |


