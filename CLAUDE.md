# ITT633 VLAN Automation: Project Context for Claude Code

Read this file before writing or changing any code in this repository.

## What this project is

A university group project (ITT633, FSKM, UiTM Shah Alam, semester September 2026 to January 2027, lecturer Kamarul Ariffin bin Abdul Basit). Group of 4 students (see the Team table in README.md). The group leader is Muhammad Afiq bin Khairul Anuar; the other roles are not decided yet, so do not assign names to tasks. Record each member's work in CONTRIBUTIONS.md.

The project builds a Python tool that deploys VLAN configuration to five Cisco switches from one YAML file, verifies the result against the file, saves only when every check passes, and rolls back its own changes when a check fails.

Working title: **Intent-Driven VLAN Automation with Verification and Rollback Using Python and Netmiko**.

Scenario: Perdana Niaga Sdn. Bhd., an imaginary retail company with a three-storey head office: one core switch (SW1) and four access switches (SW2 to SW5).

## Rules that come from the course guideline

- AI help is allowed, but every student must be able to understand, explain, modify, test and verify any AI-written code. **Keep the code simple and readable.** Prefer plain functions, clear names and short comments over clever abstractions, classes or extra libraries. If a beginner cannot follow it, simplify it.
- The project must not copy an existing project. If you use an idea or snippet from a tutorial or repository, say so in a comment and tell the user so it can be cited in the report.
- Success must be proven on the network (show commands and pings), not only by the script finishing without errors.
- Scope is fixed by the approved proposal. Do not add features outside the core list below unless the user asks. Flag anything that could break scope or the 8-week timeline.
- Approved scope change (24 Oct 2026): after reviewing the proposal, the lecturer asked for a bigger topology: 4 to 5 switches (SW1 core, SW2 to SW4 access, SW5 optional) and a new VLAN 40 Finance. The network design below already reflects this. It is a lecturer request, not a scope risk; mention it in the report as the reason the design differs from the original three-switch proposal. The group then made SW5 part of the topology after a lab RAM test passed with all five switches running.

## Environment

- Development: the user writes code on an **Apple Silicon Mac** and shares it with the group through Git/GitHub. Check arm64 compatibility before suggesting any tool.
- Lab: GNS3 (2.2.x) with the GNS3 VM, running on a Windows PC or the faculty lab computers. Cisco x86 images do not run well on Apple Silicon.
- **Paan does not run GNS3.** A friend runs the lab and sends back saved output, screenshots or error messages. Paan then adds them to the repo or pastes them into the session. Anything that needs the lab (running the tool, show commands, pings, test-case evidence) goes through that friend.
- Switch image: Cisco **IOSvL2 15.2** in GNS3 (replaced IOL-L2; manual baseline confirmed working 24 Oct 2026, tool not yet run on it). Interface names are `GigabitEthernet0/0`, `GigabitEthernet0/1`, and so on (short form `Gi0/x` in show output). Old IOL-L2 names map as e0/0->Gi0/0, e0/1->Gi0/1, e0/2->Gi0/2, e0/3->Gi0/3, e1/0->Gi1/0. If the image changes again, only plan.yaml and `PROTECTED_PORTS` in planning.py should need to change.
- End hosts: VPCS. The automation host joins VLAN 99 through a GNS3 Cloud node (method still to be tested, for example a Windows loopback adapter).
- **The manual baseline was verified on real IOSvL2 (24 Oct). The Python tool has not yet run against a real switch.** Never claim that code or commands work on the switches. Say what still needs testing in GNS3.

## Network design

| VLAN | Name | Subnet |
|---|---|---|
| 10 | Sales | 192.168.10.0/24 |
| 20 | IT | 192.168.20.0/24 |
| 30 | Guest | 192.168.30.0/24 |
| 40 | Finance | 192.168.40.0/24 |
| 99 | Management | 192.168.99.0/24 |

| Device | Port | VLAN | IP |
|---|---|---|---|
| SW1 (core, no PCs) | SVI vlan 99 | 99 | 192.168.99.11 |
| SW2 (access) | SVI vlan 99 | 99 | 192.168.99.12 |
| SW3 (access) | SVI vlan 99 | 99 | 192.168.99.13 |
| SW4 (access) | SVI vlan 99 | 99 | 192.168.99.14 |
| SW5 (access) | SVI vlan 99 | 99 | 192.168.99.15 |
| Automation host | SW1 GigabitEthernet1/0 | 99 | 192.168.99.100 |
| PC1 Sales | SW2 GigabitEthernet0/1 | 10 | 192.168.10.10 |
| PC2 IT | SW2 GigabitEthernet0/2 | 20 | 192.168.20.10 |
| PC3 Sales | SW3 GigabitEthernet0/1 | 10 | 192.168.10.11 |
| PC4 Finance | SW3 GigabitEthernet0/2 | 40 | 192.168.40.10 |
| PC5 IT | SW4 GigabitEthernet0/1 | 20 | 192.168.20.11 |
| PC6 Guest | SW4 GigabitEthernet0/2 | 30 | 192.168.30.10 |
| PC7 Finance | SW5 GigabitEthernet0/1 | 40 | 192.168.40.11 |
| PC8 Guest | SW5 GigabitEthernet0/2 | 30 | 192.168.30.11 |

Trunks (802.1Q), star from the core: SW1 GigabitEthernet0/1 to SW2, SW1 GigabitEthernet0/2 to SW3, SW1 GigabitEthernet0/3 to SW4, and SW1 GigabitEthernet1/1 to SW5. Each access switch uplinks on its GigabitEthernet0/0. Every trunk's final allowed list is 10, 20, 30, 40, 99.

Totals: 5 switches, 4 user VLANs (10, 20, 30, 40) plus management VLAN 99, 8 access ports (PC1 to PC8), and 4 trunk links (SW1 to each access switch).

There is **no router**. Pings between different VLANs must fail; that proves isolation.

### Manual baseline (done by hand before the tool runs)

Hostname, `vtp mode transparent`, VLAN 99 and its SVI address, local user (privilege 15, so the tool lands in enable mode), SSH, every trunk link (SW1 Gi0/1, Gi0/2, Gi0/3, Gi1/1; Gi0/0 on each access switch; mode trunk, allowed VLAN 99), and the host port SW1 GigabitEthernet1/0 in VLAN 99. The tool reaches SW2 to SW5 through these trunks, so **the tool must never break them**. VTP transparent mode means each switch keeps its own VLAN list (no VTP sync between switches), so the tool must create the VLANs on every switch, and VLANs are stored in the running config.

## What the tool does (core scope)

1. Read `inventory.yaml` and `plan.yaml`.
2. Pre-check: SSH to every switch. If any switch is unreachable or login fails, stop before changing anything and log which switch and why.
3. Back up each switch: save `show running-config` to `backups/<switch>_<timestamp>.cfg`, and run `copy running-config flash:pre_change.cfg` on the switch (handle the confirmation prompt).
4. Build the change list per switch by comparing plan.yaml with the current state. Only add what is missing. For every command, record a matching undo entry before applying anything.
5. `--dry-run`: print the commands (and the undo list) per switch, then stop.
6. Apply with Netmiko `send_config_set` (running config only). Treat Cisco error text in the output (`% Invalid input`, `% Incomplete command`, `% Ambiguous command`) as a failure.
7. Verify against plan.yaml using `show vlan brief`, `show interfaces trunk` and `show interfaces <port> switchport`.
8. Pass: `write memory` (Netmiko `save_config()`). Fail: run the undo list for that switch, do not save.
9. Write `reports/report_<timestamp>.json` and a log file in `logs/`.

Processing order: SW1, then SW2, SW3, SW4, SW5, one switch at a time (apply, verify, save or roll back). If a switch fails, roll it back and stop; do not touch the remaining switches. Switches already passed stay saved, and the report must say so. Rolling back all switches together is a stretch goal.

### Undo rules (rollback Method C)

| Change made | Undo entry |
|---|---|
| `vlan 30` + `name Guest` (VLAN did not exist) | `no vlan 30` |
| `switchport trunk allowed vlan add 30` | `switchport trunk allowed vlan remove 30` |
| Access port that had no config before | `default interface GigabitEthernet0/3` |
| Access port that already had settings | `default interface <port>`, then `interface <port>` and re-apply the port's old lines from `show running-config interface <port>` captured in step 3 (resetting first removes the tool's new lines, such as `switchport mode access`) |

The undo list runs in reverse order of the changes (last change undone first): access ports, then trunks, then VLANs, so no port is left in a VLAN that is being deleted.

Known limitation to keep honest in the report: the undo list only reverses what the tool recorded. The backup exists for manual restore. Cisco `configure replace` (Method B) is a stretch goal only if the image supports it; do not present it as the plan.

### Safety rules (must hold in all code)

- Trunk changes always use `switchport trunk allowed vlan add` / `remove`. Never send `switchport trunk allowed vlan <list>` without `add`/`remove`, because it replaces the list and drops VLAN 99.
- Never create, delete, remove or modify VLAN 99, the SVIs, the trunk mode of the inter-switch links, or SW1 GigabitEthernet1/0. Validate plan.yaml at load time and refuse a plan that touches them.
- VLAN names in plan.yaml must be 1 to 32 characters with no spaces (keeps parsing `show vlan brief` simple). `validate_plan()` enforces this.
- Never `write memory` before verification passes.
- Never store passwords in files or Git. Ask with `getpass` at run time (an environment variable is acceptable as an option). Add `backups/`, `reports/`, `logs/` and any `.env` to `.gitignore`.

## Input file formats (proposed; adjust only with the user)

```yaml
# inventory.yaml
SW1: {host: 192.168.99.11, device_type: cisco_ios}
SW2: {host: 192.168.99.12, device_type: cisco_ios}
SW3: {host: 192.168.99.13, device_type: cisco_ios}
SW4: {host: 192.168.99.14, device_type: cisco_ios}
SW5: {host: 192.168.99.15, device_type: cisco_ios}
```

```yaml
# plan.yaml
vlans:
  - {id: 10, name: Sales}
  - {id: 20, name: IT}
  - {id: 30, name: Guest}
  - {id: 40, name: Finance}
switches:
  SW1:
    trunks:
      GigabitEthernet0/1: [10, 20, 30, 40]    # to SW2
      GigabitEthernet0/2: [10, 20, 30, 40]    # to SW3
      GigabitEthernet0/3: [10, 20, 30, 40]    # to SW4
      GigabitEthernet1/1: [10, 20, 30, 40]    # to SW5
  SW2:
    access: {GigabitEthernet0/1: 10, GigabitEthernet0/2: 20}
    trunks: {GigabitEthernet0/0: [10, 20, 30, 40]}
  SW3:
    access: {GigabitEthernet0/1: 10, GigabitEthernet0/2: 40}
    trunks: {GigabitEthernet0/0: [10, 20, 30, 40]}
  SW4:
    access: {GigabitEthernet0/1: 20, GigabitEthernet0/2: 30}
    trunks: {GigabitEthernet0/0: [10, 20, 30, 40]}
  SW5:
    access: {GigabitEthernet0/1: 40, GigabitEthernet0/2: 30}
    trunks: {GigabitEthernet0/0: [10, 20, 30, 40]}
```

## Suggested repository layout

```
itt633-vlan-automation/
  CLAUDE.md
  README.md            # setup and run instructions (required deliverable)
  requirements.txt     # netmiko, pyyaml, pytest
  inventory.yaml
  plan.yaml
  vlan_tool.py         # main script: CLI and the nine steps
  parsers.py           # functions that turn show output into Python data
  planning.py          # plan validation and build_changes() (commands + undo list); no Netmiko, testable on the Mac
  tests/
    samples/           # real show outputs saved from the IOSvL2 switches
    test_parsers.py
    test_planning.py
    test_vlan_tool.py  # vlan_tool.py steps with fake Netmiko connections
  backups/  reports/  logs/   # git-ignored
```

Command line: `python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]`.

## Testing approach

- Parsing and planning logic must be testable on the Mac without switches: keep functions that talk to Netmiko separate from functions that parse text and build command lists, and test the pure functions with pytest.
- Output formats differ between images. Before trusting a parser, save real output from the IOSvL2 switches into `tests/samples/` and test against it. Until then, mark parsers as unverified.
- Network test cases from the proposal (each needs evidence in the report: screenshot or saved output, named by test ID):

| ID | What | Expected |
|---|---|---|
| TC01 | Pre-check on SW1 to SW5 | All reachable over SSH |
| TC02 | `--dry-run` then `show vlan brief` | Commands printed, nothing changed |
| TC03 | `show vlan brief` after run | VLANs 10, 20, 30, 40, 99 with correct names on every switch |
| TC04 | `show interfaces GigabitEthernet0/x switchport` | Each PC port in its planned VLAN |
| TC05 | `show interfaces trunk` | Every trunk allows 10, 20, 30, 40, 99 |
| TC06 | Ping PC1 to PC3 (VLAN 10), PC2 to PC5 (VLAN 20), PC6 to PC8 (VLAN 30), PC4 to PC7 (VLAN 40) | All succeed |
| TC07 | Ping PC1 to PC2 (10 to 20), PC3 to PC4 (10 to 40), PC5 to PC6 (20 to 30) | All fail |
| TC08 | report.json vs manual show output | They match |
| TC09 | Plan with a port that does not exist on SW3 | SW3 changes undone, SW3 not saved, SSH still works |
| TC10 | Wrong SW3 IP in inventory | Stops at pre-check, no switch changed |
| TC11 | Manual vs tool, 3 trials each | Tool faster, zero errors (report.json holds timings) |

## report.json content

Per run: timestamp, mode (dry-run or apply), and per switch: status (PASS, FAIL, ROLLED_BACK, NOT_RUN, UNREACHABLE), commands sent, undo list, mismatches found, whether it was saved, and seconds taken for each step. Keep it flat and easy to read; it is evidence for Chapter 4.

## Stretch goals (only if the core works by week 8 and the user asks)

Fully idempotent re-runs (skip ports already correct), roll back all switches together, Method B rollback with `configure replace`, port security, inter-VLAN routing, automated ping tests.

## Timeline (8 weeks after approval)

1 proposal; 2 lab and baseline; 3 environment and YAML files; 4 backup, planning, dry-run, apply; 5 verification and report; 6 rollback, error handling, integration; 7 run all tests and collect evidence; 8 report, slides, demo rehearsal. Week-6 checkpoint: if rollback still does not work, fall back to backup plus manual restore and say so honestly.

## Code style: demo-first

This code will be explained live to a lecturer by any of 4 students.

- One function per tool step, named after it (precheck, backup, plan,
  dry_run, apply, verify, save_or_rollback, write_report). main() should
  read like the 9-step flow.
- Print a progress line per step and switch, e.g. "[3/9] Backup SW2 ... OK".
- Plain Python only: for/if, lists, dicts. No classes, decorators,
  threads, async, or nested comprehensions.
- Functions about 30 lines max. Comments explain WHY, not what.
- Every regex has a comment with an example line it matches.
- No hard-coded IPs/VLANs in code; they live in inventory.yaml/plan.yaml.
- Must run on Python 3.9 and 3.14 (no 3.10+ only syntax).
- Prefer the simpler option even if it is a little longer.

## How to work with the user

- The user is Paan (Ahmad Farhan bin Ahmad Zahir), a networking student with little automation experience. Explain in plain English: what a change does and why, in one or two lines.
- Keep answers short and step by step. Give working code with brief comments.
- Ask before assuming team roles, topology changes or device models.
- After writing code, say clearly what was tested (for example pytest on sample output) and what still needs testing in GNS3.
- When a step needs the lab, write a short, numbered instruction sheet that Paan can forward to the friend. It should give the exact commands to run on each switch or on the automation host, and say what to save and how to name each file (for example `sw1_show_vlan_brief_real.txt`). Do not assume Paan can run or check anything in GNS3 directly.
- Commit and push only when Paan asks. When asked to see the diff first, stage the changes, show the diff, and wait before committing.

## Current status (handoff, last updated 2 Nov 2026)

Read this section first in a new session. `git log` has the full history.

### Lab status (real IOSvL2 15.2, 24 Oct 2026, run by a group member)

- Manual baseline proven: it ran on SW1 to SW5 with no `%` errors. SW1 pings 192.168.99.12 to .15 at 100%. CDP on SW1 shows SW2 on Gi0/1, SW3 on Gi0/2, SW4 on Gi0/3 and SW5 on Gi1/1, each on remote Gi0/0.
- The Python tool has **not** run on the lab yet (Windows SSH from the automation host is the next lab step).
- Quirk: `show interfaces trunk` printed the "Vlans in spanning tree forwarding state and not pruned" section twice (Gi0/1 to Gi0/3, then Gi1/1 under a second header). `parse_trunk_allowed()` reads only the "Vlans allowed on trunk" section, so it is not affected. Regression test: `test_trunk_repeated_stp_header_iosvl2` on `tests/samples/transcribed/SW1_show_interfaces_trunk.txt` (typed from a screenshot, not a raw capture). The raw SW1 capture in `tests/samples/real/` (2 Nov) does **not** show the repeated header; it has one clean header per section. Keep the transcribed test anyway.
- Native VLAN is 1 on all trunks (baseline default). The tool does not touch the native VLAN.
- Raw baseline captures are in `tests/samples/real/` (2 Nov, 30 files: SW1 to SW5 x show vlan brief, show interfaces trunk, show interfaces switchport, show ip interface brief, show running-config, show version; passwords redacted). `tests/test_parsers_real.py` runs the parsers on all of them. `parse_vlan_brief` and `parse_trunk_allowed` worked unchanged; `parse_switchport_all()` was added because the all-ports output has 16 blocks per switch.
- IOSvL2 finding: every unused port shows ` negotiation auto` in the running config, so `parse_interface_config()` will return `["negotiation auto"]` for a "blank" port, never `[]`. The undo then takes the "had settings" branch (default, then re-apply `negotiation auto`). Harmless, but `sw2_show_run_int_gi0_1_empty.txt` is unrealistic. Still needed from the lab: `show running-config interface GigabitEthernet0/1` on SW2, saved as `tests/samples/real/SW2_show_run_int_gi0_1.txt`.

### Done (tested with pytest on hand-written sample data only)

- Image moved to IOSvL2 15.2; all names are `GigabitEthernet0/x`. Five-switch topology with VLAN 40 is in `inventory.yaml`, `plan.yaml`, the samples and the tests.
- `parsers.py`: `show vlan brief`, `show interfaces trunk`, `show interfaces <port> switchport`, `show running-config interface <port>`.
- `planning.py`: `validate_plan()` (safety rules) and `build_changes()` (commands + undo list).
- `vlan_tool.py` steps 1 to 7:
  - `connect_all()`: SSH pre-check that stops if any switch fails, and checks for the `#` prompt (privilege 15).
  - `backup_switch()`: saves `show running-config` to `backups/<switch>_<timestamp>.cfg` and captures each planned access port's config.
  - `plan_switch()`: reads the switch state and builds the change list.
  - `--dry-run` prints the commands and undo list per switch.
  - Credentials come from `input()` and `getpass`. They are never stored.
  - `apply()` (step 6, apply mode only, never in dry-run): `copy running-config flash:pre_change.cfg` answering both prompts with Enter (prompts and the "bytes copied" success line NOT yet seen on IOSvL2), and fails and sends nothing if the copy shows `%Error` or no "bytes copied". Then `send_config_set` and a scan for the three IOS errors. No rollback inside; `main()` stops at the first failed switch.
  - `verify()` (step 7): runs `show vlan brief`, `show interfaces trunk`, `show interfaces switchport` and four checks on the parsed data (each returns a list of mismatch strings): `check_vlans` (ids and names, plus VLAN 99), `check_trunks` (configured "Vlans allowed on trunk" list, not the STP section, which can lag ~30 s), `check_access_ports` (ADMIN mode `static access` + VLAN; on IOSvL2 a `dynamic auto` port still shows oper mode `static access`), `check_protected_ports` (SW1 Gi1/0 still access VLAN 99). `main()` runs apply then verify per switch and stops at the first failure. Passing case tested only on synthetic post-apply samples (`tests/samples/synthetic/SW1_after_*`, `SW2_after_*`).
  - **Until step 8 exists, the lab runs `--dry-run` only** (decided with Paan, 2 Nov): apply changes the running config with no verify or rollback.
- 95 pytest tests: `tests/test_parsers.py`, `tests/test_parsers_real.py` (real IOSvL2 baseline output, 30 tests), `tests/test_planning.py`, `tests/test_vlan_tool.py`.
  - `tests/test_vlan_tool.py` replaces Netmiko's `ConnectHandler` with a `FakeSwitch` class that answers with the sample files. SW3 to SW5 reuse SW2's samples.
  - To try a dry-run without switches, patch `vlan_tool.ConnectHandler`, `vlan_tool.ask_credentials` and `vlan_tool.BACKUP_DIR` the same way.

### Not done yet (in timeline order)

1. GNS3 lab (run by Paan's friend, not Paan): baseline is done (see Lab status); next is SSH from the automation host, then TC01 and TC02. Baseline captures are in and tested; still to capture: `show running-config interface <port>` and the show outputs after a run (VLANs 10 to 40 present).
2. Lab check for step 6: capture the real `copy running-config flash:pre_change.cfg` output (first run and second run, when the file exists) to confirm the prompts and the "bytes copied" line.
3. Step 8: on pass, `save_config()`. On fail, run the undo list, do not save, and stop before the remaining switches.
4. Step 9: `reports/report_<timestamp>.json` (fields listed in "report.json content" above) and a log file in `logs/`.

### Still unverified or assumed

- The tool has not run on real IOSvL2 (only the manual baseline has). The samples directly in `tests/samples/` are hand-written; `real/` holds raw baseline captures and `transcribed/` is typed from a real screenshot. `parse_interface_config()` is still unverified.
- Assumed and not yet confirmed by the group:
  - The VLAN 40 subnet is 192.168.40.0/24.
  - The automation host stays on SW1 GigabitEthernet1/0.
- Possible lab issues:
  - `show running-config` may be slow on IOSvL2, so Netmiko timeouts may need raising.
  - The local user must be privilege 15.

### Tooling notes

- Run tests with `.venv/bin/python -m pytest` (netmiko 4.6.0 is installed in `.venv`).
- Code style: f-strings, and imports sorted per Ruff (`UP031`, `I001`). Ruff is not in `.venv`; the VS Code Ruff extension bundles it. The test files still use `%` formatting.
- Add a row to CONTRIBUTIONS.md for each piece of work, marked "(AI-assisted)" where it applies.
