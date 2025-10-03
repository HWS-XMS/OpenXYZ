import spidev
import enum
import RPi.GPIO as GPIO
from typing import *
import logging
import time

class CountMode(enum.Enum):
	NQUAD 	= 0x00  # non-quadrature mode
	QUADRX1 = 0x01  # X1 quadrature mode
	QUADRX2 = 0x02  # X2 quadrature mode
	QUADRX4 = 0x03  # X4 quadrature mode


class RunningMode(enum.Enum):
	FREE_RUN 	= 0x00
	SINGE_CYCLE = 0x04
	RANGE_LIMIT = 0x08
	MODULO_N 	= 0x0C


class IndexMode(enum.Enum):
	DISABLE_INDX 	= 0x00  # index_disabled
	INDX_LOADC 		= 0x10  # index_load_CNTR
	INDX_RESETC 	= 0x20  # index_rest_CNTR
	INDX_LOADO 		= 0x30  # index_load_OL
	ASYNCH_INDX 	= 0x00  # asynchronous index
	SYNCH_INDX 		= 0x80  # synchronous index


class ClockFilter(enum.Enum):
	FILTER_1 = 0x00  # filter clock frequncy division factor 1
	FILTER_2 = 0x80  # filter clock frequncy division factor 2


class ByteWidth(enum.Enum):
	BYTE_WIDTH_1 = 0x03
	BYTE_WIDTH_2 = 0x02
	BYTE_WIDTH_3 = 0x01
	BYTE_WIDTH_4 = 0x00


class CountingEnabled(enum.Enum):
	COUNTING_ENABLED = 0x00
	COUNTING_DISABLED = 0x04


class FlagIndicator(enum.Enum):
	FLAG_INDICATOR_IDX = 1 << 4
	FLAG_INDICATOR_CMP = 1 << 5
	FLAG_INDICATOR_BW = 1 << 6
	FLAG_INDICATOR_CY = 1 << 7


class Opcode(enum.Enum):
	CLR_MDR0 = 0x08
	CLR_MDR1 = 0x10
	CLR_CNTR = 0x20
	CLR_STR = 0x30
	READ_MDR0 = 0x48
	READ_MDR1 = 0x50
	READ_CNTR = 0x60
	READ_OTR = 0x68
	READ_STR = 0x70
	WRITE_MDR1 = 0x90
	WRITE_MDR0 = 0x88
	WRITE_DTR = 0x98
	LOAD_CNTR = 0xE0
	LOAD_OTR = 0xE4


class MDR_0:
	def __init__(self, quadrature_count_mode: CountMode, running_mode: RunningMode, index_mode: IndexMode,
				 clock_filter: ClockFilter):
		self.__value = quadrature_count_mode.value
		self.__value |= running_mode.value
		self.__value |= index_mode.value
		self.__value |= clock_filter.value

	@property
	def value(self):
		return self.__value


class MDR_1:
	def __init__(self, byte_width: ByteWidth, counting_enabled: CountingEnabled, flag_indicators: List[FlagIndicator]):
		self.__value = byte_width.value
		self.__value |= counting_enabled.value
		for flag_indicator in flag_indicators:
			self.__value |= flag_indicator.value

	@property
	def value(self):
		return self.__value


class Status:
	def __init__(self, status: int):
		self.CY = (status & 1 << 7) != 0
		self.BW = (status & 1 << 6) != 0
		self.CMP = (status & 1 << 5) != 0
		self.IDX = (status & 1 << 4) != 0
		self.CEN = (status & 1 << 3) != 0
		self.PLS = (status & 1 << 2) != 0
		self.UD = (status & 1 << 1) != 0
		self.S = (status & 1 << 0) != 0

	def __str__(self):
		print("")
		for k, v in self.__dict__.items():
			print(f"{k}:\t{1 if v else 0}")
		return ""


class EncoderAxis(enum.Enum):
	ENCODER_AXIS_X = 0
	ENCODER_AXIS_Y = 1
	ENCODER_AXIS_Z = 2


class LS7366R:
	def __init__(self, bus: int, cs_pins: Dict[EncoderAxis, int]):
		self._log = logging.getLogger(__name__)
		self.__bus = bus
		self.__spi_mode = 0
		self.__spi_speed = 100_000
		self.__spi = spidev.SpiDev()
		self.__spi.open(bus=self.__bus, device=0)
		self.__spi.no_cs = False
		self.__spi.max_speed_hz = self.__spi_speed
		self.__spi.mode = self.__spi_mode
		self.__cs_pins = cs_pins

		GPIO.setmode(GPIO.BCM)
		for pin in self.__cs_pins.values():
			GPIO.setup(pin, GPIO.OUT)
			GPIO.output(pin, True)

		# initialize
		self.initialize()

	def initialize(self):
		for axis in EncoderAxis:
			self.clear_mode_register_0(axis)
			self.clear_mode_register_1(axis)
			self.clear_counter(axis)
			self.clear_status(axis)

			mdr0 = MDR_0(
				CountMode.QUADRX4,
				RunningMode.SINGE_CYCLE,
				IndexMode.DISABLE_INDX,
				ClockFilter.FILTER_2
			)
			self.write_mode_register_0(axis, mdr0)

			mdr1 = MDR_1(
				ByteWidth.BYTE_WIDTH_4,
				CountingEnabled.COUNTING_ENABLED,
				[FlagIndicator.FLAG_INDICATOR_IDX]
			)
			self.write_mode_register_1(axis, mdr1)

	def __del__(self):
		GPIO.cleanup()

	def __slave_select(self, encoder_axis: EncoderAxis, select: bool):
		GPIO.output(self.__cs_pins[encoder_axis], not select)

	def __write(self, encoder_axis: EncoderAxis, data: List[int], deselect_slave_after: bool):
		self.__slave_select(encoder_axis, True)
		self.__spi.writebytes(data)
		if deselect_slave_after:
			self.__slave_select(encoder_axis, False)

	def __read(self, encoder_axis: EncoderAxis, length: int, deselect_slave_after: bool) -> List[int]:
		r = self.__spi.readbytes(length)
		if deselect_slave_after:
			self.__slave_select(encoder_axis, False)
		return r

	def clear_mode_register_0(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.CLR_MDR0.value], True)

	def clear_mode_register_1(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.CLR_MDR1.value], True)

	def clear_counter(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.CLR_CNTR.value], True)

	def clear_status(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.CLR_STR.value], True)

	def read_mode_register_0(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.READ_MDR0.value], False)
		response = self.__read(encoder_axis, 1, True)
		return response[0]

	def read_mode_register_1(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.READ_MDR1.value], False)
		response = self.__read(encoder_axis, 1, True)
		return response[0]

	def command_byte_width(self, encoder_axis: EncoderAxis) -> int:
		mdr_1 = self.read_mode_register_1(encoder_axis)
		if (mdr_1 & 0x03) == ByteWidth.BYTE_WIDTH_4.value:
			return 4
		elif (mdr_1 & 0x03) == ByteWidth.BYTE_WIDTH_3.value:
			return 3
		elif (mdr_1 & 0x03) == ByteWidth.BYTE_WIDTH_2.value:
			return 2
		else:
			return 1

	def counting_enabled(self, encoder_axis: EncoderAxis) -> bool:
		mdr_1 = self.read_mode_register_1(encoder_axis)
		return (mdr_1 & 0x04) == 0

	def read_counter(self, encoder_axis: EncoderAxis) -> int:
		current_byte_width = self.command_byte_width(encoder_axis)
		self.__write(encoder_axis, [Opcode.READ_CNTR.value], False)
		response = self.__read(encoder_axis, current_byte_width, True)
		return int.from_bytes(response, byteorder='big')

	def read_output_register(self, encoder_axis: EncoderAxis) -> int:
		current_byte_width = self.command_byte_width(encoder_axis)
		self.__write(encoder_axis, [Opcode.READ_OTR.value], False)
		response = self.__read(encoder_axis, current_byte_width, True)
		return int.from_bytes(response, byteorder='big')

	def read_status(self, encoder_axis: EncoderAxis) -> Status:
		self.__write(encoder_axis, [Opcode.READ_STR.value], False)
		response = self.__read(encoder_axis, 1, True)
		return Status(response[0])

	def write_mode_register_0(self, encoder_axis: EncoderAxis, mdr: MDR_0):
		self.__write(encoder_axis, [Opcode.WRITE_MDR0.value, mdr.value], True)

	def write_mode_register_1(self, encoder_axis: EncoderAxis, mdr: MDR_1):
		self.__write(encoder_axis, [Opcode.WRITE_MDR1.value, mdr.value], True)

	def write_data_register(self, encoder_axis: EncoderAxis, dtr: int):
		current_byte_width = self.command_byte_width(encoder_axis)
		dtr_as_bytes = dtr.to_bytes(current_byte_width, 'big')
		self.__write(encoder_axis, [Opcode.WRITE_DTR.value], False)
		self.__write(encoder_axis, list(dtr_as_bytes), True)

	def load_data_register_to_output_register(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.LOAD_OTR.value], True)

	def load_counter_from_data_register(self, encoder_axis: EncoderAxis):
		self.__write(encoder_axis, [Opcode.LOAD_CNTR.value], True)