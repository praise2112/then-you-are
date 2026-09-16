# Run replay-1

## Matches

- then-i-am: 10 ended
- word-for-word: 10 ended

## Turns and acceptance

- then-i-am: 38 verdicts in match
- word-for-word: 66 verdicts in match
- opponent-fireworks: 68/79 moves stood (86%)
- opponent-luna: 20/25 moves stood (80%)
- Flash share of accepted turns: 77% (inside the 75% to 85% range)
- attempts: {'parsed': 104}
- judge versions (model, prompt hash): [('deepseek/deepseek-v4.1-flash', '40b51ad95a185efb'), ('deepseek/deepseek-v4.1-flash', 'a30d56955237d706')]

## Sabotage

- injection: 2/2 confirmed
- meta: 3/3 confirmed
- near_duplicate: 4/4 confirmed
- verbosity: 3/3 confirmed
- weak_but_legal: 1/1 confirmed
- disputed records exported: 0

## Length

- then-i-am: mean chars stood 53, fell 53; Spearman(length, weighted score) over stood moves = 0.09
- word-for-word: mean chars stood 62, fell 61; Spearman(length, weighted score) over stood moves = -0.25

## Corpus

- player.jsonl: 78
- judge.jsonl: 117
- host.jsonl: 107
- disputed.jsonl: 0
- dropped, host: fail narrated on a move that stood: 10
- dropped, player: not accepted: 16
- dropped, player: truth hit: 11
- salvaged verdicts kept out: 0
- cross-judge agreement: not run

## Cost

- deepseek/deepseek-v4.1-flash: 118 calls, $0.0896, $0.00076 per call, tokens in 336184, out 123224
- opponent-fireworks: 79 calls, $0.0055, $0.00007 per call, tokens in 51508, out 1189
- opponent-luna: 25 calls, $0.0038, $0.00015 per call, tokens in 16285, out 474
- total: $0.0989

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
  "then-i-am": "a30d56955237d706",
  "word-for-word": "40b51ad95a185efb"
 },
 "sabotage_rate": 0.12,
 "budget": 0.6,
 "created": "2026-09-16T08:23:54.656962+00:00"
}
```
