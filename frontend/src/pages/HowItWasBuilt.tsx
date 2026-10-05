import { useEffect, useState, type ReactNode } from "react";

import { Link, ThemeToggle, TopBar } from "../App.tsx";
import {
  BasesChart,
  FunnelChart,
  HeadlineChart,
  LifecycleChart,
  OutcomesChart,
  PipelineChart,
  PromptChart,
  RlChart,
  TurnChart,
} from "./charts.tsx";
import { MatchScene, RlScene } from "./scenes.tsx";

type Stage = { id: string; name: string; facts?: ReactNode };

const STAGES: Stage[] = [
  {
    id: "data",
    name: "Data",
    facts: (
      <>
        <b>237</b> variants written, <b>35</b> kept
        <br />
        <b>14,941</b> training examples
      </>
    ),
  },
  { id: "evaluation", name: "Evaluation", facts: <><b>394</b> held-out positions</> },
  { id: "sft", name: "SFT", facts: "six models, one recipe" },
  {
    id: "preference",
    name: "Preference",
    facts: (
      <>
        DPO, SimPO, IPO, two rounds
        <br />
        <b>19.0</b>% to <b>67.8</b>%
      </>
    ),
  },
  {
    id: "rl",
    name: "RL",
    facts: (
      <>
        GRPO, judge as reward
        <br />
        <b>62.4</b>% to <b>68.5</b>%
        <br />
        compressed, temperature 1.0
      </>
    ),
  },
  {
    id: "serving",
    name: "Serving",
    facts: (
      <>
        <b>4</b> CPU cores
        <br />
        about <b>2</b> s a move
      </>
    ),
  },
  { id: "the-judge", name: "The judge", facts: "why Flash and not a small model" },
  { id: "infrastructure", name: "Infrastructure", facts: "cost records, budgets, tracing, kill switch" },
  { id: "limits", name: "Limits" },
];

const SECTIONS = ["the-game", ...STAGES.map((s) => s.id), "references"];

/** How the models were built: the public write-up, with its contents rail and charts. */
export function HowItWasBuiltPage() {
  const current = useCurrentSection();
  return (
    <>
      <TopBar>
        <span className="aside">
          <Link to="/">Home</Link>
          <ThemeToggle icon />
        </span>
      </TopBar>
      <main className="writeup">
        <div className="writeup-head">
          <h1>
            How the models in <i>Then&nbsp;You&nbsp;Are</i> were built
          </h1>
          <p className="writeup-lede">
            The AI player in <i>Then You Are</i> is Qwen3.5-0.8B. Fine-tuning took it from 19.0% to{" "}
            <b className="ours">68.5%</b> of moves accepted by the game's judge, on game variants it never saw in
            training. Qwen3.5-4B, five times its
            size and prompted the same way without fine-tuning, gets <b>55.1%</b>. A second judge, GPT-6 Luna, sees the
            same gap.
          </p>
        </div>

        <div className="writeup-top-chart">
          <HeadlineChart />
        </div>

        <p className="writeup-cost">
          Training it took <b>under $4</b> of GPU time and <b>about $44</b> of API calls, most of them for bigger models
          writing and judging training moves. It plays on 4 CPU cores in a container that scales to zero, answering in
          about 2 seconds, and every paid call in the pipeline is logged, budgeted and replayable.
        </p>

        <div className="writeup-lifecycle">
          <LifecycleChart />
        </div>

        <nav className="writeup-rail" aria-labelledby="writeup-rail-title">
          <h2 id="writeup-rail-title">How it was built</h2>
          <ol className="writeup-stages">
            {STAGES.map((stage, i) => (
              <li key={stage.id}>
                <a href={`#${stage.id}`} aria-current={current === stage.id ? "location" : undefined}>
                  <span className="dot">{i + 1}</span>
                  <span className="name">{stage.name}</span>
                  {stage.facts && <span className="facts">{stage.facts}</span>}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="writeup-body">
          <TheGame />
          <Data />
          <Evaluation />
          <Sft />
          <Preference />
          <Rl />
          <Serving />
          <TheJudge />
          <Infrastructure />
          <Limits />
          <References />
        </div>
      </main>
    </>
  );
}

/** The id of the section crossing the upper third of the viewport. */
function useCurrentSection(): string | null {
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) if (entry.isIntersecting) setCurrent(entry.target.id);
      },
      { rootMargin: "-30% 0px -65% 0px" },
    );
    for (const id of SECTIONS) {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    }
    return () => observer.disconnect();
  }, []);
  return current;
}

function Table({ head, rows, align }: { head: string[]; rows: string[][]; align: string }) {
  const side = (i: number) => (align[i] === "r" ? "num" : undefined);
  return (
    <div className="writeup-table">
      <table>
        <thead>
          <tr>
            {head.map((cell, i) => (
              <th key={i} className={side(i)} scope="col">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.join("|")}>
              {row.map((cell, i) => (
                <td key={i} className={side(i)}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function TheGame() {
  return (
    <section id="the-game">
      <h2>The game and its two models</h2>
      <p>
        You play <i>Then You Are</i> against friends, strangers or the House, the game's AI player. Whoever plays, a
        second model, the judge, rules on every move. In{" "}
        <Link to="/play/then-i-am">
          <i>Then I Am</i>
        </Link>
        , for example, each move has to beat the one before it, and one failed move ends the match. A move{" "}
        <strong>stands</strong> when it passes the game's own checks, such as the length limit, and the judge rules in
        its favour.
      </p>
      <p>
        The House is Qwen3.5-0.8B, an open model with 0.8 billion parameters, picked from six open models between 0.6
        and 2 billion. It learned the game in three stages: supervised fine-tuning (<strong>SFT</strong>) on moves
        written by two bigger models, two rounds of <strong>preference optimization</strong>, and reinforcement learning
        (<strong>RL</strong>) with the judge's verdict as the reward. It ships as a compressed file of 517 MiB and runs
        with llama.cpp on 4 CPU cores, with no GPU, in a container that shuts down when nobody is playing.
      </p>
      <p>
        The judge is DeepSeek V4.1 Flash, called Flash from here on. It thinks briefly before it answers, and one call
        returns both the ruling and the host's line, the short comment the game shows next to it. A small judge was
        trained to replace it and kept out of the game, because it missed the accuracy targets set before training (see{" "}
        <a href="#the-judge">"The judge"</a>).
      </p>
      <TurnChart />
    </section>
  );
}

function Data() {
  return (
    <section id="data">
      <h2>Data</h2>
      <h3>Games</h3>
      <p>
        Five hand-written games are too few to train on, so a generator wrote new ones. The five come in three families.
        In the first, each move must beat the last one (<i>Then I Am</i>). In the second, everyone writes an answer to
        the same card without seeing the others' (<i>Word for Word</i>, <i>Front Page</i>). In the third, each move adds
        a step to a story everyone shares (<i>Domino</i>, <i>Alibi</i>). A variant keeps its parent's rules and changes
        the theme, the cards and how the points are weighted.
      </p>
      <p>Each new variant had to pass five filters, in this order:</p>
      <ol>
        <li>It loads as a valid game.</li>
        <li>It is not a near-copy of a game already in the pool.</li>
        <li>A model ranking the variants written from the same brief puts it in the top half.</li>
        <li>
          It survives trial matches. A variant fails here if, for example, its pool of cards stops growing, or if its
          scores are bunched closer together than its parent's.
        </li>
        <li>
          The judges agree on it. Flash judges a sample of its moves a second time, Luna judges another sample, and both
          must agree with the first ruling at least as often as they do on the parent.
        </li>
      </ol>
      <p>
        Of 237 variants written, 35 passed, and each then got a pool of 48 to 60 cards. Once a variant passed the
        ranking, about one in five was set aside for testing, and 9 of the 35 come from that set. These{" "}
        <strong>held-back games</strong> were never used for training, and they test whether what a model learned
        carries over to games it has not seen.
      </p>
      <FunnelChart />

      <h3>Matches</h3>
      <p>
        Two hosted models, Flash and GPT-6 Luna (Luna from here on), played full matches against each other in the real
        game. Both are fast and cheap as hosted models go, and far bigger than the 0.8B model. Flash judged every move.
        Every call was logged with its cost, so a run that stopped halfway picked up where it left off without paying
        twice.
      </p>
      <MatchScene />
      <p>
        To keep an eye on the judge, some positions also got a deliberately bad move that was judged but never played.
        Some of these moves tried to give the judge orders (<strong>prompt injection</strong>), some were padded with
        filler, and some ignored the topic. In the largest run Flash caught all 111 prompt injections and 212 of the 232
        padded moves, but only 106 of the 221 off-topic ones.
      </p>
      <p>
        Only moves the judge accepted became training examples. Each example is the full prompt the model saw, plus the
        move it wrote. Three runs played 3,737 matches across 31 games and gave 14,989 examples for $18.50 of API calls.
        No game except <i>Then I Am</i> may make up more than 5% of the training set, which leaves 14,941 examples. The
        same runs gave 20,185 of Flash's rulings, the bad moves included, which later trained the small judges.
      </p>
      <PipelineChart />
    </section>
  );
}

function Evaluation() {
  return (
    <section id="evaluation">
      <h2>Evaluation</h2>
      <p>
        The training data came from Flash playing Luna. The test is built differently: Flash played 81 matches against
        itself on the 9 held-back games, and 394 positions were frozen from those matches. A position is a game, its
        card, the moves so far and the seat whose turn it is. Every model writes one move at each position, at the
        temperature the live game uses, and Flash judges it. The main number is the share of moves that stand.
      </p>
      <p>
        Two models are always compared on the same 394 positions, and a paired{" "}
        <a href="https://doi.org/10.1214/aos/1176344552">
          <strong>bootstrap</strong>
        </a>{" "}
        that resamples whole matches gives the likely range of their difference. On this test that range is about 4 to
        8 points either side, so a gap counts as clear only when its range stays above zero.
      </p>
      <p>
        The code that builds training data refuses a held-back game, so none can
        leak into training, and any training move that also appears in the judge's golden set (below) is dropped. Pass
        marks, such as the small judge's targets, were written down before training and are checked in code. And every
        training example is rendered again through the prompt code the live game uses, and refused if a single byte
        differs.
      </p>
      <p>
        The judge was chosen with a <strong>golden set</strong>: 202 moves from <i>Then I Am</i> and{" "}
        <i>Word for Word</i>, each with the ruling it should get written down in advance, 68 of them on the boundary and
        50 adversarial. Flash missed 3 of 140 on the development half and 8 of 62 on the held-back half. GPT-6 Sol missed
        1 and 6, but at twelve times Flash's cost, and the other candidates missed 8 to 17 of 202. A prompt fix for{" "}
        <i>Then I Am</i> took Flash from 8 misses to 4.
      </p>
      <p>
        Flash judges its own moves in this test, and the small models learned from moves Flash accepted, so the judge
        may favour Flash's style. To check, Luna, which took no part in judging the training data, re-judged 150
        positions of the headline comparison, drawn from all 9 games, and saw the same gap: the shipped model ahead
        of the 4B by 14.7 points [+4.2, +26.1], against 13.5 [+6.2, +19.7] with Flash. It did the same for the RL
        comparison (see <a href="#rl">"RL"</a>). No second judge has
        re-scored every model.
      </p>
      <CampfireExample />
      <p>
        Flash also plays the same positions itself, and 86.5% of its own moves stand, judged by Flash. That is the top
        of the scale for every model below it, and the most likely number to be flattered by the judge's taste for its
        own style.
      </p>
      <Table
        head={["Moves by", "Stand", "Against Flash"]}
        align="lrr"
        rows={[
          ["Flash, as a player", "86.5%", ""],
          ["Qwen3.5-0.8B after preference optimization", "67.8%", "-18.8 [-24.8, -12.9]"],
        ]}
      />
    </section>
  );
}

function CampfireExample() {
  return (
    <figure className="writeup-example">
      <p className="writeup-chart-title">One real held-out position</p>
      <p className="writeup-chart-note">
        From <i>Campfire Chain</i>, a game descended from <i>Then I Am</i> where each move must be a camp object or force
        that overcomes the one before it.
      </p>
      <div className="writeup-example-board">
        <p className="writeup-example-card">
          <span className="small-caps">Card</span> a minnow net
        </p>
        <ol className="writeup-example-moves" aria-label="The moves so far">
          <li>I am a dam, and I hold back the creek.</li>
          <li>I am a beaver, and I gnaw through the dam.</li>
          <li>I am a trapper, and I set my trap on the beaver.</li>
        </ol>
        <div className="writeup-example-replies">
          <div className="fail">
            <p className="who">Qwen3.5-4B, prompted</p>
            <p className="move">I am a tooth, and I chew the smell of the trap.</p>
            <p className="ruling">
              <span className="ruling-word">Fail</span>, 5 points
            </p>
            <p className="why">
              Flash: "Chewing a smell leaves the trap set and the beaver caught, so nothing actually changes."
            </p>
          </div>
          <div className="stands">
            <p className="who">Qwen3.5-0.8B after preference optimization</p>
            <p className="move">I am a bear, and I pound the trap into dust.</p>
            <p className="ruling">
              <span className="ruling-word">Stands</span>, 33 points
            </p>
            <p className="why">
              Flash: "The bear pounds the trap into dust, so the trapper's careful little plan is now grit."
            </p>
          </div>
        </div>
      </div>
      <p className="writeup-chart-note">
        Across the 394 positions, the 4B missed where that 0.8B stood 100 times, and the reverse happened 50 times.
      </p>
    </figure>
  );
}

function Sft() {
  return (
    <section id="sft">
      <h2>SFT</h2>
      <p>
        Six open models between 0.6 and 2 billion parameters trained with one identical <strong>SFT</strong> recipe, so
        any difference comes from the model:
      </p>
      <ul>
        <li>every parameter trained, 2 epochs, learning rate 1e-5 on a cosine schedule with 3% warmup</li>
        <li>an effective batch of 64, with the loss on the move only</li>
        <li>each model's own chat template, thinking off</li>
        <li>fp32 weights, bf16 math</li>
      </ul>
      <p>
        Loading the weights in bf16 instead of fp32 also made the optimizer's running averages bf16, and at a learning
        rate of 1e-5 many updates are smaller than the gap between neighbouring bf16 values, so they round to nothing.
        On the same data and seed, Qwen3.5-2B reached a loss of 1.08 at step 10 that way, against 0.71 in fp32, at the
        same speed.
      </p>
      <p>
        All six SFT runs cost $8.00 on rented A100 80GB GPUs at $1.39 an hour. Micro-batch 8 without gradient
        checkpointing nearly doubles training speed for the same loss: Qwen3.5-2B goes from 4,986 to 8,008 tokens a
        second, and Qwen3-1.7B from 6,916 to 12,905.
      </p>
      <BasesChart />
      <p>
        At temperature 0.7, Qwen3.5-2B and Qwen3-1.7B stood 70.1%, LFM2.5-1.2B 63.5%, Qwen3.5-0.8B 61.7% and Qwen3-0.6B
        61.4%, and the first four went on. They were retrained on the conversation-shaped prompt the live server needs
        (see <a href="#serving">"Serving"</a>) and stood 63.7%, 67.3%, 57.1% and 54.3% on the judge setup used from here
        on.
      </p>
      <p>
        Qwen3.5-0.8B scored lowest of the four and was still the one taken forward. It is the smallest, and on a CPU it
        reads a 3,000-token prompt at 88 tokens a second, against 57 for LFM2.5-1.2B. The AI player only needs to be
        playable, so the bet was that preference optimization could close the gap.
      </p>
      <Table
        head={["Model", "Cost per run", "Stand at temperature 1.0", "Against Flash"]}
        align="lrrr"
        rows={[
          ["Qwen3.5-2B", "$1.74", "61.7%", "[-28.9, -15.6]"],
          ["Qwen3-1.7B", "$1.45", "63.5%", "[-27.7, -13.5]"],
          ["LFM2.5-1.2B", "$0.78", "58.1%", "[-33.6, -19.0]"],
          ["MiniCPM5-1B", "$1.02", "47.7%", "[-44.5, -29.1]"],
          ["Qwen3.5-0.8B", "$1.68", "55.6%", "[-34.6, -22.4]"],
          ["Qwen3-0.6B", "$1.33", "57.1%", "[-33.6, -20.1]"],
        ]}
      />
      <p className="writeup-note">
        Cost per run is what each pod cost. These six were judged
        through an earlier API setup, where Flash's own moves stand 84.0% (and Luna's 76.1%) rather than 86.5%, so the
        ranges are against that 84.0%.
      </p>
    </section>
  );
}

function Preference() {
  return (
    <section id="preference">
      <h2>Preference optimization</h2>
      <p>
        <strong>Preference optimization</strong> trains on pairs, two moves at the same position where one is better,
        and makes the better one more likely. The pairs came from the SFT model's own moves. At 2,600 training positions
        it wrote 4 moves at temperature 0.9, and Flash judged each one. A position gave a pair when its best and worst
        accepted moves differed by more than 7.5 points of the judge's total score, so that judge noise alone cannot
        make a pair. That gave 1,068 pairs from 10,074 judged moves, for $9.82.
      </p>
      <p>
        Three methods were compared on those pairs.{" "}
        <a href="https://arxiv.org/abs/2305.18290">
          <strong>DPO</strong>
        </a>{" "}
        (direct preference optimization) ran with each move's probability normalised by its length.{" "}
        <a href="https://arxiv.org/abs/2405.14734">
          <strong>SimPO</strong>
        </a>{" "}
        (simple preference optimization) drops DPO's frozen reference model.{" "}
        <a href="https://arxiv.org/abs/2310.12036">
          <strong>IPO</strong>
        </a>{" "}
        (identity preference optimization) replaces DPO's loss with a squared one that is harder to overfit.{" "}
        <a href="https://arxiv.org/abs/2402.01306">
          <strong>KTO</strong>
        </a>{" "}
        (Kahneman-Tversky optimization), which learns from single moves labelled good or bad, was considered and set
        aside, because one run needed about 4.5 hours. Each of the three took 2.5 to 6.5 minutes on the A100.
      </p>
      <p>
        None of them clearly beat SFT's 54.3%: DPO stood 51.0%, SimPO 49.5% and IPO 56.9%. All three wrote longer moves,
        which DPO-style training is{" "} <a href="https://arxiv.org/abs/2403.19159">known to do</a>, and the game's
        character cap refused them: 29 for SFT, 54 for DPO, 90 for SimPO and 66 for IPO, every
        one over the cap. The pairs could not teach the cap, because only playable moves had been judged, so no pair
        ever set an over-long move against a good one.
      </p>
      <p>
        A <strong>refused pair</strong> sets the best accepted move at a position against a move the
        engine refused there. Replaying the recorded positions gave 229 of them at no cost, 227 over the cap. With
        refused pairs, IPO rose to 59.1%, still short of a clear gain. Adding the ordinary SFT loss on the better move,
        at weight 5 against IPO's 1, gave 61.2%, the first clear gain over SFT.
      </p>
      <p>
        The second round mined 1,400 new positions with that model, which gave 593 pairs and 58 refused pairs for $5.02,
        and trained the same way again. Mining fresh pairs from the latest model each round is{" "}
        <strong>iterative</strong> preference optimization. Round two stood 67.8%, a clear gain over round one, and level
        with Qwen3-1.7B after SFT. A second training seed of both rounds gave 62.4% and 64.5%, clear gains over SFT as
        well.
      </p>
      <p>
        For scale, Qwen3.5-0.8B before any training stood 19.0%, with 36.5% of its moves refused, nearly all for length.
        Given the same prompt without training, Qwen3.5-2B stood 28.4% and Qwen3.5-4B 55.1%, and round two beats the 4B
        clearly.
      </p>
      <p>
        The same round-one recipe barely moved the larger models: Qwen3.5-2B went from 63.7% to 67.0%, Qwen3-1.7B from
        67.3% to 69.0% and LFM2.5-1.2B from 57.1% to 56.3%, none a clear change. Their pairs were the 0.8B's moves, not
        their own.
      </p>
      <OutcomesChart />
      <Table
        head={["Qwen3.5-0.8B", "Stand", "Against SFT"]}
        align="lrr"
        rows={[
          ["Before training", "19.0%", ""],
          ["SFT", "54.3%", ""],
          ["DPO", "51.0%", ""],
          ["SimPO", "49.5%", ""],
          ["IPO", "56.9%", ""],
          ["IPO with refused pairs", "59.1%", "+4.8 [-1.0, +10.7]"],
          ["plus SFT loss", "61.2%", "+6.9 [+1.6, +11.7]"],
          ["Round two", "67.8%", "+13.5 [+9.2, +17.8]"],
        ]}
      />
      <p className="writeup-note">
        Round two against round one: +6.6 [+1.2, +12.1]. Against Qwen3-1.7B after SFT: +0.5 [-5.4, +6.0]. Against
        prompted Qwen3.5-4B: +12.7 [+5.5, +19.8]. The second seed: plus SFT loss +8.1 [+3.5, +13.0] and round two +10.2
        [+4.8, +15.3] against SFT. Round one on the larger models, one seed each: Qwen3.5-2B +3.3 [-2.4, +9.2],
        Qwen3-1.7B +1.8 [-2.7, +6.0], LFM2.5-1.2B -0.8 [-7.9, +6.0].
      </p>
    </section>
  );
}

function Rl() {
  return (
    <section id="rl">
      <h2>RL</h2>
      <p>
        After round two, nearly every miss was the judge's call. Of round two's 394 moves, 116 failed the judge and only
        9 were refused, so the next gains had to come from moves the judge rates higher.{" "}
        <a href="https://arxiv.org/abs/2402.03300">
          <strong>GRPO</strong>
        </a>{" "}
        (group relative policy optimization) trains on the judge's verdict directly. At each training position the model
        writes 8 moves and Flash judges each one. A move's reward is 1 if it stands, plus up to 0.5 for its score, and
        moves above their group's average become more likely while moves below it become less likely.
      </p>
      <RlScene />
      <p>
        The run trains{" "}
        <a href="https://arxiv.org/abs/2106.09685">
          <strong>LoRA</strong>
        </a>{" "}
        (low-rank adaptation) adapters on every layer, which{" "}
        <a href="https://thinkingmachines.ai/blog/lora/">match full fine-tuning for RL</a> even at low rank. It uses the{" "}
        <a href="https://arxiv.org/abs/2503.14476">DAPO</a> loss, which removes the original GRPO loss's bias toward
        shorter or longer answers, and it clips updates asymmetrically, also from DAPO, so the model keeps exploring
        instead of settling on one style of move. Following <a href="https://arxiv.org/abs/2503.20783">Dr. GRPO</a>, it
        skips dividing each group's rewards by their spread, which over-weights positions where nearly every move passes
        or fails. Positions come from the training games where earlier moves disagreed, since a position where every
        move passes, or every move fails, teaches nothing.
      </p>
      <p>
        The run covered 1,366 positions in 85 steps of 128 moves, about 80 minutes on one A100, with $10.25 of judge
        calls. No move in it addressed the judge or talked about the rules, moves kept their
        length, and refusals fell.
      </p>
      <p>
        On the held-back games, measured on the compressed file the game serves, RL lifted the model at temperature 1.0
        from 62.4% to 68.5%, a clear gain. The same round-two model stood 67.8% at 1.0 before it was compressed, so part
        of this gain wins back ground the compressed file had lost at full temperature. A second judge, Luna, re-judged
        150 of those positions and saw the same gain,
        +9.3 points. At temperature 0.7 the model before RL already stands 68.5%, and RL adds nothing there.
        RL{" "} <strong>sharpened</strong> the model's choices, cutting the weak moves that sampling at full temperature
        lets through. That matches{" "}
        <a href="https://arxiv.org/abs/2504.13837">published work</a> finding that RL mostly sharpens what a model can
        already do. The misses that remain are mostly habits in particular games, such as a thank-you note that thanks
        the wrong person, which no step of this run targeted.
      </p>
      <p>
        The game ships the RL model at temperature 1.0: the same share standing as the earlier model at 0.7, with the
        sampling the game was built for.
      </p>
      <RlChart />
      <Table
        head={["Shipped file, temperature", "Before RL", "After RL", "Change", "Luna, 150 positions"]}
        align="lrrrr"
        rows={[
          ["1.0", "62.4%", "68.5%", "+6.1 [+1.1, +10.8]", "+9.3 [+1.3, +17.4]"],
          ["0.7", "68.5%", "68.3%", "-0.3 [-5.7, +5.1]", "+3.3 [-4.7, +11.1]"],
        ]}
      />
    </section>
  );
}

function Serving() {
  return (
    <section id="serving">
      <h2>Serving</h2>
      <p>
        A turn is the model's move plus the judge's ruling. The model answers in about 2 seconds and Flash's ruling
        takes about 5, so the judge sets the pace.
      </p>
      <p>
        The 0.8B model runs with <a href="https://github.com/ggml-org/llama.cpp">llama.cpp</a> on 4 CPU cores in the
        live container. A move after a match's first takes 1.6 s at p50 and 2.1 s at p90. With two matches at once, it
        takes 2.8 s and 3.9 s. A match's first move takes about 7.5 s, because the server first reads the game's
        instructions, about 690 tokens. Moves are about 20 tokens.
      </p>
      <p>Two choices keep later moves fast, and both avoid rereading the conversation.</p>
      <p>
        The prompt grows as a conversation. Qwen3.5 keeps a fixed-size state in most layers, and llama.cpp can reuse
        that state only when a new prompt continues the previous one exactly. So the model's prompt puts the game's text
        first and each of its own moves as a reply, and every call extends the last one. Each new move then reads only
        what changed since the model's last turn.
      </p>
      <p>
        Each match keeps its own cache. llama.cpp's server holds a fixed number of conversations in memory, called{" "}
        <strong>slots</strong>. When two matches of the same game shared slots, they pushed each other's conversation
        out, and the model reread 98, 166 and 235 tokens over turns 2 to 4, slowing from 1.8 s to 3.7 s. Now the model
        holds one slot for each match it plays, from the first move to the last, and each later turn reads about 70 new
        tokens.
      </p>
      <PromptChart />
      <p>
        The target for the judge is a ruling within 6 s at p95. Flash with thinking misses it. On 376 moves it took 5.1
        s at p50, 12.1 s at p90 and 14.8 s at p95, and across 2,744 calls during pair mining 6.9 s at p50 and 18.5 s at
        p95. A call takes about 1.5 s plus 5 ms per output token, and most of the output is its hidden reasoning.
      </p>
      <p>
        The 0.8B model costs nothing per move beyond its container. As players, the two teachers take 0.94 s (Flash) and
        1.26 s (Luna) at p50, at about $0.000044 and $0.00007 a move.
      </p>
    </section>
  );
}

function TheJudge() {
  return (
    <section id="the-judge">
      <h2>The judge</h2>
      <p>
        A small judge on the same server would make every ruling free and fast, so LFM2.5-350M and LFM2.5-1.2B were
        trained on Flash's 20,185 rulings. Three targets were set before training and measured on 2,485 Flash rulings
        from the held-back games:
      </p>
      <ul>
        <li>
          <strong>Verdict agreement:</strong> the same verdict as Flash on at least 80% of moves.
        </li>
        <li>
          <a href="https://doi.org/10.1177/001316446002000104">
            <strong>Cohen's kappa</strong>
          </a>{" "}
          of at least 0.6. Kappa is agreement after removing what two judges would agree on by chance, where 0 is chance
          and 1 is perfect, and 0.6 is about the usual mark for substantial agreement.
        </li>
        <li>
          <strong>Ranking:</strong> shown two accepted moves at the same position that Flash scores far apart, pick the
          same better move as Flash at least 75% of the time.
        </li>
      </ul>
      <p>
        For scale, Flash judging 376 of its own moves twice, through two different API providers, agrees with itself
        94.7% of the time, kappa 0.72.
      </p>
      <p>
        None of the small judges came close. They agreed with Flash on about 70% of moves, and almost all of that was
        chance (kappa 0.13 to 0.18). They learned from rulings on the big models' moves, which
        Flash mostly accepted (83%), and were tested on the small models' moves, which fail far more often (31%), so
        they learned to say yes. The first one accepted 670 of the 774 moves Flash failed. Adding Flash's rulings on the
        0.8B's own moves traded one error for another: the judge caught 140 more bad moves but now failed 82 good ones.
        The 1.2B did about a point better than the 350M.
      </p>
      <p>
        Flash with thinking turned off was the last option. It answers in 1.7 s at p50 and 2.1 s at p90, but it agreed
        with full Flash on only 88.8% of moves (kappa 0.20) and passed 34 of the 41 moves full Flash failed. Flash with
        thinking stays the live judge.
      </p>
      <Table
        head={["", "Needed", "Small judges", "Flash, judging twice"]}
        align="lrrr"
        rows={[
          ["Same verdict as Flash", "80%", "69% to 71%", "94.7%"],
          ["Agreement beyond chance (kappa)", "0.6", "0.13 to 0.18", "0.72"],
          ["Picks the same better move", "75%", "57% to 59%", ""],
        ]}
      />
      <p>
        Every bad move from data generation was judged on a copy of the match, so the judge's weak spots are known by
        kind:
      </p>
      <Table
        head={["Bad move", "Ruled as expected"]}
        align="lr"
        rows={[
          ["Orders to the judge (prompt injection)", "111 of 111"],
          ["Talking about the rules instead of playing", "265 of 265"],
          ["Padded with filler", "212 of 232"],
          ["Weak but legal", "171 of 210"],
          ["Near-copy of an earlier move", "159 of 230"],
          ["Off topic", "106 of 221"],
          ["The last move, only bigger", "37 of 108"],
        ]}
      />
      <p>The 316 rulings where Flash disagreed with the expected outcome were kept out of training.</p>
    </section>
  );
}

function Infrastructure() {
  return (
    <section id="infrastructure">
      <h2>Infrastructure</h2>
      <p>
        Every paid model call is recorded with its tokens, cost and latency. If a run stops halfway, running it again
        replays the recorded calls for free and pays only for what is new, and it refuses to continue if a recorded
        call's inputs have changed. Every paid command also takes a budget, checked before each call. The largest data
        run stopped at its $15.00 limit, plus 9 cents of calls already in flight.
      </p>
      <p>
        Training ran on rented A100s, launched with <a href="https://dstack.ai">dstack</a> with a price cap, a time limit
        and an automatic retry when no GPU was free. Each training script is a single file with pinned library versions,
        so a run can be repeated exactly. Checkpoints go straight to object storage, every run logs its metrics to{" "}
        <a href="https://mlflow.org">MLflow</a>, and a script stops itself if a fast GPU kernel is missing instead of
        training at a fraction of its speed.
      </p>
      <p>
        The model runs in a serverless container, 4 vCPU and 4 GiB, that scales to zero when nobody plays. A cold start
        takes 8.4 s, so the game wakes the container as soon as a player adds the AI player to a table, while they are
        still writing their first move. A 5 EUR budget alert switches billing off automatically, and that switch has
        been tested. If the model fails mid-match, Flash takes its place, and the replay shows who played each move.
        Every judge and model call is traced in <a href="https://langfuse.com">Langfuse</a> with its timing, tokens,
        cost and errors, but never with the prompt or the reply. A CI workflow runs the linters, the type checker, the
        tests against Postgres, and the frontend build.
      </p>
      <p>Training the shipped model cost, stage by stage:</p>
      <Table
        head={["Stage", "GPU", "API calls"]}
        align="lrl"
        rows={[
          ["Supervised fine-tuning (SFT)", "$0.57", "$18.50 for the training data"],
          ["Preference optimization, two rounds", "$1.22", "$14.84 for the preference pairs"],
          ["Reinforcement learning (GRPO)", "$2.04", "$10.25 for the judge's rewards"],
          ["Total", "$3.83", "$43.59"],
        ]}
      />
    </section>
  );
}

function Limits() {
  return (
    <section id="limits">
      <h2>Limits</h2>
      <p>
        The models trained on two-player matches only, so play at a six-player table is not measured. Flash judged the
        training data, every comparison and the RL reward. Luna's re-checks agree on the headline and the RL
        comparisons, but Flash's bias toward its own style is not measured in full. The tests use frozen positions from
        Flash playing itself, not full matches against the 0.8B model, so they do not show how its own moves shape the
        turns that follow. The position set resolves differences of about 4 to 8 points, depending on the pair. Only the
        preference recipe has a second seed, and the RL run has one. The same 394 positions also guided choices along
        the way, such as the base model and the temperature. Flash's rulings take 14.8 s at p95, against a 6 s target,
        and a 4-core server was measured with at most two
        matches at once.
      </p>
      <p>
        A small judge good enough to give the RL reward would make every training ruling free, and so far none is. RL
        aimed at the habits behind the remaining misses is the obvious next step.
      </p>
    </section>
  );
}

const REFERENCES: { section: string; name: string; refs: [string, string, string, string][] }[] = [
  {
    section: "preference",
    name: "Preference optimization",
    refs: [
      [
        "DPO",
        "Direct Preference Optimization: Your Language Model is Secretly a Reward Model",
        "Rafailov et al., 2023",
        "https://arxiv.org/abs/2305.18290",
      ],
      [
        "IPO",
        "A General Theoretical Paradigm to Understand Learning from Human Preferences",
        "Azar et al., 2023",
        "https://arxiv.org/abs/2310.12036",
      ],
      [
        "SimPO",
        "SimPO: Simple Preference Optimization with a Reference-Free Reward",
        "Meng et al., 2024",
        "https://arxiv.org/abs/2405.14734",
      ],
      [
        "KTO",
        "KTO: Model Alignment as Prospect Theoretic Optimization",
        "Ethayarajh et al., 2024",
        "https://arxiv.org/abs/2402.01306",
      ],
      [
        "Length bias",
        "Disentangling Length from Quality in Direct Preference Optimization",
        "Park et al., 2024",
        "https://arxiv.org/abs/2403.19159",
      ],
    ],
  },
  {
    section: "rl",
    name: "RL",
    refs: [
      [
        "GRPO",
        "DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models",
        "Shao et al., 2024",
        "https://arxiv.org/abs/2402.03300",
      ],
      [
        "DAPO",
        "DAPO: An Open-Source LLM Reinforcement Learning System at Scale",
        "Yu et al., 2025",
        "https://arxiv.org/abs/2503.14476",
      ],
      [
        "Dr. GRPO",
        "Understanding R1-Zero-Like Training: A Critical Perspective",
        "Liu et al., 2025",
        "https://arxiv.org/abs/2503.20783",
      ],
      ["LoRA", "LoRA: Low-Rank Adaptation of Large Language Models", "Hu et al., 2021", "https://arxiv.org/abs/2106.09685"],
      [
        "LoRA for RL",
        "LoRA Without Regret",
        "Schulman and Thinking Machines Lab, 2025",
        "https://thinkingmachines.ai/blog/lora/",
      ],
      [
        "RL sharpens",
        "Does Reinforcement Learning Really Incentivize Reasoning Capacity in LLMs Beyond the Base Model?",
        "Yue et al., 2025",
        "https://arxiv.org/abs/2504.13837",
      ],
    ],
  },
  {
    section: "evaluation",
    name: "Evaluation and the judge",
    refs: [
      [
        "Bootstrap",
        "Bootstrap Methods: Another Look at the Jackknife",
        "Efron, 1979",
        "https://doi.org/10.1214/aos/1176344552",
      ],
      [
        "Cohen's kappa",
        "A Coefficient of Agreement for Nominal Scales",
        "Cohen, 1960",
        "https://doi.org/10.1177/001316446002000104",
      ],
    ],
  },
];

function References() {
  return (
    <section id="references">
      <h2>References</h2>
      {REFERENCES.map((group) => (
        <div key={group.section} className="writeup-references">
          <h3>
            <a href={`#${group.section}`}>{group.name}</a>
          </h3>
          <ul>
            {group.refs.map(([use, title, authors, href]) => (
              <li key={href}>
                <b>{use}</b> <a href={href}>{title}</a> ({authors})
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
