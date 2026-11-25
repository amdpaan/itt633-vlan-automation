# Member Contribution Log

The course guideline requires a record of each member's work.
Each member adds a row for their own work. Mark AI-assisted work with "(AI-assisted)".

| Date | Member | Task | Files/Evidence |
|---|---|---|---|
| 2026-10-15 | Ahmad Farhan | Repo skeleton, parsers.py, planning.py, 24 tests | commit a9ecc46 |
| 2026-10-16 | Ahmad Farhan | Proposal v6 alignment fixes; README team/status; contribution log | commit 3b3a6a0 |
| 2026-10-23 | Ahmad Farhan | Move to IOSvL2 (Gi names); vlan_tool.py pre-check, backup, change list, dry-run; tests with fake switches (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py |
| 2026-10-24 | Ahmad Farhan | New topology: SW1 core + SW2-SW4 access (SW5 optional), VLAN 40 Finance; updated plan, inventory, samples and tests (AI-assisted) | plan.yaml, inventory.yaml, tests/ |
| 2026-10-24 | Ahmad Farhan | SW5 enabled after lab RAM test (5 switches, 8 access ports, 4 trunk links); plan, inventory, samples, tests and docs updated (AI-assisted) | plan.yaml, inventory.yaml, tests/ |
| 2026-10-24 | Ahmad Farhan | Regression test for IOSvL2 repeated STP section in `show interfaces trunk` (transcribed sample); lab status notes in CLAUDE.md (AI-assisted) | tests/samples/transcribed/, tests/test_parsers.py |
| 2026-11-02 | Ahmad Farhan | Tests on real IOSvL2 baseline captures from SW1-SW5 (30 tests); new `parse_switchport_all()` for all-ports switchport output (AI-assisted) | tests/samples/real/, tests/test_parsers_real.py, parsers.py |
| 2026-11-02 | Ahmad Farhan | Step 6 apply: flash backup to pre_change.cfg (prompts answered), send_config_set, IOS error scan, stop at first failed switch; 10 tests with FakeSwitch (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py |
| 2026-11-02 | Ahmad Farhan | Step 7 verify: check_vlans (ids + names), check_trunks (configured allowed list), check_access_ports (admin mode + VLAN), protected Gi1/0 check; stop at first failed switch; synthetic post-apply samples; 13 tests (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py, tests/samples/synthetic/ |
| 2026-11-02 | Ahmad Farhan | Step 8 save_or_rollback: write memory checked for [OK]; rollback with VLAN 99/Gi1/0 guard, full undo batch, rollback check (VLANs, trunks, PC ports, Gi1/0); run_switch statuses and summary line; apply "sent" flag; 17 tests incl. real plan.yaml undo safety test (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py |
| 2026-11-02 | Ahmad Farhan | Step 9 report and log: write_report (report.json on every ending, inventory order, no password), say/log_output/show for the log with raw device output, unique run names; print renamed to say; 7 tests (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py |
| 2026-11-03 | Ahmad Farhan | Kali automation host (192.168.99.101) in docs and Linux run steps; TC09 bad-port plan file and its test (AI-assisted) | CLAUDE.md, README.md, plan_tc09_bad_port.yaml, tests/test_planning.py |
| 2026-11-04 | Ahmad Farhan | Fixed Kali pre-check SSH failure (paramiko 5 has no SHA-1 kex / ssh-rsa for IOSvL2): pinned requirements.txt with exact versions, paramiko 4.0.0; checked on Python 3.9 and 3.14 (AI-assisted) | requirements.txt, CLAUDE.md |
| 2026-11-04 | Ahmad Farhan | Real dry-run log and SW2 flash copy / Gi0/1 captures added to tests/samples/real with 10 tests; found flash-copy wait problem before the first real run; status in CLAUDE.md (AI-assisted) | tests/samples/real/, tests/, CLAUDE.md |
| 2026-11-04 | Ahmad Farhan | Flash copy fix: wait up to 60 s for this switch's prompt (send_command + expect_string), clean fail on instant %Error or no reply; audit of all switch calls; FakeSwitch silent mode and 5 tests (3 fail on the old code) (AI-assisted) | vlan_tool.py, tests/test_vlan_tool.py, CLAUDE.md |
| 2026-11-05 | Alya Sophea | Phase 3 lab session (5 to 6 Nov): first real apply run (all 5 SAVED), TC09 rollback run, dry-run after TC09; 6 console captures of SW1/SW2 after the run | tests/samples/real/run_20261105_235328.log, run_20261106_014041.log, run_20261106_014707.log, SW1_*_after.txt, SW2_*_after.txt |
| 2026-11-06 | Ahmad Farhan | Phase 3 evidence in tests: real post-apply output replaces the synthetic _after samples (6 files removed); console (CRLF, wrapped) and SSH parser tests; verify passes on SW1-SW5 vs plan.yaml; real write memory, TC09 apply/rollback echo and post-rollback = baseline tests; 24 new tests (2 synthetic verify tests replaced), 157 total; CLAUDE.md status (AI-assisted) | tests/, tests/samples/README.md, CLAUDE.md |
| 2026-11-07 | Ahmad Farhan | Simplicity pass: tests locking the plan-refused and unsafe-state endings, then main() split from 94 to 33 lines into step helpers; 159 tests, 0 differences on 7 endings; code frozen at this commit (13 Nov) (AI-assisted) | commits f912dee, 2ade97f |
| 2026-11-14 | Alya Sophea | Week 7 lab sessions (14 to 22 Nov): test cases TC01 to TC11 on commit 2ade97f, including the TC11 manual timing trials; captured all test evidence | report JSONs and screenshots (project report, Chapter 4) |
| 2026-11-25 | Ahmad Farhan | README updated for the final version and public release (status, roles, Windows setup, test results); CLAUDE.md status updated (AI-assisted) | README.md, CLAUDE.md, CONTRIBUTIONS.md |
