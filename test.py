from core.engine import Engine
from core.registry import discover
from models.cli import settings
from models.player import Player
from main import build_roster, _open_log
import os
import numpy as np

csv_file = "test.csv"

def regression():
    csv_values = np.loadtxt("test.csv", delimiter=",")
    valid_indices = np.where(csv_values[:,-1] > -1)
    csv_values = csv_values[valid_indices]
    # csv_values[:, -1] = np.where(csv_values[:, -1] > -1, csv_values[:, -1], 1000)
    X = csv_values[:,1:]
    # sock_ratio = csv_values[:,2] / csv_values[:,3]
    # budget_ratio = csv_values[:,1] / csv_values[:,4]
    # X = np.zeros((csv_values.shape[0], 2))
    # X[:,0] = sock_ratio
    # X[:,1] = budget_ratio

    y = csv_values[:,0]

    X_with_intercept = np.column_stack([
        np.ones(len(X)),
        X
    ])

    coefficients, _, _, _ = np.linalg.lstsq(
        X_with_intercept,
        y,
        rcond=None
    )

    print("Coefficients:", coefficients)

    predicted_y = X_with_intercept @ coefficients
    print(y)
    print("Predicted values:", predicted_y)

def run_experiments():
    budget_vals = [100, 200, 300, 400, 500]
    capacity_vals = [40, 60, 80, 100, 120, 140, 160, 180]
    player_vals = [2, 4, 6, 8, 10, 12]
    threshold_vals = [6, 8, 10, 12, 14, 16, 18, 20, 30, 40, 50, 60]

    results = []

    for budget in budget_vals:
        for capacity in capacity_vals:
            for player in player_vals:
                for threshold in threshold_vals:
                    values = [threshold, budget, capacity, player]
                    os.environ["threshold"] = str(threshold)
                    args = settings(["--player", "1", str(player), "--budget", str(budget), "--capacity", str(capacity), "--days", "1000"])
                    roster = build_roster(args.players)

                    try:
                        engine = Engine(
                            players=roster,
                            capacity=args.capacity,
                            selection_unit=args.selection_unit,
                            days=args.days,
                            seed=args.seed,
                            timeout=args.timeout,
                            budget=args.budget,
                            keep_records=not args.summary_only,
                        )
                    except:
                        values.append(-1)
                        results.append(values)
                        continue

                    log = _open_log(args, engine)
                    on_day = None
                    if log is not None:
                        from core.runlog import bind

                        on_day = bind(log, engine)

                    try:
                        if args.gui:
                            from ui.gui import run_gui

                            run_gui(engine, on_day=on_day)
                        else:
                            result = engine.run(on_day=on_day)
                            num_days = result["budget_exhausted_on_day"]
                            if num_days is None:
                                values.append(-1)
                            else:
                                values.append(float(num_days))
                            results.append(values)
                    finally:
                        if log is not None:
                            log.finish(engine)

    results = np.array(results)
    np.savetxt(csv_file, results, fmt="%.2f", delimiter=",")

regression()