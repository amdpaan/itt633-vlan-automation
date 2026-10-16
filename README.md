# ITT633 VLAN Automation

Intent-Driven VLAN Automation with Verification and Rollback Using Python and Netmiko.

A Python tool that reads one YAML plan, deploys VLANs to three Cisco switches
(SW1, SW2, SW3) over SSH, checks the result, saves only if every check passes,
and undoes its own changes if a check fails.

> **Status:** early skeleton. The parsing and planning code is tested with
> pytest on hand-written sample output. **Nothing has been tested on the
> IOL-L2 switches in GNS3 yet.**

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

- [x] Repo skeleton, parsers, planning module, 24 pytest tests (sample data only, not yet tested on IOL-L2)
- [x] Proposal v6 drafted (cover alignment fixed)
- [ ] Proposal approved by lecturer
- [ ] GNS3 + Cisco IOL-L2 lab working
- [ ] Pre-check, backup, apply, verify, rollback, report.json
- [ ] Test cases TC01–TC11 run with evidence

## Files

| File | What it does |
|---|---|
| `inventory.yaml` | Switch names and management IPs |
| `plan.yaml` | The VLANs, access ports and trunk VLANs we want |
| `parsers.py` | Turns `show` output text into Python data |
| `planning.py` | Checks the plan for safety and builds the commands + undo list |
| `vlan_tool.py` | Main script (command line, talks to the switches) |
| `tests/` | pytest tests and sample `show` output |

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

Before running, the manual baseline must be in place on all three switches
(hostname, VLAN 99 and SVI, local user, SSH, trunks allowing VLAN 99, and
SW1 Ethernet1/0 in VLAN 99). The tool never changes these.
