import numpy as np

def analyze(file_path):
    data = np.genfromtxt(file_path, delimiter=',', skip_header=1)
    
    # Column order: 
    # "capacity,budget,roomates,duration,total_embarrassment,total_sockless_days,budget_exhausted_on,budget_remaining",
    total_embarrassment = data[:, 4]
    budget_remaining = data[:, 7]
    condition = (budget_remaining != 0) & (total_embarrassment != 0)

    filtered_rows = data[condition]
    
    print("Runs where budget isn't used up but embarrasement occured")
    print(filtered_rows)

# Example usage:
analyze('test.csv')

