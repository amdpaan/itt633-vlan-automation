"""Check plan.yaml and build the command list (plus undo list) per switch.

Nothing in this file talks to a switch. The current state of a switch is
passed in as Python data made by parsers.py, so everything here can be
tested on the Mac with pytest.
"""

import yaml

# VLANs and ports the tool must never touch (manual baseline).
MANAGEMENT_VLAN = 99
RESERVED_VLANS = [1, 1002, 1003, 1004, 1005]  # Cisco built-in VLANs
# The automation host port (IOSvL2 name). If the image changes, update this name too.
PROTECTED_PORTS = {"SW1": ["GigabitEthernet1/0"]}


def load_yaml(path):
    """Read a YAML file and return its content as Python data."""
    with open(path) as f:
        return yaml.safe_load(f)


def validate_plan(plan, inventory):
    """Check plan.yaml against the safety rules before anything is sent.

    Returns a list of error messages. An empty list means the plan is OK.
    """
    errors = []
    if not isinstance(plan, dict) or "vlans" not in plan or "switches" not in plan:
        return ["plan must have 'vlans' and 'switches' sections"]

    # --- VLAN list ---
    planned_ids = []
    for vlan in plan["vlans"]:
        vlan_id = vlan.get("id")
        name = vlan.get("name")
        if not isinstance(vlan_id, int) or not 2 <= vlan_id <= 1001:
            errors.append("VLAN id %r must be a number from 2 to 1001" % vlan_id)
            continue
        if vlan_id == MANAGEMENT_VLAN:
            errors.append("VLAN 99 is the management VLAN and must not be in the plan")
        if vlan_id in planned_ids:
            errors.append("VLAN %d is listed twice" % vlan_id)
        if not isinstance(name, str) or not name or len(name) > 32 or " " in name:
            errors.append("VLAN %d needs a name of 1-32 characters with no spaces" % vlan_id)
        planned_ids.append(vlan_id)

    # --- Per switch ---
    for switch, settings in plan["switches"].items():
        if switch not in inventory:
            errors.append("%s is in the plan but not in the inventory" % switch)
        settings = settings or {}
        for key in settings:
            if key not in ("access", "trunks"):
                errors.append("%s: unknown section '%s' (only 'access' and 'trunks' are allowed)" % (switch, key))

        access = settings.get("access") or {}
        trunks = settings.get("trunks") or {}

        for port, vlan_id in access.items():
            if port in PROTECTED_PORTS.get(switch, []):
                errors.append("%s %s is a protected baseline port" % (switch, port))
            if port in trunks:
                errors.append("%s %s cannot be both an access port and a trunk" % (switch, port))
            if vlan_id not in planned_ids:
                errors.append("%s %s uses VLAN %r, which is not in the vlans list" % (switch, port, vlan_id))

        for port, vlan_list in trunks.items():
            if port in PROTECTED_PORTS.get(switch, []):
                errors.append("%s %s is a protected baseline port" % (switch, port))
            for vlan_id in vlan_list:
                if vlan_id not in planned_ids:
                    errors.append("%s trunk %s uses VLAN %r, which is not in the vlans list" % (switch, port, vlan_id))
    return errors


def vlan_list_text(vlan_ids):
    """Turn [10, 20, 30] into '10,20,30' for a Cisco command."""
    return ",".join(str(v) for v in vlan_ids)


def build_changes(switch, plan, current_vlans, current_trunks, port_configs):
    """Compare the plan with the switch's current state.

    switch:         name such as "SW1"
    plan:           the whole plan.yaml (already validated)
    current_vlans:  from parsers.parse_vlan_brief
    current_trunks: from parsers.parse_trunk_allowed
    port_configs:   {port: lines} from parsers.parse_interface_config,
                    one entry per planned access port (captured in the backup step)

    Returns (commands, undo). Only missing settings produce commands.
    Every change gets its matching undo lines BEFORE anything is applied.
    Raises ValueError if the switch is in a state the tool must not touch.
    """
    settings = plan["switches"].get(switch) or {}
    # Each change is a pair: (commands, undo commands).
    changes = []

    # 1. VLANs (created on every switch in the plan)
    for vlan in plan["vlans"]:
        vlan_id = vlan["id"]
        name = vlan["name"]
        if vlan_id not in current_vlans:
            changes.append((["vlan %d" % vlan_id, "name %s" % name],
                            ["no vlan %d" % vlan_id]))
        elif current_vlans[vlan_id]["name"] != name:
            old_name = current_vlans[vlan_id]["name"]
            changes.append((["vlan %d" % vlan_id, "name %s" % name],
                            ["vlan %d" % vlan_id, "name %s" % old_name]))

    # 2. Trunks: only ever 'add' / 'remove', never replace the allowed list,
    #    otherwise VLAN 99 would be dropped and we would lose SSH.
    for port, wanted in (settings.get("trunks") or {}).items():
        if port not in current_trunks:
            raise ValueError("%s %s is not an active trunk; the tool does not change trunk mode" % (switch, port))
        missing = [v for v in wanted if v not in current_trunks[port]]
        if missing:
            changes.append((["interface %s" % port,
                             "switchport trunk allowed vlan add %s" % vlan_list_text(missing)],
                            ["interface %s" % port,
                             "switchport trunk allowed vlan remove %s" % vlan_list_text(missing)]))

    # 3. Access ports
    vlan99_ports = current_vlans.get(MANAGEMENT_VLAN, {}).get("ports", [])
    for port, vlan_id in (settings.get("access") or {}).items():
        if port in current_trunks:
            raise ValueError("%s %s is currently a trunk; refusing to make it an access port" % (switch, port))
        if port in vlan99_ports:
            raise ValueError("%s %s is in management VLAN 99; refusing to change it" % (switch, port))
        if port not in port_configs:
            raise ValueError("%s %s: no saved running-config for this port" % (switch, port))

        old_lines = port_configs[port]
        wanted_lines = ["switchport mode access", "switchport access vlan %d" % vlan_id]
        if all(line in old_lines for line in wanted_lines):
            continue  # already correct, nothing to do

        commands = ["interface %s" % port] + wanted_lines
        if not old_lines:
            # Port had no config: putting it back to default is a full undo.
            undo = ["default interface %s" % port]
        else:
            # Port had settings: reset it, then re-apply its old lines.
            undo = ["default interface %s" % port, "interface %s" % port] + old_lines
        changes.append((commands, undo))

    # Commands run in order. Undo runs in reverse order (last change undone
    # first), so ports leave a VLAN before that VLAN is deleted.
    commands = []
    undo = []
    for change_commands, change_undo in changes:
        commands.extend(change_commands)
    for change_commands, change_undo in reversed(changes):
        undo.extend(change_undo)
    return commands, undo
