"""ITT633 VLAN automation tool (main script).

Usage:
    python vlan_tool.py --plan plan.yaml --inventory inventory.yaml [--dry-run]

STATUS: skeleton. Loading and checking the YAML files works. The steps that
talk to the switches (pre-check, backup, apply, verify, save, rollback,
report) are still TODO and have not been tested in GNS3.
"""

import argparse
import sys

from planning import load_yaml, validate_plan


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy VLANs from plan.yaml to the switches.")
    parser.add_argument("--plan", required=True, help="path to plan.yaml")
    parser.add_argument("--inventory", required=True, help="path to inventory.yaml")
    parser.add_argument("--dry-run", action="store_true", help="print the commands only, change nothing")
    return parser.parse_args()


def main():
    args = parse_args()

    # Step 1: read the files and refuse an unsafe plan.
    inventory = load_yaml(args.inventory)
    plan = load_yaml(args.plan)
    errors = validate_plan(plan, inventory)
    if errors:
        print("Plan refused:")
        for error in errors:
            print("  - " + error)
        sys.exit(1)
    print("Plan OK: %d VLANs, %d switches" % (len(plan["vlans"]), len(plan["switches"])))

    # TODO step 2: pre-check SSH to every switch (stop if any fails)
    # TODO step 3: back up each switch (local file + flash:pre_change.cfg)
    # TODO step 4: build the change list with planning.build_changes()
    # TODO step 5: if --dry-run, print commands and undo list, then stop
    # TODO steps 6-8: per switch SW1, SW2, SW3: apply, verify, save or roll back
    # TODO step 9: write reports/report_<timestamp>.json and the log file
    print("Switch steps are not written yet.")


if __name__ == "__main__":
    main()
