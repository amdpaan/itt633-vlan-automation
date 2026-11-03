"""Tests for planning.py (plan checks, command list and undo list).

NOT YET VERIFIED ON IOSvL2: switch state comes from hand-written sample
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
ALL_VLANS = [(10, "Sales"), (20, "IT"), (30, "Guest"), (40, "Finance")]


def sw1_baseline():
    """SW1 (core) state after the manual baseline: four trunks, no PC ports."""
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_baseline.txt"))
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_baseline.txt"))
    return vlans, trunks, {}


def access_baseline(switch):
    """SW2 to SW5 state after the baseline: uplink Gi0/0, PC ports empty.

    All access switches are set up the same way, so they share SW2's samples.
    """
    vlans = parse_vlan_brief(read_sample("sw2_show_vlan_brief_baseline.txt"))
    trunks = parse_trunk_allowed(read_sample("sw2_show_interfaces_trunk_baseline.txt"))
    empty = parse_interface_config(read_sample("sw2_show_run_int_gi0_1_empty.txt"))
    ports = {port: empty for port in PLAN["switches"][switch]["access"]}
    return vlans, trunks, ports


def baseline(switch):
    """Baseline state for any switch in the plan."""
    return sw1_baseline() if switch == "SW1" else access_baseline(switch)


# ---------- validate_plan ----------

def test_real_plan_is_valid():
    assert validate_plan(PLAN, INVENTORY) == []


def test_tc09_plan_is_valid_and_only_adds_bad_port():
    """TC09 must fail at apply on the switch, not be refused at step 1."""
    tc09 = load_yaml(os.path.join(PROJECT_DIR, "plan_tc09_bad_port.yaml"))
    assert validate_plan(tc09, INVENTORY) == []
    # Apart from the bad port, it must stay identical to plan.yaml.
    del tc09["switches"]["SW3"]["access"]["GigabitEthernet0/9"]
    assert tc09 == PLAN


def test_plan_matches_inventory():
    """Both files list the same five switches, in processing order SW1 to SW5."""
    assert list(PLAN["switches"]) == ["SW1", "SW2", "SW3", "SW4", "SW5"]
    assert list(INVENTORY) == ["SW1", "SW2", "SW3", "SW4", "SW5"]
    assert INVENTORY["SW5"] == {"host": "192.168.99.15", "device_type": "cisco_ios"}


def test_topology_counts():
    """5 switches, 4 user VLANs, 8 access ports, 4 trunk links (SW1 to each access switch)."""
    switches = PLAN["switches"]
    access_ports = [port for s in switches.values() for port in (s.get("access") or {})]
    assert len(switches) == 5
    assert [v["id"] for v in PLAN["vlans"]] == [10, 20, 30, 40]
    assert len(access_ports) == 8
    assert len(switches["SW1"]["trunks"]) == 4
    for switch in ["SW2", "SW3", "SW4", "SW5"]:
        assert list(switches[switch]["trunks"]) == ["GigabitEthernet0/0"]


def test_sw5_ports():
    assert PLAN["switches"]["SW5"]["access"] == {"GigabitEthernet0/1": 40, "GigabitEthernet0/2": 30}
    assert "GigabitEthernet1/1" in PLAN["switches"]["SW1"]["trunks"]


def test_refuses_sw5_in_plan_but_not_in_inventory():
    inventory = copy.deepcopy(INVENTORY)
    del inventory["SW5"]
    assert any("SW5" in e for e in validate_plan(PLAN, inventory))


def test_refuses_vlan_99():
    plan = copy.deepcopy(PLAN)
    plan["vlans"].append({"id": 99, "name": "Hack"})
    assert any("VLAN 99" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_host_port():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW1"]["access"] = {"GigabitEthernet1/0": 10}
    assert any("protected" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_unknown_section():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW1"]["svi"] = {"vlan 99": "1.2.3.4"}
    assert any("unknown section" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_switch_not_in_inventory():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW9"] = {"access": {"GigabitEthernet0/1": 10}}
    assert any("SW9" in e for e in validate_plan(plan, INVENTORY))


def test_refuses_vlan_not_in_list():
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW2"]["access"]["GigabitEthernet0/3"] = 50
    assert any("VLAN 50" in e for e in validate_plan(plan, INVENTORY))


# ---------- build_changes ----------

def test_sw1_from_baseline():
    vlans, trunks, ports = sw1_baseline()
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert commands == [
        "vlan 10", "name Sales",
        "vlan 20", "name IT",
        "vlan 30", "name Guest",
        "vlan 40", "name Finance",
        "interface GigabitEthernet0/1", "switchport trunk allowed vlan add 10,20,30,40",
        "interface GigabitEthernet0/2", "switchport trunk allowed vlan add 10,20,30,40",
        "interface GigabitEthernet0/3", "switchport trunk allowed vlan add 10,20,30,40",
        "interface GigabitEthernet1/1", "switchport trunk allowed vlan add 10,20,30,40",
    ]
    # Undo runs in reverse order: trunks first, VLANs last.
    assert undo == [
        "interface GigabitEthernet1/1", "switchport trunk allowed vlan remove 10,20,30,40",
        "interface GigabitEthernet0/3", "switchport trunk allowed vlan remove 10,20,30,40",
        "interface GigabitEthernet0/2", "switchport trunk allowed vlan remove 10,20,30,40",
        "interface GigabitEthernet0/1", "switchport trunk allowed vlan remove 10,20,30,40",
        "no vlan 40",
        "no vlan 30",
        "no vlan 20",
        "no vlan 10",
    ]


def test_sw3_from_baseline():
    vlans, trunks, ports = access_baseline("SW3")
    commands, undo = build_changes("SW3", PLAN, vlans, trunks, ports)
    assert commands[8:] == [
        "interface GigabitEthernet0/0", "switchport trunk allowed vlan add 10,20,30,40",
        "interface GigabitEthernet0/1", "switchport mode access", "switchport access vlan 10",
        "interface GigabitEthernet0/2", "switchport mode access", "switchport access vlan 40",
    ]
    # Undo runs in reverse order: PC ports first, then the uplink, then VLANs.
    assert undo[:4] == [
        "default interface GigabitEthernet0/2",
        "default interface GigabitEthernet0/1",
        "interface GigabitEthernet0/0", "switchport trunk allowed vlan remove 10,20,30,40",
    ]


def test_never_replaces_trunk_list():
    for switch in PLAN["switches"]:
        vlans, trunks, ports = baseline(switch)
        commands, undo = build_changes(switch, PLAN, vlans, trunks, ports)
        for line in commands + undo:
            if line.startswith("switchport trunk allowed vlan"):
                assert " add " in line or " remove " in line


def test_only_missing_trunk_vlans_added():
    vlans, trunks, ports = sw1_baseline()
    trunks["GigabitEthernet0/1"] = [10, 99]
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert "switchport trunk allowed vlan add 20,30,40" in commands
    assert "switchport trunk allowed vlan remove 20,30,40" in undo


def test_nothing_to_do_when_already_done():
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_after.txt"))
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_after.txt"))
    assert build_changes("SW1", PLAN, vlans, trunks, {}) == ([], [])


def test_wrong_vlan_name_is_renamed_back_on_undo():
    vlans, trunks, ports = sw1_baseline()
    vlans[10] = {"name": "OldSales", "ports": []}
    commands, undo = build_changes("SW1", PLAN, vlans, trunks, ports)
    assert commands[:2] == ["vlan 10", "name Sales"]
    assert undo[-2:] == ["vlan 10", "name OldSales"]


def test_port_with_old_config_is_restored_on_undo():
    vlans, trunks, ports = access_baseline("SW2")
    old = parse_interface_config(read_sample("sw2_show_run_int_gi0_2_configured.txt"))
    ports["GigabitEthernet0/2"] = old
    commands, undo = build_changes("SW2", PLAN, vlans, trunks, ports)
    assert undo[:5] == [
        "default interface GigabitEthernet0/2",
        "interface GigabitEthernet0/2",
        "description old guest port",
        "switchport access vlan 5",
        "switchport mode access",
    ]


def real_sw2_gi0_1_config():
    """'show running-config int GigabitEthernet0/1' on SW2, real capture 4 Nov."""
    text = read_sample("real/SW2_flash_copy_and_gi01.txt")
    return text.split("SW2#show running-config int GigabitEthernet0/1")[1]


def test_real_sw2_gi0_1_baseline_is_negotiation_auto():
    assert parse_interface_config(real_sw2_gi0_1_config()) == ["negotiation auto"]


def test_real_sw2_gi0_1_undo_restores_exactly_negotiation_auto():
    # Only Gi0/1 was captured, so plan just that port on SW2.
    plan = copy.deepcopy(PLAN)
    plan["switches"]["SW2"]["access"] = {"GigabitEthernet0/1": 10}
    vlans = parse_vlan_brief(read_sample("real/SW2_show_vlan_brief.txt"))
    trunks = parse_trunk_allowed(read_sample("real/SW2_show_interfaces_trunk.txt"))
    ports = {"GigabitEthernet0/1": parse_interface_config(real_sw2_gi0_1_config())}
    commands, undo = build_changes("SW2", plan, vlans, trunks, ports)
    assert commands[-3:] == ["interface GigabitEthernet0/1", "switchport mode access", "switchport access vlan 10"]
    # Reset the port (removes the tool's lines), then put back its only old line.
    assert undo[:3] == ["default interface GigabitEthernet0/1", "interface GigabitEthernet0/1", "negotiation auto"]


def test_refuses_trunk_that_is_not_a_trunk():
    vlans, trunks, ports = sw1_baseline()
    del trunks["GigabitEthernet0/2"]
    with pytest.raises(ValueError, match="not an active trunk"):
        build_changes("SW1", PLAN, vlans, trunks, ports)


def test_refuses_sw1_gi1_1_when_sw5_uplink_is_not_a_trunk():
    """If the SW5 link is not cabled or not a trunk yet, stop instead of guessing."""
    vlans, trunks, ports = sw1_baseline()
    del trunks["GigabitEthernet1/1"]
    with pytest.raises(ValueError, match="GigabitEthernet1/1 is not an active trunk"):
        build_changes("SW1", PLAN, vlans, trunks, ports)


def test_refuses_access_port_in_vlan_99():
    vlans, trunks, ports = access_baseline("SW2")
    vlans[99]["ports"].append("GigabitEthernet0/1")
    with pytest.raises(ValueError, match="VLAN 99"):
        build_changes("SW2", PLAN, vlans, trunks, ports)


def test_every_switch_gets_all_vlans():
    """VTP transparent: each switch needs its own VLANs, including SW1 (the core) and SW5."""
    assert list(PLAN["switches"]) == ["SW1", "SW2", "SW3", "SW4", "SW5"]
    for switch in PLAN["switches"]:
        vlans, trunks, ports = baseline(switch)
        commands, undo = build_changes(switch, PLAN, vlans, trunks, ports)
        for vlan_id, name in ALL_VLANS:
            assert f"vlan {vlan_id}" in commands, switch
            assert f"name {name}" in commands, switch
            assert f"no vlan {vlan_id}" in undo, switch


def test_sw5_from_baseline():
    vlans, trunks, ports = access_baseline("SW5")
    commands, undo = build_changes("SW5", PLAN, vlans, trunks, ports)
    assert commands[8:] == [
        "interface GigabitEthernet0/0", "switchport trunk allowed vlan add 10,20,30,40",
        "interface GigabitEthernet0/1", "switchport mode access", "switchport access vlan 40",
        "interface GigabitEthernet0/2", "switchport mode access", "switchport access vlan 30",
    ]
