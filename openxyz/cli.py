#!/usr/bin/env python3
"""
OpenXYZ Terminal — interactive cmd-based CLI for controlling the XYZ stage.
"""

import cmd
import decimal
import logging
import signal
import sys
import threading

from openxyz.marlin import Marlin
from openxyz.xyz_stage import Stage


class OpenXYZTerminal(cmd.Cmd):
    intro = "OpenXYZ Terminal — type 'help' for available commands.\n"
    prompt = "openxyz> "

    def __init__(self, stage: Stage, marlin: Marlin):
        super().__init__()
        self.stage = stage
        self.marlin = marlin

    def _run_command(self, func):
        """Run a stage command in a thread so Ctrl+C can emergency stop."""
        result = [None]
        error = [None]

        def target():
            try:
                result[0] = func()
            except Exception as e:
                error[0] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        try:
            while t.is_alive():
                t.join(timeout=0.1)
        except KeyboardInterrupt:
            print("\n  EMERGENCY STOP!")
            try:
                self.marlin.send_gcode('M410')
            except Exception:
                pass
            return None

        if error[0]:
            raise error[0]
        return result[0]

    # --- Movement ---

    def do_pos(self, arg):
        """Show current position: pos"""
        def f():
            x, y, z = self.stage.xyz
            print(f"  X={x}  Y={y}  Z={z}")
        self._run_command(f)

    def do_x(self, arg):
        """Move X axis: x <mm>"""
        if not arg:
            def f():
                print(f"  X={self.stage.x}")
            self._run_command(f)
            return
        def f():
            self.stage.x = decimal.Decimal(arg)
            print(f"  X -> {arg}")
        self._run_command(f)

    def do_y(self, arg):
        """Move Y axis: y <mm>"""
        if not arg:
            def f():
                print(f"  Y={self.stage.y}")
            self._run_command(f)
            return
        def f():
            self.stage.y = decimal.Decimal(arg)
            print(f"  Y -> {arg}")
        self._run_command(f)

    def do_z(self, arg):
        """Move Z axis: z <mm>"""
        if not arg:
            def f():
                print(f"  Z={self.stage.z}")
            self._run_command(f)
            return
        def f():
            self.stage.z = decimal.Decimal(arg)
            print(f"  Z -> {arg}")
        self._run_command(f)

    def do_goto(self, arg):
        """Move to XY position: goto <x> <y>"""
        parts = arg.split()
        if len(parts) != 2:
            print("  Usage: goto <x> <y>")
            return
        def f():
            self.stage.xy = (decimal.Decimal(parts[0]), decimal.Decimal(parts[1]))
            print(f"  XY -> ({parts[0]}, {parts[1]})")
        self._run_command(f)

    # --- Homing ---

    def do_home(self, arg):
        """Home all axes: home [force]"""
        force = arg.strip().lower() == 'force'
        def f():
            self.stage.auto_home(only_untrusted=not force)
            print("  Homed.")
        self._run_command(f)

    # --- Settings ---

    def do_feedrate(self, arg):
        """Get/set feedrate percent: feedrate [percent]"""
        if not arg:
            def f():
                print(f"  Feedrate: {self.stage.feedrate_percent}%")
            self._run_command(f)
            return
        def f():
            self.stage.feedrate_percent = int(arg)
            print(f"  Feedrate -> {arg}%")
        self._run_command(f)

    def do_lcd(self, arg):
        """Set LCD message: lcd <message>"""
        if not arg:
            print("  Usage: lcd <message>")
            return
        def f():
            self.stage.set_lcd_message(arg)
            print(f"  LCD -> '{arg}'")
        self._run_command(f)

    # --- Raw G-code ---

    def do_gcode(self, arg):
        """Send raw G-code: gcode <command>"""
        if not arg:
            print("  Usage: gcode <command>")
            return
        def f():
            response = self.marlin.send_gcode(arg)
            print(f"  {response}")
        self._run_command(f)

    # --- Encoder ---

    def do_encoder(self, arg):
        """Read encoder values: encoder"""
        def f():
            status = self.marlin.get_encoder_status()
            print(f"  Encoder X={status['x']}  Y={status['y']}")
        self._run_command(f)

    # --- Session ---

    def do_quit(self, arg):
        """Exit the terminal: quit"""
        print("  Bye.")
        return True

    do_exit = do_quit
    do_EOF = do_quit

    def emptyline(self):
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="OpenXYZ Terminal")
    parser.add_argument('--ip', default='openxyz', help='Marlin IP/hostname (default: openxyz)')
    parser.add_argument('--mock', action='store_true', help='Use mock connection')
    parser.add_argument('--feedrate', type=int, default=100, help='Movement feedrate mm/min (default: 100)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    print(f"Connecting to Marlin at {args.ip}...")
    marlin = Marlin(ip=args.ip, mock=args.mock)
    stage = Stage(marlin, feedrate=args.feedrate)
    print("Connected.\n")

    OpenXYZTerminal(stage, marlin).cmdloop()


if __name__ == '__main__':
    main()
