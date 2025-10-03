"""
Circular Scanning Example

This example demonstrates how to use a circular (spiral) scanning path
instead of a rectangular one. Useful for scanning around a specific point
of interest on a chip or PCB.
"""

from openxyz.xyz_stage import Stage
from openxyz.marlin import Marlin
from coordinate_paths import CircularPath

import decimal
import pickle
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s\t[%(levelname)s]\t%(message)s")


def measurement_callback():
	"""
	User-defined measurement function called at each position.

	Replace this with your actual measurement code.

	:return: Measurement data
	:rtype: any
	"""
	raise NotImplementedError("Please implement your measurement callback function")


def main():
	# ========== Configuration ==========

	# Define the circular scanning area
	CENTER_X = decimal.Decimal('20')    # Center X coordinate (mm)
	CENTER_Y = decimal.Decimal('20')    # Center Y coordinate (mm)
	RADIUS   = decimal.Decimal('5')     # Scan radius from center (mm)
	STEP_SIZE = decimal.Decimal('0.5')  # Step size between points (mm)

	# Probe height above the target
	PROBE_HEIGHT = decimal.Decimal('40')  # Z coordinate (mm)

	# Marlin controller IP address or hostname
	MARLIN_IP = "openxyz"

	# Output file for results
	OUTPUT_FILE = "circular_measurement_results.pckl"

	# ========== Initialize Hardware ==========

	logging.info("Initializing stage...")
	stage = Stage(Marlin(ip=MARLIN_IP))

	# ========== Create Circular Path ==========

	logging.info("Generating circular scan path...")
	path = CircularPath(
		center_xy=(CENTER_X, CENTER_Y),
		radius=RADIUS,
		step_size=STEP_SIZE
	)

	total_points = len(path.coordinates)
	logging.info(f"Circular path generated with {total_points} measurement points")
	logging.info(f"Scanning from center ({CENTER_X}, {CENTER_Y}) outward to radius {RADIUS} mm")

	# ========== Set Initial Position ==========

	logging.info("Moving to center position...")
	stage.x = CENTER_X
	stage.y = CENTER_Y
	stage.z = PROBE_HEIGHT

	input("Stage is at center position. Press Enter to begin spiral scan...")

	# ========== Perform Scan ==========

	logging.info("Starting circular scan...")

	with open(OUTPUT_FILE, 'wb') as results_file:
		for idx, coordinate in enumerate(path.coordinates, start=1):
			# Move to next position
			stage.x = coordinate[0]
			stage.y = coordinate[1]

			logging.info(f"[{idx}/{total_points}] Measuring at ({coordinate[0]}, {coordinate[1]})")

			# Perform measurement
			data = measurement_callback()

			# Save position and data
			pickle.dump((coordinate, data), results_file)

	logging.info(f"Scan complete! Results saved to {OUTPUT_FILE}")


if __name__ == "__main__":
	main()
