# Run dry-2

## Matches

- then-i-am: 10 ended
- word-for-word: 10 ended

## Turns and acceptance

- then-i-am: 23 verdicts in match
- word-for-word: 60 verdicts in match
- opponent-fireworks: 60/66 moves stood (91%)
- opponent-luna: 13/17 moves stood (76%)
- Flash share of accepted turns: 82% (inside the 75% to 85% range)
- attempts: {'parsed': 78, 'parsed_on_retry': 5}
- judge versions (model, prompt hash): [('deepseek/deepseek-v4.1-flash@fireworks', '10669238d8072caa'), ('deepseek/deepseek-v4.1-flash@fireworks', '49a2c53c66802d43')]

## Sabotage

- amplification: 1/1 confirmed
- injection: 1/1 confirmed
- meta: 1/1 confirmed
- near_duplicate: 3/3 confirmed
- verbosity: 2/2 confirmed
- weak_but_legal: 2/3 confirmed
- disputed records exported: 1

## Length

- then-i-am: mean chars stood 52, fell 56; Spearman(length, weighted score) over stood moves = 0.19
- word-for-word: mean chars stood 57, fell 0; Spearman(length, weighted score) over stood moves = -0.02

## Corpus

- player.jsonl: 75
- judge.jsonl: 93
- host.jsonl: 93
- disputed.jsonl: 1
- dropped, player: not accepted: 10
- salvaged verdicts kept out: 0
- cross-judge agreement: not run

## Cost

- deepseek/deepseek-v4.1-flash: 4 calls, $0.0002, $0.00006 per call, tokens in 2274, out 60
- deepseek/deepseek-v4.1-flash@fireworks: 94 calls, $0.0839, $0.00089 per call, tokens in 282840, out 94654
- opponent-fireworks: 66 calls, $0.0055, $0.00008 per call, tokens in 44726, out 929
- opponent-luna: 17 calls, $0.0025, $0.00015 per call, tokens in 10557, out 301
- total: $0.0921

## Plan

```json
{
 "matches": {
  "then-i-am": 10,
  "word-for-word": 10
 },
 "classes": {
  "then-i-am": "counter",
  "word-for-word": "showcase"
 },
 "provider": "fireworks",
 "teachers": {
  "opponent-fireworks": 0.8,
  "opponent-luna": 0.19999999999999996
 },
 "judge_ref": "judge-fireworks",
 "flash_ref": "opponent-fireworks",
 "judge_prompt_hash": {
  "then-i-am": "10669238d8072caa",
  "word-for-word": "49a2c53c66802d43"
 },
 "template_hash": {
  "then-i-am": "fd26ef203ced40ec",
  "word-for-word": "ea2920eb355e0a44"
 },
 "sabotage_rate": 0.12,
 "budget": 0.5,
 "created": "2026-09-27T15:38:29.558614+00:00"
}
```
