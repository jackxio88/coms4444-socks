"""Starting point for a group's player.

Copy this whole directory to ``players/player_<k>/`` using your group number,
then rename the class to ``Player<k>``. Group 4 would end up with
``players/player_4/player.py`` containing ``class Player4``. The registry looks
for exactly that; nothing else needs editing.

Keep the ``__init__.py``. Discovery uses ``pkgutil.iter_modules``, which only
reports directories that have one, so a group directory without it is silently
invisible to the simulator - no error, just a player that never turns up.

This directory is not itself discovered - the registry only matches
``player_<digits>`` - so the template can never appear in a run as a competitor.
"""

from itertools import combinations
import math

from models.player import GameContext, PlayerSnapshot, Selection, TurnContext
from models.player import Player as BasePlayer
from collections import deque
import numpy as np 
import random

class Player1(BasePlayer):
	MIN_THRESHOLD = 6
	MAX_THRESHOLD = 65
	# use the average of last N calculated threshold as current "smoother" threshold
	# min is 1, 
	THRESHOLD_AVG_N = 50
	DISCARD_PROBABILITY = 0.40
	SEED = 4444 

	"""Rename me to Player<k>, where <k> is your group number."""

	def __init__(self, snapshot: PlayerSnapshot, ctx: GameContext) -> None:
		super().__init__(snapshot, ctx)

		# super() has already set these from ctx and snapshot:
		#
		#   self.index           which roommate you are (0-based)
		#   self.id              your UUID, stable for the whole simulation
		#   self.capacity        C, the drawer size at the start
		#   self.roommates       n, how many of you share the drawer
		#   self.selection_unit  how many socks you are handed each day
		#   self.days            how long the simulation runs
		#
		# The engine constructs you once, before day 1, and it constructs you
		# itself - you cannot preload state into an already-built object. Anything
		# you want to carry between days lives on self, so initialise it here.
		self.days_seen = 0
		self.threshold_history = deque()
		self.count_discarded_over_threshold = 0
		self.sum_discarded_over_threshold = 0
		self.dicsard_probability = 0.4

		random.seed(self.SEED)


	def select_socks(self, offered: tuple[int, ...], turn: TurnContext) -> Selection:
		"""Choose two socks to wear, and decide the fate of the rest.

		Called once per day, in an order that is reshuffled daily. Everything you
		are allowed to know is in the two arguments.

		``offered`` is a tuple of ``selection_unit`` shade values, 0-255.

		WHAT YOU CAN SEE

			offered[i]                  the shade of the i-th sock on offer
			turn.day                    today's day number, 1-based
			turn.total_spent            dollars spent by the household so far
			turn.embarrassment_history  your own daily scores, one per day
			turn.total_embarrassment    the sum of that history
			self.capacity / self.roommates / self.selection_unit / self.days

		WHAT YOU CANNOT SEE

			- Which sock is which. Indices are positions in THIS tuple only. The
				same index tomorrow is a different sock, so you cannot track an
				individual sock across turns or build up a map of the drawer.
			- Anyone else's socks, choices or embarrassment.
			- The shade distribution left in the drawer.
			- How many socks have been discarded, or how close the household is to
				the next six-pack. You see total_spent only, after the fact.

		With n == 1 you are alone with the drawer, so tracking its full state IS
		possible. That is intentional, not a leak - it is what makes the pooled
		versus separate comparison in goal 3 meaningful.

		WHAT THE SHADES MEAN

		White socks start at 255 and fade by 2 per wear, stopping at 127. Black
		socks start at 0 and rise by 1 per wear, stopping at 64. The two ranges
		never overlap, so a shade above 64 is a white sock and a shade at or below
		64 is a black one. Inferring colour from shade is fair game.

		Wearing a pair whose shades differ by MORE than 6 costs you that
		difference. A difference of exactly 6 is free.

		A sock already at 127 or 64 when you are handed it has a 25% chance of
		developing a hole when worn, and is thrown out immediately. Six discards
		of one colour buy a fresh six-pack for $10, and the surplus carries over.

		RETURNING A DECISION

			wear     exactly two distinct indices into ``offered``
			discard  any subset of the REMAINING indices, possibly empty

		Anything you neither wear nor discard goes back in the drawer unworn and
		keeps its shade. Only worn socks age.

		IF YOU GET IT WRONG

		An invalid selection, an exception, or taking longer than the --timeout
		budget forfeits your turn: the engine wears the first two socks and
		discards nothing. It is recorded as a fault and shown in the results, so a
		forfeit is visible rather than silent. Your failure never affects the
		other groups.
		"""
		if self.days_seen == 0:
			self.total_budget = turn.budget_remaining
		self.days_seen += 1

		# if offered = [0, 1, 255, 253] 
		# then wear_scores = [0, 1, 0, 1]
		# by_shade = [(0, 0), (1, 1), (253, 3), (255, 2)]
		# and selected_pair = (0, 1) 
		# because the first two socks are closest in shade and have the lowest wear scores
		# although this also means black socks are preferred over white socks due to less color difference despite the same wear scores

		by_shade = sorted((sock, i) for i, sock in enumerate(offered))
		wear_scores = [self._wears(sock) for sock in offered]

		if self.is_well_clustered(turn):
			return self.well_clustered_selection(by_shade, wear_scores, turn)

		selected_pair = self.select_pair(by_shade, wear_scores)

		# modulized discard logic
		discard_method = self.choose_discard_probablistic
		discard = discard_method(offered, turn, selected_pair)
		
		return Selection(wear=selected_pair, discard=tuple(discard))

	@staticmethod
	def _wears(shade: int) -> float:
		"""Return the number of wears a sock has seen, as a float."""
		return (255 - shade) / 2 if shade > 64 else float(shade)

	def is_well_clustered(self, turn: TurnContext) -> bool:
		return self.total_budget == turn.budget_remaining

	def select_pair(self, by_shade: list[tuple[int, int]], wear_scores: list[float]) -> tuple[int, int]:
		pair = (by_shade[0][1], by_shade[1][1])
		best_diff = by_shade[1][0] - by_shade[0][0]
		pair_wear_score = wear_scores[pair[0]] + wear_scores[pair[1]]

		for (left, left_i), (right, right_i) in zip(by_shade, by_shade[1:], strict=False):
			diff = right - left
			current_pair_wear_score = wear_scores[left_i] + wear_scores[right_i]

			# wear score as tiebreaker
			# if there's no ties choose least embarassment
			# if there's a tie choose least wear score (newest socks)
			if best_diff <= 6 and diff <= 6 or best_diff == diff:
				if current_pair_wear_score < pair_wear_score:
					best_diff = diff
					pair = (left_i, right_i)
					pair_wear_score = current_pair_wear_score
			elif diff < best_diff:
				best_diff = diff
				pair = (left_i, right_i)
				pair_wear_score = current_pair_wear_score

		return pair

	def well_clustered_selection(self, by_shade: list[tuple[int, int]], wear_scores: list[float], turn: TurnContext) -> Selection:
		(darkest, darkest_i), (dark_next, dark_next_i) = by_shade[0], by_shade[1]
		(light_next, light_next_i), (lightest, lightest_i) = by_shade[-2], by_shade[-1]

		dark_pair = (darkest_i, dark_next_i)
		light_pair = (light_next_i, lightest_i)
		dark_diff = dark_next - darkest
		light_diff = lightest - light_next
		dark_free = dark_diff <= 6
		light_free = light_diff <= 6

		if dark_free and light_free:
			pair = dark_pair if wear_scores[darkest_i] <= wear_scores[lightest_i] else light_pair
		elif dark_free:
			pair = dark_pair
		elif light_free:
			pair = light_pair
		else:
			pair = dark_pair if dark_diff <= light_diff else light_pair

		return Selection(wear=pair)

	def calculate_discard_threshold(self, turn: TurnContext) -> float:
		# raw current threshold using remaining day and budget
		days_left = float(self.days - turn.day)
		raw_threshold = (
			10.0 * self.roommates * days_left / (3 * turn.budget_remaining)
			if turn.budget_remaining > 0
			else self.MAX_THRESHOLD
		)
		raw_threshold = min(raw_threshold, self.MAX_THRESHOLD)

		# smoother threshold 
		if len(self.threshold_history) >= self.THRESHOLD_AVG_N:
			self.threshold_history.popleft()
		self.threshold_history.append(raw_threshold)
		smooth_threshold = np.mean(np.array(self.threshold_history))
			
		return max(smooth_threshold, self.MIN_THRESHOLD)

	# discard sock over thresohld 
	# with a probability 
	def choose_discard_probablistic(
			self, 
			offered: tuple[int, ...], 
			turn: TurnContext, 
			selected_pair: tuple[int, ...]) -> tuple[int, ...]:
		if self.is_well_clustered(turn):
			return tuple([])

		threshold = self.calculate_discard_threshold(turn)
		discard = []
		for c in range(len(offered)):
			if c not in selected_pair \
				and offered[c] >= threshold \
				and offered[c] <= (255 - threshold * 2) \
				and random.random() < self.dicsard_probability:
				discard.append(c)
		return tuple(discard)


	# calculate probability of discarding based on how old the sock is 
	# the older the sock is (over threshold), the larger the probability of getting discarded
	def choose_discard_dynamic_probablistic(
			self, 
			offered: tuple[int, ...], 
			turn: TurnContext, 
			selected_pair: tuple[int, ...]) -> tuple[int, ...]:
		if self.is_well_clustered(turn):
			return tuple([])

		
		discard = []
		threshold = self.calculate_discard_threshold(turn)
		
		for c in range(len(offered)):
			if c not in selected_pair:
				days_worn = self.get_days_worn(offered[c])
				discard_probability_method = self.get_discard_probability_linear
				discard_probability =  discard_probability_method(days_worn, threshold)
				if random.random() < discard_probability:
					discard.append(c)
		return tuple(discard)

	@staticmethod
	def get_discard_probability_linear(
		days_worn: float,
		threshold: float, 
		base_probability: float = 0.4,
		N: int =  3) -> float:
			
		# Linear equation centered at threshold
		p = 0.5 + (days_worn - threshold) / (2 * N)
		
		# Clamp value between 0.0 and 1.0
		return max(0.0, min(1.0, p))


	@staticmethod
	def get_discard_probability_sigmoid(
		days_worn: int, 
		threshold: float,
		base_probability: float = 0.4,
		k: float = 0.5) -> float:
		
		offset = math.log(base_probability / (1 - base_probability))
		exponent = - k * (days_worn - threshold) - offset
		probability = 1 / (1 + math.exp(exponent))
		
		return probability

	@staticmethod
	def get_days_worn(shade: int) -> int:
		if shade < 65:
			return shade
		else:
			return int( (256 - shade) / 2)


	# this method doesn't seem to provide definite benefit
	@DeprecationWarning
	def _choose_discard_probablistic_improved(
			self, 
			offered: tuple[int, ...], 
			turn: TurnContext, 
			selected_pair: tuple[int, ...]) -> tuple[int, ...]:
		self.DISCARD_PROBABILITY = 0.4
		
		if self.is_well_clustered(turn):
			return tuple([])

		threshold = self.calculate_discard_threshold(turn)
		discard = []
		for c in range(len(offered)):
			if c not in selected_pair:
				offset_from_threshold = self.get_days_worn(offered[c]) - threshold
				# old socks has a chance of being discarded 
				if offered[c] >= threshold \
					and offered[c] <= (255 - threshold * 2):
						if random.random() < self.DISCARD_PROBABILITY:
							discard.append(c)
							self.sum_discarded_over_threshold += offset_from_threshold # this is positive
							self.count_discarded_over_threshold += 1
				# if discarding socks not reaching threshold restores balance, also discard with a chance
				else:
					# this should be negative since this is an under-threshold sock
					average_over_threshold = self.sum_discarded_over_threshold / self.count_discarded_over_threshold \
						if self.count_discarded_over_threshold > 0 \
						else 0
					if abs(offset_from_threshold) <= average_over_threshold:
						if random.random() < self.DISCARD_PROBABILITY:
							discard.append(c)
							self.sum_discarded_over_threshold += offset_from_threshold # this is negative
							self.count_discarded_over_threshold -= 1
		return tuple(discard)
