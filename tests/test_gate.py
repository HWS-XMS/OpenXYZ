"""
Verification of openxyz/gate.py and openxyz/marlin_uart.py.
Run from the OpenXYZ root:  python -m unittest -v tests/test_gate.py
"""

import itertools
import os
import random
import re
import select
import threading
import tty
import unittest
from decimal import Decimal

import serial

from openxyz import gate
from openxyz.gate import (Gate, MarlinError, MarlinLink, frame, marlin_checksum, parse_command, parse_request,
						  serve, unframe, Rejected)
from openxyz.marlin_uart import GateError, MarlinUart


def pty_pair() -> tuple[int, str]:
	master, slave = os.openpty()
	tty.setraw(master)
	tty.setraw(slave)
	return master, os.ttyname(slave)


class FakeMarlin:
	"""Marlin's host protocol as in Marlin/src/gcode/queue.cpp, plus the commands the gate may forward."""

	def __init__(self):
		self.master, self.device = pty_pair()
		self.position = {'X': Decimal('12.3'), 'Y': Decimal('4.5'), 'Z': Decimal('60')}
		self.last_n = 0
		self.received: list[str] = []
		self.resend_once: set[int] = set()
		self.move_error: tuple[str, Decimal] | None = None
		self.silent = False
		self._buffer = bytearray()
		threading.Thread(target=self._run, daemon=True).start()

	def moves(self) -> list[str]:
		return [command for command in self.received if command.split()[0] in ('G0', 'G1', 'G28')]

	def _write(self, text: str) -> None:
		os.write(self.master, (text + '\n').encode('ascii'))

	def _run(self) -> None:
		while True:
			self._buffer += os.read(self.master, 4096)
			while (end := self._buffer.find(b'\n')) >= 0:
				line = bytes(self._buffer[:end]).decode('ascii').strip()
				del self._buffer[:end + 1]
				if line and not self.silent:
					self._line(line)

	def _line(self, command: str) -> None:
		if command.startswith('N'):
			number = int(re.match(r'N(-?[0-9]+)', command).group(1))
			if number != self.last_n + 1 and 'M110' not in command:
				if self.last_n - 1 <= number <= self.last_n:
					return
				return self._error('Line Number is not Last Line Number+1, Last Line: ')
			star = command.rfind('*')
			if star < 0:
				return self._error('No Checksum with line number, Last Line: ')
			checksum = 0
			for byte in command[:star].encode('ascii'):
				checksum ^= byte
			if int(command[star + 1:]) != checksum or number in self.resend_once:
				self.resend_once.discard(number)
				return self._error('checksum mismatch, Last Line: ')
			self.last_n = number
			command = command[command.index(' ') + 1:star]
		self.received.append(command)
		self._execute(command)

	def _error(self, message: str) -> None:
		self._write(f'Error:{message}{self.last_n}')
		self._write(f'Resend: {self.last_n + 1}')
		self._write('ok')

	def _execute(self, command: str) -> None:
		words = command.split()
		code = words[0]
		if code in ('G0', 'G1'):
			for word in words[1:]:
				self.position[word[0]] = Decimal(word[1:])
			if self.move_error:
				axis, offset = self.move_error
				self.position[axis] += offset
		elif code == 'G28':
			self._write('echo:busy: processing')
			self._write('echo:busy: processing')
			for axis in words[1:]:
				self.position[axis] = gate.HOME[axis]
		elif code == 'M114':
			p = self.position
			self._write(f'X:{p["X"]:.4f} Y:{p["Y"]:.4f} Z:{p["Z"]:.4f} '
						f'Count X:{int(p["X"] * 6400)} Y:{int(p["Y"] * 6400)} Z:{int(p["Z"] * 6400)}')
		elif code == 'M400':
			self._write('echo:busy: processing')
		elif code == 'M110':
			self.last_n = int(words[1][1:]) if len(words) > 1 else 0
		elif code == 'M115':
			self._write('FIRMWARE_NAME:Marlin ' + 'x' * 300)
		elif code == 'M122':
			self._write('\t\tX\tY\tZ')
			self._write('Enabled\t\ttrue\ttrue\ttrue')
		elif code not in ('G21', 'G90', 'M117', 'M119', 'M410'):
			self._write(f'echo:Unknown command: "{command}"')
		self._write('ok')


class GateHarness:
	"""A gate whose PC side is driven directly: request() returns the protocol lines the PC would receive."""

	def __init__(self):
		self.marlin = FakeMarlin()
		self.link = MarlinLink(serial.Serial(self.marlin.device, 115200, timeout=0.05), idle_timeout=1.0)
		self.link.start()
		self.gate = Gate(self.link)
		self.n = 0

	def raw(self, line: bytes) -> list[str]:
		sent: list[str] = []
		self.gate.handle(line, sent.append)
		return sent

	def request(self, n: int, command: str) -> list[str]:
		return self.raw(frame(f'N{n} {command}')[:-1])

	def send(self, command: str) -> list[str]:
		"""Content lines of the next request; asserts the reply is complete."""
		self.n += 1
		lines = self.request(self.n, command)
		self.assertion_ok(lines, self.n)
		return [line.split(' ', 1)[1] for line in lines if re.fullmatch(rf'N{self.n}:[0-9]+ .*', line)]

	@staticmethod
	def assertion_ok(lines: list[str], n: int) -> None:
		contents = [line for line in lines if re.fullmatch(rf'N{n}:[0-9]+ .*', line)]
		assert lines[-1] == f'N{n} ok {len(contents)}', lines

	def home(self) -> None:
		assert self.send('G28 Z') == []
		assert self.send('G28 X Y') == []


class FaultyChannel:
	"""Byte pipe between the PC's pty and the gate's UART pty that corrupts bytes at random, both directions."""

	def __init__(self, seed: int, rate: float):
		self._random = random.Random(seed)
		self._rate = rate
		self.faults = 0
		self.pc_master, self.pc_device = pty_pair()
		self.gate_master, self.gate_device = pty_pair()
		threading.Thread(target=self._run, daemon=True).start()

	def _corrupt(self, data: bytes) -> bytes:
		out = bytearray()
		for byte in data:
			if self._random.random() >= self._rate:
				out.append(byte)
				continue
			self.faults += 1
			kind = self._random.randrange(6)
			if kind == 0:
				out.append(byte ^ (1 << self._random.randrange(8)))
			elif kind == 1:
				pass
			elif kind == 2:
				out += bytes([byte, byte])
			elif kind == 3:
				out += bytes([byte, self._random.randrange(256)])
			elif kind == 4:
				out += bytes([byte]) + b'\n'
			else:
				out += bytes(self._random.randrange(256) for _ in range(self._random.randrange(1, 12)))
		return bytes(out)

	def _run(self) -> None:
		while True:
			readable, _, _ = select.select([self.pc_master, self.gate_master], [], [])
			for fd in readable:
				data = os.read(fd, 4096)
				os.write(self.gate_master if fd == self.pc_master else self.pc_master, self._corrupt(data))


def flips(data: bytes, bits: tuple[int, ...]) -> bytes:
	out = bytearray(data)
	for bit in bits:
		out[bit // 8] ^= 1 << (bit % 8)
	return bytes(out)


FRAMES = [frame(text)[:-1] for text in ('N1 G28 Z', 'N2 G0 X51 Y45.5', 'N137 G0 Z7.6', 'N0 M110', 'N42 M114')]


class FramingTest(unittest.TestCase):

	def test_crc32_check_value(self):
		self.assertEqual(gate.crc(b'123456789'), b'CBF43926')

	def test_marlin_checksum_known_value(self):
		self.assertEqual(marlin_checksum('N1 G28'), 18)

	def test_intact_frames_parse(self):
		self.assertEqual(parse_request(frame('N2 G0 X51 Y45.5')[:-1]), (2, 'G0 X51 Y45.5'))
		self.assertEqual(unframe(frame('N7:0 X:51.0000 Y:45.5000 Z:7.6000')[:-1]), 'N7:0 X:51.0000 Y:45.5000 Z:7.6000')

	def test_all_single_and_double_bit_flips_rejected(self):
		for line in FRAMES:
			bits = range(len(line) * 8)
			for flipped in itertools.chain(itertools.combinations(bits, 1), itertools.combinations(bits, 2)):
				self.assertIsNone(parse_request(flips(line, flipped)), (line, flipped))

	def test_random_multi_bit_flips_rejected(self):
		rng = random.Random(1)
		for _ in range(200_000):
			line = rng.choice(FRAMES)
			flipped = tuple(rng.sample(range(len(line) * 8), rng.randrange(3, 9)))
			self.assertIsNone(parse_request(flips(line, flipped)), (line, flipped))

	def test_truncation_deletion_insertion_swap_rejected(self):
		for line in FRAMES:
			for i in range(len(line)):
				self.assertIsNone(parse_request(line[:i]))
				self.assertIsNone(parse_request(line[:i] + line[i + 1:]))
				for value in range(256):
					self.assertIsNone(parse_request(line[:i] + bytes([value]) + line[i:]))
				if i + 1 < len(line) and line[i] != line[i + 1]:
					self.assertIsNone(parse_request(line[:i] + line[i + 1:i + 2] + line[i:i + 1] + line[i + 2:]))

	def test_reply_bit_flips_rejected(self):
		line = frame('N12:0 X:51.0000 Y:45.5000 Z:7.6000')[:-1]
		bits = range(len(line) * 8)
		for flipped in itertools.chain(itertools.combinations(bits, 1), itertools.combinations(bits, 2)):
			self.assertIsNone(unframe(flips(line, flipped)), flipped)

	def test_frame_format_strict(self):
		data = b'N1 M114'
		self.assertIsNone(parse_request(data + b'*' + gate.crc(data).lower()))
		self.assertIsNone(parse_request(data))
		self.assertIsNone(parse_request(b'N01 M114*' + gate.crc(b'N01 M114')))
		self.assertIsNone(parse_request(b'N1 G0 X51*Y45*' + gate.crc(b'N1 G0 X51*Y45')))
		long = b'N1 M117 ' + b'A' * 200
		self.assertIsNone(parse_request(long + b'*' + gate.crc(long)))


class CommandTest(unittest.TestCase):

	def test_accepted_canonical(self):
		for command, canonical in [('G0 X51 Y45.5', 'G0 X51 Y45.5'), ('G1 Y45.5 X51', 'G1 X51 Y45.5'),
								   ('G0 X56.5', 'G0 X56.5'), ('G0 Y45.0', 'G0 Y45.0'), ('G0 X050.5', 'G0 X50.5'),
								   ('G0 Z7.6', 'G0 Z7.6'), ('G0 Z7.55', 'G0 Z7.55'), ('G0 X53.1234', 'G0 X53.1234'),
								   ('G28 Z', 'G28 Z'), ('G28 X Y', 'G28 X Y'), ('G28 Y X', 'G28 Y X'), ('G28 X', 'G28 X'),
								   ('M114', 'M114'), ('M400', 'M400'), ('G90', 'G90'), ('G21', 'G21'), ('M410', 'M410'),
								   ('M122', 'M122'), ('G0 X5', 'G0 X5'), ('G0 X0', 'G0 X0'), ('G0 Y100', 'G0 Y100'),
								   ('G0 Z100', 'G0 Z100'), ('G0 Z0', 'G0 Z0'),
								   ('M117 OpenXYZ', 'M117 OpenXYZ'), ('M117', 'M117')]:
			self.assertEqual(parse_command(command).canonical(), canonical, command)

	def test_rejected(self):
		for command in ['G0 X51 F100', 'G1 F100', 'G0 X51 Y45.5 F6000', 'G0 X51 Z7.6', 'G0 X51 X52', 'G0',
						'G0 X100.1', 'G0 Y100.0001', 'G0 Z100.5', 'G0 X999', 'G0 Z-7.6',
						'G0 X-51', 'G0 X+51', 'G0 X5.1e1', 'G0 X51.', 'G0 X.5', 'G0 X51.12345', 'G0 X1051',
						'g0 x51', 'G00 X51', 'G0  X51', ' G0 X51', 'G0 X51 ', 'G0X51', 'G0 X51 ;c', 'G0 E1', 'G0 A1',
						'G28', 'G28 X Y Z', 'G28 Z X', 'G28 X0', 'G28 O', 'G91', 'G92 X0', 'G92', 'M203 X300',
						'M220 S100', 'M201 X1', 'M204 S1', 'M205 J0.1', 'M92 X1', 'M500', 'M501', 'M502', 'M206 X1',
						'M211 S0', 'M906 X800', 'M112', 'M110 N5', 'M114 D', 'M400 S1', 'M117 a*b', 'M117 a;b',
						'M122 X', 'M122 S1', 'M122 I', 'M122 D',
						'M117 ' + 'A' * 41, 'G4 S1', 'G60', 'G61', 'M510', 'M511 P1', 'M512 P1 S2', 'M997', 'M999',
						'T0', '']:
			with self.assertRaises(Rejected, msg=command):
				parse_command(command)


class GateTest(unittest.TestCase):

	def setUp(self):
		self.h = GateHarness()

	def test_nothing_moves_before_homing(self):
		self.assertIn('Error:gate: rejected', self.h.send('G0 X51 Y45.5')[0])
		self.assertIn('Error:gate: rejected', self.h.send('G0 Z7.6')[0])
		self.assertIn('Error:gate: rejected', self.h.send('G28 X Y')[0])
		self.assertEqual(self.h.marlin.moves(), [])

	def test_homing_and_moves(self):
		self.h.home()
		self.assertEqual(self.h.send('G0 X51 Y45.5'), [])
		self.assertEqual(self.h.send('G0 Z7.6'), [])
		self.assertEqual(self.h.send('G0 X51.1'), [])
		self.assertEqual(self.h.send('M114')[0].split(' Count')[0], 'X:51.1000 Y:45.5000 Z:7.6000')
		self.assertEqual(self.h.marlin.moves(), ['G28 Z', 'G28 X Y', 'G0 X51 Y45.5', 'G0 Z7.6', 'G0 X51.1'])
		received = self.h.marlin.received
		move = received.index('G0 X51.1')
		self.assertEqual(received[move:move + 3], ['G0 X51.1', 'M400', 'M114'])

	def test_xy_homing_needs_z_at_home(self):
		self.assertEqual(self.h.send('G28 Z'), [])
		self.h.marlin.position['Z'] = Decimal('50')
		self.assertIn('Z must be at its home position', self.h.send('G28 X Y')[0])
		self.assertEqual(self.h.marlin.moves(), ['G28 Z'])

	def test_moves_anywhere_in_travel_z_alone(self):
		self.h.home()
		self.assertEqual(self.h.send('G0 X5'), [])
		self.assertEqual(self.h.send('G0 Z50'), [])
		self.assertEqual(self.h.send('G0 X100 Y0'), [])
		self.assertIn('Z has to move alone', self.h.send('G0 X5 Z40')[0])
		self.assertIn('outside the travel', self.h.send('G0 Y100.5')[0])
		self.assertEqual(self.h.marlin.moves(), ['G28 Z', 'G28 X Y', 'G0 X5', 'G0 Z50', 'G0 X100 Y0'])

	def test_forbidden_commands_never_reach_marlin(self):
		self.h.home()
		before = list(self.h.marlin.received)
		for command in ['M203 X300', 'M220 S100', 'G91', 'G92 X0', 'G0 X51 F100', 'M500', 'M92 X1']:
			self.assertIn('Error:gate: rejected', self.h.send(command)[0])
		self.assertEqual(self.h.marlin.received, before)

	def test_position_mismatch_clears_homing(self):
		self.h.home()
		self.h.marlin.move_error = ('X', Decimal('0.01'))
		self.assertIn('Error:gate: failed, homing cleared', self.h.send('G0 X51 Y45.5')[0])
		self.h.marlin.move_error = None
		self.assertIn('not homed', self.h.send('G0 X52')[0])

	def test_quickstop_clears_homing(self):
		self.h.home()
		self.assertEqual(self.h.send('M410'), [])
		self.assertIn('not homed', self.h.send('G0 X51 Y45.5')[0])

	def test_repeated_request_is_replayed_not_executed(self):
		self.h.home()
		first = self.h.request(3, 'G0 X51 Y45.5')
		second = self.h.request(3, 'G0 X51 Y45.5')
		self.assertEqual([line for line in first if 'busy' not in line], second)
		self.assertEqual(self.h.marlin.moves().count('G0 X51 Y45.5'), 1)
		third = self.h.request(4, 'M114')
		self.assertTrue(third[0].startswith('N4:0 X:51.0000 Y:45.5000'))

	def test_stale_and_out_of_order_requests(self):
		self.h.home()
		self.assertEqual(self.h.request(1, 'G28 Z'), [])
		self.assertEqual(self.h.request(5, 'M114'), ['rs N3'])
		self.assertEqual(self.h.raw(b'N3 M114*00000000'), ['rs N3'])
		self.assertEqual(self.h.raw(b'garbage'), ['rs N3'])
		self.assertEqual(self.h.marlin.moves(), ['G28 Z', 'G28 X Y'])

	def test_session_reset(self):
		self.h.home()
		self.assertEqual(self.h.request(0, 'M110'), ['N0 ok 0'])
		self.assertEqual(self.h.request(1, 'M114')[-1], 'N1 ok 1')
		self.h.n = 1
		self.assertEqual(self.h.send('G0 X51 Y45.5'), [])

	def test_marlin_resend_executes_once(self):
		self.h.home()
		self.h.marlin.resend_once.add(self.h.marlin.last_n + 2)
		self.assertEqual(self.h.send('G0 X51 Y45.5'), [])
		self.assertEqual(self.h.marlin.moves().count('G0 X51 Y45.5'), 1)

	def test_busy_is_forwarded(self):
		lines = self.h.request(1, 'G28 Z')
		self.assertEqual(lines.count('N1 busy'), 2)

	def test_driver_report_tabs_become_spaces(self):
		self.assertEqual(self.h.send('M122'), ['X Y Z', 'Enabled  true true true'])
		self.assertEqual(self.h.marlin.moves(), [])

	def test_long_marlin_line_stays_within_reply_limit(self):
		self.h.home()
		lines = self.h.request(3, 'M115')
		self.assertTrue(lines[0].startswith('N3:0 FIRMWARE_NAME:Marlin'))
		self.assertEqual(unframe(frame(lines[0])[:-1]), lines[0])

	def test_silent_marlin_raises_after_reply(self):
		self.h.home()
		self.h.marlin.silent = True
		sent: list[str] = []
		with self.assertRaises(MarlinError):
			self.h.gate.handle(frame('N3 G0 X51 Y45.5')[:-1], sent.append)
		self.assertTrue(sent[0].startswith('N3:0 Error:gate: Marlin link lost'))
		self.assertEqual(sent[-1], 'N3 ok 1')


class ResponderThread:
	"""Answers the client's lines from a script: {line number: [reply lines]}."""

	def __init__(self, script: dict[int, list[str]]):
		self.master, self.device = pty_pair()
		self._script = script
		threading.Thread(target=self._run, daemon=True).start()

	def _run(self) -> None:
		buffer = bytearray()
		while True:
			buffer += os.read(self.master, 4096)
			while (end := buffer.find(b'\n')) >= 0:
				request = parse_request(bytes(buffer[:end]))
				del buffer[:end + 1]
				for line in self._script.get(request[0], []):
					os.write(self.master, frame(line))


class ClientTest(unittest.TestCase):

	def test_session_lost_raises(self):
		responder = ResponderThread({0: ['N0 ok 0'], 1: ['N1 ok 0'], 2: ['rs N1']})
		client = MarlinUart(responder.device, timeout=0.5)
		self.assertIsNone(client.send_gcode('M400'))
		with self.assertRaisesRegex(GateError, 'session lost'):
			client.send_gcode('M400')

	def test_rejection_raises(self):
		responder = ResponderThread({0: ['N0 ok 0'], 1: ['N1:0 Error:gate: rejected: "M203" is not allowed', 'N1 ok 1']})
		client = MarlinUart(responder.device, timeout=0.5)
		with self.assertRaisesRegex(GateError, 'not allowed'):
			client.send_gcode('M203 X300')

	def test_incomplete_reply_is_requested_again(self):
		responder = ResponderThread({0: ['N0 ok 0'], 1: ['N1 ok 1', 'N1:0 X:51.0000 Y:45.5000 Z:7.6000', 'N1 ok 1']})
		client = MarlinUart(responder.device, timeout=0.5)
		self.assertEqual(client.send_gcode('M114'), 'X:51.0000 Y:45.5000 Z:7.6000')


class EndToEndTest(unittest.TestCase):
	"""PC client -> faulty UART -> gate -> fake Marlin: every command executed exactly once, in order."""

	def session(self, seed: int, rate: float) -> None:
		marlin = FakeMarlin()
		channel = FaultyChannel(seed, rate)
		link = MarlinLink(serial.Serial(marlin.device, 115200, timeout=0.05), idle_timeout=1.0)
		link.start()
		threading.Thread(target=serve, args=(serial.Serial(channel.gate_device, 115200, timeout=0.05), Gate(link)),
						 daemon=True).start()
		client = MarlinUart(channel.pc_device, timeout=0.3, attempts=100)

		moves = ['G28 Z', 'G28 X Y', 'G0 X51 Y45.5', 'G0 Z7.6']
		for column, row in itertools.product(range(5), range(5)):
			row = row if column % 2 == 0 else 4 - row
			moves.append(f'G0 X{Decimal("51") + Decimal(column) / 10} Y{Decimal("45.5") + Decimal(row) / 10}')
		for move in moves:
			self.assertIsNone(client.send_gcode(move))
			reported = client.send_gcode('M114').split(' Count')[0]
			p = marlin.position
			self.assertEqual(reported, f'X:{p["X"]:.4f} Y:{p["Y"]:.4f} Z:{p["Z"]:.4f}')
			with self.assertRaises(GateError):
				client.send_gcode('M203 X300')
		self.assertEqual(marlin.moves(), moves)
		self.assertNotIn('M203 X300', marlin.received)
		if rate:
			self.assertGreater(channel.faults, 0)

	def test_clean_channel(self):
		self.session(seed=0, rate=0.0)

	def test_noisy_channel(self):
		for seed in range(3):
			with self.subTest(seed=seed):
				self.session(seed=seed, rate=0.003)

	def test_very_noisy_channel(self):
		for seed in range(2):
			with self.subTest(seed=seed):
				self.session(seed=100 + seed, rate=0.02)


if __name__ == '__main__':
	unittest.main()
