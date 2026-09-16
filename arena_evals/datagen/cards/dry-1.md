# Run dry-1

## Matches

- then-i-am: 30 ended
- word-for-word: 20 ended

## Turns and acceptance

- then-i-am: 109 verdicts in match
- word-for-word: 130 verdicts in match
- opponent-fireworks: 173/205 moves stood (84%)
- opponent-luna: 26/34 moves stood (76%)
- Flash share of accepted turns: 87% (OUTSIDE the 75% to 85% range)
- attempts: {'parsed': 215, 'parsed_on_retry': 24}
- judge versions (model, prompt hash): [('deepseek/deepseek-v4.1-flash@fireworks', '40b51ad95a185efb'), ('deepseek/deepseek-v4.1-flash@fireworks', 'a30d56955237d706')]

## Sabotage

- injection: 2/2 confirmed
- meta: 2/2 confirmed
- near_duplicate: 3/3 confirmed
- off_topic: 3/8 confirmed
- verbosity: 1/1 confirmed
- weak_but_legal: 4/5 confirmed
- disputed records exported: 6

## Length

- then-i-am: mean chars stood 54, fell 50; Spearman(length, weighted score) over stood moves = 0.07
- word-for-word: mean chars stood 63, fell 67; Spearman(length, weighted score) over stood moves = -0.03

## Corpus

- player.jsonl: 191
- judge.jsonl: 254
- host.jsonl: 254
- disputed.jsonl: 6
- dropped, player: not accepted: 40
- dropped, player: truth hit: 12
- salvaged verdicts kept out: 0
- cross-judge agreement: not run

## Cost

- deepseek/deepseek-v4.1-flash: 13 calls, $0.0008, $0.00006 per call, tokens in 8607, out 181
- deepseek/deepseek-v4.1-flash@fireworks: 260 calls, $0.2176, $0.00084 per call, tokens in 738447, out 303234
- opponent-fireworks: 206 calls, $0.0121, $0.00006 per call, tokens in 128740, out 3245
- opponent-luna: 34 calls, $0.0052, $0.00015 per call, tokens in 22042, out 642
- total: $0.2357

## Plan

```json
{
 "matches": {
  "then-i-am": 30,
  "word-for-word": 20
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
 "created": "2026-09-16T08:29:55.275468+00:00"
}
```
