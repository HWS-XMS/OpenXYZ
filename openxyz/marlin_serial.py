# EMFI Station - Orchestrate electromagnetic fault injection attacks
# Copyright (C) 2022 Niclas Kühnapfel
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import time
import serial
import logging

BUSY_MSG = b'echo:busy: processing\n'
OK_MSG = b'ok\n'


class MarlinSerial:
	"""
	Manages serial connection to Marlin-based controller board.

	:param tty: Serial port (e.g.: '/dev/ttyACM0')
	:type tty: str
	:param mock: If True, use a mock serial connection
	:type mock: bool, optional
	"""

	def __init__(self, tty: str, mock: bool = False):
		self.log = logging.getLogger(__name__)
		self.sim = mock
		if not self.sim:
			self.ser = serial.Serial(port=tty, baudrate=115200, timeout=0.25)
			self.clear()

	def clear(self) -> None:
		"""
		Clears serial input buffer.

		:return: None
		:rtype: None
		"""
		if self.sim:
			self.log.info('Clearing serial buffer.')
		else:
			self.ser.flush()
			self.ser.reset_input_buffer()

	def read(self) -> bytes:
		"""
		Reads a line from serial interface.

		:return: Line read from serial interface
		:rtype: bytes
		"""
		if self.sim:
			time.sleep(0.5)
			return b'ok\n'
		else:
			msg = self.ser.readline()
			self.log.debug('Read from serial port: {:s}'.format(str(msg)))
			return msg

	def close(self) -> None:
		"""
		Closes serial port.

		:return: None
		:rtype: None
		"""
		self.log.info('Closing serial port.')
		if not self.sim:
			self.ser.close()

	def send_gcode(self, cmd: str) -> bytes:
		"""
		Sends command via serial.

		:param cmd: Command string (e.g.: 'M122')
		:return: None
		"""
		if self.sim:
			self.log.info('Sending: {:s}'.format(str(cmd)))
		else:
			self.log.debug('Write to serial port: {:s}'.format(str(cmd)))
			self.ser.write((cmd + '\n').encode())
			self.ser.flush()
		response = self.__wait_cmd_completed()

		# if command is a movement command, wait for it to be completed
		if cmd.startswith('G0') or cmd.startswith('G1'):
			self.__wait_move_completed()

		return response

	def emergency(self) -> None:
		"""
		Stops movement immediately but allows further commands (M410).

		:return: None
		:rtype: None
		"""
		self.send_gcode('M410')
		self.log.critical('Emergency stop initiated.')
		self.__wait_cmd_completed()

	def __wait_cmd_completed(self, max_tries: int = 100) -> bytes:
		"""
		Waits for a command to be completed.
		HOST_KEEPALIVE_FEATURE has to be enabled in Marlin configuration.
		Marlin is expected to send a 'busy' message once a second (DEFAULT_KEEPALIVE_INTERVAL 1).

		:param max_tries: Maximum number of tries to receive 'ok' message.
		:type max_tries: int
		:return: All received messages
		:rtype: bytes
		"""
		msg = b''
		try:
			counter = 0
			while True:
				res = self.ser.read()
				if res:
					msg += res
				if BUSY_MSG in msg:
					counter = 0
					msg = b''
				if OK_MSG in msg:
					break
				counter += 1
				if counter > max_tries:
					raise IOError('Timeout while waiting for a command to be completed: {:s}'.format(str(msg)))
		except KeyboardInterrupt:
			self.emergency()
			raise

		return msg + self.ser.read()

	def __wait_move_completed(self) -> None:
		"""
		Waits for movement to be completed (M400).

		:return: None
		:rtype: None
		"""
		self.clear()
		self.send_gcode('M400')


if __name__ == "__main__":
	# Example usage
	logging.basicConfig(level=logging.DEBUG)
	ser = MarlinSerial('/dev/ttyACM0', mock=False)
	r = ser.send_gcode('G91').decode('utf-8').replace('ok\n', '')
	print("\t", r)
	ser.close()
