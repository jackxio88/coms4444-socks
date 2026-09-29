# Running the tournament for player 1

`tourney.py` runs the course tournament grid, or any part of it, and writes a report on how **player 1**
(group 1) performed. To test another version of player 1, add `--as 11` or `--as 12` to any command: that
player takes group 1's seats in exactly the same games and seeds, as if it had been submitted as group 1.

Every command is resumable: stop it with Ctrl-C and run the same command again to continue.

Run everything from the repo root on `test-tourney-branch`. That branch has every group's 9/28 player plus
players 11 and 12. It also has speed-optimised copies of groups 3, 4, 6 and 8, which make exactly the same
decisions as the originals but run 4-9x faster. Never push those copies to the class repo. Player 1 on this
branch is Tony's version.

## Quick start

```bash
uv run tourney.py eta              # how long judging player 1 takes (nothing runs)
uv run tourney.py run              # run player 1's games; the verdict prints and the report opens
uv run tourney.py report           # show the latest report again
uv run tourney.py compare p1 p12   # player 12 vs player 1 on the same games (after running both)
```

## What the tournament contains

| | Values |
|---|---|
| Players | 1, 2, 3, 4, 6, 7, 8, 9, 10 |
| Households | alone; every pair; every mix of 5 different players; 9 of the same; all 9 different; 8 of one + 1 of another; 18 and 36 of the same; 18 and 36 all different (282 households) |
| Budget | $0, $150, $300, $500, $1,000, $1,500, unlimited |
| Drawer | 4n + 12 socks, and 2x, 4x, 10x that |
| Run length | 1, 2, 3, 5 and 10 years |
| Hand size | 4 socks |
| Seeds | 4401, 4402, 4403 |

## Commands and ETAs

ETAs are for this Mac: 10 cores, 9 games at a time. The script prints its own estimate before starting, and
a live "time left" every 30 seconds while it runs. `uv run tourney.py eta <same options>` gives the estimate
without running anything.

### Player 1's games: about 2 hours

```bash
uv run tourney.py run
```

That's 52,920 games:
- every household of the tournament that contains player 1 (102 households);
- every other player alone, in pairs and in nines (24 households), so the report can compare homogeneous
  households and measure household value.

| Command | Games | ETA |
|---|---|---|
| `uv run tourney.py run` | 52,920 | ~1.9 h |
| `uv run tourney.py run --five-swaps` (also the 56 five-person households without player 1, for five-person household value) | 76,440 | ~2.9 h |

### Full tournament: about 6.3 hours

```bash
uv run tourney.py run --full
```

That's 118,440 games: every household, setting and seed. The report still focuses on player 1, and it also
ranks every player on equal terms.

### Testing player 11 or player 12 as group 1

Add `--as 11` or `--as 12` to any command. It plays exactly the same games and seeds as the matching player 1
run, so the two reports can be compared side by side.

| Command | ETA |
|---|---|
| `uv run tourney.py run --as 12` | ~1.9 h |
| `uv run tourney.py run --as 12 --scenario tight` | ~75 min |
| `uv run tourney.py run --as 12 --full` | ~6.3 h |

Results are saved per version: `p1`, `p12`, `full-p1`, `full-p12`, and so on. Once both runs exist, compare
them directly:

```bash
uv run tourney.py run --as 12        # after `uv run tourney.py run`
uv run tourney.py compare p1 p12
```

See [Comparing two versions](#comparing-two-versions) below.

### Player 1 on chosen scenarios

Add `--scenario` to any command. ETAs are for player 1 on its own:

| Preset | Games | What it keeps | ETA |
|---|---|---|---|
| `broke` | 7,560 | $0 budget | ~8 min |
| `tight` | 20,460 | budget below, or just covering, what holes alone will cost | ~77 min |
| `rich` | 17,340 | plenty of money, or a drawer that never wears out during the run | ~16 min |
| `unlimited` | 7,560 | no budget limit | ~12 min |
| `solo` | 3,780 | living alone | ~2 min |
| `pairs` | 7,140 | two roommates | ~5 min |
| `five` | 52,920 | five roommates (includes the households without player 1, see below) | ~2.0 h |
| `nine` | 10,920 | nine roommates | ~34 min |
| `big-households` | 1,680 | 18 and 36 roommates | ~12 min |
| `min-drawer` | 13,230 | minimum drawer (4n + 12 socks) | ~32 min |
| `big-drawer` | 26,460 | drawer 4x or 10x the minimum | ~51 min |
| `short` | 21,168 | 1-2 year runs | ~16 min |
| `long` | 21,168 | 5-10 year runs | ~82 min |

`uv run tourney.py scenarios` prints this table. Add `--as 12` to get it for player 12.

`tight` and `rich` are judged against each household and run length, not as fixed dollar amounts. $500 is
rich for one person over a year but not enough for 36 people over 10 years. The same situations appear in
the report:

| Situation | Meaning |
|---|---|
| `broke` | $0 |
| `below-holes` | the budget can't even replace socks that get holes |
| `covers-holes` | covers holes, but not rich |
| `rich` | at least $0.35 per roommate-day, or a drawer that lasts half the run |
| `no-holes` | the drawer never wears out during the run |
| `unlimited` | no budget limit |

### Combining filters

List several presets with commas, and mix them with the options under
[Custom selections](#custom-selections).

- **Different kinds of filter must all match (AND).** `tight,five` keeps thin budgets in five-person
  households only.
- **Filters of the same kind are alternatives (OR).** `solo,pairs` keeps households of 1 or 2;
  `broke,tight` keeps $0 or thin budgets.
- **Options narrow presets the same way.** `--scenario tight --sizes 5` is the same as `tight,five`.

The kinds are budget situation (`broke`, `tight`, `rich`, `unlimited`), household size (`solo`, `pairs`,
`five`, `nine`, `big-households`), drawer (`min-drawer`, `big-drawer`) and run length (`short`, `long`).
`eta` and `run` print the filters in words before anything runs, so you can check the combination. A
combination that matches nothing, such as `--sizes 5 --scenario pairs`, stops with "no games match".

| Command | What runs | ETA |
|---|---|---|
| `uv run tourney.py run --scenario tight,five` | thin budgets, five-person households | ~78 min |
| `uv run tourney.py run --scenario tight,nine` | thin budgets, nine-person households | ~25 min |
| `uv run tourney.py run --scenario broke,tight,nine` | $0 or thin budgets, nine-person households | ~28 min |
| `uv run tourney.py run --scenario solo,pairs` | households of 1 or 2 | ~7 min |
| `uv run tourney.py run --scenario rich,long,big-drawer` | rich budgets, 5-10 years, big drawers | ~3 min |

Every five-person household in the grid contains player 1. So when a selection keeps only five-person
households, the 56 five-person households without player 1 are added automatically. They're needed to
measure household value, and they roughly double the time.

### Custom selections

Every axis can be set directly, and these options work with `run`, `eta` and `scenarios`. They combine with
each other and with `--scenario` presets as described under [Combining filters](#combining-filters):

| Option | Example | Meaning |
|---|---|---|
| `--as` | `12` | another version of player 1 in group 1's seats |
| `--full` | | the whole tournament instead of player 1's games |
| `--budgets` | `0,150,unlimited` | budgets to include |
| `--sizes` | `1,2,9` | household sizes |
| `--drawers` | `1,10` | drawer multiples of 4n + 12 |
| `--years` | `1,10` | run lengths |
| `--situations` | `below-holes,covers-holes` | budget situations, from the table above |
| `--seeds` | `1,2,3,4,5` | seeds (default 4401,4402,4403) |
| `--five-swaps` | | add the five-person households without player 1 |
| `--workers` | `6` | games at a time (default: cores - 1) |
| `--name` | `my-test` | results folder name (default: built from the options) |
| `--no-open` | | don't open the HTML report when the run finishes |

## The report

Every run ends by printing a short verdict in the terminal and opening the full report in the browser. Add
`--no-open` to skip the browser. Both versions are saved in `tourney_results/<name>/`:
- `report.html`: charts and tables, for reading;
- `report.md`: the same content as text, for Slack or GitHub.

The folder name is shown when the run starts: `p1`, `p12`, `full-p1`, `p1_scenario-tight+five`, and so on.

```bash
uv run tourney.py report                  # the latest run
uv run tourney.py report p12              # a particular run
uv run tourney.py report full-p1 --focus 3
```

`--focus` looks at another player of a full run in detail.

### The verdict

The report opens with the answer, in plain words and in the order we judge player 1. For example, from a short
test run:

```text
1. Effect on its household. Swapped in for another player, player 1 lowers the household average by
   0.34 per person-day (the household did better in 55% of swaps, worse in 25%). Player for player,
   it is better than players 2, 3, 4, 6, 7, 8, 9 and 10; worse than none. Among all 9 players it ranks 1st.
2. When every roommate is player 1. Average rank 3.9 of 9 against the other players' own households
   on the same settings (best in 29% of them). 1st of 9 players overall.
✓ 3. Safety: pass. No blowouts and no households pushed into running out of socks.

Helps its household most: budgets that just cover holes (-1.04); households of 9 (-0.51); ...
Helps least: unlimited budgets (+0.01, about even); rich budgets (-0.13); ...
Ranks best on its own: households of 9 (2.2); the minimum drawer (3.9); ...
Ranks worst on its own: households of 2 (5.5; best there: player 4, 2.7); ...
```

- **Effect on its household:** how much the household average changes when player 1 replaces one roommate,
  all else equal. It's measured from pairs of games that differ by one member.
- **When every roommate is player 1:** player 1's own households ranked against the other players' own
  households on the same size, setting and seed.
- **Safety:** passes when there are no **blowouts** (mixed games where player 1's run total is at least 900
  above its roommates' average and at least 3x it) and no **sockless flips** (households that survived
  before player 1 joined and ran out of socks after).
- **Helps most / least** and **ranks best / worst:** found automatically by checking every budget situation,
  household size, drawer and run length. When another player does clearly better in a weak spot, it's named.
- **About even:** used whenever a difference is smaller than seed-to-seed noise or smaller than 0.02 per
  person-day, so small differences aren't over-read.

Scores are embarrassment per person-day, and lower is better. "Official" scores include the 65,536-per-day
penalty for a sockless day. Household effects leave it out, so ordinary mismatch is visible.

### What follows the verdict

1. **Effect on its household.**
   - A chart and a table of the change when player 1 replaces each other player, with a better, about even
     or worse verdict for each.
   - The same broken down by budget situation, household size, drawer and run length.
   - In a `--full` run, every player's average effect as well, ranked, with player 1 highlighted.
2. **When every roommate is player 1.**
   - A grid of player 1's average rank by budget situation and household size, where darker means better.
   - Every player's average rank.
   - The best player in each budget situation and household size.
3. **Safety.**
   - Blowouts and sockless flips for every player.
   - Player 1's worst games against its roommates.
   - The households it pushed into running out of socks, if any.
4. **Appendix.** Mean official scores by scenario, and each player's gap to its roommates.

A player 1 run only compares the other players with player 1, and with each other in households of 1, 2 and
9. Use `--full` for a complete ranking of everyone.

### Comparing two versions

`compare` pairs every game two runs have in common, the same roommates, setting and seed, with a different
player in group 1's seats:

```bash
uv run tourney.py compare p1 p12
uv run tourney.py compare full-p1 full-p12
```

It prints a verdict:
- **Its own result:** who scored better, in how many games, and by how much on average.
- **Its household:** whether the household did better or worse with the second player.
- **Running out of socks:** the households that went sockless with only one of the two.
- **Where each version is ahead.**

It also writes `tourney_results/compare_p12_vs_p1/report.html` and `report.md`, with a chart by budget
situation and tables by budget situation, household size, drawer and run length.

## Tips

- **Keep the computer awake with the lid open.** A closed lid pauses the run. To stop idle sleep while it
  runs: `caffeinate -i uv run tourney.py run ...`
- **Results are kept per selection** in `tourney_results/<name>/games.jsonl`. Re-running the same command
  skips finished games. Delete the folder to start over.
- **Refresh the ETAs after players change:** `uv run tourney.py bench` (about 10 seconds) re-measures how fast
  each player decides.
- **Pass an unlimited budget as `unlimited` in `--budgets`.** The engine crashes on `--budget inf` from the
  course's own tools, because `inf // 10` is NaN.
