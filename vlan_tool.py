"""ITT633 VLAN automation tool (main script).

Usage:
    python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]

STATUS: all 9 steps are written (load, pre-check, backup, build changes,
dry-run, apply, verify, save or roll back, report). They are tested with
pytest using fake switches, and ran on real IOSvL2 15.2 in GNS3 (5 to 6 Nov):
apply + verify + write memory on SW1 to SW5 (run_20261105_235328) and the
TC09 rollback on SW3 (run_20261106_014041), logs in tests/samples/real/.
"""

import argparse
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime

from netmiko import (
    ConnectHandler,
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
    ReadTimeout,
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


REPORT_DIR = "reports"
LOG_DIR = "logs"
LOG_PATH = None   # set once in main(); say() also writes here when set


def say(line):
    """Print a progress line AND append it to the run's log file."""
    print(line)
    if LOG_PATH is not None:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")


def log_output(title, text):
    """Raw device output goes to the log only (TC08 evidence), not the screen."""
    if LOG_PATH is not None:
        with open(LOG_PATH, "a") as f:
            f.write(f"----- {title} -----\n{text}\n----- end -----\n")


def show(conn, command):
    """Run a show command and keep its raw output in the log.

    Not used for 'show running-config': it holds the password hash, and the
    backup file already keeps it.
    """
    output = conn.send_command(command)
    log_output(command, output)
    return output


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


def start_log(started, dry_run, plan_file):
    """Pick this run's name and start logs/run_<name>.log. Returns the name."""
    global LOG_PATH   # say() writes every progress line to this run's log
    timestamp = new_run_name(started)
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG_PATH = os.path.join(LOG_DIR, f"run_{timestamp}.log")
    say(f"Run {timestamp}: {'dry-run' if dry_run else 'apply'}, plan {plan_file}")
    return timestamp


# ---------- Step 1: read the files ----------

def read_files(inventory_path, plan_path):
    """Load both YAML files and refuse an unsafe plan (exit code 1, no report).

    Returns (inventory, plan).
    """
    inventory = load_yaml(inventory_path)
    plan = load_yaml(plan_path)
    errors = validate_plan(plan, inventory)
    if errors:
        say("Plan refused:")
        for error in errors:
            say("  - " + error)
        sys.exit(1)
    say(f"Plan OK: {len(plan['vlans'])} VLANs, {len(plan['switches'])} switches")
    return inventory, plan


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


def precheck(inventory, records):
    """Step 2: ask for the login, then SSH to every switch.

    Returns (connections, reachable). reachable is False if any switch failed;
    those switches are marked UNREACHABLE and every connection is already closed.
    """
    username, password = ask_credentials()
    connections, errors = connect_all(inventory, username, password)
    if errors:
        say("Pre-check FAILED, nothing was changed:")
        for switch, reason in errors.items():
            say(f"  - {switch}: {reason}")
            records[switch]["status"] = "UNREACHABLE"
            records[switch]["error_lines"].append(reason)
        disconnect_all(connections)
        return connections, False
    say(f"Pre-check OK: all {len(connections)} switches reachable over SSH")
    return connections, True


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
    current_vlans = parse_vlan_brief(show(connection, "show vlan brief"))
    current_trunks = parse_trunk_allowed(show(connection, "show interfaces trunk"))
    return build_changes(switch, plan, current_vlans, current_trunks, port_configs)


def backup_and_plan_all(plan, connections, records, timestamp):
    """Steps 3 and 4 for every switch, in plan order, before anything is changed.

    Returns (changes, unsafe_state):
        changes:      {switch: (commands, undo_list)}
        unsafe_state: None, or the reason a switch cannot be changed safely
    """
    changes = {}
    for switch in plan["switches"]:
        connection = connections[switch]
        settings = plan["switches"][switch] or {}
        access_ports = list((settings.get("access") or {}).keys())
        begin = time.monotonic()

        # Step 3: backup to a local file (the flash copy happens in apply, step 6).
        backup_path, port_configs = backup_switch(switch, connection, access_ports, timestamp)
        say(f"{switch}: backup saved to {backup_path}")
        records[switch]["backup_file"] = backup_path

        # Step 4: build the change list. ValueError = unsafe switch state.
        try:
            changes[switch] = plan_switch(switch, connection, plan, port_configs)
        except ValueError as error:
            say(f"Stopped, nothing was changed: {error}")
            return changes, str(error)
        records[switch]["planned_commands"], records[switch]["undo_list"] = changes[switch]
        records[switch]["seconds"] = round(time.monotonic() - begin, 2)
    return changes, None


# ---------- Step 5: dry-run output ----------

def print_changes(switch, commands, undo):
    say(f"\n=== {switch} ===")
    if not commands:
        say("Nothing to change.")
        return
    say("Commands:")
    for line in commands:
        say("  " + line)
    say("Undo list (runs only if verification fails):")
    for line in undo:
        say("  " + line)


def end_dry_run(changes, records):
    """Dry run ends after step 5: nothing was sent, so every switch is NOT_CHANGED."""
    say("\nDry run: nothing was sent to the switches.")
    for switch in changes:
        records[switch]["status"] = "NOT_CHANGED"


# ---------- Step 6: apply ----------

def copy_to_flash(conn):
    """Copy the running config to flash:pre_change.cfg (backup on the switch).

    Real SW2 output (4 Nov): "Destination filename [pre_change.cfg]?" and, if the
    file already exists, "%Warning:There is a file already existing with this name"
    and "Do you want to over write? [confirm]". We answer both with Enter.
    The copy is then silent for 7 to 13 s before "3611 bytes copied in 13.375 secs".
    send_command waits for an expected pattern (up to 60 s here); send_command_timing
    stops after 2 s of silence and would miss "bytes copied".
    """
    # This switch's own prompt, e.g. "SW2#" (base_prompt is "SW2"), so a stray "#" does not end the wait.
    prompt = re.escape(conn.base_prompt) + "#"
    output = ""
    try:
        # Matches "Destination filename [pre_change.cfg]?" or "SW2#" (after an instant %Error).
        output += conn.send_command("copy running-config flash:pre_change.cfg",
                                    expect_string=r"Destination filename \[\S+\]\?|" + prompt,
                                    strip_prompt=False, strip_command=False)
        if "Destination filename" in output:
            # Matches "Do you want to over write? [confirm]" or "SW2#" after "bytes copied".
            output += conn.send_command("\n", expect_string=r"\[confirm\]|" + prompt,
                                        read_timeout=60, strip_prompt=False, strip_command=False)
        if "[confirm]" in output:
            # Matches "SW2#" after "3611 bytes copied in 6.801 secs (531 bytes/sec)".
            output += conn.send_command("\n", expect_string=prompt,
                                        read_timeout=60, strip_prompt=False, strip_command=False)
    except ReadTimeout:
        # No expected reply in time: apply() then fails with "no 'bytes copied' line".
        output += "\n(no reply from the switch within the time limit)"
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
    # "bytes copied" is the IOS success line (seen on SW2, 4 Nov).
    copy_output = copy_to_flash(conn)
    log_output("copy running-config flash:pre_change.cfg", copy_output)
    copy_error = find_ios_error(copy_output, ["%Error"])
    if copy_error or "bytes copied" not in copy_output:
        result["output"] = copy_output
        result["error_line"] = "flash copy failed: " + (copy_error or "no 'bytes copied' line")
        say(f"[6/9] Apply {switch_name} ... FAILED ({result['error_line']})")
        return result

    if commands:
        result["output"] = conn.send_config_set(commands)
        result["sent"] = True
        log_output("apply: send_config_set", result["output"])
    result["error_line"] = find_ios_error(result["output"], IOS_ERRORS)
    result["ok"] = result["error_line"] == ""

    if result["ok"]:
        say(f"[6/9] Apply {switch_name} ... OK ({len(commands)} commands)")
    else:
        say(f"[6/9] Apply {switch_name} ... FAILED ({result['error_line']})")
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
    vlans = parse_vlan_brief(show(conn, "show vlan brief"))
    trunks = parse_trunk_allowed(show(conn, "show interfaces trunk"))
    switchports = parse_switchport_all(show(conn, "show interfaces switchport"))

    mismatches = []
    mismatches.extend(check_vlans(planned_vlans, vlans))
    mismatches.extend(check_trunks(switch_plan.get("trunks") or {}, trunks))
    mismatches.extend(check_access_ports(switch_plan.get("access") or {}, switchports))
    mismatches.extend(check_protected_ports(switch_name, switchports))

    ok = mismatches == []
    if ok:
        say(f"[7/9] Verify {switch_name} ... OK")
    else:
        say(f"[7/9] Verify {switch_name} ... FAILED ({len(mismatches)} mismatches)")
        for mismatch in mismatches:
            say("  - " + mismatch)
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
    vlans = parse_vlan_brief(show(conn, "show vlan brief"))
    trunks = parse_trunk_allowed(show(conn, "show interfaces trunk"))
    switchports = parse_switchport_all(show(conn, "show interfaces switchport"))

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
    result = {"ok": False, "action": "rollback", "output": "", "error_lines": [], "mismatches": [],
              "sent": False}
    unsafe = unsafe_undo_lines(switch_name, undo_commands)
    if unsafe:
        # Send NO undo at all: a half-sent undo list is worse than none.
        result["mismatches"] = ["unsafe undo line refused: " + line for line in unsafe]
    else:
        # One batch: IOS keeps going after a % line (e.g. "no vlan 40" when
        # apply stopped before creating VLAN 40), so every undo line runs.
        result["output"] = conn.send_config_set(undo_commands)
        result["sent"] = True
        log_output("rollback: send_config_set", result["output"])
        result["error_lines"] = find_all_percent_lines(result["output"])
        result["mismatches"] = check_rollback(conn, switch_name, undo_commands)
    result["ok"] = result["mismatches"] == []

    say(f"[8/9] Rollback {switch_name} ... {'OK' if result['ok'] else 'INCOMPLETE'}")
    for line in result["error_lines"]:
        say("  IOS said: " + line)
    for mismatch in result["mismatches"]:
        say("  - " + mismatch)
    if not result["ok"]:
        say(f"ROLLBACK INCOMPLETE on {switch_name}: restore flash:pre_change.cfg by hand")
    return result


def save_or_rollback(conn, switch_name, passed, undo_commands):
    """Step 8: verify passed -> write memory. Failed -> roll back, never save."""
    if not passed:
        return rollback(conn, switch_name, undo_commands)

    # "[OK]" is the normal IOS line after write memory. Real IOSvL2 through
    # Netmiko (5 Nov): "Compressed configuration from 4091 bytes to 2223 bytes[OK]".
    output = conn.save_config()
    log_output("write memory", output)
    ok = "[OK]" in output
    say(f"[8/9] Save {switch_name} ... {'OK' if ok else 'FAILED'}")
    return {"ok": ok, "action": "save", "output": output, "error_lines": [], "mismatches": []}


def add_to_record(record, result, commands):
    """Copy what one step did into the switch's report.json entry."""
    if result.get("sent"):
        record["commands_sent"].extend(commands)
    if result.get("error_line"):
        record["error_lines"].append(result["error_line"])
    record["error_lines"].extend(result.get("error_lines", []))
    record["mismatches"].extend(result.get("mismatches", []))


def run_switch(conn, switch_name, plan, commands, undo_commands, record):
    """Steps 6 to 8 for one switch. Fills record (for report.json) and returns
    its status: SAVED, NOT_SAVED, ROLLED_BACK, ROLLBACK_INCOMPLETE or NOT_CHANGED.
    """
    # Overwritten when this function returns, so only a crash leaves it in the report.
    record["status"] = "IN_PROGRESS"
    applied = apply(conn, switch_name, commands)
    add_to_record(record, applied, commands)
    passed = applied["ok"]
    if passed:
        switch_plan = plan["switches"][switch_name] or {}
        checked = verify(conn, switch_name, switch_plan, plan["vlans"])
        add_to_record(record, checked, [])
        passed = checked["ok"]

    if not passed and not applied["sent"]:
        # e.g. the flash copy failed: nothing reached the switch, nothing to undo.
        say(f"[8/9] Rollback {switch_name} ... SKIPPED (nothing was sent)")
        return "NOT_CHANGED"

    outcome = save_or_rollback(conn, switch_name, passed, undo_commands)
    add_to_record(record, outcome, undo_commands)
    if passed and outcome["ok"]:
        return "SAVED"
    if passed:
        # Running config is correct but not saved; a reload would lose it.
        say(f"{switch_name} verified but NOT saved")
        return "NOT_SAVED"
    if outcome["ok"]:
        return "ROLLED_BACK"
    return "ROLLBACK_INCOMPLETE"


def run_all_switches(changes, connections, plan, records):
    """Steps 6 to 8 for each switch in plan order (SW1, SW2, SW3, SW4, SW5).

    Stops at the first switch that is not SAVED, so the remaining switches
    are never touched. Switches already saved stay saved.
    Returns (statuses, stop_reason).
    """
    statuses = {}
    stop_reason = "completed"
    for switch in changes:
        commands, undo = changes[switch]
        begin = time.monotonic()
        statuses[switch] = run_switch(connections[switch], switch, plan, commands, undo, records[switch])
        records[switch]["status"] = statuses[switch]
        records[switch]["seconds"] = round(records[switch]["seconds"] + time.monotonic() - begin, 2)
        if statuses[switch] != "SAVED":
            stop_reason = f"stopped at {switch} ({statuses[switch]})"
            break
    return statuses, stop_reason


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
    say(f"\nSummary: saved {', '.join(saved) or 'none'}"
          f" | rolled back {', '.join(rolled_back) or 'none'}"
          f" | untouched {', '.join(untouched) or 'none'}")
    if attention:
        say("Needs attention: " + ", ".join(attention))


# ---------- Step 9: report ----------

def new_run_name(started):
    """Timestamp for this run's backup, report and log names: 20261102_140311.

    Two runs in the same second get _2, _3 ... so no run overwrites another.
    """
    base = started.strftime("%Y%m%d_%H%M%S")
    name = base
    number = 2
    while (os.path.exists(os.path.join(LOG_DIR, f"run_{name}.log"))
           or os.path.exists(os.path.join(REPORT_DIR, f"report_{name}.json"))):
        name = f"{base}_{number}"
        number += 1
    return name


def new_record(name, ip):
    """One switch's entry in report.json. Every switch starts as NOT_RUN."""
    return {
        "name": name,
        "ip": ip,
        "status": "NOT_RUN",
        "seconds": 0.0,
        "commands_sent": [],       # what really reached the switch (apply, then undo)
        "mismatches": [],          # from verify, and from the rollback check
        "error_lines": [],         # IOS % lines, or why the switch failed
        "backup_file": None,
        "planned_commands": [],    # what the tool planned (dry-run evidence, TC02)
        "undo_list": [],
    }


def make_records(inventory):
    """One report.json entry per switch, in inventory order."""
    records = {}
    for switch, details in inventory.items():
        records[switch] = new_record(switch, details["host"])
    return records


def write_report(timestamp, started, dry_run, plan_file, stop_reason, records):
    """Step 9: save reports/report_<timestamp>.json (evidence for Chapter 4).

    It holds only the records built during the run, never the username or password.
    """
    finished = datetime.now()
    report = {
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "total_seconds": round((finished - started).total_seconds(), 2),
        "dry_run": dry_run,
        "plan_file": os.path.basename(plan_file),
        "stop_reason": stop_reason,
        "switches": list(records.values()),   # inventory order
    }
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"report_{timestamp}.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    say(f"[9/9] Report saved: {path}")
    return path


def main(argv=None):
    args = parse_args(argv)
    started = datetime.now()
    timestamp = start_log(started, args.dry_run, args.plan)

    # Step 1: a plan that breaks a safety rule (e.g. VLAN 99 or SW1 Gi1/0 in it) stops here, before any SSH.
    inventory, plan = read_files(args.inventory, args.plan)
    records = make_records(inventory)

    # Step 2: if any switch fails, stop before changing anything (TC10).
    connections, reachable = precheck(inventory, records)
    if not reachable:
        write_report(timestamp, started, args.dry_run, args.plan, "pre-check failed", records)
        sys.exit(1)

    # Step 9 runs in 'finally', so every ending below writes a report.
    stop_reason = "unexpected error (see the log)"
    try:
        # Steps 3 and 4 run on ALL switches before any change, so a switch in an
        # unsafe state stops the run while every switch is still untouched.
        changes, unsafe_state = backup_and_plan_all(plan, connections, records, timestamp)
        if unsafe_state is not None:
            stop_reason = f"unsafe switch state: {unsafe_state}"
            sys.exit(1)

        # Step 5: always print the plan, so it is on screen and in the log in both modes.
        for switch, (commands, undo) in changes.items():
            print_changes(switch, commands, undo)
        if args.dry_run:
            end_dry_run(changes, records)
            stop_reason = "dry run"
            return

        # Steps 6 to 8, one switch at a time. Stop at the first switch not saved,
        # so the rest are never touched and the saved ones stay saved.
        statuses, stop_reason = run_all_switches(changes, connections, plan, records)
        print_summary(list(changes), statuses)
        for status in statuses.values():
            if status != "SAVED":
                sys.exit(1)
    except Exception as error:
        # e.g. SSH dropped mid-run: record why, then let Python show the error.
        stop_reason = f"unexpected error: {error}"
        raise
    finally:
        disconnect_all(connections)
        write_report(timestamp, started, args.dry_run, args.plan, stop_reason, records)


if __name__ == "__main__":
    main()
