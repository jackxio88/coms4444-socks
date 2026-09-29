"""Group 1's experimental player: player_1 plus a handful of budget and pairing rules.

Cluster is the original player_1, split into small hooks. Player11 overrides those hooks.

What Player11 adds:

- Budget plan. On day 1 the budget is compared with what holes alone will cost.
    tight: spend like player_1.
    open:  plan for the money to run out a little before the end, since the drawer can last
           a while without it.
    rich:  start discarding on day 1.
- Capped discards. Unless money is tight, throw out leftover socks at their cap (127 or 64), so
  their replacements age together and keep matching. Stop if a roommate is spending early.
- Colour thresholds. Keep black socks longer than white ones: a black's shade changes half as
  fast, so its mismatches cost half as much.
- Catch-up pairing. Before the household's first purchase, prefer pairs that even out wear.
- Money-out safety. No discards under $10, a fair share of discards when the money can't
  replace everyone's, and the least risky pair once socks run short.
"""

import math
from collections import deque

from models.player import Player as BasePlayer
from models.player import Selection

WHITE_FLOOR = 127
BLACK_CEILING = 64
PACK_COST = 10.0
HOLE_P = 0.25


def wears(shade: int) -> float:
	"""How many times a sock has been worn, read from its shade."""
	return (255 - shade) / 2 if shade > 64 else float(shade)


def is_white(shade: int) -> bool:
	return shade > 64


def worn_out(shade: int) -> bool:
	"""True for a sock at its cap: every further wear risks a hole."""
	return shade in (WHITE_FLOOR, BLACK_CEILING)


def pair_cost(a: int, b: int) -> float:
	"""Embarrassment of wearing two socks together; a gap of 6 or less is free."""
	d = abs(a - b)
	return float(d) if d > 6 else 0.0


class Cluster(BasePlayer):
	"""The original player_1, split into hooks that Player11 overrides."""

	def __init__(self, snapshot, ctx) -> None:
		super().__init__(snapshot, ctx)
		self.days_seen = 0
		self.total_budget = None
		self.setup()

	# Hooks

	def setup(self) -> None:
		pass

	def first_day(self, turn) -> None:
		pass

	def observe(self, offered, turn) -> None:
		pass

	def after(self, offered, selection, turn) -> None:
		pass

	def early_phase(self, turn) -> bool:
		return self.total_budget == turn.budget_remaining

	def early_pair(self, by_shade, wear_scores, turn):
		return self.well_clustered_pair(by_shade, wear_scores)

	def early_discard(self, offered, pair, wear_scores, turn):
		return ()

	def main_pair(self, by_shade, wear_scores, turn):
		return self.select_pair(by_shade, wear_scores)

	def main_discard(self, offered, pair, wear_scores, turn):
		threshold = self.choose_discard_threshold(turn)
		# A black qualifies when shade >= threshold, a white when shade <= 255 - 2 * threshold.
		return tuple(
			c
			for c in range(len(offered))
			if c not in pair and offered[c] >= threshold and offered[c] <= (255 - threshold * 2)
		)

	# The daily decision

	def select_socks(self, offered, turn):
		if self.days_seen == 0:
			self.total_budget = turn.budget_remaining
			self.first_day(turn)
		self.days_seen += 1
		self.observe(offered, turn)

		by_shade = sorted((sock, i) for i, sock in enumerate(offered))
		wear_scores = [wears(sock) for sock in offered]

		if self.early_phase(turn):
			pair = self.early_pair(by_shade, wear_scores, turn)
			discard = self.early_discard(offered, pair, wear_scores, turn)
		else:
			pair = self.main_pair(by_shade, wear_scores, turn)
			discard = self.main_discard(offered, pair, wear_scores, turn)

		selection = Selection(wear=tuple(pair), discard=tuple(discard))
		self.after(offered, selection, turn)
		return selection

	# player_1's pairing and discard threshold

	@staticmethod
	def _wears(shade: int) -> float:
		return wears(shade)

	def select_pair(self, by_shade, wear_scores):
		"""The newest free pair if there is one; otherwise the pair closest in shade."""
		pair = (by_shade[0][1], by_shade[1][1])
		best_diff = by_shade[1][0] - by_shade[0][0]
		pair_wear_score = wear_scores[pair[0]] + wear_scores[pair[1]]
		for (left, left_i), (right, right_i) in zip(by_shade, by_shade[1:], strict=False):
			diff = right - left
			current = wear_scores[left_i] + wear_scores[right_i]
			if best_diff <= 6 and diff <= 6 or best_diff == diff:
				if current < pair_wear_score:
					best_diff = diff
					pair = (left_i, right_i)
					pair_wear_score = current
			elif diff < best_diff:
				best_diff = diff
				pair = (left_i, right_i)
				pair_wear_score = current
		return pair

	def well_clustered_pair(self, by_shade, wear_scores):
		"""Picks between the two darkest and the two lightest socks."""
		(darkest, darkest_i), (dark_next, dark_next_i) = by_shade[0], by_shade[1]
		(light_next, light_next_i), (lightest, lightest_i) = by_shade[-2], by_shade[-1]
		dark_pair = (darkest_i, dark_next_i)
		light_pair = (light_next_i, lightest_i)
		dark_diff = dark_next - darkest
		light_diff = lightest - light_next
		dark_free = dark_diff <= 6
		light_free = light_diff <= 6
		if dark_free and light_free:
			return dark_pair if wear_scores[darkest_i] <= wear_scores[lightest_i] else light_pair
		if dark_free:
			return dark_pair
		if light_free:
			return light_pair
		return dark_pair if dark_diff <= light_diff else light_pair

	def choose_discard_threshold(self, turn) -> float:
		days_left = float(self.days - turn.day)
		threshold = (
			5.0 * self.roommates * days_left / turn.budget_remaining
			if turn.budget_remaining > 0
			else 65
		)
		return threshold if threshold > 6 else 6


INF = float('inf')
SOCKLESS = 65536.0  # penalty for a day without a pair


def forced_spend(n: int, C: int, days: float) -> float:
	"""Dollars that holes alone will cost over the run if nobody discards early.

	A fit to simulations: nothing holes until the socks have worn out once, then they hole
	at a steady rate.
	"""
	w = 2.0 * n * days
	if w < 63.0 * C:
		return 0.0
	return max(0.0, (10.0 / 6.0) * (w / 69.0 - 0.38 * C) - 8.0)


def flush_excess(n: int, days: float) -> float:
	"""Extra dollars that capped discards cost over the run, on top of forced_spend."""
	return (10.0 / 6.0) * 2.0 * n * days * (1.0 / 64.5 - 1.0 / 68.0)


def wave_excess(n: int, days: float) -> float:
	"""Extra dollars that the wave costs over the run, on top of forced_spend."""
	return (10.0 / 6.0) * 2.0 * n * days * (1.0 / 61.0 - 1.0 / 68.0)


class Player11(Cluster):
	"""Group 1's player. The module docstring lists what it adds to player_1."""

	SURVIVE = True  # least risky pairing once broke and short of socks
	WAVE = True  # also discard 60+ wear socks while a colour is mostly worn
	CATCHUP_K = 10.0  # most we pay to even out wear early on; None uses player_1's pairing
	BLACK_SLOPE = 1.5  # how much longer blacks are kept than whites; 1.0 treats them the same

	# State

	def setup(self) -> None:
		self.recent = deque(maxlen=30 * self.selection_unit)  # wears seen in our last 30 hands
		self.near = deque(maxlen=15 * self.selection_unit)  # (white?, 60+ wears?) for 15 hands
		self.first_buy = None  # first day the household spent money
		self.last_total = 0.0
		self.crisis = False  # broke and short of socks
		self.regime = 'open'
		self.brake = 0.8  # see burner()
		self.level = False  # use player_1's early pairing
		self.wave_ok = self.WAVE
		self.long_run = False

	def first_day(self, turn) -> None:
		"""Rate the budget and set everything that depends only on day 1."""
		n, C, days = self.roommates, self.capacity, self.days
		b = turn.budget_remaining
		if b != INF:
			b = math.floor(b / PACK_COST) * PACK_COST  # only whole packs can be bought
		per_rd = b / (n * days)  # dollars per roommate-day
		t0frac = 32.0 * C / (n * days)  # share of the run before the first socks wear out
		self.spare = b - forced_spend(n, C, days)  # money left after paying for holes
		margin = self.spare - flush_excess(n, days)  # ... and after capped discards

		# Tight means the money can't cover holes and capped discards. Households of 8+ also keep a
		# spare pack: with so many people wearing socks, the drawer runs short soon after the money
		# is gone.
		tight_margin = PACK_COST if n >= 8 else -7.0
		if per_rd >= 0.35 or (t0frac >= 0.5 and per_rd >= 0.12):
			self.regime = 'rich'
		elif self.spare < 0 or margin < tight_margin:
			self.regime = 'tight'
		self.floors = (2.0, 4.0) if per_rd >= 1.0 else (6.0, 6.0)  # fewest wears before a discard

		# When money is thin, count a later first purchase as early spending too.
		self.brake = 0.9 if margin < 0 else 0.8
		# If the budget can't cover holes, player_1's pairing delays them the longest.
		self.level = (b < PACK_COST and forced_spend(n, C, days) > 0) or self.spare < -10.0
		# On long runs even a slow early spender drains the money, so look harder.
		self.long_run = 2.0 * n * days > 12000.0
		if self.long_run and n >= 3:
			self.brake = 0.9
		# The wave only runs if the spare money also covers its extra cost.
		self.wave_ok = self.WAVE and self.spare >= wave_excess(n, days)

	def observe(self, offered, turn) -> None:
		for s in offered:
			w = (255 - s) / 2 if s > 64 else float(s)
			self.recent.append(w)
			self.near.append((s > 64, w >= 60))
		if self.first_buy is None and turn.total_spent > 0:
			self.first_buy = turn.day
		# Broke, and handed too few socks or sockless since our last turn: from now on, pair to
		# avoid holes.
		if (
			self.SURVIVE
			and turn.budget_remaining < PACK_COST
			and (
				len(offered) < self.selection_unit
				or turn.total_embarrassment - self.last_total >= SOCKLESS
			)
		):
			self.crisis = True
		self.last_total = turn.total_embarrassment

	# Budget plan

	def early_phase(self, turn) -> bool:
		"""player_1's no-discard phase before the first purchase; rich budgets skip it."""
		return self.regime != 'rich' and self.total_budget == turn.budget_remaining

	def calculate_min_runaway_days(self) -> float:
		"""How many days the drawer lasts once the money runs out.

		Open budgets are paced to run out this many days before the end. The estimate is
		calibrated and kept pessimistic, because running out too early costs far more than it saves.
		"""
		n = self.roommates
		m = sum(max(0.0, 64.0 - w) for w in self.recent) / len(self.recent) if self.recent else 64.0
		drawer = self.capacity - 10
		floor = (n - 1) * self.selection_unit + 2  # fewest socks that still give everyone a pair
		return 0.9 * (drawer * m * m / 64.0 + 4 * max(1, drawer - floor + 1)) / (2 * n)

	def pace(self, turn) -> float:
		"""Wears before a discard: the more money per remaining day, the sooner socks go."""
		b = turn.budget_remaining
		if b == INF:
			return 0.0
		left = (
			self.days
			- turn.day
			- (self.calculate_min_runaway_days() if self.regime != 'tight' else 0.0)
		)
		return 5.0 * self.roommates * left / b

	# Pairing

	def survival_pair(self, by_shade, wear_scores):
		"""Fewest capped socks first (each wear risks a hole), then cheapest, then freshest."""
		best = None
		for a in range(len(by_shade)):
			sa, ia = by_shade[a]
			for b in range(a + 1, len(by_shade)):
				sb, ib = by_shade[b]
				key = (
					worn_out(sa) + worn_out(sb),
					pair_cost(sa, sb),
					wear_scores[ia] + wear_scores[ib],
				)
				if best is None or key < best[0]:
					best = (key, (ia, ib))
		return best[1]

	def main_pair(self, by_shade, wear_scores, turn):
		if self.crisis:
			return self.survival_pair(by_shade, wear_scores)
		return self.select_pair(by_shade, wear_scores)

	def early_pair(self, by_shade, wear_scores, turn):
		"""Catch-up pairing: wear the freshest socks when it's cheap, so wear stays even."""
		if self.crisis:
			return self.survival_pair(by_shade, wear_scores)
		if self.CATCHUP_K is None or self.level:
			return self.well_clustered_pair(by_shade, wear_scores)
		shade = [0] * len(by_shade)
		for s, i in by_shade:
			shade[i] = s
		idx = range(len(shade))

		# Each colour's two freshest socks: (cost, wears of the freshest, the pair).
		fresh = []
		for white in (False, True):
			col = sorted(
				(i for i in idx if is_white(shade[i]) == white), key=lambda i: (wear_scores[i], i)
			)
			if len(col) >= 2:
				fresh.append(
					(pair_cost(shade[col[0]], shade[col[1]]), wear_scores[col[0]], (col[0], col[1]))
				)
		free = [f for f in fresh if f[0] == 0]
		if free:
			return min(free, key=lambda f: f[1])[2]

		pairs = [(i, j) for i in idx for j in idx if i < j]
		newest_free = min(
			(p for p in pairs if abs(shade[p[0]] - shade[p[1]]) <= 6),
			key=lambda p: (wear_scores[p[0]] + wear_scores[p[1]], p),
			default=None,
		)
		if newest_free is None:  # nothing is free: wear the cheapest pair
			return min(
				pairs,
				key=lambda p: (
					pair_cost(shade[p[0]], shade[p[1]]),
					wear_scores[p[0]] + wear_scores[p[1]],
					p,
				),
			)
		# Pay a little to wear the freshest pair, otherwise wear the newest free pair.
		catch = min(fresh, key=lambda f: (f[0], f[1]))
		return catch[2] if catch[0] <= self.CATCHUP_K else newest_free

	# Discards

	def burner(self) -> bool:
		"""True if a roommate spends early and our spare money is thin.

		Under even wear the first holes come around day 32C / n, so a household purchase well
		before that means someone discards voluntarily.
		"""
		return (
			self.first_buy is not None
			and self.spare < (150 if self.long_run else 40)
			and self.first_buy < self.brake * 32.0 * self.capacity / self.roommates
		)

	def main_discard(self, offered, pair, wear_scores, turn):
		b = turn.budget_remaining
		if b < PACK_COST:  # no pack can replace it, so a discard is just a lost sock
			return ()

		# Blacks are kept longer: their mismatches cost half as much.
		t = self.pace(turn)
		fw, fb = self.floors
		tw = max(fw, t)
		tb = max(fb, min(64.0, 6.0 + self.BLACK_SLOPE * (t - 6.0))) if t <= 64 else 65.0

		caps = self.regime != 'tight' and b >= 20 and not self.burner()
		# Wave: while most of a colour's recent socks have 60+ wears, discard those too.
		wave = {True: False, False: False}
		if self.wave_ok and caps and self.spare >= 20:
			for white in (True, False):
				xs = [near for c, near in self.near if c == white]
				wave[white] = len(xs) >= 10 and 2 * sum(xs) >= len(xs)

		out = []
		for i, s in enumerate(offered):
			if i in pair:
				continue
			w, white = wear_scores[i], s > 64
			if w >= (tw if white else tb) or (caps and worn_out(s)) or (wave[white] and w >= 60):
				out.append(i)

		# Dump quota: when the money can't replace a full day of household discards, keep to our
		# share of what it can replace, most-worn first. The remainder rotates between roommates by
		# day, so the shares never all round down to zero.
		n, u = self.roommates, self.selection_unit
		if out and b != INF and b < PACK_COST * (1 + math.ceil(n * (u - 2) / 6.0)):
			share = int(b // PACK_COST) * 6
			quota = share // n + (1 if (self.index + turn.day) % n < share % n else 0)
			out = sorted(out, key=lambda i: -wear_scores[i])[:quota]
		return tuple(out)
