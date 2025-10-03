from openxyz.marlin import Marlin
from openxyz.utils import GCode, parse_gcode

import enum
import decimal
from matplotlib import pyplot as plt
import numpy as np
import tqdm
import time

class GCode(enum.Enum):
	G0 		= "G0"  	# G0 for move without extrusion, G1 for move with extrusion.
	G1 		= "G1"  	# Linear move
	G4 		= "G4"  	# Dwell
	G20 	= "G20"  	# Set units to inches
	G21 	= "G21"  	# Set units to mm
	G28 	= "G28"  	# Home axes
	G60 	= "G60"  	# Save current position
	G61 	= "G61"  	# Return to saved position
	G90 	= "G90"  	# Set absolute positioning mode
	G91 	= "G91"  	# Set relative positioning mode
	M73 	= "M73"  	# Set LCD progress bar
	M112 	= "M112"  	# Emergency stop
	M114 	= "M114"  	# Get current position
	M117 	= "M117"  	# Set LCD message
	M203 	= "M203"  	# Set MAXIMUM feed rate
	M204 	= "M204"  	# Set Acceleration
	M220 	= "M220"  	# Set feedrate
	M503 	= "M503"  	# Get Settings, currently fails at temperature readout.
	M510 	= "M510"  	# Lock stage
	M511 	= "M511"  	# Unlock stage
	M512 	= "M512"  	# Set passcode

class PositioningUnit(enum.Enum):
	POSITIONING_UNIT_INCH 		= 0x00
	POSITIONING_UNIT_MILLIMETER = 0x01

class PositioningMode(enum.Enum):
	POSITIONING_MODE_ABSOLUTE 	= 0x00
	POSITIONING_MODE_RELATIVE 	= 0x01

class Stage(object):
	def __init__(self, marlin: Marlin):
		self.__marlin 			= marlin
		self.__context 			= decimal.getcontext()
		self.__context.prec 	= 4
		self.__initialize_stage()

	def __send_gcode(self, gcode: GCode, *args) -> str or None:
		gcode = gcode.value
		if args:
			gcode += ' ' + ' '.join(args)
		return self.__marlin.send_gcode(gcode)

	def __initialize_stage(self):
		self.set_positioning_unit(mode=PositioningUnit.POSITIONING_UNIT_MILLIMETER)
		self.set_positioning_mode(mode=PositioningMode.POSITIONING_MODE_ABSOLUTE)
		self.auto_home(only_untrusted=True)
		self.set_max_feedrates(max_feedrates=(300, 300, 300))
		self.feedrate_percent = 100
		self.set_lcd_message(message='Open-FML')

	def set_positioning_unit(self, mode: PositioningUnit):
		self.__send_gcode(GCode.G20 if mode == PositioningUnit.POSITIONING_UNIT_INCH else GCode.G21)

	def set_positioning_mode(self, mode: PositioningMode):
		self.__send_gcode(GCode.G90 if mode == PositioningMode.POSITIONING_MODE_ABSOLUTE else GCode.G91)

	def set_lcd_message(self, message: str):
		self.__send_gcode(GCode.M117, message)

	def auto_home(self, only_untrusted: bool):
		if only_untrusted:
			self.__send_gcode(GCode.G28, "O")
		else:
			self.__send_gcode(GCode.G28, 'X', 'Y', 'Z')

	def set_max_feedrates(self, max_feedrates: tuple[int, int, int]):
		self.__send_gcode(GCode.M203, "X{}", "Y{}", "Z{}".format(max_feedrates[0], max_feedrates[1], max_feedrates[2]))

	@property
	def feedrate_percent(self) -> int:
		response = self.__send_gcode(GCode.M220)
		return int(response)

	@feedrate_percent.setter
	def feedrate_percent(self, percent: int):
		assert 0 <= percent <= 100, 'Percent must be between 0 and 100'
		self.__send_gcode(GCode.M220, "S{}".format(percent))

	def print_settings(self):
		print(self.__send_gcode(GCode.M503))

	@property
	def xyz(self) -> tuple[decimal.Decimal, decimal.Decimal, decimal.Decimal]:
		pos_str = self.__send_gcode(GCode.M114)
		s = pos_str.split(':')
		x = decimal.Decimal(s[1].split(' ', 1)[0])
		y = decimal.Decimal(s[2].split(' ', 1)[0])
		z = decimal.Decimal(s[3].split(' ', 1)[0])
		return x, y, z

	@property
	def acceleration(self):
		response = self.__send_gcode(GCode.M204)
		print(response)

	@acceleration.setter
	def acceleration(self, acceleration):
		pass

	@property
	def x(self) -> decimal.Decimal:
		return self.xyz[0]

	@x.setter
	def x(self, value: decimal.Decimal):
		self.__send_gcode(GCode.G0, "X{} F100".format(value))

	@property
	def y(self) -> decimal.Decimal:
		return self.xyz[1]

	@y.setter
	def y(self, value: decimal.Decimal):
		self.__send_gcode(GCode.G0, "Y{} F100".format(value))

	@property
	def z(self) -> decimal.Decimal:
		return self.xyz[2]

	@z.setter
	def z(self, value: decimal.Decimal):
		self.__send_gcode(GCode.G0, "Z{} F100".format(value))

	@property
	def xy(self) -> tuple[decimal.Decimal, decimal.Decimal]:
		xyz = self.xyz
		return (xyz[0], xyz[1])

	@xy.setter
	def xy(self, xy: tuple[decimal.Decimal, decimal.Decimal]):
		self.x = xy[0]
		self.y = xy[1]

	def apply_delta(self, delta: tuple[decimal.Decimal, decimal.Decimal, decimal.Decimal]):
		self.x = self.x + delta[0]
		self.y = self.y + delta[1]
		self.z = self.z + delta[2]