import matplotlib.pyplot as plt
import numpy as np

# 1. Read the 2D array from CSV file
data = np.loadtxt("threshold_history_4408483792.csv", delimiter=",")

# Handle edge case where a single row is read as a 1D array
if data.ndim == 1:
    data = np.array([data])

# 2. Get number of columns for x-axis coordinates
num_columns = data.shape[1]
x_axis = np.arange(num_columns)

# 3. Create figure and plot each row as a distinct line series
plt.figure(figsize=(10, 6))

average_days = [1, 20, 40, 80] 
for i, row in enumerate(data):
    plt.plot(x_axis, row, label=f"m = {average_days[i]}")

# 4. Add axis labels, title, legend, and grid
plt.xlabel("Day")
plt.ylabel("Threshold")
plt.title("Threshold average of last m days")
plt.legend(loc="best")
plt.grid(True)

# 5. Display plot
plt.tight_layout()
plt.show()