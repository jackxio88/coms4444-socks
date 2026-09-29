"""Run the course tournament grid for player 1 (group 1) and report how it did.

    uv run tourney.py eta                          # how long judging player 1 takes (nothing runs)
    uv run tourney.py run                          # player 1's games, then the report
    uv run tourney.py run --scenario tight,five    # only thin budgets in five-person households
    uv run tourney.py run --as 12                  # the same games with player 12 in group 1's seats
    uv run tourney.py run --full                   # the whole tournament, reported for player 1
    uv run tourney.py report                       # show the latest report again
    uv run tourney.py compare p1 p12               # player 12 vs player 1 on the same games
    uv run tourney.py scenarios                    # scenario presets with ETAs
    uv run tourney.py bench                        # re-measure player speeds for the ETAs

See run-tourney.md for the full guide. Results go to tourney_results/<name>/.
"""

import argparse
import contextlib
import html
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
import webbrowser
from collections import Counter, defaultdict
from itertools import combinations, combinations_with_replacement
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, 'tourney_results')
sys.path.insert(0, HERE)

# ---- the course tournament grid

FIELD = ['1', '2', '3', '4', '6', '7', '8', '9', '10']
BUDGETS = [0.0, 150.0, 300.0, 500.0, 1000.0, 1500.0, None]  # None = unlimited
DRAWERS = [1, 2, 4, 10]  # capacity = (4n + 12) x this
YEARS = [1, 2, 3, 5, 10]
SIZES = [1, 2, 5, 9, 18, 36]
SEEDS = [4401, 4402, 4403]
UNIT = 4
PENALTY = 65536.0

SITUATIONS = ['broke', 'below-holes', 'covers-holes', 'rich', 'no-holes', 'unlimited']
SITUATION_HELP = {
	'broke': '$0 budget',
	'below-holes': 'budget below what holes alone will cost over the run',
	'covers-holes': 'budget covers holes but is not rich',
	'rich': 'at least $0.35 per roommate-day, or a drawer that lasts half the run',
	'no-holes': 'drawer never wears out during the run',
	'unlimited': 'no budget limit',
}

# Named scenario presets: each narrows one axis of the grid.
SCENARIOS = {
	'broke': dict(situations=['broke'], help='no money at all ($0)'),
	'tight': dict(
		situations=['below-holes', 'covers-holes'],
		help='money barely covers holes, or not even that',
	),
	'rich': dict(
		situations=['rich', 'no-holes'], help='plenty of money, or a drawer that never wears out'
	),
	'unlimited': dict(situations=['unlimited'], help='unlimited budget'),
	'solo': dict(sizes=[1], help='living alone'),
	'pairs': dict(sizes=[2], help='two roommates'),
	'five': dict(sizes=[5], help='five roommates (mixed groups)'),
	'nine': dict(sizes=[9], help='nine roommates'),
	'big-households': dict(sizes=[18, 36], help='18 and 36 roommates'),
	'min-drawer': dict(drawers=[1], help='minimum drawer (4n + 12 socks)'),
	'big-drawer': dict(drawers=[4, 10], help='drawer 4x or 10x the minimum'),
	'short': dict(years=[1, 2], help='1-2 year runs'),
	'long': dict(years=[5, 10], help='5-10 year runs'),
}


def forced_spend(n, capacity, days):
	"""Dollars that holes alone cost over the run with no voluntary discards (fitted estimate)."""
	wears = 2.0 * n * days
	if wears < 63.0 * capacity:
		return 0.0
	return max(0.0, (10 / 6) * (wears / 69.0 - 0.38 * capacity) - 8)


def situation(n, capacity, days, budget):
	if budget is None:
		return 'unlimited'
	if budget == 0:
		return 'broke'
	holes = forced_spend(n, capacity, days)
	if holes == 0:
		return 'no-holes'
	if budget < holes:
		return 'below-holes'
	per_roommate_day = budget / (n * days)
	if per_roommate_day >= 0.35 or (32 * capacity / (n * days) >= 0.5 and per_roommate_day >= 0.12):
		return 'rich'
	return 'covers-holes'


def households(field):
	"""Every roster of the course grid for this field of players."""
	out = [[p] for p in field]
	out += [list(pair) for pair in combinations_with_replacement(field, 2)]
	out += [list(five) for five in combinations(field, 5)]
	for n in (9, 18, 36):
		out += [[p] * n for p in field]
		if n % len(field) == 0:
			out.append(field * (n // len(field)))
	out += [[a] * 8 + [b] for a in field for b in field if a != b]
	return out


def sort_codes(codes):
	return sorted(codes, key=lambda c: (len(c), c))


def roster_key(roster):
	return '-'.join(sort_codes(roster))


# ---- selecting what to run


def build_selection(args):
	# Player 1 is always the focus. --as puts another version of it (e.g. 11 or 12) in group 1's
	# seats, exactly as if that version had been submitted as group 1.
	seat = getattr(args, 'as_player', None) or '1'
	if seat != '1' and seat in FIELD:
		sys.exit(
			f'--as {seat}: player {seat} is already in the tournament; use --as for a version of player 1'
		)
	field = [seat if p == '1' else p for p in FIELD]
	axes = dict(
		budgets=parse_budgets(args.budgets) if args.budgets else list(BUDGETS),
		drawers=[int(x) for x in args.drawers.split(',')] if args.drawers else list(DRAWERS),
		years=[int(x) for x in args.years.split(',')] if args.years else list(YEARS),
		sizes=[int(x) for x in args.sizes.split(',')] if args.sizes else list(SIZES),
		situations=args.situations.split(',') if args.situations else list(SITUATIONS),
	)
	# Presets of the same kind widen each other (solo,pairs = either size); presets of different
	# kinds narrow each other (tight,five = tight budgets in five-person households), and so do
	# explicit flags (--scenario tight --sizes 5 is the same as tight,five).
	wanted = defaultdict(set)
	for name in args.scenario.split(',') if args.scenario else []:
		if name not in SCENARIOS:
			sys.exit(f'unknown scenario {name!r}; see: uv run tourney.py scenarios')
		for axis, values in SCENARIOS[name].items():
			if axis != 'help':
				wanted[axis].update(values)
	for axis, values in wanted.items():
		axes[axis] = [v for v in axes[axis] if v in values]
	seeds = [int(x) for x in args.seeds.split(',')] if args.seeds else list(SEEDS)

	rosters = households(field)
	if not args.full:
		mine = [r for r in rosters if seat in r]
		# Comparison households: every other player alone, in pairs and in nines, so the report can
		# rank homogeneous households and measure household value (player 1 swapped for a roommate).
		others = [[p] * n for p in field if p != seat for n in (1, 2, 9)]
		extra = []
		five_only = 5 in axes['sizes'] and not {2, 9} & set(axes['sizes'])
		if args.five_swaps or five_only:
			# Every five-person household in the grid contains player 1, so household value needs
			# the ones without it; added automatically when fives are all that is selected.
			extra = [r for r in rosters if len(r) == 5 and seat not in r]
		rosters = mine + others + extra
	rosters = [r for r in rosters if len(r) in axes['sizes']]

	jobs = []
	for roster in rosters:
		n = len(roster)
		for mult in axes['drawers']:
			capacity = (4 * n + 12) * mult
			for years in axes['years']:
				days = 365 * years
				for budget in axes['budgets']:
					if situation(n, capacity, days, budget) not in axes['situations']:
						continue
					for seed in seeds:
						jobs.append(
							dict(
								roster=roster,
								n=n,
								mult=mult,
								capacity=capacity,
								years=years,
								days=days,
								budget=budget,
								seed=seed,
							)
						)
	name = args.name or default_name(args, seat)
	return dict(
		name=name,
		mode='full' if args.full else 'games',
		seat=seat,
		field=field,
		axes=axes,
		seeds=seeds,
		jobs=jobs,
		filters=filter_text(axes, seeds),
	)


def filter_text(axes, seeds):
	"""The filters that narrow the full grid, in words."""
	defaults = dict(
		budgets=BUDGETS, drawers=DRAWERS, years=YEARS, sizes=SIZES, situations=SITUATIONS
	)
	shown = {
		'situations': ('budget situations', lambda v: v),
		'budgets': ('budgets', lambda v: 'unlimited' if v is None else f'${v:g}'),
		'sizes': ('household sizes', str),
		'drawers': ('drawers', lambda v: f'x{v}'),
		'years': ('years', str),
	}
	parts = []
	for axis, (title, fmt) in shown.items():
		if list(axes[axis]) != list(defaults[axis]):
			parts.append(f'{title} {", ".join(fmt(v) for v in axes[axis]) or "(none)"}')
	if seeds != SEEDS:
		parts.append(f'seeds {", ".join(str(s) for s in seeds)}')
	return ' AND '.join(parts) if parts else 'none (the whole grid)'


def parse_budgets(text):
	return [
		None if x.strip() in ('inf', 'unlimited', 'none') else float(x) for x in text.split(',')
	]


def default_name(args, seat):
	"""e.g. p1, p12, full-p1, p1_scenario-tight+five."""
	parts = [('full-' if args.full else '') + f'p{seat}']
	for flag in ('scenario', 'budgets', 'sizes', 'drawers', 'years', 'situations'):
		value = getattr(args, flag)
		if value:
			parts.append(f'{flag}-{value.replace(",", "+")}')
	if getattr(args, 'five_swaps', False) and not args.full:
		parts.append('five-swaps')
	if args.seeds:
		parts.append('seeds-' + args.seeds.replace(',', '+'))
	return '_'.join(parts)


def job_key(job):
	budget = 'unlimited' if job['budget'] is None else f'{job["budget"]:g}'
	return f'{roster_key(job["roster"])}|x{job["mult"]}|{job["years"]}y|{budget}|{job["seed"]}'


# ---- time estimates

ENGINE_SECONDS = 13e-6  # engine overhead per decision
CALIBRATION = 0.85  # measured runs on this branch took ~85% of the raw estimate
# Seconds per decision measured on test-tourney-branch (all-same households, one year).
# `uv run tourney.py bench` re-measures and overrides these via tourney_results/costs.json.
DEFAULT_COSTS = {
	'1': dict(broke=3.72e-06, unlimited=2.10e-05, small=2.49e-05, big=1.61e-05),
	'2': dict(broke=8.99e-06, unlimited=3.14e-05, small=5.47e-05, big=5.43e-05),
	'3': dict(broke=2.75e-04, unlimited=3.34e-04, small=3.42e-04, big=1.61e-04),
	'4': dict(broke=0.00e00, unlimited=1.64e-05, small=9.46e-04, big=8.53e-04),
	'6': dict(broke=3.73e-04, unlimited=3.52e-04, small=4.70e-04, big=2.72e-04),
	'7': dict(broke=7.25e-05, unlimited=2.52e-05, small=8.22e-05, big=6.09e-05),
	'8': dict(broke=0.00e00, unlimited=3.24e-04, small=1.61e-04, big=1.27e-04),
	'9': dict(broke=0.00e00, unlimited=1.79e-05, small=6.70e-06, big=3.20e-06),
	'10': dict(broke=0.00e00, unlimited=6.52e-06, small=0.00e00, big=3.01e-06),
	'11': dict(broke=0.00e00, unlimited=2.00e-05, small=8.28e-06, big=7.81e-06),
	'12': dict(broke=8.27e-08, unlimited=3.27e-05, small=7.18e-06, big=6.97e-06),
}
UNKNOWN_COST = 3e-05


def load_costs():
	costs = {k: dict(v) for k, v in DEFAULT_COSTS.items()}
	path = os.path.join(RESULTS, 'costs.json')
	if os.path.exists(path):
		with open(path) as fh:
			costs.update(json.load(fh))
	return costs


def cost_class(job):
	if job['budget'] == 0:
		return 'broke'
	if job['budget'] is None:
		return 'unlimited'
	return 'small' if job['mult'] <= 2 else 'big'


def job_cost(job, costs):
	"""Estimated CPU seconds for one game."""
	cls = cost_class(job)
	per_day = sum(costs.get(p, {}).get(cls, UNKNOWN_COST) + ENGINE_SECONDS for p in job['roster'])
	return per_day * job['days'] * CALIBRATION


def default_workers():
	return max(1, (os.cpu_count() or 2) - 1)


def fmt_duration(seconds):
	if seconds < 90:
		return f'{seconds:.0f} s'
	if seconds < 5400:
		return f'{seconds / 60:.0f} min'
	return f'{seconds / 3600:.1f} h'


def describe(selection, workers, costs, done=frozenset()):
	jobs = selection['jobs']
	todo = [j for j in jobs if job_key(j) not in done]
	cpu = sum(job_cost(j, costs) for j in todo)
	rosters = {roster_key(j['roster']) for j in jobs}
	decisions = sum(j['n'] * j['days'] for j in todo)
	longest = max((job_cost(j, costs) for j in todo), default=0.0)
	wall = max(cpu / workers, longest)
	return dict(
		games=len(jobs),
		todo=len(todo),
		households=len(rosters),
		decisions=decisions,
		cpu=cpu,
		wall=wall,
	)


# ---- running


_CLASSES = {}


def play(job):
	from core.engine import Engine
	from core.registry import discover

	if not _CLASSES:
		_CLASSES.update(discover()[0])
	random.seed(job['seed'])
	start = time.time()
	with contextlib.redirect_stdout(io.StringIO()):
		result = Engine(
			[_CLASSES[p] for p in job['roster']],
			capacity=job['capacity'],
			selection_unit=UNIT,
			days=job['days'],
			seed=job['seed'],
			timeout=1.0,
			keep_records=False,
			budget=job['budget'],
		).run()
	seats = [
		[p, s['mean_daily_embarrassment'], s['sockless_days']]
		for p, s in zip(job['roster'], result['players'], strict=True)
	]
	return dict(
		job,
		key=job_key(job),
		seats=seats,
		spent=result['total_spent'],
		faults=len(result['faults']),
		secs=time.time() - start,
	)


def run_dir(name):
	return os.path.join(RESULTS, name)


def load_games(name):
	path = os.path.join(run_dir(name), 'games.jsonl')
	games = {}
	if os.path.exists(path):
		with open(path) as fh:
			for line in fh:
				with contextlib.suppress(json.JSONDecodeError):
					g = json.loads(line)
					games[g['key']] = g
	return games


def cmd_run(args):
	from core.registry import discover

	selection = build_selection(args)
	print(f'filters: {selection["filters"]}')
	if not selection['jobs']:
		sys.exit('no games match these filters')
	known = discover()[0]
	missing = sorted({p for j in selection['jobs'] for p in j['roster']} - set(known))
	if missing:
		sys.exit(f'unknown players: {missing} (registry has {sort_codes(known)})')
	costs = load_costs()
	workers = args.workers or default_workers()
	folder = run_dir(selection['name'])
	os.makedirs(folder, exist_ok=True)
	with open(os.path.join(folder, 'run.json'), 'w') as fh:
		json.dump(
			{k: v for k, v in selection.items() if k != 'jobs'} | {'games': len(selection['jobs'])},
			fh,
			indent=1,
		)

	done = set(load_games(selection['name']))
	todo = [j for j in selection['jobs'] if job_key(j) not in done]
	plan = describe(selection, workers, costs, done)
	print(
		f'{selection["name"]}: {plan["games"]:,} games ({plan["todo"]:,} to run, {len(done):,} already done) '
		f'on {workers} workers - estimated {fmt_duration(plan["wall"])}'
	)
	if todo:
		print(
			'keep the computer awake (lid open); stop any time with Ctrl-C and re-run the same command to resume.'
		)
		run_jobs(todo, folder, workers, costs)
	write_report(selection['name'], open_browser=not args.no_open)


def run_jobs(todo, folder, workers, costs):
	todo.sort(key=lambda j: -job_cost(j, costs))  # longest games first keeps every worker busy
	total = sum(job_cost(j, costs) for j in todo)
	finished = 0.0
	start = last = time.time()
	with (
		open(os.path.join(folder, 'games.jsonl'), 'a') as fh,
		Pool(workers, maxtasksperchild=500) as pool,
	):
		for i, game in enumerate(pool.imap_unordered(play, todo, chunksize=1), 1):
			fh.write(json.dumps(game) + '\n')
			finished += job_cost(game, costs)
			now = time.time()
			if now - last > 30 or i == len(todo):
				fh.flush()
				last = now
				share = finished / total
				eta = max(0.0, (now - start) * (1 - share) / share) if share else 0.0
				print(
					f'  {i:,}/{len(todo):,} games, {100 * share:.0f}% of the work, '
					f'{fmt_duration(now - start)} elapsed, about {fmt_duration(eta)} left',
					flush=True,
				)
	print(f'done in {fmt_duration(time.time() - start)}')


def cmd_eta(args):
	selection = build_selection(args)
	costs = load_costs()
	workers = args.workers or default_workers()
	done = set(load_games(selection['name']))
	plan = describe(selection, workers, costs, done)
	print(f'{selection["name"]}')
	print(f'  filters: {selection["filters"]}')
	if not selection['jobs']:
		sys.exit('  no games match these filters')
	print(
		f'  {plan["households"]:,} households, {plan["games"]:,} games ({plan["todo"]:,} still to run), '
		f'{plan["decisions"] / 1e6:,.0f}M decisions'
	)
	print(
		f'  estimated {plan["cpu"] / 3600:.1f} CPU-hours -> about {fmt_duration(plan["wall"])} on {workers} workers'
	)


def cmd_scenarios(args):
	costs = load_costs()
	workers = args.workers or default_workers()
	seat = args.as_player or '1'
	who = f'player {seat}' + (" in group 1's seats" if seat != '1' else '')
	print(f'Scenario presets for {who}, with ETAs on {workers} workers (combine with commas):\n')
	for name, spec in SCENARIOS.items():
		sub = argparse.Namespace(**vars(args))
		sub.scenario = name
		sub.full = False
		plan = describe(build_selection(sub), workers, costs)
		print(
			f'  {name:15s} {spec["help"]:46s} {plan["games"]:7,} games  ~{fmt_duration(plan["wall"])}'
		)
	flag = f' --as {seat}' if seat != '1' else ''
	print(f'\nexample: uv run tourney.py run{flag} --scenario tight,five')


def cmd_bench(args):
	"""Measure seconds per decision for each player (all-same households, one year)."""
	from core.registry import discover

	codes = sort_codes([p for p in discover()[0] if p.isdigit()])
	cases = []
	for code in codes:
		for cls, (n, mult, budget) in dict(
			broke=(5, 1, 0.0), unlimited=(2, 1, None), small=(9, 1, 500.0), big=(9, 4, 500.0)
		).items():
			cases.append(
				dict(
					code=code,
					cls=cls,
					roster=[code] * n,
					n=n,
					mult=mult,
					capacity=(4 * n + 12) * mult,
					years=1,
					days=365,
					budget=budget,
					seed=1,
				)
			)
	print(f'timing {len(codes)} players ({len(cases)} one-year games)...')
	with Pool(args.workers or default_workers()) as pool:
		results = pool.map(_bench_one, cases)
	costs = defaultdict(dict)
	for case, seconds in zip(cases, results, strict=True):
		costs[case['code']][case['cls']] = max(
			0.0, seconds / (case['n'] * case['days']) - ENGINE_SECONDS
		)
	os.makedirs(RESULTS, exist_ok=True)
	with open(os.path.join(RESULTS, 'costs.json'), 'w') as fh:
		json.dump(costs, fh, indent=1)
	for code in codes:
		print(
			f'  player {code:3s} '
			+ '  '.join(f'{cls} {max(0.0, v) * 1000:.3f} ms' for cls, v in costs[code].items())
		)


def _bench_one(case):
	return play(case)['secs']


# ---- report: measurements

# Differences smaller than these are "about even" even when the data is plentiful.
PRACTICAL_SCORE = 0.02  # embarrassment per person-day
PRACTICAL_RANK = 0.25  # places in an all-same ranking
MIN_SWAPS = 20  # a slice needs this many swaps before it is called a strength or weakness
MIN_CELLS = 5  # ...or this many all-same settings
NOTE_MARGIN = 0.05  # name a player that beats the focus in a slice only if it does by this much

SITUATION_WORDS = {
	'broke': '$0 budgets',
	'below-holes': 'budgets below the cost of holes',
	'covers-holes': 'budgets that just cover holes',
	'rich': 'rich budgets',
	'no-holes': 'runs where socks never wear out',
	'unlimited': 'unlimited budgets',
}


def slice_words(axis, value):
	if axis == 'situation':
		return SITUATION_WORDS[value]
	if axis == 'n':
		return 'living alone' if value == 1 else f'households of {value}'
	if axis == 'mult':
		return 'the minimum drawer' if value == 1 else f'drawers {value}x the minimum'
	return f'{value}-year runs'


def household_average(game, penalty=True):
	if penalty:
		return statistics.fmean(s[1] for s in game['seats'])
	return statistics.fmean(s[1] - s[2] * PENALTY / game['days'] for s in game['seats'])


def household_sockless(game):
	return sum(s[2] for s in game['seats'])


def seat_average(game, code, penalty=True):
	values = [
		s[1] - (0 if penalty else s[2] * PENALTY / game['days'])
		for s in game['seats']
		if s[0] == code
	]
	return statistics.fmean(values)


def setting_of(game):
	return (game['mult'], game['years'], game['budget'], game['seed'])


def mean_se(values):
	if not values:
		return math.nan, math.inf
	if len(values) < 2:
		return values[0], math.inf
	return statistics.fmean(values), statistics.stdev(values) / math.sqrt(len(values))


def judge(mean, se, practical):
	"""'better' / 'worse' / 'about even' for a difference where negative favours the focus player."""
	if math.isnan(mean) or abs(mean) < max(2 * se, practical):
		return 'about even'
	return 'better' if mean < 0 else 'worse'


class Tally:
	"""Running totals of household changes, for players whose individual swaps are not needed."""

	def __init__(self):
		self.n = self.total = self.squares = self.better = self.worse = self.flips = (
			self.rescues
		) = 0

	def add(self, swap):
		self.n += 1
		self.total += swap['change']
		self.squares += swap['change'] ** 2
		self.better += swap['change_official'] < 0
		self.worse += swap['change_official'] > 0
		self.flips += swap['flip']
		self.rescues += swap['rescue']

	def summary(self):
		if not self.n:
			return dict(n=0, mean=math.nan, se=math.inf, better=0.0, worse=0.0, flips=0, rescues=0)
		mean = self.total / self.n
		variance = (
			max(0.0, (self.squares - self.n * mean**2) / (self.n - 1)) if self.n > 1 else math.inf
		)
		return dict(
			n=self.n,
			mean=mean,
			se=math.sqrt(variance / self.n),
			better=100 * self.better / self.n,
			worse=100 * self.worse / self.n,
			flips=self.flips,
			rescues=self.rescues,
		)


def find_swaps(games, players, focus):
	"""Pairs of games with the same setting whose households differ by exactly one member.

	Returns the focus player's swaps in full, and running totals for every player."""
	index = {(setting_of(g), roster_key(g['roster'])): g for g in games}
	mine = []
	tallies = {p: Tally() for p in players}
	for g in games:
		if not 2 <= g['n'] <= 9:
			continue  # the grid only has one-member swaps between households of 2 to 9
		members = Counter(g['roster'])
		for leaving in members:
			for joining in players:
				if joining == leaving:
					continue
				swapped = members.copy()
				swapped[leaving] -= 1
				swapped[joining] += 1
				other = index.get((setting_of(g), roster_key(list(swapped.elements()))))
				if other is None:
					continue
				swap = dict(
					leaving=leaving,
					change=household_average(other, False) - household_average(g, False),
					change_official=household_average(other) - household_average(g),
					flip=household_sockless(g) == 0 and household_sockless(other) > 0,
					rescue=household_sockless(g) > 0 and household_sockless(other) == 0,
				)
				tallies[joining].add(swap)
				if joining == focus:
					swap.update(
						situation=g['situation'], n=g['n'], mult=g['mult'], years=g['years']
					)
					if swap['flip']:
						swap['game'] = other
					mine.append(swap)
	return mine, tallies


def summarize_swaps(swaps):
	mean, se = mean_se([s['change'] for s in swaps])
	count = len(swaps) or 1
	return dict(
		n=len(swaps),
		mean=mean,
		se=se,
		better=100 * sum(s['change_official'] < 0 for s in swaps) / count,
		worse=100 * sum(s['change_official'] > 0 for s in swaps) / count,
		flips=sum(s['flip'] for s in swaps),
		rescues=sum(s['rescue'] for s in swaps),
	)


def rank_all_same(games):
	"""Per setting: the players whose all-same households share its size, setting and seed, ranked."""
	cells = defaultdict(dict)
	for g in games:
		codes = set(g['roster'])
		if len(codes) == 1:
			cells[(g['n'], setting_of(g))][codes.pop()] = g
	ranked = []
	for (n, _), entries in cells.items():
		if len(entries) < 2:
			continue
		scores = {p: household_average(g) for p, g in entries.items()}
		order = sorted(scores, key=scores.get)
		ranks = {}
		i = 0
		while i < len(order):
			j = i
			while j + 1 < len(order) and abs(scores[order[j + 1]] - scores[order[i]]) <= 1e-9:
				j += 1
			for k in range(i, j + 1):
				ranks[order[k]] = (i + j) / 2 + 1
			i = j + 1
		g = next(iter(entries.values()))
		ranked.append(
			dict(
				ranks=ranks,
				scores=scores,
				situation=g['situation'],
				n=n,
				mult=g['mult'],
				years=g['years'],
			)
		)
	return ranked


def all_same_summary(cells, players):
	out = {}
	for p in players:
		mine = [c for c in cells if p in c['ranks']]
		ranks = [c['ranks'][p] for c in mine]
		best = [c for c in mine if c['ranks'][p] == min(c['ranks'].values())]
		out[p] = dict(
			n=len(mine),
			rank=statistics.fmean(ranks) if ranks else math.nan,
			best=100 * len(best) / len(mine) if mine else 0.0,
			field=statistics.fmean(len(c['ranks']) for c in mine) if mine else 0,
		)
	return out


def blowouts(games, players):
	"""Mixed games where a player's run total is 900+ above its roommates' average and 3x+ it."""
	out = {p: dict(games=0, count=0, gaps=[], worst=[]) for p in players}
	for g in games:
		codes = set(g['roster'])
		if len(codes) < 2:
			continue
		for p in codes:
			ours = seat_average(g, p, False) * g['days']
			theirs = (
				statistics.fmean(s[1] - s[2] * PENALTY / g['days'] for s in g['seats'] if s[0] != p)
				* g['days']
			)
			out[p]['games'] += 1
			out[p]['gaps'].append(ours - theirs)
			if ours - theirs >= 900 and ours >= 3 * theirs:
				out[p]['count'] += 1
				out[p]['worst'].append(dict(game=g, ours=ours, theirs=theirs))
	for v in out.values():
		v['gap95'] = sorted(v['gaps'])[int(0.95 * (len(v['gaps']) - 1))] if v['gaps'] else 0.0
		v['worst'] = sorted(v['worst'], key=lambda e: e['theirs'] - e['ours'])[:5]
	return out


def roster_words(roster):
	"""e.g. 'p1 + 8×p8' or 'p1 + p2 + p3 + p4 + p6'."""
	counts = Counter(roster)
	return ' + '.join(
		f'p{c}' if counts[c] == 1 else f'{counts[c]}×p{c}' for c in sort_codes(counts)
	)


def describe_game(g):
	budget = 'unlimited' if g['budget'] is None else f'${g["budget"]:g}'
	return f'{roster_words(g["roster"])}, drawer x{g["mult"]}, {g["years"]} years, {budget}, seed {g["seed"]}'


# ---- report: content
#
# A report is a list of blocks, rendered once as Markdown and once as HTML:
#   ('h1'|'h2'|'h3', text) ('p', text) ('list', [text]) ('table', headers, rows, highlight_rows)
#   ('chart', svg, caption) ('appendix',)
# Text may use **bold**.


def ordinal(k):
	k = int(round(k))
	return f'{k}{"th" if 10 <= k % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(k % 10, "th")}'


def signed(x, digits=2):
	"""+0.12 / -0.12, and 0.00 (not -0.00) for anything that rounds to zero."""
	if abs(x) < 0.5 * 10**-digits:
		return f'{0:.{digits}f}'
	return f'{x:+.{digits}f}'


def players_words(codes):
	"""'player 9', 'players 9 and 10', 'players 2, 3 and 4'."""
	codes = sort_codes(codes)
	if len(codes) == 1:
		return f'player {codes[0]}'
	return 'players ' + ', '.join(codes[:-1]) + f' and {codes[-1]}'


def plural(count, word):
	return f'{count} {word}{"" if count == 1 else "s"}'


def verdict_blocks(ctx):
	f = ctx['focus']
	blocks = [('h2', f'Verdict for player {f}')]
	items = []

	# 1. household effect
	direct = ctx['direct']
	if direct['n']:
		verb = 'lowers' if direct['mean'] < 0 else 'raises'
		groups = ctx['against']
		text = (
			f'**1. Effect on its household.** Swapped in for another player, player {f} {verb} the household '
			f'average by {abs(direct["mean"]):.2f} per person-day (the household did better in {direct["better"]:.0f}% '
			f'of swaps, worse in {direct["worse"]:.0f}%).'
		)
		parts = []
		if groups['better']:
			parts.append(f'better than {players_words(groups["better"])}')
		even = groups['about even']
		if even:
			parts.append(
				f'about even with {players_words(even)}'
				if len(even) <= 3
				else f'about even with the other {len(even)}'
			)
		if groups['worse']:
			worst = ', '.join(
				f'player {q} (by {ctx["vs"][q]["mean"]:.2f})' for q in sort_codes(groups['worse'])
			)
			parts.append(f'worse than {worst}')
		else:
			parts.append('worse than none')
		text += ' Player for player, it is ' + '; '.join(parts) + '.'
		if ctx['full'] and ctx['effect_rank']:
			rank, best = ctx['effect_rank']
			text += f' Among all {len(ctx["players"])} players it ranks **{ordinal(rank)}**'
			text += (
				f' (best: player {best["player"]}, {signed(best["mean"])}).'
				if best['player'] != f
				else '.'
			)
		items.append(text)
	else:
		items.append(
			'**1. Effect on its household.** Not measured: this selection has no households of 2 to 9.'
		)

	# 2. all-same households
	own = ctx['same'].get(f, {})
	if own.get('n'):
		text = (
			f'**2. When every roommate is player {f}.** Average rank **{own["rank"]:.1f} of {own["field"]:.0f}** '
			f"against the other players' own households on the same settings (best in {own['best']:.0f}% of them)."
		)
		place, total, best, even = ctx['same_rank']
		text += f' {ordinal(place)} of {total} players overall'
		if best != f:
			text += f'; the best is player {best}' + (' (about even)' if even else '')
		text += '.'
		items.append(text)
	else:
		items.append(
			f'**2. When every roommate is player {f}.** Not measured: no all-same households to compare in this selection.'
		)

	# 3. safety
	b = ctx['blow'][f]['count']
	flips = direct['flips'] if direct['n'] else 0
	if b == 0 and flips == 0:
		items.append(
			'✓ **3. Safety: pass.** No blowouts and no households pushed into running out of socks.'
		)
	else:
		items.append(
			f'✗ **3. Safety: check.** {plural(b, "blowout")} and {plural(flips, "household")} pushed into '
			'running out of socks (see section 3).'
		)
	blocks.append(('list', items))

	# strengths and weaknesses
	lines = []
	helps = ctx['slices_effect']
	if helps:
		best = [s for s in helps[:3] if s['verdict'] == 'better']
		worst = [s for s in reversed(helps[-3:]) if s['mean'] > helps[0]['mean'] and s not in best]
		if best:
			lines.append(
				'**Helps its household most:** '
				+ '; '.join(f'{s["words"]} ({signed(s["mean"])})' for s in best)
				+ '.'
			)
		if worst:
			parts = []
			for s in worst:
				part = f'{s["words"]} ({signed(s["mean"])}'
				part += ', about even' if s['verdict'] == 'about even' else ''
				if s.get('beaten_by'):
					q, m = s['beaten_by']
					part += f'; player {q} better by {m:.2f}'
				parts.append(part + ')')
			lines.append('**Helps least:** ' + '; '.join(parts) + '.')
	ranks = ctx['slices_same']
	if ranks:
		best = ranks[:3]
		worst = [s for s in reversed(ranks[-3:]) if s['rank'] > ranks[0]['rank'] and s not in best]
		lines.append(
			'**Ranks best on its own:** '
			+ '; '.join(f'{s["words"]} ({s["rank"]:.1f})' for s in best)
			+ '.'
		)
		if worst:
			parts = []
			for s in worst:
				part = f'{s["words"]} ({s["rank"]:.1f}'
				if s['leader'] != f:
					part += f'; best there: player {s["leader"]}, {s["leader_rank"]:.1f}'
				parts.append(part + ')')
			lines.append('**Ranks worst on its own:** ' + '; '.join(parts) + '.')
	if lines:
		blocks.append(('list', lines))
	return blocks


def build_context(games, focus, full):
	players = sort_codes({s[0] for g in games for s in g['seats']})
	mine, tallies = find_swaps(games, players, focus)
	vs = {q: summarize_swaps([s for s in mine if s['leaving'] == q]) for q in players if q != focus}
	vs = {q: v for q, v in vs.items() if v['n']}
	against = {'better': [], 'about even': [], 'worse': []}
	for q, v in vs.items():
		against[judge(v['mean'], v['se'], PRACTICAL_SCORE)].append(q)
	for v in vs.values():
		v['verdict'] = judge(v['mean'], v['se'], PRACTICAL_SCORE)

	effect = {p: tallies[p].summary() for p in players}
	effect_rank = None
	if full and effect.get(focus, {}).get('n'):
		order = sorted((p for p in players if effect[p]['n']), key=lambda p: effect[p]['mean'])
		effect_rank = (order.index(focus) + 1, dict(player=order[0], mean=effect[order[0]]['mean']))

	cells = rank_all_same(games)
	same = all_same_summary(cells, players)
	same_rank = None
	if same.get(focus, {}).get('n'):
		order = sorted((p for p in players if same[p]['n']), key=lambda p: same[p]['rank'])
		best = order[0]
		shared = [
			c['ranks'][focus] - c['ranks'][best]
			for c in cells
			if focus in c['ranks'] and best in c['ranks']
		]
		mean, se = mean_se(shared) if best != focus else (0.0, 0.0)
		same_rank = (
			order.index(focus) + 1,
			len(order),
			best,
			abs(mean) < max(2 * se, PRACTICAL_RANK),
		)

	slices_effect = []
	for axis in ('situation', 'n', 'mult', 'years'):
		for value in sorted({s[axis] for s in mine}, key=str):
			part = [s for s in mine if s[axis] == value]
			if len(part) < MIN_SWAPS:
				continue
			mean, se = mean_se([s['change'] for s in part])
			entry = dict(
				axis=axis, value=value, words=slice_words(axis, value), mean=mean, n=len(part)
			)
			entry['verdict'] = judge(mean, se, PRACTICAL_SCORE)
			per = defaultdict(list)
			for s in part:
				per[s['leaving']].append(s['change'])
			worst = max(
				((q, statistics.fmean(v)) for q, v in per.items() if len(v) >= 5),
				key=lambda x: x[1],
				default=None,
			)
			if worst and worst[1] >= NOTE_MARGIN:
				entry['beaten_by'] = worst
			slices_effect.append(entry)
	slices_effect.sort(key=lambda s: s['mean'])

	slices_same = []
	for axis in ('situation', 'n', 'mult', 'years'):
		for value in sorted({c[axis] for c in cells if focus in c['ranks']}, key=str):
			part = [c for c in cells if c[axis] == value and focus in c['ranks']]
			if len(part) < MIN_CELLS:
				continue
			means = {}
			for p in players:
				rs = [c['ranks'][p] for c in part if p in c['ranks']]
				if len(rs) >= len(part) // 2:
					means[p] = statistics.fmean(rs)
			leader = min(means, key=means.get)
			slices_same.append(
				dict(
					axis=axis,
					value=value,
					words=slice_words(axis, value),
					rank=means[focus],
					leader=leader,
					leader_rank=means[leader],
				)
			)
	slices_same.sort(key=lambda s: s['rank'])

	return dict(
		focus=focus,
		full=full,
		players=players,
		mine=mine,
		direct=summarize_swaps(mine),
		vs=vs,
		against=against,
		effect=effect,
		effect_rank=effect_rank,
		cells=cells,
		same=same,
		same_rank=same_rank,
		blow=blowouts(games, players),
		slices_effect=slices_effect,
		slices_same=slices_same,
	)


def report_blocks(name, games, meta, focus):
	full = meta.get('mode') != 'games'
	ctx = build_context(games, focus, full)
	f = focus
	players = ctx['players']
	seat_note = " (playing group 1's seats)" if f != '1' and f == meta.get('seat') else ''
	blocks = [
		('h1', f'Tournament report: player {f}{seat_note}'),
		(
			'p',
			f'Run `{name}`: {len(games):,} games, {sum(g["faults"] for g in games):,} faults, '
			f'{sum(g["secs"] for g in games) / 3600:.1f} CPU-hours. Players: {", ".join(players)}. '
			f'Filters: {meta.get("filters", "none")}.',
		),
	]
	if not full:
		blocks.append(
			(
				'p',
				f'This run holds the games needed to judge player {f}, so every comparison is between '
				f'player {f} and the other players. Use `--full` to rank all players against each other.',
			)
		)
	blocks += verdict_blocks(ctx)
	blocks.append(
		(
			'p',
			'Scores are embarrassment per person-day, lower is better. "About even" means the difference is '
			'smaller than seed-to-seed noise or smaller than 0.02 per person-day.',
		)
	)

	# 1. effect on the household
	blocks.append(('h2', '1. Effect on its household'))
	blocks.append(
		(
			'p',
			f'How much the household average changes when player {f} replaces one roommate and everything '
			'else stays the same. Negative means the household does better with player '
			f'{f}. Measured without the sockless penalty; "household better / worse" counts games on the '
			'official score.',
		)
	)
	vs = ctx['vs']
	if vs:
		rows = sorted(vs.items(), key=lambda kv: kv[1]['mean'])
		blocks.append(
			(
				'chart',
				chart_effect(ctx),
				f"Each player's average effect on its household, player {f} highlighted"
				if full
				else f'Change in the household average when player {f} replaces each player',
			)
		)
		blocks.append(
			(
				'table',
				[
					'instead of',
					'swaps',
					'change per person-day',
					'household better / worse',
					'verdict',
					'sockless flips',
				],
				[
					[
						f'player {q}',
						f'{v["n"]:,}',
						signed(v['mean']),
						f'{v["better"]:.0f}% / {v["worse"]:.0f}%',
						v['verdict'],
						v['flips'],
					]
					for q, v in rows
				],
				set(),
			)
		)
		for axis, title in (
			('situation', 'By budget situation'),
			('n', 'By household size'),
			('mult', 'By drawer'),
			('years', 'By run length'),
		):
			parts = defaultdict(list)
			for s in ctx['mine']:
				parts[s[axis]].append(s)
			if len(parts) < 2:
				continue
			order = SITUATIONS if axis == 'situation' else sorted(parts)
			table_rows = []
			for key in order:
				if key not in parts:
					continue
				v = summarize_swaps(parts[key])
				table_rows.append(
					[
						slice_words(axis, key),
						f'{v["n"]:,}',
						signed(v['mean']),
						f'{v["better"]:.0f}% / {v["worse"]:.0f}%',
						judge(v['mean'], v['se'], PRACTICAL_SCORE),
					]
				)
			blocks.append(('h3', title))
			blocks.append(
				(
					'table',
					['', 'swaps', 'change per person-day', 'household better / worse', 'verdict'],
					table_rows,
					set(),
				)
			)
		if full:
			ranked = sorted(
				(p for p in players if ctx['effect'][p]['n']),
				key=lambda p: ctx['effect'][p]['mean'],
			)
			blocks.append(('h3', 'Every player, replacing a roommate'))
			blocks.append(
				(
					'table',
					[
						'rank',
						'player',
						'change per person-day',
						'household better / worse',
						'sockless flips',
					],
					[
						[
							ordinal(i + 1),
							f'player {p}',
							signed(ctx['effect'][p]['mean']),
							f'{ctx["effect"][p]["better"]:.0f}% / {ctx["effect"][p]["worse"]:.0f}%',
							ctx['effect'][p]['flips'],
						]
						for i, p in enumerate(ranked)
					],
					{ranked.index(f)} if f in ranked else set(),
				)
			)
	else:
		blocks.append(('p', 'Not measured: this selection has no households of 2 to 9.'))

	# 2. all-same households
	blocks.append(('h2', f'2. When every roommate is player {f}'))
	same = ctx['same']
	if same.get(f, {}).get('n'):
		blocks.append(
			(
				'p',
				f"Player {f}'s own households (every seat player {f}) ranked against the other players' own "
				'households on the same size, setting and seed. Rank 1 is best; official score.',
			)
		)
		blocks.append(
			(
				'chart',
				chart_rank_grid(ctx),
				f"Player {f}'s average rank by budget situation and household size",
			)
		)
		ranked = sorted((p for p in players if same[p]['n']), key=lambda p: same[p]['rank'])
		blocks.append(
			(
				'table',
				['place', 'player', 'average rank', 'best in', 'settings'],
				[
					[
						ordinal(i + 1),
						f'player {p}',
						f'{same[p]["rank"]:.2f} of {same[p]["field"]:.0f}',
						f'{same[p]["best"]:.0f}%',
						same[p]['n'],
					]
					for i, p in enumerate(ranked)
				],
				{ranked.index(f)},
			)
		)
		for axis, title in (('situation', 'By budget situation'), ('n', 'By household size')):
			entries = [s for s in ctx['slices_same'] if s['axis'] == axis]
			if len(entries) < 2:
				continue
			order = SITUATIONS if axis == 'situation' else sorted({s['value'] for s in entries})
			rows = []
			for key in order:
				s = next((e for e in entries if e['value'] == key), None)
				if s:
					rows.append(
						[
							s['words'],
							f'{s["rank"]:.2f}',
							'player ' + s['leader'] if s['leader'] != f else f'player {f}',
							f'{s["leader_rank"]:.2f}',
						]
					)
			blocks.append(('h3', title))
			blocks.append(
				('table', ['', f'player {f} rank', 'best player there', 'its rank'], rows, set())
			)
	else:
		blocks.append(
			(
				'p',
				'Not measured: the grid only has all-same households of 1, 2, 9, 18 and 36 people, and at '
				'least two players need one on the same setting and seed.',
			)
		)

	# 3. safety
	blocks.append(('h2', '3. Safety'))
	blocks.append(
		(
			'p',
			"A **blowout** is a mixed game where a player's run total (no penalty) is at least 900 above its "
			"roommates' average and at least 3x it. A **sockless flip** is a household that survived before "
			'the player joined and ran out of socks after.',
		)
	)
	blow = ctx['blow']
	blocks.append(
		(
			'table',
			['player', 'mixed games', 'blowouts', 'sockless flips caused'],
			[
				[
					f'player {p}',
					f'{blow[p]["games"]:,}',
					blow[p]['count'],
					ctx['effect'][p]['flips'],
				]
				for p in players
			],
			{players.index(f)},
		)
	)
	if blow[f]['worst']:
		blocks.append(('h3', f"Player {f}'s worst games against its roommates"))
		blocks.append(
			(
				'table',
				['household and setting', f'player {f} run total', "roommates' average"],
				[
					[describe_game(e['game']), f'{e["ours"]:,.0f}', f'{e["theirs"]:,.0f}']
					for e in blow[f]['worst']
				],
				set(),
			)
		)
	flips = [s for s in ctx['mine'] if s['flip']]
	if flips:
		blocks.append(('h3', f'Households player {f} pushed into running out of socks'))
		blocks.append(
			(
				'table',
				['household and setting (with player ' + f + ')', 'replaced', 'sockless seat-days'],
				[
					[
						describe_game(s['game']),
						f'player {s["leaving"]}',
						household_sockless(s['game']),
					]
					for s in flips[:10]
				],
				set(),
			)
		)

	# appendix
	blocks.append(('appendix',))
	blocks.append(('h2', 'Appendix'))
	blocks.append(
		('h3', 'Mean official score by scenario (per person-day, every seat a player held)')
	)
	if not full:
		blocks.append(
			(
				'p',
				f"Other players' numbers here come from the households chosen to judge player {f}, so "
				'compare them with care.',
			)
		)
	for axis, keys, title in (
		('situation', SITUATIONS, 'budget situation'),
		('n', sorted({g['n'] for g in games}), 'household size'),
		('mult', sorted({g['mult'] for g in games}), 'drawer (x minimum)'),
		('years', sorted({g['years'] for g in games}), 'run length (years)'),
	):
		acc = defaultdict(lambda: defaultdict(list))
		for g in games:
			for code, emb, _ in g['seats']:
				acc[code][g[axis]].append(emb)
		shown = [k for k in keys if any(acc[p].get(k) for p in players)]
		blocks.append(
			(
				'table',
				[title] + [str(k) for k in shown],
				[
					[f'player {p}']
					+ [
						f'{statistics.fmean(acc[p][k]):.2f}' if acc[p].get(k) else '-'
						for k in shown
					]
					for p in players
				],
				{players.index(f)},
			)
		)
	blocks.append(
		(
			'h3',
			"Gap to roommates (95th percentile of run total minus roommates' average, no penalty)",
		)
	)
	blocks.append(
		(
			'table',
			['player', 'gap'],
			[[f'player {p}', f'{blow[p]["gap95"]:+,.0f}'] for p in players],
			{players.index(f)},
		)
	)
	return blocks, ctx


def compare_blocks(a_name, b_name, games_a, games_b, seat_a, seat_b):
	"""Paired comparison of two runs of the same games with different players in group 1's seats."""

	def key(g, seat):
		roster = ['@' if c == seat else c for c in g['roster']]
		return (roster_key(roster), setting_of(g))

	index_b = {key(g, seat_b): g for g in games_b if seat_b in g['roster']}
	pairs = [
		(g, index_b[key(g, seat_a)])
		for g in games_a
		if seat_a in g['roster'] and key(g, seat_a) in index_b
	]
	if not pairs:
		sys.exit(f'no shared games between {a_name} and {b_name}')
	rows = []
	for ga, gb in pairs:
		rows.append(
			dict(
				seat=seat_average(gb, seat_b, False) - seat_average(ga, seat_a, False),
				seat_official=seat_average(gb, seat_b) - seat_average(ga, seat_a),
				house=household_average(gb, False) - household_average(ga, False),
				house_official=household_average(gb) - household_average(ga),
				only_b=household_sockless(gb) > 0 and household_sockless(ga) == 0,
				only_a=household_sockless(ga) > 0 and household_sockless(gb) == 0,
				situation=ga['situation'],
				n=ga['n'],
				mult=ga['mult'],
				years=ga['years'],
			)
		)

	def stats(part):
		seat_mean, seat_se = mean_se([r['seat'] for r in part])
		house_mean, _ = mean_se([r['house'] for r in part])
		count = len(part)
		return dict(
			n=count,
			seat=seat_mean,
			seat_se=seat_se,
			house=house_mean,
			won=100 * sum(r['seat_official'] < -1e-9 for r in part) / count,
			lost=100 * sum(r['seat_official'] > 1e-9 for r in part) / count,
			house_better=100 * sum(r['house_official'] < 0 for r in part) / count,
			house_worse=100 * sum(r['house_official'] > 0 for r in part) / count,
			only_a=sum(r['only_a'] for r in part),
			only_b=sum(r['only_b'] for r in part),
			verdict=judge(seat_mean, seat_se, PRACTICAL_SCORE),
		)

	a, b = f'player {seat_a}', f'player {seat_b}'
	total = stats(rows)
	blocks = [
		('h1', f'Comparison: {b} vs {a}'),
		(
			'p',
			f'`{b_name}` vs `{a_name}`: {len(pairs):,} games played by both, with the same roommates, settings and seeds. '
			f'Negative differences mean {b} did better.',
		),
		('h2', 'Verdict'),
	]
	word = {'better': f'{b} is better', 'worse': f'{a} is better', 'about even': 'about even'}[
		total['verdict']
	]
	B, A = b.capitalize(), a.capitalize()
	items = [
		f'**Its own result: {word}.** {B} scored better than {a} in {total["won"]:.0f}% of games and worse in '
		f'{total["lost"]:.0f}% (official score); on average {signed(total["seat"])} per person-day without the penalty.',
		f'**Its household.** The household did better with {b} in {total["house_better"]:.0f}% of games and worse in '
		f'{total["house_worse"]:.0f}%; household average {signed(total["house"])} per person-day.',
		f'{"✓" if total["only_b"] <= total["only_a"] else "✗"} **Running out of socks.** Households went sockless only with '
		f'{b} in {plural(total["only_b"], "game")}, and only with {a} in {plural(total["only_a"], "game")}.',
	]
	blocks.append(('list', items))
	slices = []
	for axis in ('situation', 'n', 'mult', 'years'):
		for value in sorted({r[axis] for r in rows}, key=str):
			part = [r for r in rows if r[axis] == value]
			if len(part) >= MIN_SWAPS:
				s = stats(part)
				slices.append(dict(words=slice_words(axis, value), **s))
	slices.sort(key=lambda s: s['seat'])
	better = [s for s in slices if s['verdict'] == 'better'][:3]
	worse = [s for s in reversed(slices) if s['verdict'] == 'worse'][:3]
	lines = []
	if better:
		lines.append(
			f'**{B} is most ahead in:** '
			+ '; '.join(f'{s["words"]} ({signed(s["seat"])})' for s in better)
			+ '.'
		)
	if worse:
		lines.append(
			f'**{A} is ahead in:** '
			+ '; '.join(f'{s["words"]} ({signed(s["seat"])})' for s in worse)
			+ '.'
		)
	if not worse:
		lines.append(f'**{A} is not clearly ahead anywhere.**')
	blocks.append(('list', lines))

	chart_rows = []
	for sit in SITUATIONS:
		part = [r for r in rows if r['situation'] == sit]
		if part:
			s = stats(part)
			chart_rows.append((SITUATION_WORDS[sit], s['seat'], s['verdict'], f'{s["n"]:,} games'))
	blocks.append(
		(
			'chart',
			verdict_legend(f'{b} better', f'{a} better')
			+ chart_bars(chart_rows, f'{b} minus {a}, per person-day (left = {b} better)'),
			'Difference in its own score by budget situation',
		)
	)
	for axis, title in (
		('situation', 'By budget situation'),
		('n', 'By household size'),
		('mult', 'By drawer'),
		('years', 'By run length'),
	):
		order = SITUATIONS if axis == 'situation' else sorted({r[axis] for r in rows})
		table_rows = []
		for key_value in order:
			part = [r for r in rows if r[axis] == key_value]
			if not part:
				continue
			s = stats(part)
			table_rows.append(
				[
					slice_words(axis, key_value),
					f'{s["n"]:,}',
					f'{s["won"]:.0f}% / {s["lost"]:.0f}%',
					signed(s['seat']),
					s['verdict'],
					signed(s['house']),
					f'{s["only_b"]} / {s["only_a"]}',
				]
			)
		blocks.append(('h3', title))
		blocks.append(
			(
				'table',
				[
					'',
					'games',
					f'{b} better / worse',
					'own score change',
					'verdict',
					'household change',
					f'sockless only with {b} / {a}',
				],
				table_rows,
				set(),
			)
		)
	return blocks


# ---- report: rendering


def to_markdown(blocks):
	out = []
	for block in blocks:
		kind = block[0]
		if kind in ('h1', 'h2', 'h3'):
			out += ['#' * int(kind[1]) + ' ' + block[1], '']
		elif kind == 'p':
			out += [block[1], '']
		elif kind == 'list':
			out += [f'- {item}' for item in block[1]] + ['']
		elif kind == 'table':
			headers, rows, highlight = block[1], block[2], block[3]
			out.append('| ' + ' | '.join(headers) + ' |')
			out.append(
				'|' + '|'.join('---' if i == 0 else '---:' for i in range(len(headers))) + '|'
			)
			for i, row in enumerate(rows):
				cells = [str(c) for c in row]
				if i in highlight:
					cells = [f'**{c}**' for c in cells]
				out.append('| ' + ' | '.join(cells) + ' |')
			out.append('')
	return '\n'.join(out)


def inline_html(text):
	escaped = html.escape(text)
	escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
	escaped = re.sub(r'`(.+?)`', r'<code>\1</code>', escaped)
	escaped = escaped.replace('✓', '<span class="ok" aria-hidden="true">✓</span>')
	return escaped.replace('✗', '<span class="bad" aria-hidden="true">✗</span>')


def to_html(title, blocks):
	body = []
	in_appendix = False
	for block in blocks:
		kind = block[0]
		if kind == 'appendix':
			body.append('<details class="appendix"><summary>Appendix: more tables</summary>')
			in_appendix = True
		elif kind in ('h1', 'h2', 'h3'):
			body.append(f'<{kind}>{inline_html(block[1])}</{kind}>')
		elif kind == 'p':
			body.append(f'<p>{inline_html(block[1])}</p>')
		elif kind == 'list':
			body.append('<ul>' + ''.join(f'<li>{inline_html(i)}</li>' for i in block[1]) + '</ul>')
		elif kind == 'table':
			headers, rows, highlight = block[1], block[2], block[3]
			head = ''.join(f'<th>{html.escape(str(h))}</th>' for h in headers)
			lines = []
			for i, row in enumerate(rows):
				cls = ' class="focus"' if i in highlight else ''
				lines.append(
					f'<tr{cls}>' + ''.join(f'<td>{html.escape(str(c))}</td>' for c in row) + '</tr>'
				)
			body.append(
				f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(lines)}</tbody></table></div>'
			)
		elif kind == 'chart':
			body.append(
				f'<figure>{block[1]}<figcaption>{html.escape(block[2])}</figcaption></figure>'
			)
	if in_appendix:
		body.append('</details>')
	return HTML_PAGE.replace('{title}', html.escape(title)).replace('{body}', '\n'.join(body))


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {
	color-scheme: light;
	--page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
	--grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
	--accent: #2a78d6; --other: #898781; --better: #2a78d6; --worse: #e34948; --even: #898781;
	--good: #006300; --critical: #d03b3b; --focus-row: #eef4fc;
}
@media (prefers-color-scheme: dark) {
	:root:where(:not([data-theme="light"])) {
		color-scheme: dark;
		--page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
		--grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
		--accent: #3987e5; --other: #898781; --better: #3987e5; --worse: #e66767; --even: #898781;
		--good: #0ca30c; --critical: #d03b3b; --focus-row: #18263a;
	}
}
:root[data-theme="dark"] {
	color-scheme: dark;
	--page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
	--grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
	--accent: #3987e5; --other: #898781; --better: #3987e5; --worse: #e66767; --even: #898781;
	--good: #0ca30c; --critical: #d03b3b; --focus-row: #18263a;
}
body { margin: 0; background: var(--page); color: var(--ink);
	font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 26px; margin: 8px 0 4px; }
h2 { font-size: 20px; margin: 36px 0 8px; padding-top: 12px; border-top: 1px solid var(--grid); }
h3 { font-size: 16px; margin: 24px 0 6px; }
p, li { color: var(--ink-2); }
strong { color: var(--ink); }
ul { padding-left: 20px; }
li { margin: 6px 0; }
code { font-size: 13px; background: var(--surface); border: 1px solid var(--ring); border-radius: 4px; padding: 1px 4px; }
.ok { color: var(--good); font-weight: 700; }
.bad { color: var(--critical); font-weight: 700; }
.table-wrap { overflow-x: auto; margin: 8px 0 16px; }
table { border-collapse: collapse; background: var(--surface); border: 1px solid var(--ring); border-radius: 8px;
	font-variant-numeric: tabular-nums; font-size: 14px; min-width: 50%; }
th, td { padding: 6px 12px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; font-size: 13px; }
tr.focus td { background: var(--focus-row); color: var(--ink); font-weight: 600; }
figure { margin: 12px 0 20px; background: var(--surface); border: 1px solid var(--ring); border-radius: 8px;
	padding: 12px; overflow-x: auto; }
figcaption { color: var(--muted); font-size: 13px; margin-top: 4px; }
p.legend { font-size: 13px; margin: 0 0 6px; color: var(--ink-2); }
svg { display: block; max-width: 100%; height: auto; }
svg text { font: 12px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--ink-2); }
svg .muted { fill: var(--muted); }
svg .grid { stroke: var(--grid); stroke-width: 1; }
svg .axis { stroke: var(--axis); stroke-width: 1; }
svg .bar-focus { fill: var(--accent); } svg .bar-other { fill: var(--other); }
svg .bar-better { fill: var(--better); } svg .bar-worse { fill: var(--worse); } svg .bar-even { fill: var(--even); }
svg [data-tip] { cursor: default; outline: none; }
svg [data-tip]:hover .mark, svg [data-tip]:focus .mark { opacity: 0.8; }
svg [data-tip]:focus .hit { stroke: var(--ink); stroke-width: 1.5; }
.appendix { margin-top: 36px; }
.appendix summary { cursor: pointer; font-weight: 600; font-size: 16px; }
#tip { position: fixed; pointer-events: none; background: var(--surface); color: var(--ink); border: 1px solid var(--ring);
	border-radius: 6px; padding: 6px 10px; font-size: 13px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); max-width: 320px; }
</style>
</head>
<body>
<main>
{body}
</main>
<div id="tip" hidden></div>
<script>
const tip = document.getElementById('tip');
function place(x, y) {
	tip.style.left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8) + 'px';
	tip.style.top = (y + 14) + 'px';
}
document.querySelectorAll('[data-tip]').forEach((el) => {
	const show = () => { tip.textContent = el.getAttribute('data-tip'); tip.hidden = false; };
	el.addEventListener('pointerenter', show);
	el.addEventListener('pointermove', (e) => place(e.clientX, e.clientY));
	el.addEventListener('pointerleave', () => { tip.hidden = true; });
	el.addEventListener('focus', () => { show(); const r = el.getBoundingClientRect(); place(r.right, r.top); });
	el.addEventListener('blur', () => { tip.hidden = true; });
});
</script>
</body>
</html>
"""


# ---- report: charts (inline SVG, colors from the page's CSS tokens)


def nice_ticks(low, high, count=4):
	span = high - low or 1.0
	raw = span / count
	magnitude = 10 ** math.floor(math.log10(raw))
	step = min(
		(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), default=magnitude * 10
	)
	start = math.ceil(low / step) * step
	ticks = []
	t = start
	while t <= high + 1e-9:
		ticks.append(round(t, 10))
		t += step
	return ticks


def bar_path(x0, x1, y, h, r=4):
	"""Horizontal bar from the baseline x0 to x1 with a rounded data end and a square baseline end."""
	if abs(x1 - x0) < 0.5:
		x1 = x0 + (0.5 if x1 >= x0 else -0.5)
	r = min(r, abs(x1 - x0), h / 2)
	if x1 >= x0:
		return (
			f'M{x0:.1f},{y:.1f} H{x1 - r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} V{y + h - r:.1f} '
			f'Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z'
		)
	return (
		f'M{x0:.1f},{y:.1f} H{x1 + r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} V{y + h - r:.1f} '
		f'Q{x1:.1f},{y + h:.1f} {x1 + r:.1f},{y + h:.1f} H{x0:.1f} Z'
	)


def chart_bars(rows, axis_label):
	"""rows: (label, value, kind, extra tooltip text); kind is focus/other/better/worse/about even."""
	width, right, row_h, bar_h, top = 720, 24, 30, 16, 8
	left = max(110, int(6.8 * max(len(r[0]) for r in rows)) + 20)
	room = 48  # space inside the plot for a value label beyond the longest bar, on either side
	plot = width - left - right - 2 * room
	values = [r[1] for r in rows]
	low, high = min(0.0, min(values)), max(0.0, max(values))
	pad = (high - low) * 0.08 or 0.1
	low, high = low - (pad if low < 0 else 0), high + (pad if high > 0 else 0)
	ticks = nice_ticks(low, high)
	low, high = min(low, ticks[0]), max(high, ticks[-1])

	def x(v):
		return left + room + (v - low) / (high - low) * plot

	height = top + len(rows) * row_h + 44
	out = [
		f'<svg viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="{html.escape(axis_label)}">'
	]
	for t in ticks:
		out.append(
			f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top}" y2="{top + len(rows) * row_h}"/>'
		)
		out.append(
			f'<text class="muted" x="{x(t):.1f}" y="{top + len(rows) * row_h + 16}" text-anchor="middle">{(t or 0.0):g}</text>'
		)
	out.append(
		f'<line class="axis" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{top}" y2="{top + len(rows) * row_h}"/>'
	)
	for i, (label, value, kind, extra) in enumerate(rows):
		y = top + i * row_h + (row_h - bar_h) / 2
		cls = 'bar-' + ('even' if kind == 'about even' else kind)
		tipt = (
			f'{label}: {value:+.3f} per person-day'
			+ (f' ({kind})' if kind in ('better', 'worse', 'about even') else '')
			+ (f', {extra}' if extra else '')
		)
		anchor, tx = ('start', x(value) + 6) if value >= 0 else ('end', x(value) - 6)
		out.append(
			f'<g tabindex="0" data-tip="{html.escape(tipt)}">'
			f'<rect class="hit" x="0" y="{top + i * row_h}" width="{width}" height="{row_h}" fill="transparent"/>'
			f'<text x="{left - 10}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="end">{html.escape(label)}</text>'
			f'<path class="mark {cls}" d="{bar_path(x(0), x(value), y, bar_h)}"/>'
			f'<text x="{tx:.1f}" y="{y + bar_h / 2 + 4:.1f}" text-anchor="{anchor}">{signed(value)}</text></g>'
		)
	out.append(
		f'<text class="muted" x="{left + room + plot / 2:.1f}" y="{height - 6}" text-anchor="middle">{html.escape(axis_label)}</text>'
	)
	out.append('</svg>')
	return ''.join(out)


def verdict_legend(better, worse):
	def key(cls, text):
		return (
			f'<svg width="10" height="10" style="display:inline"><rect width="10" height="10" rx="2" '
			f'class="{cls}"/></svg> {html.escape(text)}'
		)

	return f'<p class="legend">{key("bar-better", better)} &nbsp; {key("bar-even", "about even")} &nbsp; {key("bar-worse", worse)}</p>'


def chart_effect(ctx):
	f = ctx['focus']
	if ctx['full']:
		ranked = sorted(
			(p for p in ctx['players'] if ctx['effect'][p]['n']),
			key=lambda p: ctx['effect'][p]['mean'],
		)
		rows = [
			(
				f'player {p}',
				ctx['effect'][p]['mean'],
				'focus' if p == f else 'other',
				f'{ctx["effect"][p]["n"]:,} swaps',
			)
			for p in ranked
		]
		legend = f'<p class="legend">Every player\'s average effect when it replaces a roommate; player {f} highlighted.</p>'
		return legend + chart_bars(
			rows, 'change in household average per person-day (left = household does better)'
		)
	rows = [
		(f'player {q}', v['mean'], v['verdict'], f'{v["n"]:,} swaps')
		for q, v in sorted(ctx['vs'].items(), key=lambda kv: kv[1]['mean'])
	]
	return verdict_legend(f'player {f} better', f'player {f} worse') + chart_bars(
		rows, f'change in household average per person-day (left = better with player {f})'
	)


RANK_STEPS = [  # (rank at most, sequential blue step): darker = better rank
	(1.5, '#104281'),
	(2.5, '#1c5cab'),
	(3.5, '#2a78d6'),
	(4.5, '#5598e7'),
	(6.0, '#86b6ef'),
	(99, '#b7d3f6'),
]


def chart_rank_grid(ctx):
	f = ctx['focus']
	cells = [c for c in ctx['cells'] if f in c['ranks']]
	situations = [s for s in SITUATIONS if any(c['situation'] == s for c in cells)]
	sizes = sorted({c['n'] for c in cells})
	width, left, top, cell_w, cell_h = 720, 250, 28, 88, 34
	width = max(width, left + cell_w * len(sizes) + 20)
	height = top + cell_h * len(situations) + 64
	field = statistics.fmean(len(c['ranks']) for c in cells)
	out = [
		f'<svg viewBox="0 0 {width} {height}" width="{width}" role="img" aria-label="Player {f} rank grid">'
	]
	for j, n in enumerate(sizes):
		out.append(
			f'<text class="muted" x="{left + j * cell_w + cell_w / 2:.1f}" y="{top - 10}" text-anchor="middle">'
			f'{"alone" if n == 1 else f"{n} people"}</text>'
		)
	for i, sit in enumerate(situations):
		y = top + i * cell_h
		out.append(
			f'<text x="{left - 10}" y="{y + cell_h / 2 + 4:.1f}" text-anchor="end">{html.escape(SITUATION_WORDS[sit])}</text>'
		)
		for j, n in enumerate(sizes):
			x = left + j * cell_w
			part = [c['ranks'][f] for c in cells if c['situation'] == sit and c['n'] == n]
			if not part:
				out.append(
					f'<rect x="{x + 1}" y="{y + 1}" width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="none" '
					f'class="grid"/><text class="muted" x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 4:.1f}" '
					'text-anchor="middle">–</text>'
				)
				continue
			rank = statistics.fmean(part)
			color = next(c for limit, c in RANK_STEPS if rank <= limit)
			ink = '#ffffff' if rank <= 3.5 else '#0b0b0b'
			tipt = f'{SITUATION_WORDS[sit]}, {"living alone" if n == 1 else f"households of {n}"}: average rank {rank:.1f} ({len(part)} settings)'
			out.append(
				f'<g tabindex="0" data-tip="{html.escape(tipt)}"><rect class="mark hit" x="{x + 1}" y="{y + 1}" '
				f'width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="{color}"/>'
				f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 4:.1f}" text-anchor="middle" style="fill:{ink}">'
				f'{rank:.1f}</text></g>'
			)
	ly = top + cell_h * len(situations) + 22
	out.append(f'<text class="muted" x="20" y="{ly + 9}">rank (of {field:.0f}):</text>')
	labels = ['1-1.5 best', '1.5-2.5', '2.5-3.5', '3.5-4.5', '4.5-6', 'over 6']
	for k, ((_, color), text) in enumerate(zip(RANK_STEPS, labels, strict=True)):
		lx = 110 + k * 88
		out.append(
			f'<rect x="{lx}" y="{ly}" width="12" height="12" rx="2" fill="{color}"/>'
			f'<text class="muted" x="{lx + 16}" y="{ly + 10}">{text}</text>'
		)
	out.append('</svg>')
	return ''.join(out)


# ---- report: commands


def load_meta(name):
	path = os.path.join(run_dir(name), 'run.json')
	if os.path.exists(path):
		with open(path) as fh:
			return json.load(fh)
	return {}


def load_run(name):
	games = list(load_games(name).values())
	if not games:
		sys.exit(f'no results in {run_dir(name)}')
	for g in games:
		g['situation'] = situation(g['n'], g['capacity'], g['days'], g['budget'])
	return games


def save_report(folder, title, blocks, open_browser):
	os.makedirs(folder, exist_ok=True)
	markdown = to_markdown(blocks)
	md_path = os.path.join(folder, 'report.md')
	html_path = os.path.join(folder, 'report.html')
	with open(md_path, 'w') as fh:
		fh.write(markdown + '\n')
	with open(html_path, 'w') as fh:
		fh.write(to_html(title, blocks))
	# The terminal shows the verdict; the files hold everything.
	end = next(
		(
			i
			for i, b in enumerate(blocks)
			if b[0] in ('chart', 'h3', 'appendix')
			or (b[0] == 'h2' and not b[1].startswith('Verdict'))
		),
		len(blocks),
	)
	print('\n' + to_markdown(blocks[:end]))
	print(f'full report: {os.path.relpath(md_path, HERE)} and {os.path.relpath(html_path, HERE)}')
	if open_browser:
		webbrowser.open('file://' + os.path.abspath(html_path))


def write_report(name, focus=None, open_browser=True):
	games = load_run(name)
	meta = load_meta(name)
	focus = focus or meta.get('seat') or '1'
	blocks, _ = report_blocks(name, games, meta, focus)
	save_report(run_dir(name), f'Player {focus}: {name}', blocks, open_browser)


def latest_run():
	runs = (
		[d for d in os.listdir(RESULTS) if os.path.exists(os.path.join(RESULTS, d, 'games.jsonl'))]
		if os.path.isdir(RESULTS)
		else []
	)
	if not runs:
		sys.exit('no saved runs yet; start one with: uv run tourney.py run')
	return max(runs, key=lambda d: os.path.getmtime(os.path.join(RESULTS, d, 'games.jsonl')))


def cmd_report(args):
	write_report(args.name or latest_run(), focus=args.focus, open_browser=not args.no_open)


def cmd_compare(args):
	a, b = args.first, args.second
	seat_a, seat_b = load_meta(a).get('seat', '1'), load_meta(b).get('seat', '1')
	if seat_a == seat_b:
		sys.exit(
			f"{a} and {b} both have player {seat_a} in group 1's seats; compare runs of different versions"
		)
	blocks = compare_blocks(a, b, load_run(a), load_run(b), seat_a, seat_b)
	save_report(
		os.path.join(RESULTS, f'compare_{b}_vs_{a}'),
		f'Player {seat_b} vs player {seat_a}',
		blocks,
		not args.no_open,
	)


# ---- command line


def main():
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	sub = parser.add_subparsers(dest='command', required=True)

	def selection_flags(p):
		p.add_argument(
			'--as',
			dest='as_player',
			metavar='PLAYER',
			help="put another version of player 1 (e.g. 11 or 12) in group 1's seats",
		)
		p.add_argument(
			'--full', action='store_true', help="the whole tournament, not only player 1's games"
		)
		p.add_argument('--scenario', help='comma-separated presets: ' + ', '.join(SCENARIOS))
		p.add_argument('--budgets', help='e.g. 0,150,unlimited')
		p.add_argument('--sizes', help='household sizes, e.g. 1,2,9')
		p.add_argument('--drawers', help='drawer multiples of the minimum, e.g. 1,10')
		p.add_argument('--years', help='run lengths in years, e.g. 1,10')
		p.add_argument('--situations', help='budget situations: ' + ', '.join(SITUATIONS))
		p.add_argument('--seeds', help='comma-separated seeds (default 4401,4402,4403)')
		p.add_argument(
			'--five-swaps',
			action='store_true',
			help='also run the 5-person households without player 1, for 5-person household value',
		)
		p.add_argument('--name', help='results folder name (default derived from the options)')
		p.add_argument('--workers', type=int, help='parallel games (default: CPU cores - 1)')
		p.add_argument(
			'--no-open',
			action='store_true',
			help="don't open the HTML report when the run finishes",
		)

	for name, fn, text in (
		('run', cmd_run, "run player 1's games, or --full (resumable), then write the report"),
		('eta', cmd_eta, 'estimate how long a selection takes, without running it'),
		('scenarios', cmd_scenarios, 'list scenario presets with ETAs for player 1'),
	):
		p = sub.add_parser(name, help=text)
		selection_flags(p)
		p.set_defaults(fn=fn)
	p = sub.add_parser('report', help='show the report for a saved run again (default: the latest)')
	p.add_argument(
		'name', nargs='?', help='results folder name under tourney_results/ (default: latest run)'
	)
	p.add_argument(
		'--focus', help="player to report on in detail (default: whoever played group 1's seats)"
	)
	p.add_argument('--no-open', action='store_true', help="don't open the HTML report")
	p.set_defaults(fn=cmd_report)
	p = sub.add_parser('compare', help='compare two runs of the same games, e.g. p1 and p12')
	p.add_argument('first', help='results folder of the first run, e.g. p1')
	p.add_argument('second', help='results folder of the second run, e.g. p12')
	p.add_argument('--no-open', action='store_true', help="don't open the HTML report")
	p.set_defaults(fn=cmd_compare)
	p = sub.add_parser('bench', help='re-measure how fast each player decides (for the ETAs)')
	p.add_argument('--workers', type=int)
	p.set_defaults(fn=cmd_bench)

	args = parser.parse_args()
	args.fn(args)


if __name__ == '__main__':
	main()
