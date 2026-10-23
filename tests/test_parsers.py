"""Tests for parsers.py.

NOT YET VERIFIED ON IOSvL2: the sample files are hand-written. Passing
these tests only shows the parsers handle typical Cisco IOS output.
"""

from conftest import read_sample
from parsers import (
    expand_vlan_list,
    full_interface_name,
    parse_interface_config,
    parse_switchport,
    parse_trunk_allowed,
    parse_vlan_brief,
)


def test_full_interface_name():
    assert full_interface_name("Gi0/1") == "GigabitEthernet0/1"
    assert full_interface_name("Et0/1") == "Ethernet0/1"  # old IOL-L2 short names still work
    assert full_interface_name("Gi0/2") == "GigabitEthernet0/2"
    assert full_interface_name("GigabitEthernet1/0") == "GigabitEthernet1/0"


def test_expand_vlan_list():
    assert expand_vlan_list("10,20,30,99") == [10, 20, 30, 99]
    assert expand_vlan_list("1,5-7") == [1, 5, 6, 7]
    assert expand_vlan_list("none") == []


def test_vlan_brief_baseline():
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_baseline.txt"))
    assert vlans[99] == {"name": "Management", "ports": ["GigabitEthernet1/0"]}
    # Wrapped second line of ports is joined to VLAN 1
    assert "GigabitEthernet2/3" in vlans[1]["ports"]
    assert 10 not in vlans


def test_vlan_brief_after():
    vlans = parse_vlan_brief(read_sample("sw1_show_vlan_brief_after.txt"))
    assert vlans[10] == {"name": "Sales", "ports": []}
    assert vlans[30] == {"name": "Guest", "ports": ["GigabitEthernet0/3"]}
    assert vlans[1002]["name"] == "fddi-default"


def test_trunk_baseline():
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_baseline.txt"))
    assert trunks == {"GigabitEthernet0/1": [99], "GigabitEthernet0/2": [99]}


def test_trunk_after():
    trunks = parse_trunk_allowed(read_sample("sw1_show_interfaces_trunk_after.txt"))
    assert trunks["GigabitEthernet0/1"] == [10, 20, 30, 99]


def test_trunk_wrapped_line():
    output = (
        "Port        Vlans allowed on trunk\n"
        "Gi0/1       1,10,20,30,99,100-102\n"
        "            ,300\n"
        "\n"
    )
    assert parse_trunk_allowed(output) == {"GigabitEthernet0/1": [1, 10, 20, 30, 99, 100, 101, 102, 300]}


def test_switchport():
    info = parse_switchport(read_sample("sw1_show_interfaces_gi0_3_switchport.txt"))
    assert info == {"admin_mode": "static access", "oper_mode": "static access", "access_vlan": 30}


def test_interface_config_empty():
    assert parse_interface_config(read_sample("sw1_show_run_int_gi0_3_empty.txt")) == []


def test_interface_config_with_lines():
    lines = parse_interface_config(read_sample("sw3_show_run_int_gi0_3_configured.txt"))
    assert lines == ["description old guest port", "switchport access vlan 5", "switchport mode access"]
