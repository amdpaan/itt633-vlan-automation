# ITT633 VLAN Automation: Project Context for Claude Code

Read this file before writing or changing any code in this repository.

## What this project is

A university group project (ITT633, FSKM, UiTM Shah Alam, semester September 2026 to January 2027, lecturer Kamarul Ariffin bin Abdul Basit). Group of 4 students. The leader and the roles are not decided yet, so do not assign names to tasks.

The project builds a Python tool that deploys VLAN configuration to three Cisco switches from one YAML file, verifies the result against the file, saves only when every check passes, and rolls back its own changes when a check fails.

Working title: **Intent-Driven VLAN Automation with Verification and Rollback Using Python and Netmiko**.

Scenario: Perdana Niaga Sdn. Bhd., an imaginary retail company with a three-storey head office and three switches.

## Rules that come from the course guideline

- AI help is allowed, but every student must be able to understand, explain, modify, test and verify any AI-written code. **Keep the code simple and readable.** Prefer plain functions, clear names and short comments over clever abstractions, classes or extra libraries. If a beginner cannot follow it, simplify it.
- The project must not copy an existing project. If you use an idea or snippet from a tutorial or repository, say so in a comment and tell the user so it can be cited in the report.
- Success must be proven on the network (show commands and pings), not only by the script finishing without errors.
- Scope is fixed by the approved proposal. Do not add features outside the core list below unless the user asks. Flag anything that could break scope or the 8-week timeline.

## Environment

- Development: the user writes code on an **Apple Silicon Mac** and shares it with the group through Git/GitHub. Check arm64 compatibility before suggesting any tool.
- Lab: GNS3 (2.2.x) with the GNS3 VM, running on a Windows PC or the faculty lab computers. Cisco x86 images do not run well on Apple Silicon.
- Switch image: Cisco **IOL-L2** in GNS3 (not confirmed working yet). Interface names are `Ethernet0/0`, `Ethernet0/1`, and so on. If the group switches to IOSvL2, names become `GigabitEthernet0/x`; only plan.yaml should need to change.
- End hosts: VPCS. The automation host joins VLAN 99 through a GNS3 Cloud node (method still to be tested, for example a Windows loopback adapter).
- **Nothing has been tested on a real Cisco image yet.** Never claim that code or commands work on the switches. Say what still needs testing in GNS3.

## Network design

| VLAN | Name | Subnet |
|---|---|---|
| 10 | Sales | 192.168.10.0/24 |
| 20 | IT | 192.168.20.0/24 |
| 30 | Guest | 192.168.30.0/24 |
| 99 | Management | 192.168.99.0/24 |

| Device | Port | VLAN | IP |
|---|---|---|---|
| SW1 (core, Level 1) | SVI vlan 99 | 99 | 192.168.99.11 |
| SW2 (access, Level 2) | SVI vlan 99 | 99 | 192.168.99.12 |
| SW3 (access, Level 3) | SVI vlan 99 | 99 | 192.168.99.13 |
| Automation host | SW1 Ethernet1/0 | 99 | 192.168.99.100 |
| PC1 Sales | SW2 Ethernet0/1 | 10 | 192.168.10.10 |
| PC2 Sales | SW3 Ethernet0/1 | 10 | 192.168.10.11 |
| PC3 IT | SW2 Ethernet0/2 | 20 | 192.168.20.10 |
| PC4 IT | SW3 Ethernet0/2 | 20 | 192.168.20.11 |
| PC5 Guest | SW1 Ethernet0/3 | 30 | 192.168.30.10 |
| PC6 Guest | SW3 Ethernet0/3 | 30 | 192.168.30.11 |

Trunks (802.1Q): SW1 Ethernet0/1 to SW2 Ethernet0/0, and SW1 Ethernet0/2 to SW3 Ethernet0/0. The final allowed list is 10, 20, 30, 99.

There is **no router**. Pings between different VLANs must fail; that proves isolation.

### Manual baseline (done by hand before the tool runs)

Hostname, VLAN 99 and its SVI address, local user, SSH, both trunk links (mode trunk, allowed VLAN 99), and the host port SW1 Ethernet1/0 in VLAN 99. The tool reaches SW2 and SW3 through these trunks, so **the tool must never break them**.

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

Processing order: SW1, then SW2, then SW3, one switch at a time (apply, verify, save or roll back). If a switch fails, roll it back and stop; do not touch the remaining switches. Switches already passed stay saved, and the report must say so. Rolling back all switches together is a stretch goal.

### Undo rules (rollback Method C)

| Change made | Undo entry |
|---|---|
| `vlan 30` + `name Guest` (VLAN did not exist) | `no vlan 30` |
| `switchport trunk allowed vlan add 30` | `switchport trunk allowed vlan remove 30` |
| Access port that had no config before | `default interface Ethernet0/3` |
| Access port that already had settings | re-apply the port's old lines from `show running-config interface <port>` captured in step 3 |

Known limitation to keep honest in the report: the undo list only reverses what the tool recorded. The backup exists for manual restore. Cisco `configure replace` (Method B) is a stretch goal only if the image supports it; do not present it as the plan.

### Safety rules (must hold in all code)

- Trunk changes always use `switchport trunk allowed vlan add` / `remove`. Never send `switchport trunk allowed vlan <list>` without `add`/`remove`, because it replaces the list and drops VLAN 99.
- Never create, delete, remove or modify VLAN 99, the SVIs, the trunk mode of the inter-switch links, or SW1 Ethernet1/0. Validate plan.yaml at load time and refuse a plan that touches them.
- Never `write memory` before verification passes.
- Never store passwords in files or Git. Ask with `getpass` at run time (an environment variable is acceptable as an option). Add `backups/`, `reports/`, `logs/` and any `.env` to `.gitignore`.

## Input file formats (proposed; adjust only with the user)

```yaml
# inventory.yaml
SW1: {host: 192.168.99.11, device_type: cisco_ios}
SW2: {host: 192.168.99.12, device_type: cisco_ios}
SW3: {host: 192.168.99.13, device_type: cisco_ios}
```

```yaml
# plan.yaml
vlans:
  - {id: 10, name: Sales}
  - {id: 20, name: IT}
  - {id: 30, name: Guest}
switches:
  SW1:
    access: {Ethernet0/3: 30}
    trunks: {Ethernet0/1: [10, 20, 30], Ethernet0/2: [10, 20, 30]}
  SW2:
    access: {Ethernet0/1: 10, Ethernet0/2: 20}
    trunks: {Ethernet0/0: [10, 20, 30]}
  SW3:
    access: {Ethernet0/1: 10, Ethernet0/2: 20, Ethernet0/3: 30}
    trunks: {Ethernet0/0: [10, 20, 30]}
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
  tests/
    samples/           # real show outputs saved from the IOL-L2 switches
    test_parsers.py
    test_planning.py
  backups/  reports/  logs/   # git-ignored
```

Command line: `python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]`.

## Testing approach

- Parsing and planning logic must be testable on the Mac without switches: keep functions that talk to Netmiko separate from functions that parse text and build command lists, and test the pure functions with pytest.
- Output formats differ between images. Before trusting a parser, save real output from the IOL-L2 switches into `tests/samples/` and test against it. Until then, mark parsers as unverified.
- Network test cases from the proposal (each needs evidence in the report: screenshot or saved output, named by test ID):

| ID | What | Expected |
|---|---|---|
| TC01 | Pre-check on SW1 to SW3 | All reachable over SSH |
| TC02 | `--dry-run` then `show vlan brief` | Commands printed, nothing changed |
| TC03 | `show vlan brief` after run | VLANs 10, 20, 30, 99 with correct names |
| TC04 | `show interfaces Ethernet0/x switchport` | Each PC port in its planned VLAN |
| TC05 | `show interfaces trunk` | Both trunks allow 10, 20, 30, 99 |
| TC06 | Ping PC1 to PC2, PC3 to PC4, PC5 to PC6 | All succeed |
| TC07 | Ping PC1 to PC3, PC1 to PC5, PC3 to PC6 | All fail |
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

## How to work with the user

- The user is Paan (Ahmad Farhan bin Ahmad Zahir), a networking student with little automation experience. Explain in plain English: what a change does and why, in one or two lines.
- Keep answers short and step by step. Give working code with brief comments.
- Ask before assuming team roles, topology changes or device models.
- After writing code, say clearly what was tested (for example pytest on sample output) and what still needs testing in GNS3.
