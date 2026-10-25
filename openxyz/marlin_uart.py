"""PC side of openxyz/gate.py: Marlin through the CM4 gate over the board's UART (e.g. /dev/ttyUSB0)."""

import logging
import re
import time

import serial
import serial.tools.list_ports

from openxyz.gate import MAX_REPLY, frame, unframe

CH340_USB_ID = (0x1a86, 0x7523)

_CONTENT = re.compile(r'N([0-9]+):([0-9]+) (.*)')
_BUSY = re.compile(r'N([0-9]+) busy')
_OK = re.compile(r'N([0-9]+) ok ([0-9]+)')
_RESEND = re.compile(r'rs N([0-9]+)')


class GateError(Exception):
	pass


def find_uart() -> str:
	ports = [port.device for port in serial.tools.list_ports.comports() if (port.vid, port.pid) == CH340_USB_ID]
	if len(ports) != 1:
		raise GateError(f'expected one CH340 port {CH340_USB_ID[0]:04x}:{CH340_USB_ID[1]:04x}, found {ports}')
	return ports[0]


class MarlinUart:
	"""
	Same send_gcode interface as openxyz.marlin.Marlin. Every line is numbered and CRC-32 checked in
	both directions; corrupted or lost lines are sent again, and the gate never executes a line twice.
	Raises GateError on rejected commands, Marlin errors and lost sessions.
	"""

	def __init__(self, tty: str | None = None, baudrate: int = 115200, timeout: float = 10.0, attempts: int = 5):
		self._log = logging.getLogger(__name__)
		self._port = serial.Serial(tty or find_uart(), baudrate, timeout=0.05)
		self._timeout = timeout
		self._attempts = attempts
		self._buffer = bytearray()
		self._n = 0
		self._port.reset_input_buffer()
		self._request(0, 'M110')

	def send_gcode(self, gcode: str) -> str | None:
		self._n += 1
		lines = self._request(self._n, gcode)
		errors = [line for line in lines if line.startswith('Error:')]
		if errors:
			raise GateError(f'"{gcode}": ' + '; '.join(errors))
		return '\n'.join(lines) or None

	def close(self) -> None:
		self._port.close()

	def _request(self, n: int, command: str) -> list[str]:
		data = frame(f'N{n} {command}')
		for attempt in range(self._attempts):
			self._port.write(data)
			lines = self._reply(n)
			if lines is not None:
				return lines
			self._log.warning(f'line {n}: no intact reply, sending again ({attempt + 1}/{self._attempts})')
		raise GateError(f'no intact reply to line {n} after {self._attempts} attempts')

	def _reply(self, n: int) -> list[str] | None:
		"""Complete reply to line n, or None if the line has to be sent again."""
		lines: dict[int, str] = {}
		deadline = time.monotonic() + self._timeout
		while time.monotonic() < deadline:
			raw = self._read_line()
			text = unframe(raw) if raw is not None else None
			if text is None:
				continue
			if match := _RESEND.fullmatch(text):
				k = int(match.group(1))
				if k == n:
					return None
				if k < n:
					raise GateError(f'gate expects line {k} while line {n} was sent: session lost, gate restarted?')
				continue
			match = _CONTENT.fullmatch(text) or _BUSY.fullmatch(text) or _OK.fullmatch(text)
			if match is None:
				continue
			m = int(match.group(1))
			if m < n:
				continue
			if m > n:
				raise GateError(f'reply to line {m} while waiting for line {n}')
			if match.re is _BUSY:
				deadline = time.monotonic() + self._timeout
			elif match.re is _CONTENT:
				lines[int(match.group(2))] = match.group(3)
			else:
				count = int(match.group(2))
				return [lines[i] for i in range(count)] if sorted(lines) == list(range(count)) else None
		return None

	def _read_line(self) -> bytes | None:
		while True:
			end = self._buffer.find(b'\n')
			if end >= 0:
				line = bytes(self._buffer[:end]).rstrip(b'\r')
				del self._buffer[:end + 1]
				return line
			if len(self._buffer) > MAX_REPLY:
				self._buffer.clear()
			chunk = self._port.read(self._port.in_waiting or 1)
			if not chunk:
				return None
			self._buffer += chunk
