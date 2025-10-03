from enum import Enum
import logging
import os
import pickle
import re

def parse_gcode(gcode: str) -> tuple[float, float, float]:
	"""
	Parses the G-code action using regex.

	:param gcode: G-code action in the format "G1 X{float} Y{float} Z{float} F{float}" (e.g., "G1 X10.0 Y20.0 Z30.0 F100.0")
	:type gcode: str
	:return: Parsed x, y, and z values
	:rtype: tuple[float, float, float]
	:raises ValueError: If the G-code action is invalid
	"""

	# TODO: improve trash code
	# Using regex to parse the G-code action (e.g., "G1 X10.0")
	pattern = re.compile(r"G1 (X(?P<x>-?\d*\.?\d+)|Y(?P<y>-?\d*\.?\d+)|Z(?P<z>-?\d*\.?\d+))")
	match = pattern.match(gcode)
	if match:
		x = match.group("x") or 0.0
		y = match.group("y") or 0.0
		z = match.group("z") or 0.0

		# Returning the parsed values
		return x, y, z
	else:
		raise ValueError(f"Detected invalid G-code action, aborting! ('{gcode}')")


class GCode(Enum):
	"""
	G-code enumeration.
	"""
	G1 = "G1"  # Linear move
	G21 = "G21"  # Set units to mm
	G28 = "G28"  # Home axes
	M203 = "M203"  # Set maximum feed rate
	M112 = "M112"  # Emergency stop
	M114 = "M114"  # Get current position
	M117 = "M117"  # Set LCD message
	M73 = "M73"  # Set LCD progress bar
	M510 = "M510"  # Lock stage
	M511 = "M511"  # Unlock stage
	M512 = "M512"  # Set passcode
	G4 = "G4"  # Dwell
	G60 = "G60"  # Save current position
	G61 = "G61"  # Return to saved position
	G90 = "G90"  # Set absolute positioning mode
	G91 = "G91"  # Set relative positioning mode

	@staticmethod
	def from_string(s: str):
		"""
		Converts a string to a GCode enum.

		:param s: String representation of the G-code
		:type s: str
		:return: GCode enum
		:rtype: GCode
		"""
		return GCode[s.upper()]


class PositioningMode(Enum):
	"""
	Positioning mode for the stage.
	"""
	ABSOLUTE = 90  # Absolute positioning mode (from origin 0, 0, 0)
	RELATIVE = 91  # Relative positioning mode (from current position)


class FileManager:
	"""
	FileManager class to handle file operations.
	TODO: use file manager to save and load files instead of using with open (also create example in documentation)

	:param directory: The directory where files will be saved
	:type directory: str
	"""

	def __init__(self, directory: str):
		self._log = logging.getLogger(__name__)
		self.directory = directory
		os.makedirs(directory, exist_ok=True)

	def _get_file_path(self, filename: str) -> str:
		"""
		Constructs the full file path for a given filename.

		:param filename: The name of the file
		:type filename: str
		:return: The full file path
		:rtype: str
		"""
		return os.path.join(self.directory, filename)

	def save_new_file(self, filename: str, data: any) -> None:
		"""
		Saves data to a new file.

		:param filename: The name of the file
		:type filename: str
		:param data: The data to be saved
		:type data: any
		:return: None
		:rtype: None
		"""
		file_path = self._get_file_path(filename)
		with open(file_path, 'wb') as file:
			pickle.dump(data, file)
		self._log.info(f"Data saved to {file_path}")

	def append_to_file(self, filename: str, data: any) -> None:
		"""
		Appends data to an existing file.

		:param filename: The name of the file
		:type filename: str
		:param data: The data to be appended
		:type data: any
		:return: None
		:rtype: None
		:raises FileNotFoundError: If the file does not exist
		"""
		file_path = self._get_file_path(filename)
		if os.path.exists(file_path):
			with open(file_path, 'ab') as file:
				pickle.dump(data, file)
			self._log.info(f"Data appended to {file_path}")
		else:
			self._log.info(f"File {file_path} does not exist. Creating a new file.")
			self.save_new_file(filename, data)

	def load_file(self, filename: str) -> any:
		"""
		Loads and returns data from a file.

		:param filename: The name of the file
		:type filename: str
		:return: The loaded data
		:rtype: any
		:raises FileNotFoundError: If the file does not exist
		"""
		file_path = self._get_file_path(filename)
		if os.path.exists(file_path):
			with open(file_path, 'rb') as file:
				data = pickle.load(file)
			return data
		else:
			self._log.info(f"File {file_path} does not exist.")
			return None
