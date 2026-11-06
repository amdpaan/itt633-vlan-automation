"""Tests for parsers.py on REAL IOSvL2 15.2 output.

The files in tests/samples/real/ were captured from our five GNS3 switches:
the baseline (before the tool ran, 2 Nov), the tool's own run logs, and
console captures of SW1 and SW2 after the first real apply run (5 Nov).
"""

import os

import pytest

from conftest import TESTS_DIR, log_block, read_sample
from parsers import parse_switchport_all, parse_trunk_allowed, parse_vlan_brief

SWITCHES = ["SW1", "SW2", "SW3", "SW4", "SW5"]
ACCESS_SWITCHES = ["SW2", "SW3", "SW4", "SW5"]


def read_real(switch, command):
    """Return the text of tests/samples/real/<switch>_<command>.txt."""
    return read_sample(f"real/{switch}_{command}.txt")


# ---------- show vlan brief ----------

@pytest.mark.parametrize("switch", SWITCHES)
def test_real_vlan_brief_has_1_and_99(switch):
    vlans = parse_vlan_brief(read_real(switch, "show_vlan_brief"))
    assert vlans[1]["name"] == "default"
    assert vlans[99]["name"] == "Management"
    # Baseline: no user VLANs yet, only 1, 99 and the 1002-1005 defaults.
    assert sorted(vlans) == [1, 99, 1002, 1003, 1004, 1005]


@pytest.mark.parametrize("switch", SWITCHES)
def test_real_vlan_brief_wrapped_ports(switch):
    # VLAN 1's ports wrap over 3 lines (SW1) or 4 lines (SW2-SW5).
    # Counting them catches a dropped wrap line; Gi3/3 is on the last line.
    ports = parse_vlan_brief(read_real(switch, "show_vlan_brief"))[1]["ports"]
    expected_count = 11 if switch == "SW1" else 15
    assert len(ports) == expected_count
    assert ports[-1] == "GigabitEthernet3/3"
    assert all(p.startswith("GigabitEthernet") for p in ports)


@pytest.mark.parametrize("switch", SWITCHES)
def test_real_vlan_brief_vlan_99_ports(switch):
    vlans = parse_vlan_brief(read_real(switch, "show_vlan_brief"))
    if switch == "SW1":
        # Automation host port.
        assert vlans[99]["ports"] == ["GigabitEthernet1/0"]
    else:
        # Access switches: the "99 Management active" line has an empty Ports column.
        assert vlans[99]["ports"] == []
    # act/unsup VLANs have no ports either, and must not pick up VLAN 99's.
    assert vlans[1002]["ports"] == []


# ---------- show interfaces trunk ----------

def test_real_trunk_sw1():
    trunks = parse_trunk_allowed(read_real("SW1", "show_interfaces_trunk"))
    assert trunks == {"GigabitEthernet0/1": [99], "GigabitEthernet0/2": [99],
                      "GigabitEthernet0/3": [99], "GigabitEthernet1/1": [99]}


@pytest.mark.parametrize("switch", ACCESS_SWITCHES)
def test_real_trunk_access_switch(switch):
    trunks = parse_trunk_allowed(read_real(switch, "show_interfaces_trunk"))
    assert trunks == {"GigabitEthernet0/0": [99]}


# ---------- show interfaces switchport (all ports) ----------

@pytest.mark.parametrize("switch", SWITCHES)
def test_real_switchport_all_16_ports(switch):
    ports = parse_switchport_all(read_real(switch, "show_interfaces_switchport"))
    assert len(ports) == 16
    assert "GigabitEthernet0/0" in ports
    assert "GigabitEthernet3/3" in ports  # last block in the file
    for info in ports.values():
        assert info["admin_mode"] is not None
        assert info["oper_mode"] is not None
        assert info["access_vlan"] is not None


def test_real_switchport_sw1_values():
    ports = parse_switchport_all(read_real("SW1", "show_interfaces_switchport"))
    # Unused port: "dynamic auto" and "down", and no
    # "Operational Trunking Encapsulation" line in its block.
    assert ports["GigabitEthernet0/0"] == {"admin_mode": "dynamic auto", "oper_mode": "down", "access_vlan": 1}
    # Block right after that down port must still be read correctly.
    assert ports["GigabitEthernet0/1"] == {"admin_mode": "trunk", "oper_mode": "trunk", "access_vlan": 1}
    # Automation host port.
    assert ports["GigabitEthernet1/0"] == {"admin_mode": "static access", "oper_mode": "static access",
                                           "access_vlan": 99}
    assert ports["GigabitEthernet3/3"] == {"admin_mode": "dynamic auto", "oper_mode": "down", "access_vlan": 1}


@pytest.mark.parametrize("switch", ACCESS_SWITCHES)
def test_real_switchport_access_switch_values(switch):
    ports = parse_switchport_all(read_real(switch, "show_interfaces_switchport"))
    assert ports["GigabitEthernet0/0"] == {"admin_mode": "trunk", "oper_mode": "trunk", "access_vlan": 1}
    # PC ports before the tool runs: not configured yet, but up (a VPCS is
    # connected), so they negotiate to "static access" in VLAN 1.
    for pc_port in ["GigabitEthernet0/1", "GigabitEthernet0/2"]:
        assert ports[pc_port] == {"admin_mode": "dynamic auto", "oper_mode": "static access", "access_vlan": 1}
    # Gi1/0 comes right after a down port (Gi0/3).
    assert ports["GigabitEthernet1/0"] == {"admin_mode": "dynamic auto", "oper_mode": "down", "access_vlan": 1}


# ---------- the tool's own log from the first real dry-run (Kali, 4 Nov) ----------

def log_section(switch, command):
    """Cut one switch's '----- <command> -----' block out of run_20261103_113312.log."""
    text = read_sample("real/run_20261103_113312.log")
    after_switch = text.split(f"{switch}: backup saved")[1]
    return after_switch.split(f"----- {command} -----")[1].split("----- end -----")[0]


def test_dryrun_log_trunk_single_stp_header_sw1():
    """Here the STP section prints once; the 24 Oct screenshot had it twice
    (test_trunk_repeated_stp_header_iosvl2). The parser must handle both."""
    output = log_section("SW1", "show interfaces trunk")
    assert output.count("Vlans in spanning tree forwarding state") == 1
    assert parse_trunk_allowed(output) == {"GigabitEthernet0/1": [99], "GigabitEthernet0/2": [99],
                                           "GigabitEthernet0/3": [99], "GigabitEthernet1/1": [99]}


@pytest.mark.parametrize("switch", ACCESS_SWITCHES)
def test_dryrun_log_trunk_access_switch(switch):
    assert parse_trunk_allowed(log_section(switch, "show interfaces trunk")) == {"GigabitEthernet0/0": [99]}


def test_dryrun_log_vlan_brief_ports_on_one_line():
    """Through Netmiko the port list is one long line, not wrapped as on the console."""
    vlans = parse_vlan_brief(log_section("SW1", "show vlan brief"))
    assert sorted(vlans) == [1, 99, 1002, 1003, 1004, 1005]
    assert vlans[99]["ports"] == ["GigabitEthernet1/0"]
    assert len(vlans[1]["ports"]) == 11


# ---------- after the first real apply run (5 Nov): SW1 and SW2 ----------
# Console captures (Solar-PuTTY): CRLF line ends, and the VLAN 1 port list in
# 'show vlan brief' wraps onto continuation lines. Kept raw, never edited.
# The same commands over SSH are in run_20261105_235328.log (no wrap).

APPLY_LOG = "run_20261105_235328.log"   # frozen sample of the real apply run
AFTER_COMMANDS = ["show vlan brief", "show interfaces trunk", "show interfaces switchport"]
PARSERS = {"show vlan brief": parse_vlan_brief,
           "show interfaces trunk": parse_trunk_allowed,
           "show interfaces switchport": parse_switchport_all}
AFTER_TRUNKS = {
    "SW1": {"GigabitEthernet0/1": [10, 20, 30, 40, 99], "GigabitEthernet0/2": [10, 20, 30, 40, 99],
            "GigabitEthernet0/3": [10, 20, 30, 40, 99], "GigabitEthernet1/1": [10, 20, 30, 40, 99]},
    "SW2": {"GigabitEthernet0/0": [10, 20, 30, 40, 99]},
}


def read_console_after(switch, command):
    """Return tests/samples/real/<switch>_<command>_after.txt (console capture)."""
    return read_sample(f"real/{switch}_{command.replace(' ', '_')}_after.txt")


def read_raw(filename):
    """Read with newline="" so Python keeps the console's CRLF line ends."""
    with open(os.path.join(TESTS_DIR, "samples", "real", filename), newline="") as f:
        return f.read()


def ssh_after(switch, command):
    """The same command as verify() read it over SSH in the real apply run."""
    return log_block(APPLY_LOG, f"[6/9] Apply {switch} ...", command)


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_console_after_vlan_brief(switch):
    vlans = parse_vlan_brief(read_console_after(switch, "show vlan brief"))
    assert sorted(vlans) == [1, 10, 20, 30, 40, 99, 1002, 1003, 1004, 1005]
    names = {vlan_id: vlans[vlan_id]["name"] for vlan_id in [10, 20, 30, 40, 99]}
    assert names == {10: "Sales", 20: "IT", 30: "Guest", 40: "Finance", 99: "Management"}
    # VLAN 1 wraps over 3 console lines (SW1) or 4 (SW2); Gi3/3 is on the last one.
    assert len(vlans[1]["ports"]) == (11 if switch == "SW1" else 13)
    assert vlans[1]["ports"][-1] == "GigabitEthernet3/3"
    if switch == "SW1":
        # Core switch: no PCs, only the automation host port in VLAN 99.
        assert vlans[10]["ports"] == [] and vlans[99]["ports"] == ["GigabitEthernet1/0"]
    else:
        assert vlans[10]["ports"] == ["GigabitEthernet0/1"]
        assert vlans[20]["ports"] == ["GigabitEthernet0/2"]
        assert vlans[99]["ports"] == []


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_console_after_crlf_read_raw(switch):
    """read_sample() turns CRLF into LF, so this reads the file raw: the parser
    must give the same result with the \\r still in the text."""
    raw = read_raw(f"{switch}_show_vlan_brief_after.txt")
    assert "\r\n" in raw
    vlans = parse_vlan_brief(raw)
    assert vlans == parse_vlan_brief(read_console_after(switch, "show vlan brief"))
    for vlan in vlans.values():
        assert "\r" not in vlan["name"]
        assert not any("\r" in port for port in vlan["ports"])


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_console_after_trunk_allows_all_vlans(switch):
    trunks = parse_trunk_allowed(read_console_after(switch, "show interfaces trunk"))
    assert trunks == AFTER_TRUNKS[switch]


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_console_after_switchport(switch):
    ports = parse_switchport_all(read_console_after(switch, "show interfaces switchport"))
    assert len(ports) == 16
    if switch == "SW1":
        assert ports["GigabitEthernet1/0"] == {"admin_mode": "static access", "oper_mode": "static access",
                                               "access_vlan": 99}
        assert ports["GigabitEthernet1/1"] == {"admin_mode": "trunk", "oper_mode": "trunk", "access_vlan": 1}
    else:
        assert ports["GigabitEthernet0/0"] == {"admin_mode": "trunk", "oper_mode": "trunk", "access_vlan": 1}
        assert ports["GigabitEthernet0/1"] == {"admin_mode": "static access", "oper_mode": "static access",
                                               "access_vlan": 10}
        assert ports["GigabitEthernet0/2"] == {"admin_mode": "static access", "oper_mode": "static access",
                                               "access_vlan": 20}


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_ssh_after_parses_like_console(switch):
    """Console (wrapped, CRLF) and SSH (one line per VLAN) give the same data."""
    for command in AFTER_COMMANDS:
        parse = PARSERS[command]
        assert parse(ssh_after(switch, command)) == parse(read_console_after(switch, command))


def test_ssh_after_stp_section_still_99_only():
    """Right after apply, the spanning tree section still showed only VLAN 99
    (it catches up later; the console capture minutes after shows 10-40 too).
    parse_trunk_allowed reads the configured allowed list, so it is not fooled."""
    output = ssh_after("SW1", "show interfaces trunk")
    stp_lines = output.split("not pruned\n")[1].splitlines()
    assert stp_lines == ["Gi0/1       99", "Gi0/2       99", "Gi0/3       99", "Gi1/1       99"]
    assert parse_trunk_allowed(output) == AFTER_TRUNKS["SW1"]
