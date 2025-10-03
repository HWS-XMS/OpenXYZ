import json
import logging
import time
import requests


class Marlin:
	"""
	Abstracts the connection to a Marlin (either via IP or USB).

	:param ip: IP address of Marlin
	:type ip: str, optional
	:param mock: If True, use a mock Marlin connection
	:type mock: bool, optional
	:raises Exception: If unable to connect to Marlin
	"""

	def __init__(self, ip: str, mock: bool = False):
		self._log = logging.getLogger(__name__)
		self._mock = mock
		if self._mock:
			self.ip = None
			self.url = None
			self._log.info("[Mock Marlin] Using mock Marlin.")
			return

		self.ip = ip
		self.url = f'http://{self.ip}:5000'
		logging.info(f"[Connecting to Marlin] {self.ip}")
		try:
			response = requests.get(f'{self.url}/status')
		except requests.exceptions.ConnectionError:
			raise Exception(f"Could not connect to Marlin at {self.ip}")
		if response.status_code == 200:
			logging.info(f"\tconnected!")
		else:
			raise Exception(f"Could not connect to Marlin: {response.text}")

	def send_gcode(self, gcode: str) -> str or None:
		"""
		Sends a G-code command to Marlin.

		:param gcode: The G-code command to send
		:type gcode: str
		:return: The response from Marlin
		:rtype: str or None
		:raises Exception: If unable to send G-code
		"""
		if self._mock:
			self._log.info(f"\tSending G-code: {gcode}")
			return

		payload = {'gcode': gcode}

		retry_count = 0
		while True:
			try:
				response = requests.post(f'{self.url}/send_gcode', json=payload)
				while response.status_code != 200:
					response = requests.post(f'{self.url}/send_gcode', json=payload)
					if response.status_code == 200:
						break
				if response.status_code == 200:
					break
			except:
				self._log.warning(f"({retry_count})\tCould not send G-code, retrying...")
				time.sleep(5)
				retry_count += 1

		response = response.text
		response = json.loads(response)["response"]
		if "echo:Unknown command:" in response:
			raise Exception(f"Unknown command: {gcode}")
		return response if response else None

	def get_encoder_status(self) -> dict:
		"""
		Returns the x, y encoder values.

		:return: Dictionary with x, y encoder values
		:rtype: dict
		"""
		response = requests.get(f'{self.url}/encoder_status')
		response = json.loads(response.text)
		return response