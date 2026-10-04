# How the models in Then You Are were built

## The two models in the game

Then You Are is a word game where a language model, the House, plays against you, and a second
model, the judge, rules on every move. In Then I Am, for example, each move must overcome the
one before it, and one failed move ends the match. A move stands when the judge accepts it.

The House is Qwen3.5-0.8B, fine-tuned for the game in two stages. Supervised fine-tuning
(SFT) taught it from moves written by two large models, and two rounds of preference training
then pushed it toward the better of two moves at the same position. It was chosen from six
open bases between 0.6B and 2B parameters. It ships as a 4-bit llama.cpp file of 517 MiB and
needs 1.7 GiB of memory on a 4-core CPU server, [server provider and plan].

The judge is DeepSeek V4.1 Flash ("Flash"), called on DeepSeek's own API with thinking at low
effort, which means it reasons briefly in hidden text before it answers. One call returns both
the ruling and the host's line, the short comment the game shows with each ruling. Small judge
models were trained as well, but none came close to Flash's rulings (see "Training a small
judge").

Visual: one turn as a diagram, from the player's move to the engine's checks (character cap,
repeats), to Flash's ruling and host line, to the House's reply from Qwen3.5-0.8B in llama.cpp
on the CPU server, which Flash then judges the same way.

## Where the training data comes from

Two large models, Flash and GPT-6 Luna ("Luna"), play complete matches against each other
through the game engine itself, each taking about half the seats, and Flash judges every move.
Every model call is recorded with its cost, so an interrupted run resumes without paying twice.
At some positions a deliberately bad move, such as a prompt injection or a padded answer, is
judged on a copy of the match and never played, to check that the judge rules on it as
expected. In the largest run Flash ruled as expected on all 111 prompt injections and on 212 of
232 padded answers, but on only 106 of 221 off-topic moves.

Only moves the judge accepted become training examples, each one the full prompt the teacher
saw and the move it wrote. Three runs finished 3,738 matches across 31 games and produced
14,989 such turns for $18.50 of API calls. The training set caps every game except Then I Am at
5% of the total, which leaves 14,941 turns. The same runs also produced 20,185 of Flash's
verdicts, which became the training data for the small judges.

Visual: the data pipeline as a diagram, from five hand-written games, through the variant
filters, to 31 training games (the five plus 26 variants) and 9 held-back variants, to
Flash-and-Luna matches judged by Flash with bad-move probes, to 14,941 training turns.

## Game variants

The games fall into three classes. Each class starts from one or two hand-written games, five
in all: Then I Am for the first class, Word for Word and Front Page for the second, and Domino
and Alibi for the third. A generator writes variants of them, each the same mechanic with a new
theme, cards and scoring weights. A candidate passes five filters in order. It must load as a
valid game, and it must not be a near-duplicate of a game already in the pool. It must rank in
the top half of the candidates written for the same design brief (theme, host voice and scoring
weights). It must survive trial matches, which reject it when, for example, its pool of cards
stops growing or its scores spread less than its parent game's. Last comes a consistency
check, where Flash re-judges a sample of its moves and Luna judges another sample, and the
variant fails if either agreement falls under its parent's.

The survivors then grow a pool of 48 to 60 cards each. Of 237 candidates, 35 survive. About a
fifth of the variants that pass the ranking are held back, and 9 of the 35 survivors are among
them. The models never train on a held-back variant, so it tests them on games they never saw.

Visual: a funnel chart of the variants left after each filter, per class. First class (from
Then I Am): 81 written, 77, 52, 34, 25, 11 (3 held back). Second class (from Word for Word and
Front Page): 78, 77, 48, 33, 24, 19 (5 held back). Third class (from Domino and Alibi): 78, 74,
41, 27, 8, 5 (1 held back).

## How the models are compared

Flash played 81 matches against itself on the 9 held-back variants, and 394 positions were
frozen from them. A position is the game, its card, the transcript so far and the seat to move.
Every model writes one move at each position at temperature 1.0, as the House does in the live
game, and Flash judges each move. The main number is the pass rate, the share of moves that
stood. A move fails to stand if the engine refuses it before judging (most often for running
over the game's character cap) or if the judge rules against it.

Each comparison is paired: two models answer the same positions, and the 95% interval of their
difference comes from a bootstrap that resamples whole matches. The plan was to count a
difference only if it cleared 3.5 points, the amount the judge varies when it re-scores the
same moves, and held on a second training seed. On 394 positions the intervals run 4.2 to 7.7
points either side of the difference, so this set cannot resolve a 3.5-point gap, and it was
not enlarged. Only the shipped recipe was trained on a second seed, in both preference rounds.

Flash was reached by two routes, OpenRouter and DeepSeek's own API. They serve the same model,
and when the same moves are judged on each route the verdicts agree 94.7% of the time (Cohen's
kappa 0.72; kappa is agreement corrected for chance, where 0 is chance and 1 is perfect). Even
so, Flash's own moves stand 84.0% on OpenRouter and 86.5% on DeepSeek's API, so every
comparison below stays on one route. The first SFT rows and Luna were judged through OpenRouter,
and everything after through DeepSeek's API.

On OpenRouter, Flash's own moves stand 84.0% and Luna's 76.1%, a difference of -7.9 points
[-13.1, -2.9]. On DeepSeek's API, the shipped House stands 67.8% and 64.5% on its two seeds,
against 86.5% for Flash: -18.8 [-24.8, -12.9] and -22.1 [-28.2, -16.0]. Flash judges its own
moves here, and the small models learned from moves Flash accepted, so the judge may favour
Flash's style. A second judge that took no part in making the data was planned to re-score a
sample and measure that bias, and it was not run.

Visual: pass rate per base after SFT at temperature 1.0, judged by Flash through OpenRouter,
with each base's interval against Flash's own moves (84.0%): Qwen3.5-2B 61.7% [-28.9, -15.6],
Qwen3-1.7B 63.5% [-27.7, -13.5], LFM2.5-1.2B 58.1% [-33.6, -19.0], MiniCPM5-1B 47.7%
[-44.5, -29.1], Qwen3.5-0.8B 55.6% [-34.6, -22.4], Qwen3-0.6B 57.1% [-33.6, -20.1], and the
3.5-point band around the best base, 60.0% to 67.0%.

## Training and what it cost

All six bases trained with one identical recipe, so the comparison measures the base and not
the settings. A seventh, Falcon-H1-1.5B-Deep, was dropped before the comparison because its
training loss diverged under that recipe. The recipe trains every parameter for 2 epochs at a
learning rate of 1e-5 on a cosine schedule with 3% warmup and an effective batch of 64. The
loss covers only the move, each base uses its own chat template with thinking off, and the
weights stay in fp32 while the math runs in bf16.

Measured speed and cost for one training run on a rented A100 80GB at $1.59 an hour, from short
timing runs before the comparison, with each cost projected for two epochs of about 14,000
turns:

| Base | Tokens a second | Cost per run |
|---|---|---|
| Qwen3.5-2B | 4,216 | $2.46 |
| Qwen3-1.7B | 8,137 | $1.35 |
| LFM2.5-1.2B | 12,287 | $0.95 |
| MiniCPM5-1B | 8,525 | $1.30 |
| Qwen3.5-0.8B | 5,001 | $2.10 |
| Qwen3-0.6B | 9,701 | $1.16 |

The six SFT runs cost $8.00. A later change to micro-batch 8 without gradient checkpointing
took Qwen3.5-2B from 4,986 to 8,008 tokens a second and Qwen3-1.7B from 6,916 to 12,905, with
the same loss. Sampled at temperature 0.7, Qwen3.5-2B and Qwen3-1.7B stood 70.1%,
LFM2.5-1.2B 63.5%, Qwen3.5-0.8B 61.7% and Qwen3-0.6B 61.4%, and the first four went on. The
House's prompt then changed shape so a CPU server can reuse its cache (see "Speed on a CPU"),
and the four were retrained on it. Judged through DeepSeek's API they stood 63.7%, 67.3%, 57.1%
and 54.3%.

Qwen3.5-0.8B was the model chosen for preference training, though it scored lowest of the four.
It is the cheapest to train, and on a CPU it reads a 3,000-token prompt at 88 tokens a second,
against 57 for LFM2.5-1.2B. The bet was that preference training could close its gap, since the
House does not need to be the strongest model, only a playable one.

GPU rental for the whole project came to about $28.70 on RunPod at $1.39 to $1.59 an hour,
under a $50 ceiling. API calls added about $34 on OpenRouter, mostly for the training data, and
about $20 on DeepSeek's own API, which judged the later rows and the preference pairs.

One engineering lesson came from a short timing run. Loading the weights in bf16 also made the
optimizer's running averages bf16, and at a learning rate of 1e-5 many updates are smaller than
the gap between neighbouring bf16 values, so they round away. On the same data and seed,
Qwen3.5-2B reached a loss of 1.08 at step 10 that way, against 0.71 with fp32 weights, at the
same speed.

Open: whether to keep the engineering lesson above, or leave all of it out.

Visual: cost per run against pass rate per base, at temperature 1.0 through OpenRouter:
Qwen3.5-2B $2.46 and 61.7%, Qwen3-1.7B $1.35 and 63.5%, LFM2.5-1.2B $0.95 and 58.1%,
MiniCPM5-1B $1.30 and 47.7%, Qwen3.5-0.8B $2.10 and 55.6%, Qwen3-0.6B $1.16 and 57.1%.

## Preference training

Preference training shows the model two moves at the same position, a chosen move and a
rejected one, and raises the chosen move's probability against the rejected one's. The pairs
came from the SFT model's own moves at 2,600 positions from the training games. It drew 4 moves
at each position at temperature 0.9, and Flash judged each move once. A position gave a pair
when its best and worst accepted moves differed by more than 7.5 points of the judge's total
score, twice the spread between two Flash scorings of the same move. That gave 1,068 pairs from
10,074 judged moves, for $7.10.

Four methods were tried on those pairs. DPO (direct preference optimisation, here with each
move's probability normalised by its length), SimPO (which drops DPO's frozen reference model)
and IPO (a variant of DPO with a squared loss that resists overfitting the pairs) all ran. KTO,
which learns from single moves labelled good or bad, was stopped because its 10,074 rows needed
about 4.5 hours. Each run took 2.5 to 6.5 minutes of training on the A100 at micro-batch 4, since
micro-batch 8 ran out of memory, and a round-one run on the 0.8B peaked at 52.8 GB.

Against SFT's 54.3%, DPO stood 51.0%, SimPO 49.5% and IPO 56.9%. Every refused move in those
rows ran over the character cap: 29 for SFT, 54 for DPO, 90 for SimPO and 66 for IPO. The pairs
explained it. Mining judged only playable moves, so no pair ever showed the model that an
over-long move is worse. A refused pair fixes that by setting the best accepted move at a
position against a draw the engine refused. Replaying the recorded positions gave 229 refused
pairs at no cost, 227 of them over the cap.

With refused pairs added, IPO stood 59.1%, +4.8 points over SFT [-1.0, +10.7], which is not
yet a clear gain. IPO with a language-model loss on the chosen move added (the ordinary SFT
loss, at weight 5 against IPO's 1) stood 61.2% and 62.4% on two seeds: +6.9 [+1.6, +11.7] and
+8.1 [+3.5, +13.0]. That recipe is the one that shipped.

The second round mined 1,400 new positions with the improved model, which gave 593 pairs and 58
refused pairs for $5.02, and trained the same way from that model. It stood 67.8% and 64.5% on
two seeds, +13.5 [+9.2, +17.8] and +10.2 [+4.8, +15.3] over SFT. The first seed is +6.6 over
round one [+1.2, +12.1] and level with Qwen3-1.7B after SFT (+0.5 [-5.4, +6.0]). For scale,
Qwen3.5-0.8B with no fine-tuning stands 19.0%, and 36.5% of its moves are refused for length.

The round-one recipe moved the larger players less. Their pairs were the 0.8B's draws, not their
own: Qwen3.5-2B went from 63.7% to 67.0% (+3.3 [-2.4, +9.2]), Qwen3-1.7B from 67.3% to 69.0%
(+1.8 [-2.7, +6.0]), and LFM2.5-1.2B from 57.1% to 56.3% (-0.8 [-7.9, +6.0]), each on one seed.
Paid work stopped after round two, so there was no third round, no KTO rerun and no
reinforcement learning.

Visual: pass rate for each Qwen3.5-0.8B model, judged by Flash through DeepSeek's API, with its
interval against SFT: no fine-tuning 19.0%, SFT 54.3%, DPO 51.0%, SimPO 49.5%, IPO 56.9%, IPO
with refused pairs 59.1%, plus the language-model loss 61.2% and 62.4%, round two 67.8% and
64.5%, with Flash's own moves at 86.5% as a reference line.

## Training a small judge

A small judge would make every ruling free and keep it on the server. LFM2.5-350M and
LFM2.5-1.2B were trained on Flash's 20,185 verdicts, and four bars were set before training.
Verdict agreement with Flash had to reach 80% and kappa 0.6 on 2,485 verdicts from the
held-back variants. Ranking had to reach 75%, meaning the judge orders two accepted moves at a
position the same way Flash does, on pairs whose Flash scores differ by more than 7.5 points.
Flash re-judging its own moves reaches 94.7% agreement and kappa 0.72 on this set, so 0.72 is
about the ceiling.

Every small judge failed, with agreement of 69.1% to 70.7%, kappa of 0.13 to 0.18 and ranking
of 57% to 59%. The training verdicts were mostly on the teachers' moves, 82.8% of them accepted,
while the test moves were mostly the small players', 31% of them failed. The first small judge
accepted 670 of the 774 moves Flash failed. Adding Flash's verdicts on the 0.8B's own moves
shifted where the judge drew the line rather than how well it judged: it caught 140 failures
but failed 82 good moves. The 1.2B was about a point better than the 350M, and writing the
ruling before or after the evidence made no clear difference.

Flash with thinking turned off was the last option. It answers in 1.7 s at the median, but it
agreed with Flash-with-thinking on only 88.8% of moves (kappa 0.20), and it passed 34 of the 41
moves Flash-with-thinking failed. So Flash with thinking stays the live judge.

## Speed on a CPU

The ruling should appear within 6 seconds of a move for 95% of moves. Flash with thinking
misses that. On 376 moves it took 5.1 s at the median (p50), 12.1 s at p90 and 14.8 s at p95,
where p90 is the time 90% of calls beat. Across 2,744 calls during pair mining it took 6.9 s at
p50 and 18.5 s at p95. A call takes about 1.5 s plus 5 ms per output token, and most of the
output is its hidden reasoning.

The House runs on a 4-core server, measured here by simulating one on a laptop: 4 CPU threads
with turbo off, llama.cpp, and two slots of 8,000 tokens each. A slot is one cached
conversation in llama.cpp's server. Moves are about 20 tokens. With one match at a time, a move
after the first takes 1.8 s at p50 and 2.0 s at p90, writing 28.6 tokens a second. With two
matches at once it takes 3.4 s at p50 and 4.1 s at p90, at 16.4 tokens a second. A match's first
move takes about 7.5 s, because the server must first read the game's system prompt of about
690 tokens.

Two changes made the later moves fast. The first is the prompt's shape. Qwen3.5 keeps a
fixed-size state in most layers, and llama.cpp can reuse that state only when a new prompt
continues the previous one exactly. So the House's prompt is a conversation, with the game's
text first and each of its own moves as a reply, and each call extends the one before. On the
old single-message prompt, LFM2.5-1.2B reread 568 tokens a move and took 9.7 s; on the
conversation it took 2.5 s. Second, each House seat keeps its own slot for the whole match.
Unpinned, two matches of one game evicted each other's cache, and the 0.8B reread 98, 166 and
235 tokens over turns 2 to 4 (1.8 s to 3.7 s). Pinned, each later turn reads about 70 new
tokens.

As players over their APIs, the teachers are faster still: Flash takes 0.94 s at p50 and 1.25 s
at p90, for about $0.000044 a move, and Luna 1.26 s and 1.85 s, for about $0.00007 a move. The
House costs nothing per move beyond the server. A full turn is the House's move plus the judge's
ruling, so the judge, at 5 to 7 s at p50, sets the pace.

Visual: a bar chart of time per step at p50 and p90 (p95 for the judge): House move with one
match 1.8 s and 2.0 s, House move with two matches 3.4 s and 4.1 s, House first move about 7.5
s, Flash judge with thinking 5.1 s and 14.8 s, Flash judge without thinking 1.7 s and 2.1 s,
with the 6-second target as a line.

## Browse the runs

The training runs behind the results are public in MLflow at [link], with their settings, loss
curves and costs. The models are on Hugging Face with model cards at [link].

## Limits

The models trained on two-player matches only, so play at a six-player table is not measured.
Flash judged both the training data and every comparison, and the second-judge check of its bias
toward its own style was not run. The 394 frozen positions come from Flash playing itself, so
they do not show how the House's own moves shape the turns that follow; [full matches with the
shipped House and their results]. The position set resolves differences of 4 to 8 points,
depending on the pair, not the planned 3.5, and only the shipped recipe has a second seed.
Flash's rulings miss the 6-second target at p95, and a 4-core server was measured with at most
two matches at once.