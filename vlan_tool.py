"""ITT633 VLAN automation tool (main script).

Usage:
    python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]

STATUS: steps 1 to 5 are written (load, pre-check, backup, build changes,
dry-run). They are tested with pytest using fake switches only, NOT on
IOSvL2 in GNS3 yet. Steps 6 to 9 (apply, verify, save/rollback, report)
are still TODO.
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

from parsers import parse_interface_config, parse_trunk_allowed, parse_vlan_brief
from planning import build_changes, load_yaml, validate_plan

BACKUP_DIR = "backups"


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
        # Processing order follows plan.yaml: SW1, SW2, SW3, SW4 (then SW5 if enabled).
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

        # TODO steps 6-8: per switch in plan order: apply, verify, save or roll back
        # TODO step 9: write reports/report_<timestamp>.json and the log file
        print("\nApply/verify/rollback are not written yet. Nothing was changed.")
    finally:
        disconnect_all(connections)


if __name__ == "__main__":
    main()
