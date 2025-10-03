"""
Basic OpenXYZ Scanning Example with Callback

This example demonstrates how to:
1. Create a rectangular scanning path
2. Initialize the XYZ stage
3. Iterate over coordinates
4. Call a measurement callback at each position
5. Save results with position data
"""

from openxyz.xyz_stage import Stage
from openxyz.marlin import Marlin
from coordinate_paths import RectangularPath

import decimal
import pickle
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s\t[%(levelname)s]\t%(message)s")


def measurement_callback():
	"""
	User-defined measurement function called at each position.

	Replace this with your actual measurement code (e.g., oscilloscope capture,
	spectrum analyzer scan, multimeter reading, camera capture, etc.)

	:return: Measurement data (can be any type: dict, list, numpy array, etc.)
	:rtype: any
	"""
	# Example: return dummy data
	# In practice, this could be:
	#   - scope.capture_data()
	#   - spectrum_analyzer.scan()
	#   - camera.take_picture()
	#   - power_meter.read()
	#   etc.

	raise NotImplementedError("Please implement your measurement callback function")

	# Example implementation:
	# return {
	#     'timestamp': time.time(),
	#     'voltage': random.random(),
	#     'current': random.random()
	# }


def main():
	# ========== Configuration ==========

	# Define the scanning area
	START_X = decimal.Decimal('0')      # Starting X coordinate (mm)
	START_Y = decimal.Decimal('0')      # Starting Y coordinate (mm)
	END_X   = decimal.Decimal('10')     # Ending X coordinate (mm)
	END_Y   = decimal.Decimal('10')     # Ending Y coordinate (mm)
	STEP_X  = decimal.Decimal('0.5')    # Step size in X direction (mm)
	STEP_Y  = decimal.Decimal('0.5')    # Step size in Y direction (mm)

	# Probe height above the target
	PROBE_HEIGHT = decimal.Decimal('40')  # Z coordinate (mm)

	# Marlin controller IP address or hostname
	MARLIN_IP = "openxyz"  # or use IP like "10.3.141.218"

	# Output file for results
	OUTPUT_FILE = "measurement_results.pckl"

	# ========== Initialize Hardware ==========

	logging.info("Initializing stage...")
	stage = Stage(Marlin(ip=MARLIN_IP))

	# ========== Create Scanning Path ==========

	logging.info("Generating scan path...")
	path = RectangularPath(
		start_xy=(START_X, START_Y),
		end_xy=(END_X, END_Y),
		step_size_x=STEP_X,
		step_size_y=STEP_Y
	)

	total_points = len(path.coordinates)
	logging.info(f"Path generated with {total_points} measurement points")

	# ========== Set Initial Position ==========

	logging.info("Moving to starting position...")
	stage.x = path.coordinates[0][0]
	stage.y = path.coordinates[0][1]
	stage.z = PROBE_HEIGHT

	input("Stage is at starting position. Press Enter to begin scan...")

	# ========== Perform Scan ==========

	logging.info("Starting scan...")

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

	# Optional: Return to starting position
	# stage.x = START_X
	# stage.y = START_Y


if __name__ == "__main__":
	main()
