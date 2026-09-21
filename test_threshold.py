import json

from core.engine import Engine
from core.registry import discover
from models.cli import settings
from models.player import Player
from main import build_roster, _open_log
import numpy as np

csv_file = "test.csv"


def run_simulation(args):
    roster = build_roster(args.players)

    engine = Engine(
        players=roster,
        capacity=args.capacity,
        selection_unit=args.selection_unit,
        days=args.days,
        seed=args.seed,
        timeout=args.timeout,
        budget=args.budget,
        keep_records=False,
    )

    log = _open_log(args, engine)
    on_day = None
    if log is not None:
        from core.runlog import bind
        on_day = bind(log, engine)

    try:
        engine_result = engine.run(on_day=on_day)

        run_result = [
            engine_result["total_embarrassment"],
            engine_result["total_sockless_days"],
            engine_result["budget_exhausted_on_day"] or -1, # this is none when not exhausted
            engine_result["budget_remaining"],
        ]
    finally:
        if log is not None:
            log.finish(engine)

    return run_result


def run_experiments():
    # budget_vals = [100, 200, 300, 400, 500]
    # capacity_vals = [40, 60, 80, 100, 120, 140, 160, 180]
    # player_vals = [2, 4, 6, 8, 10, 12]
    # player_type = ["1", "greedy", "random"]

    budget_vals = [100, 200, 300]
    capacity_vals = [40, 60, 80]
    player_count_vals = [4, 6, 8]
    duration_vals = [120, 240, 360]


    results = []

    for budget in budget_vals:
        for capacity in capacity_vals:
            for player_count in player_count_vals:
                for duration in duration_vals:

                    # check if valid, skip invalid experiments 
                    
                    if capacity < 4 * player_count + 10:
                        continue

                    args = settings([
                        "--player", "1", str(player_count), 
                        "--budget", str(budget), 
                        "--capacity", str(capacity), 
                        "--days", str(duration)])

                    run_params = [budget, capacity, player_count, duration]
                    run_result = run_simulation(args)

                    results.append(run_params + run_result)
                
    results = np.array(results)
    np.savetxt(csv_file, 
               results, 
               fmt="%.2f", 
               delimiter=",",
               header="capacity,budget,roomates,duration,total_embarrassment,total_sockless_days,budget_exhausted_on,budget_remaining",
            )

run_experiments()