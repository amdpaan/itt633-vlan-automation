# ITT633 VLAN Automation

Intent-Driven VLAN Automation with Verification and Rollback Using Python and Netmiko.

A Python tool that reads one YAML plan, deploys VLANs to five Cisco switches
(core SW1, access SW2 to SW5) over SSH, checks the result, saves only if every check passes,
and undoes its own changes if a check fails.

Topology: 5 switches, 4 user VLANs (10 Sales, 20 IT, 30 Guest, 40 Finance) plus
management VLAN 99, 8 access ports (PC1 to PC8), and 4 trunk links from SW1 to
each access switch. Same-VLAN pings across switches (TC06): PC1 to PC3 (VLAN 10),
PC2 to PC5 (VLAN 20), PC6 to PC8 (VLAN 30), PC4 to PC7 (VLAN 40).

> **Status:** early skeleton. The parsing and planning code is tested with
> pytest on hand-written sample output. **Nothing has been tested on the
> IOSvL2 switches in GNS3 yet.**

## Team

Course: ITT633, Sep 2026 – Jan 2027, lecturer Kamarul Ariffin bin Abdul Basit,
FSKM UiTM Shah Alam.

| No | Name | Student ID | Role |
|---|---|---|---|
| 1 | Muhammad Afiq bin Khairul Anuar | 2024802934 | Group Leader |
| 2 | Ahmad Farhan bin Ahmad Zahir | 2024419906 | TBD |
| 3 | Alya Sophea binti Shahrul Munir | 2024276562 | TBD |
| 4 | Nur Ain Nasuha binti Abdul Fatah | 2024276376 | TBD |

Each member's work is recorded in [CONTRIBUTIONS.md](CONTRIBUTIONS.md).

## Project status

- [x] Repo skeleton, parsers, planning module, 24 pytest tests (sample data only, not yet tested on IOSvL2)
- [x] Proposal v6 drafted (cover alignment fixed)
- [ ] Proposal approved by lecturer
- [ ] GNS3 + Cisco IOSvL2 lab working
- [x] Switched lab image to Cisco IOSvL2 15.2 (GigabitEthernet0/x names, VTP transparent)
- [x] Pre-check, local backup, change list and `--dry-run` (40 pytest tests, fake switches, not yet tested in GNS3)
- [ ] Flash backup copy, apply, verify, rollback, report.json
- [ ] Test cases TC01–TC11 run with evidence

## Files

| File | What it does |
|---|---|
| `inventory.yaml` | Switch names and management IPs |
| `plan.yaml` | The VLANs, access ports and trunk VLANs we want |
| `parsers.py` | Turns `show` output text into Python data |
| `planning.py` | Checks the plan for safety and builds the commands + undo list |
| `vlan_tool.py` | Main script (command line, talks to the switches) |
| `tests/` | pytest tests, sample `show` output, and fake switches for vlan_tool.py |

## Setup (Mac, Windows or Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the tests (no switches needed)

```bash
python -m pytest
```

## Run the tool

```bash
python vlan_tool.py --plan plan.yaml --inventory inventory.yaml --dry-run
```

The username and password are asked at run time and are never saved to a file.

Before running, the manual baseline must be in place on every switch
(hostname, `vtp mode transparent`, VLAN 99 and SVI, local user with privilege 15, SSH, trunks allowing VLAN 99, and
SW1 GigabitEthernet1/0 in VLAN 99). The tool never changes these.
