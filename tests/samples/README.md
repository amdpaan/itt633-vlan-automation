# Sample show output

**NOT YET VERIFIED ON IOSvL2.** Every file here was written by hand from
typical Cisco IOS output, not captured from our switches. Spacing and
wording on IOSvL2 may be different.

To do in GNS3 (week 2-3): run each command on the real switches, paste the
output into a file named `<switch>_<command>.txt` (for example
`sw1_show_vlan_brief_real.txt`), add a test for it, and re-run `pytest`.
When a parser passes on real output, update this note and the STATUS line
in `parsers.py`.

Subfolders:

- `transcribed/`: typed by hand from screenshots of real IOSvL2 output.
  The content is real, but spacing may differ. Each file says so in a
  `#` header.
- `real/` (to be added): raw `.txt` captures from the IOSvL2 switches.
  Only these count as verified samples.
- `synthetic/`: made-up variants of real layouts, built to test one
  specific parser behaviour. Never evidence of real switch output.
