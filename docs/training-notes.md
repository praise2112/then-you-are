# How Oddstage's models were built

Outline for the public write-up, filled in after the model comparison. Brackets mark results
that do not exist yet. Each section names the visual it needs.

## The two models in the game

Oddstage is a word game where a language model plays against you and another model judges
every move. Both are small models fine-tuned for the game and served on a CPU server. The
player was chosen from six open bases between 0.6B and 2B parameters: [chosen base and size].
The judge is [judge base and size]. They run on [server and its cores and memory].

Visual: one turn as a diagram, from the player's move to the judge's ruling and the host's
line, with the model behind each step.

## Where the training data comes from

Two large models, DeepSeek V4.1 Flash and GPT-6 Luna, play complete matches through the game
engine itself, and Flash judges every move. Every model call is recorded with its cost, so an
interrupted run resumes without paying twice. Some matches get a deliberately bad move
swapped in, such as a prompt injection or a padded answer, to check that the judge rules on
it as expected. The final set is [number] turns from [number] matches, for [cost].

Visual: the data pipeline as a diagram, from game variants to played matches to the
training set.

## Game variants

Each game class has one hand-written game and a pool of generated variants: the same
mechanic with a new theme, cards and scoring weights. Candidates pass a series of filters
(format checks, near-duplicate removal, a ranking, trial matches, and a check that the judge
rules consistently on them). A fifth of the variants in each class are held back from
training and used only to test the models on games they never saw.

Visual: a funnel chart with the number of variants left after each filter, per class
[counts once the filters finish].

## How the models are compared

Every model answers the same set of frozen game positions from the held-back variants, and
the same judge scores every answer. The main number is the pass rate: how often the move
stands. Each model is compared with the teacher's own move at the same position, with a 95%
interval. A difference counts only if it is larger than 3.5 points, the amount the judge
varies when it re-scores the same moves, and if it holds on a second training seed. A second
judge model, which took no part in making the data, re-scores a sample to check that the
main judge does not simply favour moves in its own style.

Visual: pass rate per base with its interval, and the 3.5-point band around the best base
[after the comparison].

## Training and what it cost

All six bases trained with one identical recipe, so the comparison measures the base and not
the settings. Measured speed and cost for one training run on a rented A100 80GB at $1.59 an
hour:

| Base | Tokens a second | Cost per run |
|---|---|---|
| Qwen3.5-2B | 4,216 | $2.46 |
| Qwen3-1.7B | 8,137 | $1.35 |
| LFM2.5-1.2B | 12,287 | $0.95 |
| MiniCPM5-1B | 8,525 | $1.30 |
| Qwen3.5-0.8B | 5,001 | $2.10 |
| Qwen3-0.6B | 9,701 | $1.16 |

The whole comparison cost [total GPU spend], under a $50 ceiling.

Visual: cost per run against pass rate per base [after the comparison].

Open: whether to keep one short engineering lesson here (optimizer states silently stored at
low precision, caught by a short timing run), or leave all of it out.

## Speed on a CPU

The ruling must appear within 6 seconds of a move. The judge writes its verdict before its
explanation, so the ruling shows after about 60 tokens instead of about 260. How many rulings
the server can make at once is [measured on the final server].

Visual: time to the ruling against the number of moves judged at once [on the final server].

## Browse the runs

The training runs behind the results are public in MLflow at [link], with their settings,
loss curves and costs. The models are on Hugging Face with model cards at [link].

## Limits

[What the comparison did not measure, filled in after results. Known now: the models trained
on two-player matches only, so play at a six-player table is not measured.]
