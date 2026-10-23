"""Turn Cisco "show" output (plain text) into Python data.

STATUS: NOT YET VERIFIED ON IOSvL2.
These parsers were written against typical Cisco IOS output. The sample
files in tests/samples/ are hand-written, not captured from our switches.
Before trusting them, save real output from the IOSvL2 switches into
tests/samples/ and re-run pytest.

Nothing in this file talks to a switch, so it can be tested on the Mac.
"""

import re

# Short interface names used in "show" output, and their full names.
SHORT_NAMES = {
    "Et": "Ethernet",
    "Fa": "FastEthernet",
    "Gi": "GigabitEthernet",
}


def full_interface_name(name):
    """Turn a short name like 'Gi0/1' into 'GigabitEthernet0/1'.

    Names that are already full (or unknown) are returned unchanged.
    """
    match = re.match(r"^([A-Za-z]+)(\d.*)$", name.strip())
    if not match:
        return name.strip()
    prefix, numbers = match.groups()
    return SHORT_NAMES.get(prefix, prefix) + numbers


def expand_vlan_list(text):
    """Turn a Cisco VLAN list like '1,10,20-22' into [1, 10, 20, 21, 22].

    'none' (or an empty string) gives an empty list.
    """
    text = text.strip()
    if text == "" or text.lower() == "none":
        return []
    vlans = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            vlans.extend(range(int(start), int(end) + 1))
        elif part:
            vlans.append(int(part))
    return vlans


def parse_vlan_brief(output):
    """Parse 'show vlan brief'.

    Returns a dict keyed by VLAN id, for example:
        {1: {"name": "default", "ports": ["GigabitEthernet0/3"]},
         99: {"name": "Management", "ports": ["GigabitEthernet1/0"]}}
    """
    vlans = {}
    last_id = None
    # A VLAN line: id, name (may contain spaces), status, then the ports.
    vlan_line = re.compile(r"^(\d+)\s+(.+?)\s+(active|act/\S+|suspended|sus/\S+)\s*(.*)$")

    for line in output.splitlines():
        match = vlan_line.match(line)
        if match:
            vlan_id = int(match.group(1))
            vlans[vlan_id] = {"name": match.group(2), "ports": _split_ports(match.group(4))}
            last_id = vlan_id
        elif line.startswith(" ") and line.strip() and last_id is not None:
            # Long port lists wrap onto the next line, which starts with spaces.
            vlans[last_id]["ports"].extend(_split_ports(line))
        else:
            # Header, dashes, or anything else: stop treating lines as wrapped ports.
            last_id = None
    return vlans


def _split_ports(text):
    """Turn 'Gi0/1, Gi0/2' into ['GigabitEthernet0/1', 'GigabitEthernet0/2']."""
    return [full_interface_name(p) for p in text.split(",") if p.strip()]


def parse_trunk_allowed(output):
    """Parse 'show interfaces trunk' and return the allowed VLANs per trunk.

    Only the 'Vlans allowed on trunk' section is read. Example result:
        {"GigabitEthernet0/1": [99], "GigabitEthernet0/2": [99]}
    A port that is not in the result is not an active trunk.
    """
    trunks = {}
    in_section = False
    last_port = None

    for line in output.splitlines():
        if line.startswith("Port"):
            # Each section starts with a 'Port ...' header; we want only one of them.
            in_section = "Vlans allowed on trunk" in line
            last_port = None
            continue
        if not in_section or not line.strip():
            continue
        if line.startswith(" ") and last_port is not None:
            # Wrapped continuation of a long VLAN list, e.g. '            ,300'
            trunks[last_port].extend(expand_vlan_list(line.strip().lstrip(",")))
            continue
        parts = line.split()
        if len(parts) >= 2:
            last_port = full_interface_name(parts[0])
            trunks[last_port] = expand_vlan_list(parts[1])
    return trunks


def parse_switchport(output):
    """Parse 'show interfaces <port> switchport'.

    Returns, for example:
        {"admin_mode": "static access", "oper_mode": "static access", "access_vlan": 30}
    Missing fields are None.
    """
    result = {"admin_mode": None, "oper_mode": None, "access_vlan": None}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "Administrative Mode":
            result["admin_mode"] = value
        elif key == "Operational Mode":
            result["oper_mode"] = value
        elif key == "Access Mode VLAN":
            # Value looks like '30 (Guest)'; keep only the number.
            result["access_vlan"] = int(value.split()[0])
    return result


def parse_interface_config(output):
    """Parse 'show running-config interface <port>'.

    Returns the config lines under the interface, without the leading space:
        ["switchport access vlan 30", "switchport mode access"]
    An empty list means the port had no configuration.
    """
    lines = []
    inside = False
    for line in output.splitlines():
        if line.startswith("interface "):
            inside = True
        elif inside and line.startswith(" "):
            lines.append(line.strip())
        elif inside:
            # '!' or 'end' (or anything not indented) closes the block.
            break
    return lines
