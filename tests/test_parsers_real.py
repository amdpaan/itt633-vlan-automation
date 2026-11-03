"""Tests for parsers.py on REAL IOSvL2 15.2 output.

The files in tests/samples/real/ were captured from our five GNS3 switches
(baseline state, before the tool has run). Every test here runs once per
switch, SW1 to SW5.
"""

import pytest

from conftest import read_sample
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
