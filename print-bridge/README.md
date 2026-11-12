# Wise print bridge

Prints every KOT automatically on the right kitchen printer: shake counter KOTs on the shake counter
printer, kitchen KOTs on the kitchen printer. Each KOT prints once. A reprint comes out marked
"REPRINT 2", and a cancelled order prints a "CANCELLED" slip if the kitchen already has its KOT.

## Which printers

Network thermal printers (LAN or Wi-Fi, ESC/POS, port 9100): Epson TM-T82, TVS RP 3160, Posiflex,
Everycom, most "80 mm Ethernet" models. Give each printer a fixed IP address (from its setup menu or
your router), then enter it on **Kitchen setup > preparation area > Printer address**.

USB printers? Use the **print station** in Wise instead (Kitchen setup > Printing > Open print station)
on the PC the printer is plugged into.

## Setup (one PC per outlet, on the same Wi-Fi/LAN as the printers)

1. Install Python 3 from python.org (on Windows tick "Add Python to PATH").
2. Copy `wise_print_bridge.py` to the PC.
3. Test each printer:  `python wise_print_bridge.py --test 192.168.1.50`
4. In Wise turn on **Print KOTs** for the outlet and copy the print key from Kitchen setup > Printing.
5. Start it:  `python wise_print_bridge.py --server https://your-wise-address --key YOUR_KEY`
   (On Windows, put this line in `start-wise-print.bat` and add it to the Startup folder so it starts
   with the PC.)

Kitchen setup shows "Printer working" or the last error for each area, so you can see if the bridge
or a printer stops. If the internet drops, KOTs print as soon as it is back. If a printer is off, its
KOTs wait and print when it is switched on.

Run one bridge per outlet. Running two by mistake is safe: a KOT is claimed by one bridge at a time.

Thermal printers' built-in fonts print English only; Hindi item names print as "?". Keep a short
English name for each item.

Tests: `python -m pytest test_bridge.py`
