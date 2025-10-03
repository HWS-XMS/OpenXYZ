"""
Example: Reading OpenXYZ Scan Results

This example demonstrates how to load and process measurement data
saved by the basic_scan_with_callback.py example.
"""

import pickle
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s\t[%(levelname)s]\t%(message)s")


def load_scan_results(filename: str):
	"""
	Load all measurement data from a pickle file.

	:param filename: Path to the pickle file containing scan results
	:type filename: str
	:return: List of (coordinate, data) tuples
	:rtype: list
	"""
	results = []

	with open(filename, 'rb') as f:
		while True:
			try:
				coordinate, data = pickle.load(f)
				results.append((coordinate, data))
			except EOFError:
				break

	return results


def main():
	INPUT_FILE = "measurement_results.pckl"

	logging.info(f"Loading scan results from {INPUT_FILE}...")
	results = load_scan_results(INPUT_FILE)

	logging.info(f"Loaded {len(results)} measurements")

	# Example: Print all measurements
	for coordinate, data in results:
		print(f"Position ({coordinate[0]}, {coordinate[1]}): {data}")

	# Example: Extract specific data
	# x_coords = [coord[0] for coord, data in results]
	# y_coords = [coord[1] for coord, data in results]
	# values = [data['voltage'] for coord, data in results]

	# Example: Plot heatmap (requires matplotlib and numpy)
	# import numpy as np
	# import matplotlib.pyplot as plt
	#
	# # Create grid
	# x_unique = sorted(set(x_coords))
	# y_unique = sorted(set(y_coords))
	# grid = np.zeros((len(y_unique), len(x_unique)))
	#
	# # Fill grid with values
	# for (x, y), data in results:
	#     i = y_unique.index(y)
	#     j = x_unique.index(x)
	#     grid[i, j] = data['voltage']
	#
	# # Plot
	# plt.imshow(grid, origin='lower', extent=[min(x_coords), max(x_coords), min(y_coords), max(y_coords)])
	# plt.colorbar(label='Voltage')
	# plt.xlabel('X (mm)')
	# plt.ylabel('Y (mm)')
	# plt.title('Scan Results Heatmap')
	# plt.show()


if __name__ == "__main__":
	main()
