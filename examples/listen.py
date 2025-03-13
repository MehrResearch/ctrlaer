"""
Listens to commands from stdin and executes them.
The commands are expected to be in the format:
<cmd>,<duration> where <cmd> is the command to be executed
and <duration> is the duration in milliseconds.

If the received command is 'END', listening stops and the function returns."""

from pico_ctrlaer import CtrlAer
from sys import stdin

ctrlaer = CtrlAer(sm_number=0, base_pin=0, n_pins=1, freq=5000)

ctrlaer.listen(stdin)
