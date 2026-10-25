#!/usr/bin/env python3
"""
OpenXYZ gate: the only path from the PC to Marlin. Runs on the CM4 between the board's CH340
UART (PC side, /dev/serial0) and Marlin's USB CDC port, and forwards only commands that pass
the safety policy below.

PC -> gate      N<n> <command>*<CRC-32 of "N<n> <command>" as 8 upper-case hex digits>
gate -> PC      N<n>:<i> <text>     reply line i to request n
                N<n> busy           keepalive while Marlin works
                N<n> ok <count>     end of the reply to request n, which has <count> lines
                rs N<k>             input corrupted, truncated or out of order: resend line k
                Every gate -> PC line carries *<CRC-32> as well.
                A repeated request n gets its cached reply again and is never executed twice.
                "N0 M110" starts a new session.
gate -> Marlin  N<m> <command>*<Marlin checksum>, re-serialised from the parsed values (never the
                received text). Every move is followed by M400 and M114, and the reported position
                must equal the target.

Safety policy, everything else is rejected:
  G28 Z                      always (Z homes upwards, away from the target)
  G28 X | G28 Y | G28 X Y    only after G28 Z, with Z at its home position
  G0/G1 X<v> Y<v> | Z<v>     after homing, targets inside the machine TRAVEL, Z moves alone
  G90 G21 M114 M400 M115 M119 M122 M410, M117 <text>
  No F words, no relative mode, no offsets, no settings.
  Limits of a particular scan area belong to the PC-side script that instantiates the stage.
"""

import dataclasses
import logging
import re
import time
import zlib
from collections.abc import Callable
from decimal import Decimal

import serial
import serial.tools.list_ports

UART = '/dev/serial0'
BAUDRATE = 115200
MARLIN_USB_ID = (0x0483, 0x5740)

TRAVEL = {axis: (Decimal('0'), Decimal('100')) for axis in 'XYZ'}  # Configuration.h: X/Y_BED_SIZE, Z_MAX_POS
HOME = {'X': Decimal('0'), 'Y': Decimal('0'), 'Z': Decimal('100')}
POSITION_TOLERANCE = Decimal('0.0001')

MAX_REQUEST = 128
MAX_REPLY = 512
MAX_TEXT = 400
MARLIN_IDLE_TIMEOUT = 30.0
MARLIN_ATTEMPTS = 3

MOVES = {'G0', 'G1'}
NO_ARGUMENT_COMMANDS = {'G90', 'G21', 'M114', 'M400', 'M115', 'M119', 'M122', 'M410'}
HOMING = {('Z',), ('X',), ('Y',), ('X', 'Y'), ('Y', 'X')}

_REQUEST = re.compile(r'N(0|[1-9][0-9]{0,8}) ([\x20-\x29\x2B-\x7E]+)')
_CHECK = re.compile(rb'[0-9A-F]{8}')
_PRINTABLE = re.compile(rb'[\x20-\x7E]*')
_VALUE = re.compile(r'[0-9]{1,3}(\.[0-9]{1,4})?')
_M117_TEXT = re.compile(r'[A-Za-z0-9 ._:-]{0,40}')
_POSITION = re.compile(r'X:(-?[0-9]+\.[0-9]+) Y:(-?[0-9]+\.[0-9]+) Z:(-?[0-9]+\.[0-9]+)')

log = logging.getLogger(__name__)


class Rejected(Exception):
	"""The command was not executed."""


class Failed(Exception):
	"""The command was executed, but Marlin did not end up where it should; homing is cleared."""


class MarlinError(Exception):
	"""The link to Marlin is broken or out of sync; the gate has to restart."""


def crc(data: bytes) -> bytes:
	return format(zlib.crc32(data), '08X').encode('ascii')


def frame(text: str) -> bytes:
	data = text.encode('ascii')
	return data + b'*' + crc(data) + b'\n'


def unframe(line: bytes, limit: int = MAX_REPLY) -> str | None:
	"""Text of one CRC-framed line (without newline), or None unless it is intact."""
	star = line.rfind(b'*')
	if star < 0 or len(line) > limit:
		return None
	data, check = line[:star], line[star + 1:]
	if not _CHECK.fullmatch(check) or not _PRINTABLE.fullmatch(data) or crc(data) != check:
		return None
	return data.decode('ascii')


def parse_request(line: bytes) -> tuple[int, str] | None:
	text = unframe(line, MAX_REQUEST)
	match = _REQUEST.fullmatch(text) if text is not None else None
	return (int(match.group(1)), match.group(2)) if match else None


def sanitize(text: str) -> str:
	return ''.join(c if ' ' <= c <= '~' and c != '*' else ' ' if c == '\t' else '?' for c in text[:MAX_TEXT])


def marlin_checksum(line: str) -> int:
	checksum = 0
	for byte in line.encode('ascii'):
		checksum ^= byte
	return checksum


def within(value: Decimal, limits: tuple[Decimal, Decimal]) -> bool:
	return limits[0] <= value <= limits[1]


@dataclasses.dataclass(frozen=True)
class Command:
	code: str
	axes: tuple[tuple[str, Decimal | None], ...] = ()
	text: str | None = None

	def canonical(self) -> str:
		if self.text is not None:
			return f'{self.code} {self.text}' if self.text else self.code
		return ' '.join([self.code] + [axis if value is None else f'{axis}{value:f}' for axis, value in self.axes])


def parse_command(command: str) -> Command:
	"""Syntax and static limits of one command; raises Rejected."""
	if command == 'M117' or command.startswith('M117 '):
		text = command[5:]
		if not _M117_TEXT.fullmatch(text):
			raise Rejected('M117 text may only contain letters, digits, spaces and ._:-')
		return Command('M117', text=text)
	words = command.split(' ')
	if '' in words:
		raise Rejected('words must be separated by single spaces')
	code, arguments = words[0], words[1:]
	if code in NO_ARGUMENT_COMMANDS:
		if arguments:
			raise Rejected(f'{code} takes no arguments')
		return Command(code)
	if code == 'G28':
		if tuple(arguments) not in HOMING:
			raise Rejected('homing must be "G28 Z", "G28 X", "G28 Y" or "G28 X Y"')
		return Command(code, tuple((axis, None) for axis in arguments))
	if code in MOVES:
		targets = {}
		for word in arguments:
			axis, value = word[:1], word[1:]
			if axis not in TRAVEL:
				raise Rejected(f'{code} takes only X, Y or Z, got "{word}"')
			if axis in targets:
				raise Rejected(f'{axis} given twice')
			if not _VALUE.fullmatch(value):
				raise Rejected(f'invalid number in "{word}"')
			targets[axis] = Decimal(value)
		if not targets:
			raise Rejected(f'{code} without a target')
		if 'Z' in targets and len(targets) > 1:
			raise Rejected('Z has to move alone')
		for axis, target in targets.items():
			if not within(target, TRAVEL[axis]):
				raise Rejected(f'{axis}{target} outside the travel [{TRAVEL[axis][0]}, {TRAVEL[axis][1]}]')
		return Command(code, tuple(sorted(targets.items())))
	raise Rejected(f'"{code}" is not allowed')


class MarlinLink:
	"""Marlin's host protocol: numbered lines with checksum, resend on request, wait for "ok"."""

	def __init__(self, port: serial.Serial, idle_timeout: float = MARLIN_IDLE_TIMEOUT):
		self._port = port
		self._idle_timeout = idle_timeout
		self._buffer = bytearray()
		self._n = 0

	def start(self) -> None:
		"""Restart Marlin's line numbering and select absolute millimetre coordinates."""
		self._drain()
		self._port.write(b'M110 N0\n')
		self._read_reply(lambda: None)
		self._n = 0
		for command in ('G21', 'G90'):
			self.command(command, lambda: None)

	def command(self, command: str, busy: Callable[[], None]) -> list[str]:
		self._n += 1
		line = f'N{self._n} {command}'
		data = f'{line}*{marlin_checksum(line)}\n'.encode('ascii')
		self._drain()
		for _ in range(MARLIN_ATTEMPTS):
			self._port.write(data)
			lines, resend = self._read_reply(busy)
			if resend is None:
				return lines
			if resend != self._n:
				raise MarlinError(f'Marlin asked for line {resend} while line {self._n} was sent')
		raise MarlinError(f'Marlin rejected line {self._n} {MARLIN_ATTEMPTS} times')

	def position(self, busy: Callable[[], None]) -> dict[str, Decimal]:
		for line in self.command('M114', busy):
			match = _POSITION.match(line)
			if match:
				return dict(zip('XYZ', (Decimal(value) for value in match.groups())))
		raise MarlinError('no position in the reply to M114')

	def _drain(self) -> None:
		self._port.reset_input_buffer()
		self._buffer.clear()

	def _read_reply(self, busy: Callable[[], None]) -> tuple[list[str], int | None]:
		lines, resend = [], None
		deadline = time.monotonic() + self._idle_timeout
		while True:
			line = self._read_line()
			if line is None:
				if time.monotonic() > deadline:
					raise MarlinError('Marlin does not answer' + (f', its last lines: {" | ".join(lines[-5:])}' if lines else ''))
				continue
			log.info(f'<- {line}')
			deadline = time.monotonic() + self._idle_timeout
			if line == 'ok' or line.startswith('ok '):
				return lines, resend
			if line.startswith('Resend:'):
				try:
					resend = int(line[len('Resend:'):])
				except ValueError:
					raise MarlinError(f'malformed "{line}"') from None
			elif line.startswith('echo:busy:'):
				busy()
			elif line:
				lines.append(line)

	def _read_line(self) -> str | None:
		while True:
			end = self._buffer.find(b'\n')
			if end >= 0:
				line = bytes(self._buffer[:end])
				del self._buffer[:end + 1]
				return line.decode('ascii', errors='replace').strip()
			chunk = self._port.read(self._port.in_waiting or 1)
			if not chunk:
				return None
			self._buffer += chunk


class Gate:
	def __init__(self, marlin: MarlinLink):
		self._marlin = marlin
		self._last_n = 0
		self._reply: list[str] = []
		self._homed: set[str] = set()

	def request_resend(self, send: Callable[[str], None]) -> None:
		send(f'rs N{self._last_n + 1}')

	def handle(self, line: bytes, send: Callable[[str], None]) -> None:
		"""One received line (without newline); send() transmits one reply line to the PC."""
		request = parse_request(line)
		if request is None:
			self.request_resend(send)
			return
		n, command = request
		if n == 0 and command == 'M110':
			self._last_n, self._reply = 0, []
			send('N0 ok 0')
			return
		if n == self._last_n and n > 0:
			for i, text in enumerate(self._reply):
				send(f'N{n}:{i} {text}')
			send(f'N{n} ok {len(self._reply)}')
			return
		if 0 < n < self._last_n:
			return
		if n != self._last_n + 1:
			self.request_resend(send)
			return

		reply: list[str] = []

		def content(text: str) -> None:
			text = sanitize(text)
			send(f'N{n}:{len(reply)} {text}')
			reply.append(text)

		def busy() -> None:
			send(f'N{n} busy')

		failure = None
		try:
			self._execute(parse_command(command), content, busy)
		except Rejected as error:
			log.warning(f'rejected "{command}": {error}')
			content(f'Error:gate: rejected: {error}')
		except Failed as error:
			log.error(f'failed "{command}": {error}')
			self._homed.clear()
			content(f'Error:gate: failed, homing cleared: {error}')
		except MarlinError as error:
			log.error(f'Marlin link lost during "{command}": {error}')
			self._homed.clear()
			content(f'Error:gate: Marlin link lost: {error}')
			failure = error
		self._last_n, self._reply = n, reply
		send(f'N{n} ok {len(reply)}')
		if failure is not None:
			raise failure

	def _execute(self, command: Command, content: Callable[[str], None], busy: Callable[[], None]) -> None:
		if command.code in MOVES:
			self._move(command, content, busy)
		elif command.code == 'G28':
			self._home(command, content, busy)
		else:
			self._forward(command.canonical(), content, busy)
			if command.code == 'M410':
				self._homed.clear()

	def _home(self, command: Command, content: Callable[[str], None], busy: Callable[[], None]) -> None:
		axes = [axis for axis, _ in command.axes]
		if axes != ['Z']:
			if 'Z' not in self._homed:
				raise Rejected('home Z first (G28 Z)')
			z = self._marlin.position(busy)['Z']
			if z != HOME['Z']:
				raise Rejected(f'Z must be at its home position {HOME["Z"]} before X/Y are homed, it is at {z}')
		for axis in axes:
			self._homed.discard(axis)
		self._forward(command.canonical(), content, busy)
		position = self._marlin.position(busy)
		for axis in axes:
			if abs(position[axis] - HOME[axis]) > POSITION_TOLERANCE:
				raise Failed(f'{axis} is at {position[axis]} after homing, expected {HOME[axis]}')
		self._homed.update(axes)

	def _move(self, command: Command, content: Callable[[str], None], busy: Callable[[], None]) -> None:
		if self._homed != {'X', 'Y', 'Z'}:
			raise Rejected('not homed: G28 Z, then G28 X Y')
		targets = dict(command.axes)
		self._forward(command.canonical(), content, busy)
		self._forward('M400', content, busy)
		position = self._marlin.position(busy)
		for axis, target in targets.items():
			if abs(position[axis] - target) > POSITION_TOLERANCE:
				raise Failed(f'{axis} is at {position[axis]} after the move, expected {target}')

	def _forward(self, command: str, content: Callable[[str], None], busy: Callable[[], None]) -> None:
		log.info(f'-> {command}')
		lines = self._marlin.command(command, busy)
		for line in lines:
			content(line)
		if any(line.startswith('Error:') for line in lines):
			raise Failed(f'Marlin reported an error on "{command}"')


def serve(uart: serial.Serial, gate: Gate) -> None:
	buffer = bytearray()

	def send(text: str) -> None:
		uart.write(frame(text))

	while True:
		chunk = uart.read(uart.in_waiting or 1)
		if not chunk:
			continue
		buffer += chunk
		while (end := buffer.find(b'\n')) >= 0:
			line = bytes(buffer[:end]).rstrip(b'\r')
			del buffer[:end + 1]
			if line:
				gate.handle(line, send)
		if len(buffer) > MAX_REQUEST:
			buffer.clear()
			gate.request_resend(send)


def find_marlin() -> str:
	ports = [port.device for port in serial.tools.list_ports.comports() if (port.vid, port.pid) == MARLIN_USB_ID]
	if len(ports) != 1:
		raise MarlinError(f'expected one Marlin USB port {MARLIN_USB_ID[0]:04x}:{MARLIN_USB_ID[1]:04x}, found {ports}')
	return ports[0]


def main() -> int:
	logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
	marlin = MarlinLink(serial.Serial(find_marlin(), BAUDRATE, timeout=0.1))
	marlin.start()
	uart = serial.Serial(UART, BAUDRATE, timeout=0.1)
	uart.reset_input_buffer()
	log.info(f'gate up: {UART} <-> Marlin')
	serve(uart, Gate(marlin))
	return 1


if __name__ == '__main__':
	raise SystemExit(main())
