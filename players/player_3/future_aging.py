"""Future mismatch with equal expected aging for the candidate and observed drawer.

One future candidate wear advances the observed partner by one expected wash.
This is a uniform-wear approximation; the geometric replacement target stays
stationary. Individual holes, selective wearing and pack arrivals are not simulated.
"""

from functools import lru_cache
from math import ceil

import numpy as np

# Speed-optimised for group 1's internal tournament runs (test-tourney-branch only).
# project_life_values below computes every projection step at once with numpy instead of
# 64 Python passes, and the replacement-target costs are cached. Every sum keeps the original
# left-to-right order (cumsum is sequential), so the returned values are bit-identical to the
# original implementation.
_AGES = np.arange(65, dtype=np.float64)
_AGE_INDEX = np.arange(65)


def _mismatch_matrix(masses: np.ndarray, fade: int) -> np.ndarray:
	"""mismatch_values for every row of ``masses`` (steps x 65) at once, same arithmetic order."""
	rows = masses.shape[0]
	weights = np.zeros((rows, 66))
	moments = np.zeros((rows, 66))
	np.cumsum(masses, axis=1, out=weights[:, 1:])
	np.cumsum(_AGES * masses, axis=1, out=moments[:, 1:])
	free_ages = 6 // fade
	lo = np.maximum(0, _AGE_INDEX - free_ages)
	hi = np.minimum(65, _AGE_INDEX + free_ages + 1)
	gap = _AGES * weights[:, lo] - moments[:, lo]
	gap = gap + (
		(moments[:, 65:66] - moments[:, hi]) - _AGES * (weights[:, 65:66] - weights[:, hi])
	)
	return np.maximum(0.0, fade * gap)


def mismatch_values(masses: list[float], fade: int) -> list[float]:
	"""Thresholded mismatch for every candidate age against an age distribution.

	Prefix sums give the mass and first moment outside the free shade gap,
	so all 65 candidate ages can be evaluated in linear time.
	"""
	weights = [0.0]
	moments = [0.0]
	for age, mass in enumerate(masses):
		weights.append(weights[-1] + mass)
		moments.append(moments[-1] + age * mass)
	free_ages = 6 // fade
	values = []
	for age in range(65):
		lo = max(0, age - free_ages)
		hi = min(65, age + free_ages + 1)
		gap = age * weights[lo] - moments[lo]
		gap += moments[65] - moments[hi] - age * (weights[65] - weights[hi])
		values.append(max(0.0, fade * gap))
	return values


@lru_cache(maxsize=512)
def _target_costs(geometric_survival: float, fade: int) -> tuple[float, ...]:
	"""Mismatch against the geometric replacement target; depends only on its inputs, so cached."""
	target_mass = [(1.0 - geometric_survival) * geometric_survival**age for age in range(65)]
	target_mass[-1] = geometric_survival**64
	return tuple(mismatch_values(target_mass, fade))


def project_life_values(
	observed_mass: list[float],
	fade: int,
	alpha: float,
	geometric_survival: float,
	life_survival_cap: float,
	horizon: float,
) -> list[float]:
	"""Return the discounted future mismatch for each starting age, 0..64.

	The finite horizon is days_left * 2n/C expected wears. A fractional last
	interval receives q**step - q**horizon weight.
	After 64 steps the observed pair shares a cap, leaving only target cost.
	"""
	values = [0.0] * 65
	if horizon <= 0:
		return values
	q = min(geometric_survival, life_survival_cap)
	total = sum(observed_mass)
	masses = [mass / total for mass in observed_mass] if total else [0.0] * 65
	target_costs = _target_costs(geometric_survival, fade)
	steps = min(64, ceil(horizon))
	step_weights = np.array([q**step - q ** min(step + 1.0, horizon) for step in range(steps)])

	# Row s is the drawer after s shifts: masses move up one age and the last two ages merge.
	# The merged top age is a running sum of masses[64], masses[63], ... in that order.
	start = np.array(masses)
	shifted = np.zeros((steps, 65))
	for step in range(steps):
		shifted[step, step:64] = start[: 64 - step]
	shifted[:, 64] = np.cumsum(start[::-1])[:steps]

	observed_costs = _mismatch_matrix(shifted, fade)
	future_age = np.minimum(64, _AGE_INDEX[None, :] + np.arange(steps)[:, None])
	observed = observed_costs[np.arange(steps)[:, None], future_age]
	target = np.array(target_costs)[future_age]
	terms = step_weights[:, None] * ((1.0 - alpha) * observed + alpha * target)
	values = np.cumsum(terms, axis=0)[-1].tolist()
	if horizon > 64:
		tail = alpha * (q**64 - q**horizon) * target_costs[64]
		values = [value + tail for value in values]
	return values
