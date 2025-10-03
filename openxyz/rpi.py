# Open-XYZ - An Open-Source Positioning System for Physical Attacks Exhibiting Spatial Resolution
# Copyright (C) 2024 Jakob Frömbgen
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

from flask import Flask, request, jsonify
import serial
import logging

from openxyz.marlin_serial 	import MarlinSerial
from openxyz.encoder 		import LS7366R, EncoderAxis

app = Flask(__name__)

enc = LS7366R(bus=0, cs_pins={
	EncoderAxis.ENCODER_AXIS_X: 23,
	EncoderAxis.ENCODER_AXIS_Y: 24
})

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@app.route('/send_gcode', methods=['POST'])
def send_gcode() -> jsonify:
	"""
	Endpoint to send a G-code command to Marlin board.

	:return: JSON response with success message or error
	:rtype: flask.Response
	:raises KeyError: If no G-code is received in request
	:raises serial.SerialException: If a serial communication error occurs
	:raises Exception: If an unexpected error occurs
	"""
	gcode = request.json.get('gcode').strip()
	if not gcode:
		logger.error("No G-code received in request.")
		return jsonify({"error": "No G-code received"}), 400

	try:
		marlin_serial = MarlinSerial('/dev/ttyACM0')

		response = marlin_serial.send_gcode(gcode)
		response = response.decode('utf-8').replace('ok\n', '').strip()

		marlin_serial.close()

		logger.info(f"G-code '{gcode}' executed successfully.")
		return jsonify({"response": response}), 200
	except serial.SerialException as e:
		logger.error(f"SerialException: {str(e)}")
		return jsonify({"error": "Serial communication error"}), 500
	except Exception as e:
		logger.error(f"Unexpected error: {str(e)}")
		return jsonify({"error": "An unexpected error occurred"}), 500


@app.route('/status', methods=['GET'])
def status() -> jsonify:
	"""
	Endpoint to check the status of Marlin board.

	:return: JSON response with status message
	:rtype: flask.Response
	"""
	logger.info("Status check requested.")
	return jsonify({"message": "Marlin is ready."}), 200


@app.route('/encoder_status', methods=['GET'])
def encoder_status() -> jsonify:
	global enc
	"""
	Endpoint to get x, y encoder values.

	:return: JSON response with status message
	:rtype: flask.Response
	"""
	x = enc.read_counter(EncoderAxis.ENCODER_AXIS_X)
	y = enc.read_counter(EncoderAxis.ENCODER_AXIS_Y)
	return jsonify({"x": x, "y": y}), 200
	

if __name__ == '__main__':
	app.run(host='0.0.0.0', port=5000)
