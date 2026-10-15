"""Tests for planning.py (plan checks, command list and undo list).

NOT YET VERIFIED ON IOL-L2: switch state comes from hand-written sample
output. The commands themselves still need to be tried in GNS3.
"""

import copy
import os

import pytest

from conftest import read_sample
from parsers import parse_interface_config, parse_trunk_allowed, parse_vlan_brief
from planning import build_changes, load_yaml, validate_plan

PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
PLAN = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
INVENTORY = load_yaml(os.path.join(PROJECT_DIR, "inventory.yaml"))


def sw1_baseline():
    """SW1 state after the manual baseline, before the tool runs."""
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_baseline.txt"))
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_baseline.txt"))
    ports = {"Ethernet0/3": parse_interface_config(read_sample("sw1_show_run_int_e0_3_empty.txt"))}
    return vlans, trunks, ports


# ---------- validate_plan ----------

def test_real_plan_is_valid():
    assert validate_plan(PLAN, INVENTORY) == []


def test_refuses_vlan_99():
    plan = copy.deepcopy(PLAN)
    plan["vlans"].append({"id": 99, "name": "Hack"})
    assert any("VLAN 99" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_host_port():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW1"]["access"]["Ethernet1/0"] = 10
    assert any("protected" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_unknown_section():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW1"]["svi"] = {"vlan 99": "1.2.3.4"}
    assert any("unknown section" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_switch_not_in_inventory():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW4"] = {"access": {"Ethernet0/1": 10}}
    assert any("SW4" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_vlan_not_in_list():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW2"]["access"]["Ethernet0/3"] = 40
    assert any("VLAN 40" in e for e in validate_plan(plan, INVENTORY))


# ---------- build_changes ----------

def test_sw1_from_baseline():
    vlans, trunks, ports = sw1_baseline()
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert commands == [
        "vlan 10", "name Sales",
        "vlan 20", "name IT",
        "vlan 30", "name Guest",
        "interface Ethernet0/1", "switchport trunk allowed vlan add 10,20,30",
        "interface Ethernet0/2", "switchport trunk allowed vlan add 10,20,30",
        "interface Ethernet0/3", "switchport mode access", "switchport access vlan 30",
    ]
    # Undo runs in reverse order: port first, VLANs last.
    assert undo == [
        "default interface Ethernet0/3",
        "interface Ethernet0/2", "switchport trunk allowed vlan remove 10,20,30",
        "interface Ethernet0/1", "switchport trunk allowed vlan remove 10,20,30",
        "no vlan 30",
        "no vlan 20",
        "no vlan 10",
    ]


def test_never_replaces_trunk_list():
    vlans, trunks, ports = sw1_baseline()
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    for line in commands + undo:
        if line.startswith("switchport trunk allowed vlan"):
            assert " add " in line or " remove " in line


def test_only_missing_trunk_vlans_added():
    vlans, trunks, ports = sw1_baseline()
    trunks["Ethernet0/1"] = [10, 99]
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert "switchport trunk allowed vlan add 20,30" in commands
    assert "switchport trunk allowed vlan remove 20,30" in undo


def test_nothing_to_do_when_already_done():
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_after.txt"))
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_after.txt"))
    ports = {"Ethernet0/3": ["switchport access vlan 30", "switchport mode access"]}
    assert build_changes("SW1", PLAN, vlans, trunks, ports) == ([], [])


def test_wrong_vlan_name_is_renamed_back_on_undo():
    vlans, trunks, ports = sw1_baseline()
    vlans[10] = {"name": "OldSales", "ports": []}
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert commands[:2] == ["vlan 10", "name Sales"]
    assert undo[-2:] == ["vlan 10", "name OldSales"]


def test_port_with_old_config_is_restored_on_undo():
    vlans, trunks, ports = sw1_baseline()
    old = parse_interface_config(read_sample("sw3_show_run_int_e0_3_configured.txt"))
    ports["Ethernet0/3"] = old
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert undo[:5] == [
        "default interface Ethernet0/3",
        "interface Ethernet0/3",
        "description old guest port",
        "switchport access vlan 5",
        "switchport mode access",
    ]


def test_refuses_trunk_that_is_not_a_trunk():
    vlans, trunks, ports = sw1_baseline()
    del trunks["Ethernet0/2"]
    with pytest.raises(ValueError, match="not an active trunk"):
        build_changes("SW1", PLAN, vlans, trunks, ports)


def test_refuses_access_port_in_vlan_99():
    vlans, trunks, ports = sw1_baseline()
    vlans[99]["ports"].append("Ethernet0/3")
    with pytest.raises(ValueError, match="VLAN 99"):
        build_changes("SW1", PLAN, vlans, trunks, ports)
