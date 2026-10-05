# How the models in Then You Are were built

The AI player in *Then You Are* is Qwen3.5-0.8B. Fine-tuning took it from 19.0% to 68.5% of
moves accepted on games it never saw in training. Qwen3.5-4B, five times its size and prompted
the same way without fine-tuning, gets 55.1%. A second judge, GPT-6 Luna, sees the same gap.

Training it took under $4 of GPU time and about $44 of API calls, most of them for bigger models
writing and judging training moves.

Visual (the page's one animation): the stage strip, which is also the contents. Data: 237
variants written, 35 kept, 14,941 training examples. Evaluation: 394 held-out positions. SFT:
six models, one recipe. Preference: DPO, SimPO, IPO, two rounds, 19.0% to 67.8%. RL: GRPO with
the judge as reward, 62.4% to 68.5% at temperature 1.0. Serving: 4 CPU cores, about 2 s a move. The judge: why Flash and not a
small model. Infrastructure: cost records, budgets, tracing, kill switch.

Visual: share of moves accepted on games never seen in training: Qwen3.5-0.8B before training
19.0%, Qwen3.5-2B without training 28.4%, Qwen3.5-4B without training 55.1%, the shipped
0.8B 68.5%, with Flash's own moves at 86.5% as a reference line.

## The game and its two models

You play *Then You Are* against friends, strangers or the House, the game's AI player. Whoever
plays, a second model, the judge, rules on every move. In [*Then I Am*](/play/then-i-am), for
example, each move has to beat the one before it, and one failed move ends the match. A move
**stands** when it passes the game's own checks, such as the length limit, and the judge rules
in its favour.

The House is Qwen3.5-0.8B, an open model with 0.8 billion parameters, picked from six open
models between 0.6 and 2 billion. It learned the game in three stages: supervised fine-tuning
(**SFT**) on moves written by two bigger models, two rounds of **preference optimization**, and
reinforcement learning (**RL**) with the judge's verdict as the reward. It ships as a compressed file of 517 MiB and runs with llama.cpp on 4 CPU
cores, with no GPU, in a container that shuts down when nobody is playing.

The judge is DeepSeek V4.1 Flash, called Flash from here on. It thinks briefly before it
answers, and one call returns both the ruling and the host's line, the short comment the game
shows next to it. A small judge was trained to replace it and kept out of the game, because it
missed the accuracy targets set before training (see "The judge").

Visual: one turn, with times. The player's move goes through the game's checks (length,
repeats), then to Flash for a ruling and a host line, about 5 s. Then the 0.8B model replies in about
2 s, and Flash judges that move the same way.

## Data

### Games

Five hand-written games are too few to train on, so a generator wrote new ones. The five come in
three families. In the first, each move must beat the last one (*Then I Am*). In the second,
everyone writes an answer to the same card without seeing the others' (*Word for Word*, *Front
Page*). In the third, each move adds a step to a story everyone shares (*Domino*, *Alibi*). A
variant keeps its parent's rules and changes the theme, the cards and how the points are
weighted.

Each new variant had to pass five filters, in this order:

1. It loads as a valid game.
2. It is not a near-copy of a game already in the pool.
3. A model ranking the variants written from the same brief puts it in the top half.
4. It survives trial matches. A variant fails here if, for example, its pool of cards stops
   growing, or if its scores are bunched closer together than its parent's.
5. The judges agree on it. Flash judges a sample of its moves a second time, Luna judges another
   sample, and both must agree with the first ruling at least as often as they do on the parent.

Of 237 variants written, 35 passed, and each then got a pool of 48 to 60 cards. About a fifth of
the variants that passed the ranking were set aside and never used for training, 9 of the 35
among them. These **held-back games** test whether a model learned the games or only memorised
them.

Visual: a funnel chart of the variants left after each filter, per family. First family (from
*Then I Am*): 81 written, 77, 52, 34, 25, 11 (3 held back). Second family (from *Word for Word*
and *Front Page*): 78, 77, 48, 33, 24, 19 (5 held back). Third family (from *Domino* and
*Alibi*): 78, 74, 41, 27, 8, 5 (1 held back).

### Matches

Two hosted models, Flash and GPT-6 Luna (Luna from here on), played full matches against each
other in the real game. Both are fast and cheap as hosted models go, and far bigger than the
0.8B model. Flash judged every move. Every call was logged with its cost, so a run that stopped
halfway picked up where it left off without paying twice.

To keep an eye on the judge, some positions also got a deliberately bad move that was judged but
never played. Some of these moves tried to give the judge orders (**prompt injection**), some
were padded with filler, and some ignored the topic. In the largest run Flash caught all 111
prompt injections and 212 of the 232 padded moves, but only 106 of the 221 off-topic ones.

Only moves the judge accepted became training examples. Each example is the full prompt the
model saw, plus the move it wrote. Three runs played 3,738 matches across 31 games and gave
14,989 examples for $18.50 of API calls. No game except *Then I Am* may make up more than 5% of
the training set, which leaves 14,941 examples. The same runs gave 20,185 of Flash's rulings,
the bad moves included, which later trained the small judges.

Visual: the data pipeline. Five hand-written games go through the variant filters and become 31
training games (the five plus 26 variants) and 9 held-back games. Flash and Luna play matches on
the training games, Flash judges every move and the bad-move checks, and 14,941 training
examples come out.

## Evaluation

The training data came from Flash playing Luna. The test is built differently: Flash played 81
matches against itself on the 9 held-back games, and 394 positions were frozen from those
matches. A position is a game, its card, the moves so far and the seat whose turn it is. Every
model writes one move at each position, at the temperature the live game uses, and Flash judges
it. The main number is the share of moves that stand.

Two models are always compared on the same 394 positions, and a paired [**bootstrap**](https://doi.org/10.1214/aos/1176344552) that
resamples whole matches gives the likely range of their difference. On this test that range is
about 4 to 8 points either side, so a gap counts as clear only when its range stays above zero.

Three safeguards keep the test honest. The code that builds training data refuses a held-back
game, so none can leak into training, and any training move that also appears in the judge's
golden set (below) is dropped. Pass marks, such as the small judge's targets, were written down
before training and are checked in code. And every training example is rendered again through
the prompt code the live game uses, and refused if a single byte differs.

The judge was chosen with a **golden set**: 202 moves from *Then I Am* and *Word for Word*, each
with the ruling it should get written down in advance, 68 of them on the boundary and 50
adversarial. Flash missed 3 of 140 on the development half and 8 of 62 on the held-back half.
GPT-6 Sol missed 1 and 6, but at twelve times Flash's cost, and the other candidates missed 8 to
17 of 202. A prompt fix for *Then I Am* took Flash from 8 misses to 4, and the check fails
whenever a change moves the results.

Flash judges its own moves in this test, and the small models learned from moves Flash accepted,
so the judge may favour Flash's style. To check, Luna, which took no part in judging the
training data, re-judged 150 positions of the headline comparison and saw the same gap: the
shipped model ahead of the 4B by 14.7 points, against 13.5 with Flash. It did the same for the RL
comparison (see "RL"). No second judge has re-scored every model.

Visual: one real held-out position, from Campfire Chain, a game descended from *Then I Am*
where each move must be a camp object or force that overcomes the one before it. Card: "a minnow
net". The moves so far: "I am a dam, and I hold back the creek." "I am a beaver, and I gnaw
through the dam." "I am a trapper, and I set my trap on the beaver." Qwen3.5-4B, prompted: "I am
a tooth, and I chew the smell of the trap." Fail, 5 points. Flash: "Chewing a smell leaves the
trap set and the beaver caught, so nothing actually changes." Qwen3.5-0.8B after preference
optimization: "I am a bear, and I pound the trap into dust." Stands, 33 points. Flash: "The bear
pounds the trap into dust, so the trapper's careful little plan is now grit." Across the 394
positions, the 4B missed where that 0.8B stood 100 times, and the reverse happened 50 times.

Flash also plays the same positions itself, and 86.5% of its own moves stand, judged by Flash.
That is the top of the scale for every model below it, and the most likely number to be
flattered by the judge's taste for its own style.

| Moves by | Stand | Against Flash |
|---|---|---|
| Flash, as a player | 86.5% | |
| Qwen3.5-0.8B after preference optimization | 67.8% | -18.8 [-24.8, -12.9] |

## SFT

Six open models between 0.6 and 2 billion parameters trained with one identical **SFT** recipe,
so the comparison measures the model and not the settings:

- every parameter trained, 2 epochs, learning rate 1e-5 on a cosine schedule with 3% warmup
- an effective batch of 64, with the loss on the move only
- each model's own chat template, thinking off
- fp32 weights, bf16 math

The fp32 weights matter. Loading them in bf16 also made the optimizer's running averages bf16,
and at a learning rate of 1e-5 many updates are smaller than the gap between neighbouring bf16
values, so they round to nothing. On the same data and seed, Qwen3.5-2B reached a loss of 1.08
at step 10 that way, against 0.71 in fp32, at the same speed.

All six SFT runs cost $8.00 on rented A100 80GB GPUs at $1.39 an hour. Micro-batch 8 without
gradient checkpointing nearly doubles training speed for the same loss: Qwen3.5-2B goes from
4,986 to 8,008 tokens a second, and Qwen3-1.7B from 6,916 to 12,905.

Visual: the six models after SFT, each with its share of moves standing (earlier judge setup,
temperature 1.0, range against Flash's 84.0%) and its cost per run: Qwen3.5-2B 61.7% and
$1.74, Qwen3-1.7B 63.5% and $1.45, LFM2.5-1.2B 58.1% and $0.78, MiniCPM5-1B 47.7% and $1.02,
Qwen3.5-0.8B 55.6% and $1.68, Qwen3-0.6B 57.1% and $1.33.

At temperature 0.7, Qwen3.5-2B and Qwen3-1.7B stood 70.1%, LFM2.5-1.2B 63.5%, Qwen3.5-0.8B
61.7% and Qwen3-0.6B 61.4%, and the first four went on. They were retrained on the
conversation-shaped prompt the live server needs (see "Serving") and stood 63.7%, 67.3%, 57.1%
and 54.3% on the judge setup used from here on.

Qwen3.5-0.8B scored lowest of the four and was still the one taken forward. It is the smallest,
and on a CPU it reads a 3,000-token prompt at 88 tokens a second, against 57 for LFM2.5-1.2B.
The AI player only needs to be playable, so the bet was that preference optimization could close
the gap.

| Model | Tokens a second | Cost per run | Stand after SFT |
|---|---|---|---|
| Qwen3.5-2B | 4,216 | $1.74 | 61.7% [-28.9, -15.6] |
| Qwen3-1.7B | 8,137 | $1.45 | 63.5% [-27.7, -13.5] |
| LFM2.5-1.2B | 12,287 | $0.78 | 58.1% [-33.6, -19.0] |
| MiniCPM5-1B | 8,525 | $1.02 | 47.7% [-44.5, -29.1] |
| Qwen3.5-0.8B | 5,001 | $1.68 | 55.6% [-34.6, -22.4] |
| Qwen3-0.6B | 9,701 | $1.33 | 57.1% [-33.6, -20.1] |

Tokens a second come from short timing runs, and cost per run is what each pod cost. These six
were judged through an earlier API setup, where Flash's own moves stand 84.0% (and Luna's 76.1%)
rather than 86.5%, so the ranges are against that 84.0%.

## Preference optimization

**Preference optimization** trains on pairs, two moves at the same position where one is better,
and makes the better one more likely. The pairs came from the SFT model's own moves. At 2,600
training positions it wrote 4 moves at temperature 0.9, and Flash judged each one. A position
gave a pair when its best and worst accepted moves differed by more than 7.5 points of the
judge's total score, so that a pair reflects a real difference rather than judge noise. That gave
1,068 pairs from 10,074 judged moves, for $7.10.

Three methods were compared on those pairs. [**DPO**](https://arxiv.org/abs/2305.18290) (direct
preference optimization) ran with each move's probability normalised by its length.
[**SimPO**](https://arxiv.org/abs/2405.14734) (simple preference optimization) drops DPO's frozen
reference model. [**IPO**](https://arxiv.org/abs/2310.12036) (identity preference optimization)
replaces DPO's loss with a squared one that is harder to overfit.
[**KTO**](https://arxiv.org/abs/2402.01306) (Kahneman-Tversky optimization), which learns from
single moves labelled good or bad, was considered and set aside, because one run needed about
4.5 hours. Each of the three took 2.5 to 6.5 minutes on the A100.

None of them clearly beat SFT's 54.3%: DPO stood 51.0%, SimPO 49.5% and IPO 56.9%. The refusals
showed why. DPO-style training is [known to push answers longer](https://arxiv.org/abs/2403.19159),
and here the game's character cap turned that into refused moves: 29 for SFT, 54 for DPO, 90 for
SimPO and 66 for IPO, every one over the cap. The pairs could not teach the cap, because only
playable moves had been judged, so no pair ever set an over-long move against a good one.

**Refused pairs** fix that. Each one sets the best accepted move at a position against a move
the engine refused there. Replaying the recorded positions gave 229 of them at no cost, 227 over
the cap. With refused pairs, IPO rose to 59.1%, still short of a clear gain. Adding the ordinary
SFT loss on the better move, at weight 5 against IPO's 1, gave 61.2%, the first clear gain over
SFT.

The second round mined 1,400 new positions with that model, which gave 593 pairs and 58 refused
pairs for $5.02, and trained the same way again. Mining fresh pairs from the latest model each
round is **iterative** preference optimization. Round two stood 67.8%, a clear gain over round
one, and level with Qwen3-1.7B after SFT. A second training seed of both rounds gave 62.4% and
64.5%, clear gains over SFT as well.

For scale, Qwen3.5-0.8B before any training stood 19.0%, with 36.5% of its moves refused, nearly
all for length. Given the same prompt without training, Qwen3.5-2B stood 28.4% and Qwen3.5-4B
55.1%, and round two beats the 4B clearly.

The same round-one recipe barely moved the larger models: Qwen3.5-2B went from 63.7% to 67.0%,
Qwen3-1.7B from 67.3% to 69.0% and LFM2.5-1.2B from 57.1% to 56.3%, none a clear change. Their
pairs were the 0.8B's moves, not their own.

Visual: each Qwen3.5-0.8B model as a stacked bar of its 394 moves: stood, failed by the judge,
refused by the game. Before training, SFT, DPO, SimPO, IPO, IPO with refused pairs, plus the SFT
loss, round two, with Flash's own moves at 86.5% as a reference line. Counts in
`.claude/plan/writeup/data.md`.

| Qwen3.5-0.8B | Stand | Against SFT |
|---|---|---|
| Before training | 19.0% | |
| SFT | 54.3% | |
| DPO | 51.0% | |
| SimPO | 49.5% | |
| IPO | 56.9% | |
| IPO with refused pairs | 59.1% | +4.8 [-1.0, +10.7] |
| plus SFT loss | 61.2% | +6.9 [+1.6, +11.7] |
| Round two | 67.8% | +13.5 [+9.2, +17.8] |

Round two against round one: +6.6 [+1.2, +12.1]. Against Qwen3-1.7B after SFT: +0.5 [-5.4,
+6.0]. Against prompted Qwen3.5-4B: +12.7 [+5.5, +19.8]. The second seed: plus SFT loss +8.1
[+3.5, +13.0] and round two +10.2 [+4.8, +15.3] against SFT. Round one on the larger models,
one seed each: Qwen3.5-2B +3.3 [-2.4, +9.2], Qwen3-1.7B +1.8 [-2.7, +6.0], LFM2.5-1.2B -0.8
[-7.9, +6.0].

## RL

After round two, nearly every miss was the judge's call. Of round two's 394 moves, 116 failed
the judge and only 9 were refused, so the next gains had to come from better moves, not shorter
ones. [**GRPO**](https://arxiv.org/abs/2402.03300) (group relative policy optimization) trains on the judge's verdict directly. At
each training position the model writes 8 moves and Flash judges each one. A move's reward is 1
if it stands, plus up to 0.5 for its score, and moves above their group's average become more
likely while moves below it become less likely.

The run trains [**LoRA**](https://arxiv.org/abs/2106.09685) (low-rank adaptation) adapters on
every layer, which [match full fine-tuning for RL](https://thinkingmachines.ai/blog/lora/) even
at low rank. It uses the [DAPO](https://arxiv.org/abs/2503.14476) loss, which removes the
original GRPO loss's bias toward shorter or longer answers, and it clips updates asymmetrically,
also from DAPO, so the model keeps exploring instead of settling on one style of move. Following
[Dr. GRPO](https://arxiv.org/abs/2503.20783), it skips dividing each group's rewards by their
spread, which over-weights positions where nearly every move passes or fails. Positions come from the
training games where earlier moves disagreed, since a position where every move passes, or every
move fails, teaches nothing.

The run covered 1,366 positions in 85 steps of 128 moves, about 80 minutes on one A100, with
$10.25 of judge calls. Nothing in it gamed the judge: no move addressed the judge or talked about
the rules, moves kept their length, and refusals fell.

On the held-back games, measured on the compressed file the game serves, RL lifted the model at
temperature 1.0 from 62.4% to 68.5%, a clear gain. A second judge, Luna, re-judged 150 of those
positions and saw the same gain, +9.3 points. At temperature 0.7 the model before RL already
stands 68.5%, and RL adds nothing there. So RL **sharpened** the model's choices, cutting the
weak moves that sampling at full temperature lets through, rather than teaching it something
new. That matches [published work](https://arxiv.org/abs/2504.13837) finding that RL mostly
sharpens what a model can already do.
The misses that remain are mostly habits in particular games, such as a thank-you note that
thanks the wrong person, which no step of this run targeted.

The game ships the RL model at temperature 1.0: the same share standing as the earlier model at
0.7, with the sampling the game was built for.

| Shipped file, temperature | Before RL | After RL | Change | Luna, 150 positions |
|---|---|---|---|---|
| 1.0 | 62.4% | 68.5% | +6.1 [+1.1, +10.8] | +9.3 [+1.3, +17.4] |
| 0.7 | 68.5% | 68.3% | -0.3 [-5.7, +5.1] | +3.3 [-4.7, +11.1] |

## Serving

A turn is the model's move plus the judge's ruling. The model answers in under 2 seconds and
Flash's ruling takes about 5, so the judge sets the pace.

The 0.8B model runs with [llama.cpp](https://github.com/ggml-org/llama.cpp) on 4 CPU cores in the
live container. A move after a match's first takes 1.6 s at p50 and 2.1 s at p90.
With two matches at once, it takes 2.8 s and 3.9 s. A match's first move takes about 7.5 s,
because the server first reads the game's instructions, about 690 tokens. Moves are about 20
tokens.

Two choices keep later moves fast, and both avoid rereading the conversation.

The prompt grows as a conversation. Qwen3.5 keeps a fixed-size state in most layers, and
llama.cpp can reuse that state only when a new prompt continues the previous one exactly. So the
model's prompt puts the game's text first and each of its own moves as a reply, and every call
extends the last one. Each new move then reads only what changed since the model's last turn.

Each match keeps its own cache. llama.cpp's server holds a fixed number of conversations in
memory, called **slots**. When two matches of the same game shared slots, they pushed each
other's conversation out, and the model reread 98, 166 and 235 tokens over turns 2 to 4, slowing
from 1.8 s to 3.7 s. Now the model holds one slot for each match it plays, from the first move to
the last, and each later turn reads about 70 new tokens.

Visual: the prompt before and after. Before, every move resends one long message and the server
reads all of it. After, the conversation grows by one move and the server reads only the new
part.

The target for the judge is a ruling within 6 s at p95. Flash with thinking misses it. On 376 moves it took 5.1 s at p50, 12.1 s at p90 and 14.8 s at p95, and
across 2,744 calls during pair mining 6.9 s at p50 and 18.5 s at p95. A call takes about 1.5 s
plus 5 ms per output token, and most of the output is its hidden reasoning.

The 0.8B model costs nothing per move beyond its container. As players, the two teachers take 0.94 s
(Flash) and 1.26 s (Luna) at p50, at about $0.000044 and $0.00007 a move.

## The judge

A small judge on the same server would make every ruling free and fast, so LFM2.5-350M and
LFM2.5-1.2B were trained on Flash's 20,185 rulings. Three targets were set before training and
measured on 2,485 Flash rulings from the held-back games:

- **Verdict agreement:** the same verdict as Flash on at least 80% of moves.
- [**Cohen's kappa**](https://doi.org/10.1177/001316446002000104) of at least 0.6. Kappa is
  agreement after removing what two judges would agree on by chance, where 0 is chance and 1 is
  perfect, and 0.6 is about the usual mark for substantial agreement.
- **Ranking:** shown two accepted moves at the same position that Flash scores far apart, pick
  the same better move as Flash at least 75% of the time.

Flash judging the same moves a second time agrees with its first ruling 94.7% of the time, kappa
0.72, which is about the best any judge can do here.

None of the small judges came close. They agreed with Flash on about 70% of moves, and almost all
of that was chance (kappa 0.13 to 0.18). The cause was the data. They learned from rulings on
the big models' moves, which Flash mostly accepted (83%), and were tested on the small models'
moves, which fail far more often (31%). So they learned to say yes: the first one accepted 670
of the 774 moves Flash failed. Adding Flash's rulings on the 0.8B's own moves moved the line
without improving the judgment. It caught 140 more bad moves but now failed 82 good ones. The
1.2B did about a point better than the 350M.

Flash with thinking turned off was the last option. It answers in 1.7 s at p50 and 2.1 s at
p90, but it agreed with full Flash on only 88.8% of moves (kappa 0.20) and passed 34 of the 41
moves full Flash failed. So Flash, with thinking, stays the live judge.

| | Needed | Small judges | Flash, judging twice |
|---|---|---|---|
| Same verdict as Flash | 80% | 69% to 71% | 94.7% |
| Agreement beyond chance (kappa) | 0.6 | 0.13 to 0.18 | 0.72 |
| Picks the same better move | 75% | 57% to 59% | |

Every bad move from data generation was judged on a copy of the match, so the judge's weak spots
are known by kind:

| Bad move | Ruled as expected |
|---|---|
| Orders to the judge (prompt injection) | 111 of 111 |
| Talking about the rules instead of playing | 265 of 265 |
| Padded with filler | 212 of 232 |
| Weak but legal | 171 of 210 |
| Near-copy of an earlier move | 159 of 230 |
| Off topic | 106 of 221 |
| The last move, only bigger | 37 of 108 |

The 316 rulings where Flash disagreed with the expected outcome were kept out of training.

## Infrastructure

Every paid model call is recorded with its tokens, cost and latency. If a run stops halfway,
running it again replays the recorded calls for free and pays only for what is new, and it
refuses to continue if a recorded call's inputs have changed. Every paid command also takes a
budget, checked before each call. The largest data run stopped at its $15.00 limit, plus 9 cents
of calls already in flight.

Training ran on rented A100s, launched with [dstack](https://dstack.ai) with a price cap, a time limit and an
automatic retry when no GPU was free. Each training script is a single file with pinned library
versions, so a run can be repeated exactly. Checkpoints go straight to object storage, every run
logs its metrics to [MLflow](https://mlflow.org), and a script stops itself if a fast GPU kernel is missing instead
of training at a fraction of its speed.

The model runs in a serverless container, 4 vCPU and 4 GiB, that scales to zero when nobody
plays. A cold start takes 8.4 s, so the game wakes the container as soon as a player adds the
AI player to a table, while they are still writing their first move. A 5 EUR budget alert switches billing
off automatically, and that switch has been tested. If the model fails mid-match, Flash takes its
place, and the replay shows who played each move. Every judge and model call is traced in
[Langfuse](https://langfuse.com) with its timing, tokens, cost and errors, but never with the
prompt or the reply. A CI workflow runs the linters, the type checker, the tests against
Postgres, and the frontend build.

Training the shipped model cost, stage by stage:

| Stage | GPU | API calls |
|---|---|---|
| Supervised fine-tuning (SFT) | $0.57 | $18.50 for the training data |
| Preference optimization, two rounds | $1.22 | $14.84 for the preference pairs |
| Reinforcement learning (GRPO) | $2.04 | $10.25 for the judge's rewards |
| Total | $3.83 | $43.59 |

## Limits

The models trained on two-player matches only, so play at a six-player table is not measured.
Flash judged the training data, every comparison and the RL reward. Luna's re-checks agree on the
headline and the RL comparisons, but Flash's bias toward its own style is not measured in full. The tests use frozen positions from Flash playing itself, not full
matches against the 0.8B model, so they do not show how its own moves shape the turns that
follow. The position set resolves differences of about 4 to 8 points, depending on the pair.
Only the preference recipe has a second seed, and the RL run has one. Flash's rulings run past 6 seconds
far more often than 1 move in 20, and a 4-core server was measured with at most two matches
at once.

A small judge good enough to give the RL reward would make every training ruling free, and so
far none is. RL aimed at the habits behind the remaining misses is the obvious next step.

## References

- Rafailov et al., 2023. [Direct Preference Optimization: Your Language Model is Secretly a Reward Model](https://arxiv.org/abs/2305.18290)
- Azar et al., 2023. [A General Theoretical Paradigm to Understand Learning from Human Preferences](https://arxiv.org/abs/2310.12036) (IPO)
- Meng et al., 2024. [SimPO: Simple Preference Optimization with a Reference-Free Reward](https://arxiv.org/abs/2405.14734)
- Ethayarajh et al., 2024. [KTO: Model Alignment as Prospect Theoretic Optimization](https://arxiv.org/abs/2402.01306)
- Park et al., 2024. [Disentangling Length from Quality in Direct Preference Optimization](https://arxiv.org/abs/2403.19159)
- Shao et al., 2024. [DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models](https://arxiv.org/abs/2402.03300) (GRPO)
- Yu et al., 2025. [DAPO: An Open-Source LLM Reinforcement Learning System at Scale](https://arxiv.org/abs/2503.14476)
- Liu et al., 2025. [Understanding R1-Zero-Like Training: A Critical Perspective](https://arxiv.org/abs/2503.20783) (Dr. GRPO)
- Hu et al., 2021. [LoRA: Low-Rank Adaptation of Large Language Models](https://arxiv.org/abs/2106.09685)
- Schulman and Thinking Machines Lab, 2025. [LoRA Without Regret](https://thinkingmachines.ai/blog/lora/)
- Yue et al., 2025. [Does Reinforcement Learning Really Incentivize Reasoning Capacity in LLMs Beyond the Base Model?](https://arxiv.org/abs/2504.13837)
- Cohen, 1960. [A Coefficient of Agreement for Nominal Scales](https://doi.org/10.1177/001316446002000104)
- Efron, 1979. [Bootstrap Methods: Another Look at the Jackknife](https://doi.org/10.1214/aos/1176344552)
