# Sample show output

The files directly in this folder were written by hand from typical Cisco
IOS output, not captured from our switches. Spacing and wording on IOSvL2
may be different. They are kept for the older tests.

Subfolders:

- `real/`: raw `.txt` captures from the five IOSvL2 15.2 switches in GNS3
  (baseline state, before the tool ran; added 2 Nov 2026). Only these count
  as verified samples. Tested in `tests/test_parsers_real.py`. Passwords in
  `*_show_running_config.txt` are replaced with `<REDACTED>`.
  Not captured yet: `show running-config interface <port>`, and any output
  after the tool has added VLANs 10 to 40.
- `transcribed/`: typed by hand from screenshots of real IOSvL2 output.
  The content is real, but spacing may differ. Each file says so in a
  `#` header.
- `synthetic/`: made-up variants of real layouts, built to test one
  specific parser behaviour. Never evidence of real switch output.
  `SW1_after_*` and `SW2_after_*` are hand-edited copies of the real
  baseline showing the expected state after the tool runs (used to test
  that verify() passes). Replace them with real captures after a lab run.

To add a capture: save it as `real/<SWITCH>_<command>.txt` (for example
`SW2_show_run_int_gi0_1.txt`), add a test for it, and re-run `pytest`.
