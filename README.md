# ITT633 VLAN Automation

Intent-Driven VLAN Automation with Verification and Rollback Using Python and Netmiko.

A Python tool that reads one YAML plan, deploys VLANs to five Cisco switches
(core SW1, access SW2 to SW5) over SSH, checks the result, saves only if every check passes,
and undoes its own changes if a check fails.

Topology: 5 switches, 4 user VLANs (10 Sales, 20 IT, 30 Guest, 40 Finance) plus
management VLAN 99, 8 access ports (PC1 to PC8), and 4 trunk links from SW1 to
each access switch. Same-VLAN pings across switches (TC06): PC1 to PC3 (VLAN 10),
PC2 to PC5 (VLAN 20), PC6 to PC8 (VLAN 30), PC4 to PC7 (VLAN 40).

> **Status:** complete. The code described in the project report is commit
> `2ade97f` (frozen 13 Nov 2026). Later commits change documentation only.
> All 11 test cases (TC01 to TC11) passed on five Cisco IOSvL2 15.2 switches
> in GNS3 between 14 and 22 Nov 2026, and the 159 pytest tests pass.

## Team

Course: ITT633, Sep 2026 – Jan 2027, lecturer Kamarul Ariffin bin Abdul Basit,
FSKM UiTM Shah Alam.

| No | Name | Student ID | Role |
|---|---|---|---|
| 1 | Muhammad Afiq bin Khairul Anuar | 2024802934 | Group Leader |
| 2 | Ahmad Farhan bin Ahmad Zahir | 2024419906 | Developer |
| 3 | Alya Sophea binti Shahrul Munir | 2024276562 | Lab Owner |
| 4 | Nur Ain Nasuha binti Abdul Fatah | 2024276376 | Documentation |

Each member's work is recorded in [CONTRIBUTIONS.md](CONTRIBUTIONS.md).
AI help was used as the course guideline allows; it is marked "(AI-assisted)" there.

## Files

| File | What it does |
|---|---|
| `inventory.yaml` | Switch names and management IPs |
| `plan.yaml` | The VLANs, access ports and trunk VLANs we want |
| `plan_tc09_bad_port.yaml` | `plan.yaml` plus a port that does not exist on SW3 (rollback test TC09) |
| `parsers.py` | Turns `show` output text into Python data |
| `planning.py` | Checks the plan for safety and builds the commands + undo list |
| `vlan_tool.py` | Main script: the 9-step workflow (talks to the switches) |
| `requirements.txt` | Exact package versions (netmiko 4.6.0, paramiko 4.0.0, PyYAML 6.0.3, pytest 8.4.2) |
| `tests/` | 159 pytest tests, real IOSvL2 output in `tests/samples/real/`, and fake switches for vlan_tool.py |

## 1. Download the code

1. Open https://github.com/amdpaan/itt633-vlan-automation (no GitHub account needed).
2. Click the green **Code** button, then **Download ZIP**, and extract it.

Or, with Git installed:

```bash
git clone https://github.com/amdpaan/itt633-vlan-automation.git
cd itt633-vlan-automation
```

## 2. Set up Python

Needs Python 3.9 or newer (tested on 3.9 and 3.14). Open a terminal in the project folder.

Linux or macOS (including the Kali automation host):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (Command Prompt):

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On Kali, always use the venv: Kali refuses `pip install` outside one
("externally-managed-environment"). If `python3 -m venv` fails, run
`sudo apt install python3-venv` first.

Keep paramiko at 4.0.0: version 5.x refuses the SHA-1 key exchange that
IOSvL2 15.2 offers, so SSH to the switches fails.

## 3. Run the tests (no switches needed)

```bash
python -m pytest
```

Expected result: `159 passed`. The tests use saved output from the real
switches and fake SSH connections, so no lab is needed.

## 4. Run the tool on the lab

This needs the GNS3 lab with the manual baseline on every switch
(hostname, `vtp mode transparent`, VLAN 99 and its SVI, local user with privilege 15, SSH,
trunks allowing VLAN 99, and SW1 GigabitEthernet1/0 in VLAN 99). The tool never changes these.

The automation host (a Kali VM) is connected to SW1 GigabitEthernet1/0 (VLAN 99)
through a GNS3 Cloud node. Before running the tool:

1. Kali has 192.168.99.101/24 on the interface that faces the Cloud.
2. Kali can ping every switch: 192.168.99.11 to 192.168.99.15.

Activate the venv first. Both `--plan` and `--inventory` are required.

```bash
# Dry-run: prints the commands and undo list per switch, sends nothing
python vlan_tool.py --plan plan.yaml --inventory inventory.yaml --dry-run

# Real run: apply, verify, save or roll back.
# Only after the dry-run output has been checked.
python vlan_tool.py --plan plan.yaml --inventory inventory.yaml
```

The username and password are asked at run time and are never saved to a file.
Output goes to `backups/`, `reports/` and `logs/`. These folders are not in Git,
because the backups contain password hashes.

`plan_tc09_bad_port.yaml` is for test TC09 only (a bad port on SW3, which must be rolled back).
Run it in real-run mode, only from a restored baseline snapshot.

## Test results (real lab, commit 2ade97f)

| Test | What it checks | Result |
|---|---|---|
| TC01 | SSH pre-check on all 5 switches | PASS |
| TC02 | Dry-run prints commands, changes nothing | PASS |
| TC03 | VLANs 10, 20, 30, 40 created on all 5 | PASS |
| TC04 | 8 PC ports in their planned VLAN | PASS |
| TC05 | Trunks allow 10, 20, 30, 40, 99; SW1 Gi1/0 still VLAN 99 | PASS |
| TC06 | Same-VLAN pings across switches succeed | PASS |
| TC07 | Cross-VLAN pings fail (no router) | PASS |
| TC08 | report.json matches the switches; re-run finds nothing to change | PASS |
| TC09 | Bad port on SW3 is rolled back, run stops | PASS |
| TC10 | Wrong SW3 IP stops the tool at pre-check | PASS |
| TC11 | Manual vs tool on SW3, 3 trials each | PASS: 181.85 s vs 41.63 s average, 0 errors |

Screenshots and report files for each test are in the project report (Chapter 4).
