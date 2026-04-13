import numpy as np

x = np.array([1, 2, 3, 4, 5])
y = np.array([2, 3, 5, 7, 11])

# Calculate the slope (m) and intercept (b) for the line of best fit
m = (np.mean(x) * np.mean(y) - np.mean(x * y)) / (np.mean(x) ** 2 - np.mean(x ** 2))
b = np.mean(y) - m * np.mean(x)

print(1+1)