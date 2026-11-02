"""ITT633 VLAN automation tool (main script).

Usage:
    python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]

STATUS: steps 1 to 8 are written (load, pre-check, backup, build changes,
dry-run, apply, verify, save or roll back). They are tested with pytest
using fake switches only, NOT on IOSvL2 in GNS3 yet. Step 9 (report.json
and log file) is still TODO.
"""

import argparse
import getpass
import os
import sys
from datetime import datetime

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from parsers import (
    expand_vlan_list,
    parse_interface_config,
    parse_switchport_all,
    parse_trunk_allowed,
    parse_vlan_brief,
)
from planning import (
    MANAGEMENT_VLAN,
    PROTECTED_PORTS,
    build_changes,
    load_yaml,
    validate_plan,
)

BACKUP_DIR = "backups"

# Any of these in send_config_set output means IOS rejected a command.
IOS_ERRORS = ["% Invalid input", "% Incomplete command", "% Ambiguous command"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Deploy VLANs from plan.yaml to the switches.")
    parser.add_argument("--plan", required=True, help="path to plan.yaml")
    parser.add_argument("--inventory", required=True, help="path to inventory.yaml")
    parser.add_argument("--dry-run", action="store_true", help="print the commands only, change nothing")
    return parser.parse_args(argv)


def ask_credentials():
    """Ask for the switch login. The password is never saved anywhere."""
    username = input("Switch username: ")
    password = getpass.getpass("Switch password: ")
    return username, password


# ---------- Step 2: pre-check ----------

def connect_all(inventory, username, password):
    """SSH to every switch in the inventory.

    Returns (connections, errors):
        connections: {switch: netmiko connection} for switches that worked
        errors:      {switch: reason} for switches that failed
    """
    connections = {}
    errors = {}
    for switch, details in inventory.items():
        try:
            connection = ConnectHandler(
                host=details["host"],
                device_type=details["device_type"],
                username=username,
                password=password,
            )
        except NetmikoAuthenticationException:
            errors[switch] = "login failed (wrong username or password)"
            continue
        except (NetmikoTimeoutException, OSError) as error:
            errors[switch] = f"cannot reach {details['host']}: {error}"
            continue

        # The local user must have privilege 15 so we start at the '#' prompt.
        if not connection.find_prompt().endswith("#"):
            errors[switch] = "logged in but not in privileged mode (user needs privilege 15)"
            connection.disconnect()
            continue
        connections[switch] = connection
    return connections, errors


def disconnect_all(connections):
    for connection in connections.values():
        connection.disconnect()


# ---------- Step 3: backup ----------

def backup_switch(switch, connection, ports, timestamp):
    """Save the running config to backups/<switch>_<timestamp>.cfg.

    Also captures 'show running-config interface <port>' for each planned
    access port, because the undo list needs each port's old lines.
    Returns (backup_path, port_configs).
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, f"{switch}_{timestamp}.cfg")
    running_config = connection.send_command("show running-config")
    with open(backup_path, "w") as f:
        f.write(running_config)

    port_configs = {}
    for port in ports:
        output = connection.send_command(f"show running-config interface {port}")
        port_configs[port] = parse_interface_config(output)
    return backup_path, port_configs


# ---------- Step 4: compare plan with the switch ----------

def plan_switch(switch, connection, plan, port_configs):
    """Read the switch's current VLANs and trunks, then build commands + undo list."""
    current_vlans = parse_vlan_brief(connection.send_command("show vlan brief"))
    current_trunks = parse_trunk_allowed(connection.send_command("show interfaces trunk"))
    return build_changes(switch, plan, current_vlans, current_trunks, port_configs)


# ---------- Step 5: dry-run output ----------

def print_changes(switch, commands, undo):
    print(f"\n=== {switch} ===")
    if not commands:
        print("Nothing to change.")
        return
    print("Commands:")
    for line in commands:
        print("  " + line)
    print("Undo list (runs only if verification fails):")
    for line in undo:
        print("  " + line)


# ---------- Step 6: apply ----------

def copy_to_flash(conn):
    """Copy the running config to flash:pre_change.cfg (backup on the switch).

    IOS asks "Destination filename [pre_change.cfg]?" and, if the file
    already exists, "Do you want to over write? [confirm]". We answer both
    with Enter. These prompts are NOT yet seen on our real IOSvL2.
    send_command_timing is used because these prompts do not end in '#'.
    """
    output = conn.send_command_timing("copy running-config flash:pre_change.cfg")
    if "Destination filename" in output:
        output += conn.send_command_timing("\n")
    if "[confirm]" in output:
        output += conn.send_command_timing("\n")
    return output


def find_ios_error(output, patterns):
    """Return the first output line that contains one of the patterns, or ""."""
    for line in output.splitlines():
        for pattern in patterns:
            if pattern in line:
                return line.strip()
    return ""


def apply(conn, switch_name, commands):
    """Step 6: back up to flash, then send the commands (running config only).

    Does NOT roll back on failure; save_or_rollback (step 8) does that.
    Returns {"ok", "output", "error_line", "sent"}; sent = config reached the switch.
    """
    result = {"ok": False, "output": "", "error_line": "", "sent": False}

    # No flash backup = stop before sending anything.
    # "bytes copied" is the normal IOS success line (NOT yet seen on our IOSvL2).
    copy_output = copy_to_flash(conn)
    copy_error = find_ios_error(copy_output, ["%Error"])
    if copy_error or "bytes copied" not in copy_output:
        result["output"] = copy_output
        result["error_line"] = "flash copy failed: " + (copy_error or "no 'bytes copied' line")
        print(f"[6/9] Apply {switch_name} ... FAILED ({result['error_line']})")
        return result

    if commands:
        result["output"] = conn.send_config_set(commands)
        result["sent"] = True
    result["error_line"] = find_ios_error(result["output"], IOS_ERRORS)
    result["ok"] = result["error_line"] == ""

    if result["ok"]:
        print(f"[6/9] Apply {switch_name} ... OK ({len(commands)} commands)")
    else:
        print(f"[6/9] Apply {switch_name} ... FAILED ({result['error_line']})")
    return result


# ---------- Step 7: verify ----------
# Each check takes PARSED data and returns a list of mismatch strings.
# An empty list means that check passed.

def check_vlans(planned_vlans, vlans):
    """Every planned VLAN exists with its planned name, and VLAN 99 is still there."""
    mismatches = []
    for vlan in planned_vlans:
        vlan_id = vlan["id"]
        if vlan_id not in vlans:
            mismatches.append(f"VLAN {vlan_id} missing")
        elif vlans[vlan_id]["name"] != vlan["name"]:
            mismatches.append(f"VLAN {vlan_id} name is {vlans[vlan_id]['name']}, planned {vlan['name']}")
    if MANAGEMENT_VLAN not in vlans:
        mismatches.append(f"management VLAN {MANAGEMENT_VLAN} missing")
    return mismatches


def check_trunks(trunk_plan, trunks):
    """Each planned trunk is trunking and allows its planned VLANs plus VLAN 99.

    trunks comes from the 'Vlans allowed on trunk' section (the configured
    list), not the spanning tree section, because a new VLAN can take ~30 s
    to start forwarding right after apply.
    """
    mismatches = []
    for port, planned in trunk_plan.items():
        if port not in trunks:
            # parse_trunk_allowed only lists ports that are trunking.
            mismatches.append(f"{port} is not trunking")
            continue
        missing = []
        for vlan_id in planned + [MANAGEMENT_VLAN]:
            if vlan_id not in trunks[port]:
                missing.append(str(vlan_id))
        if missing:
            mismatches.append(f"{port} allowed list is missing {', '.join(missing)}")
    return mismatches


def check_access_ports(access_plan, switchports):
    """Each planned port is in static access mode with the planned VLAN.

    We check the ADMIN mode: on IOSvL2 a port left at 'dynamic auto' still
    shows 'Operational Mode: static access', so the oper mode proves nothing.
    """
    mismatches = []
    for port, planned in access_plan.items():
        if port not in switchports:
            mismatches.append(f"{port} not found in show interfaces switchport")
            continue
        info = switchports[port]
        if info["admin_mode"] != "static access":
            mismatches.append(f"{port} mode is {info['admin_mode']}, planned static access")
        if info["access_vlan"] != planned:
            mismatches.append(f"{port} access VLAN is {info['access_vlan']}, planned {planned}")
    return mismatches


def check_protected_ports(switch_name, switchports):
    """The automation host port (SW1 Gi1/0) must still be access VLAN 99,
    otherwise the tool has cut off its own SSH path."""
    planned = {}
    for port in PROTECTED_PORTS.get(switch_name, []):
        planned[port] = MANAGEMENT_VLAN
    mismatches = []
    for mismatch in check_access_ports(planned, switchports):
        mismatches.append("protected port " + mismatch)
    return mismatches


def verify(conn, switch_name, switch_plan, planned_vlans):
    """Step 7: read the switch again and compare it with plan.yaml.

    Returns {"ok": True/False, "mismatches": [text, ...]}.
    """
    vlans = parse_vlan_brief(conn.send_command("show vlan brief"))
    trunks = parse_trunk_allowed(conn.send_command("show interfaces trunk"))
    switchports = parse_switchport_all(conn.send_command("show interfaces switchport"))

    mismatches = []
    mismatches.extend(check_vlans(planned_vlans, vlans))
    mismatches.extend(check_trunks(switch_plan.get("trunks") or {}, trunks))
    mismatches.extend(check_access_ports(switch_plan.get("access") or {}, switchports))
    mismatches.extend(check_protected_ports(switch_name, switchports))

    ok = mismatches == []
    if ok:
        print(f"[7/9] Verify {switch_name} ... OK")
    else:
        print(f"[7/9] Verify {switch_name} ... FAILED ({len(mismatches)} mismatches)")
        for mismatch in mismatches:
            print("  - " + mismatch)
    return {"ok": ok, "mismatches": mismatches}


# ---------- Step 8: save or roll back ----------

def unsafe_undo_lines(switch_name, undo_commands):
    """Second safety net: undo lines that touch VLAN 99 or a protected port.

    validate_plan() should already make this impossible; this check stops a
    bug in build_changes() from cutting off our own SSH path.
    """
    unsafe = []
    for line in undo_commands:
        words = line.split()
        # e.g. "no vlan 40" or "switchport trunk allowed vlan remove 10,20,30,40"
        if line.startswith(("vlan ", "no vlan ", "switchport trunk allowed vlan remove ")):
            if MANAGEMENT_VLAN in expand_vlan_list(words[-1]):
                unsafe.append(line)
        # e.g. "interface GigabitEthernet1/0" or "default interface GigabitEthernet1/0"
        if line.startswith(("interface ", "default interface ")):
            if words[-1] in PROTECTED_PORTS.get(switch_name, []):
                unsafe.append(line)
    return unsafe


def find_all_percent_lines(output):
    """Every IOS message line, e.g. '% Invalid input detected at '^' marker.'"""
    lines = []
    for line in output.splitlines():
        if line.strip().startswith("%"):
            lines.append(line.strip())
    return lines


def undo_targets(undo_commands):
    """Read back from the undo list what the tool changed on this switch.

    Returns (vlan_ids, trunk_vlans, ports):
        vlan_ids:    VLANs the tool created, from "no vlan 40"
        trunk_vlans: {port: [10, 20]} the tool added to each trunk
        ports:       access ports the tool reset, from "default interface <port>"
    """
    vlan_ids = []
    trunk_vlans = {}
    ports = []
    current_port = None
    for line in undo_commands:
        if line.startswith("no vlan "):
            vlan_ids.append(int(line.split()[-1]))
        elif line.startswith("default interface "):
            ports.append(line.split()[-1])
        elif line.startswith("interface "):
            current_port = line.split()[-1]
        elif line.startswith("switchport trunk allowed vlan remove "):
            trunk_vlans[current_port] = expand_vlan_list(line.split()[-1])
        elif line == "switchport mode access" and current_port in ports:
            # The port was static access before the run, so it may stay that way.
            ports.remove(current_port)
    return vlan_ids, trunk_vlans, ports


def check_rollback(conn, switch_name, undo_commands):
    """After a rollback: are the tool's changes really gone? (TC09)

    Returns a list of mismatch strings (empty = rollback complete).
    """
    vlan_ids, trunk_vlans, ports = undo_targets(undo_commands)
    vlans = parse_vlan_brief(conn.send_command("show vlan brief"))
    trunks = parse_trunk_allowed(conn.send_command("show interfaces trunk"))
    switchports = parse_switchport_all(conn.send_command("show interfaces switchport"))

    mismatches = []
    for vlan_id in vlan_ids:
        if vlan_id in vlans:
            mismatches.append(f"VLAN {vlan_id} still exists")
    for port, added in trunk_vlans.items():
        still_allowed = [str(v) for v in added if v in trunks.get(port, [])]
        if still_allowed:
            mismatches.append(f"{port} still allows {', '.join(still_allowed)}")
    for port in ports:
        if switchports.get(port, {}).get("admin_mode") == "static access":
            mismatches.append(f"{port} is still static access")
    mismatches.extend(check_protected_ports(switch_name, switchports))
    return mismatches


def rollback(conn, switch_name, undo_commands):
    """Send the undo list (running config only, never saved) and check the result."""
    result = {"ok": False, "action": "rollback", "output": "", "error_lines": [], "mismatches": []}
    unsafe = unsafe_undo_lines(switch_name, undo_commands)
    if unsafe:
        # Send NO undo at all: a half-sent undo list is worse than none.
        result["mismatches"] = ["unsafe undo line refused: " + line for line in unsafe]
    else:
        # One batch: IOS keeps going after a % line (e.g. "no vlan 40" when
        # apply stopped before creating VLAN 40), so every undo line runs.
        result["output"] = conn.send_config_set(undo_commands)
        result["error_lines"] = find_all_percent_lines(result["output"])
        result["mismatches"] = check_rollback(conn, switch_name, undo_commands)
    result["ok"] = result["mismatches"] == []

    print(f"[8/9] Rollback {switch_name} ... {'OK' if result['ok'] else 'INCOMPLETE'}")
    for line in result["error_lines"]:
        print("  IOS said: " + line)
    for mismatch in result["mismatches"]:
        print("  - " + mismatch)
    if not result["ok"]:
        print(f"ROLLBACK INCOMPLETE on {switch_name}: restore flash:pre_change.cfg by hand")
    return result


def save_or_rollback(conn, switch_name, passed, undo_commands):
    """Step 8: verify passed -> write memory. Failed -> roll back, never save."""
    if not passed:
        return rollback(conn, switch_name, undo_commands)

    # "[OK]" is the normal IOS line after write memory
    # (NOT yet seen through Netmiko on our IOSvL2).
    output = conn.save_config()
    ok = "[OK]" in output
    print(f"[8/9] Save {switch_name} ... {'OK' if ok else 'FAILED'}")
    return {"ok": ok, "action": "save", "output": output, "error_lines": [], "mismatches": []}


def run_switch(conn, switch_name, plan, commands, undo_commands):
    """Steps 6 to 8 for one switch. Returns its status:
    SAVED, NOT_SAVED, ROLLED_BACK, ROLLBACK_INCOMPLETE or NOT_CHANGED.
    """
    applied = apply(conn, switch_name, commands)
    passed = applied["ok"]
    if passed:
        switch_plan = plan["switches"][switch_name] or {}
        passed = verify(conn, switch_name, switch_plan, plan["vlans"])["ok"]

    if not passed and not applied["sent"]:
        # e.g. the flash copy failed: nothing reached the switch, nothing to undo.
        print(f"[8/9] Rollback {switch_name} ... SKIPPED (nothing was sent)")
        return "NOT_CHANGED"

    outcome = save_or_rollback(conn, switch_name, passed, undo_commands)
    if passed and outcome["ok"]:
        return "SAVED"
    if passed:
        # Running config is correct but not saved; a reload would lose it.
        print(f"{switch_name} verified but NOT saved")
        return "NOT_SAVED"
    if outcome["ok"]:
        return "ROLLED_BACK"
    return "ROLLBACK_INCOMPLETE"


def print_summary(switch_names, statuses):
    """One line per group: saved, rolled back, untouched, needs attention."""
    saved = []
    rolled_back = []
    untouched = []
    attention = []
    for switch in switch_names:
        status = statuses.get(switch)
        if status is None:
            untouched.append(switch)
        elif status == "SAVED":
            saved.append(switch)
        elif status == "ROLLED_BACK":
            rolled_back.append(switch)
        else:
            attention.append(f"{switch} {status}")
    print(f"\nSummary: saved {', '.join(saved) or 'none'}"
          f" | rolled back {', '.join(rolled_back) or 'none'}"
          f" | untouched {', '.join(untouched) or 'none'}")
    if attention:
        print("Needs attention: " + ", ".join(attention))


def main(argv=None):
    args = parse_args(argv)

    # Step 1: read the files and refuse an unsafe plan.
    inventory = load_yaml(args.inventory)
    plan = load_yaml(args.plan)
    errors = validate_plan(plan, inventory)
    if errors:
        print("Plan refused:")
        for error in errors:
            print("  - " + error)
        sys.exit(1)
    print(f"Plan OK: {len(plan['vlans'])} VLANs, {len(plan['switches'])} switches")

    # Step 2: pre-check. If any switch fails, stop before changing anything.
    username, password = ask_credentials()
    connections, errors = connect_all(inventory, username, password)
    if errors:
        print("Pre-check FAILED, nothing was changed:")
        for switch, reason in errors.items():
            print(f"  - {switch}: {reason}")
        disconnect_all(connections)
        sys.exit(1)
    print(f"Pre-check OK: all {len(connections)} switches reachable over SSH")

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        changes = {}
        # Processing order follows plan.yaml: SW1, SW2, SW3, SW4, SW5.
        for switch in plan["switches"]:
            connection = connections[switch]
            settings = plan["switches"][switch] or {}
            access_ports = list((settings.get("access") or {}).keys())

            # Step 3: backup (local file only; flash copy is still TODO).
            backup_path, port_configs = backup_switch(switch, connection, access_ports, timestamp)
            print(f"{switch}: backup saved to {backup_path}")

            # Step 4: build the change list. ValueError = unsafe switch state.
            try:
                changes[switch] = plan_switch(switch, connection, plan, port_configs)
            except ValueError as error:
                print(f"Stopped, nothing was changed: {error}")
                sys.exit(1)

        # Step 5: print commands and undo list per switch.
        for switch, (commands, undo) in changes.items():
            print_changes(switch, commands, undo)

        if args.dry_run:
            print("\nDry run: nothing was sent to the switches.")
            return

        # Steps 6 to 8: apply, verify, save or roll back, one switch at a
        # time in plan order. Stop at the first switch that is not saved, so
        # the remaining switches are never touched. Saved switches stay saved.
        statuses = {}
        for switch in changes:
            commands, undo = changes[switch]
            statuses[switch] = run_switch(connections[switch], switch, plan, commands, undo)
            if statuses[switch] != "SAVED":
                break

        # TODO step 9: write reports/report_<timestamp>.json and the log file
        print_summary(list(changes), statuses)
        for status in statuses.values():
            if status != "SAVED":
                sys.exit(1)
    finally:
        disconnect_all(connections)


if __name__ == "__main__":
    main()
