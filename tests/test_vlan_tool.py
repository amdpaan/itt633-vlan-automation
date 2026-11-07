"""Tests for vlan_tool.py steps 1 to 9.

No real switches: Netmiko's ConnectHandler is replaced with FakeSwitch,
which answers show commands with sample files.
FLASH_PROMPTS and the canned config output below are guesses from normal IOS
behaviour; the real_flash_lines() tests use real SW2 flash copy text (4 Nov).
Post-apply and TC09 tests use the REAL output saved in the tool's own logs
(run_20261105_235328.log = real apply run, run_20261106_014041.log = real TC09).
"""

import json
import os
import re
from unittest import mock

import pytest
import yaml
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException, ReadTimeout

import vlan_tool
from conftest import log_block, read_sample
from parsers import parse_interface_config, parse_switchport_all, parse_trunk_allowed, parse_vlan_brief
from planning import MANAGEMENT_VLAN, PROTECTED_PORTS, build_changes, load_yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(__file__))
ARGS = ["--plan", os.path.join(PROJECT_DIR, "plan.yaml"),
        "--inventory", os.path.join(PROJECT_DIR, "inventory.yaml")]

# Which sample files each fake switch answers with (SW3 to SW5 look like SW2).
ACCESS = ("sw2_show_vlan_brief_baseline.txt", "sw2_show_interfaces_trunk_baseline.txt")
SAMPLES = {
    "192.168.99.11": ("sw1_show_vlan_brief_baseline.txt", "sw1_show_interfaces_trunk_baseline.txt"),
    "192.168.99.12": ACCESS,
    "192.168.99.13": ACCESS,
    "192.168.99.14": ACCESS,
    "192.168.99.15": ACCESS,
}

# What IOS prints for "copy running-config flash:pre_change.cfg" when the file
# already exists: one reply per step (the copy command, then each Enter).
FLASH_PROMPTS = [
    "Destination filename [pre_change.cfg]? ",
    "Do you want to over write? [confirm]",
    "3456 bytes copied in 0.512 secs (6750 bytes/sec)\nSW#",
]
CLEAN_CONFIG_OUTPUT = "configure terminal\nSW(config)#vlan 10\nSW(config-vlan)#name Sales\nSW(config-vlan)#end\nSW#"
INVALID_CONFIG_OUTPUT = (
    "configure terminal\nSW(config)#interface GigabitEthernet9/9\n"
    "                              ^\n"
    "% Invalid input detected at '^' marker.\n\nSW(config)#end\nSW#"
)


class FakeSwitch:
    """Stands in for a Netmiko connection to a switch in its baseline state."""

    def __init__(self, host, prompt="SW#"):
        self.host = host
        self.prompt = prompt
        self.base_prompt = prompt[:-1]   # what Netmiko keeps: "SW2#" -> "SW2"
        self.sent = []          # every show command we received
        self.config_sent = []   # anything sent as config (must stay empty)
        self.disconnected = False
        self.flash_sent = []    # (command, expect_string, read_timeout) per flash copy step
        self.timing_sent = []   # send_command_timing calls (vlan_tool no longer makes any)
        self.flash_replies = list(FLASH_PROMPTS)
        self.silent_seconds = 0  # quiet time before "bytes copied" / "[OK]" (real copy: 7 to 13 s)
        self.config_output = CLEAN_CONFIG_OUTPUT
        self.config_outputs = []  # one reply per send_config_set call (apply, then undo); then config_output
        self.outputs = {}       # {show command: text}, overrides the answers below
        self.after_config = {}  # {show command: text} that replaces outputs once config is sent
        self.save_output = "write memory\nBuilding configuration...\n[OK]\nSW#"

    def find_prompt(self):
        return self.prompt

    def flash_step(self, command, expect_string, read_timeout):
        """Acts like Netmiko's send_command(expect_string=...): returns the reply once
        the pattern shows up, or raises ReadTimeout if it does not come in time."""
        self.flash_sent.append((command, expect_string, read_timeout))
        reply = self.flash_replies.pop(0)
        quiet = self.silent_seconds if "bytes copied" in reply else 0
        if quiet >= read_timeout or not re.search(expect_string, reply):
            raise ReadTimeout("pattern %r not seen within %s s" % (expect_string, read_timeout))
        return reply

    def send_command(self, command, expect_string=None, read_timeout=10, **kwargs):
        if command in ("copy running-config flash:pre_change.cfg", "\n"):
            return self.flash_step(command, expect_string, read_timeout)
        self.sent.append(command)
        if command in self.outputs:
            return self.outputs[command]
        if command == "show interfaces switchport":
            # Real IOSvL2 baseline capture (SW3 to SW5 look like SW2).
            switch = "SW1" if self.host == "192.168.99.11" else "SW2"
            return read_sample("real/%s_show_interfaces_switchport.txt" % switch)
        vlan_file, trunk_file = SAMPLES[self.host]
        if command == "show running-config":
            return "hostname fake-%s\n!\nend\n" % self.host
        if command == "show vlan brief":
            return read_sample(vlan_file)
        if command == "show interfaces trunk":
            return read_sample(trunk_file)
        if command.startswith("show running-config interface "):
            port = command.split()[-1]
            return "Building configuration...\n!\ninterface %s\nend\n" % port
        raise AssertionError("unexpected command: " + command)

    def send_command_timing(self, command):
        # Netmiko stops reading after 2 s of silence (last_read=2.0), so a slow
        # "bytes copied" never reaches the caller. This is why vlan_tool stopped using it.
        self.timing_sent.append(command)
        reply = self.flash_replies.pop(0)
        if self.silent_seconds > 2 and "bytes copied" in reply:
            return ""
        return reply

    def send_config_set(self, commands):
        self.config_sent.append(commands)
        self.outputs.update(self.after_config)
        if self.config_outputs:
            return self.config_outputs.pop(0)
        return self.config_output

    def save_config(self, cmd="write mem", confirm=False):
        # Netmiko: confirm=False waits for the prompt (read_timeout=100);
        # confirm=True uses send_command_timing, which gives up after 2 s of silence.
        self.config_sent.append("write memory")
        if confirm and self.silent_seconds > 2:
            return "write memory\nBuilding configuration...\n"
        if self.silent_seconds >= 100:
            raise ReadTimeout("write memory: no prompt within 100 s")
        return self.save_output

    def disconnect(self):
        self.disconnected = True


# Easy to search for: it must never appear in a report or a log.
PASSWORD = "S3cret-pw!"


@pytest.fixture(autouse=True)
def output_dirs(tmp_path, monkeypatch):
    """Every test writes backups, reports and logs into tmp_path, never the repo."""
    monkeypatch.setattr(vlan_tool, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(vlan_tool, "REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(vlan_tool, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(vlan_tool, "LOG_PATH", None)


@pytest.fixture
def switches(tmp_path, monkeypatch):
    """Patch Netmiko and the login prompt. Returns {host: FakeSwitch}."""
    monkeypatch.setattr(vlan_tool, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(vlan_tool, "ask_credentials", lambda: ("admin", PASSWORD))
    fakes = {}

    def fake_connect(host, **kwargs):
        fakes[host] = FakeSwitch(host)
        return fakes[host]

    monkeypatch.setattr(vlan_tool, "ConnectHandler", fake_connect)
    return fakes


# ---------- Step 1: read the files ----------

def test_plan_refused_stops_before_ssh(switches, tmp_path, capsys):
    """A plan that touches VLAN 99 stops at step 1: exit 1, no SSH, no backup, no report."""
    plan = load_yaml(ARGS[1])
    plan["vlans"].append({"id": MANAGEMENT_VLAN, "name": "Management"})
    bad_plan = tmp_path / "plan_vlan99.yaml"
    with open(bad_plan, "w") as f:
        yaml.safe_dump(plan, f)

    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(["--plan", str(bad_plan), "--inventory", ARGS[3]])

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "Plan refused:" in out
    assert "  - VLAN 99 is the management VLAN and must not be in the plan" in out
    assert switches == {}                        # ConnectHandler never called
    assert not os.path.exists(tmp_path / "backups")
    assert not os.path.exists(tmp_path / "reports")   # today: no report for a refused plan
    assert "Plan refused:" in read_log(tmp_path)


# ---------- Step 2: pre-check ----------

def test_connect_all_ok(switches):
    inventory = {"SW1": {"host": "192.168.99.11", "device_type": "cisco_ios"}}
    connections, errors = vlan_tool.connect_all(inventory, "admin", "secret")
    assert errors == {}
    assert list(connections) == ["SW1"]


def test_connect_all_reports_which_switch_failed(monkeypatch):
    def fake_connect(host, **kwargs):
        if host == "10.0.0.3":
            raise NetmikoTimeoutException("timed out")
        if host == "10.0.0.2":
            raise NetmikoAuthenticationException("bad password")
        return FakeSwitch("192.168.99.11")

    monkeypatch.setattr(vlan_tool, "ConnectHandler", fake_connect)
    inventory = {
        "SW1": {"host": "10.0.0.1", "device_type": "cisco_ios"},
        "SW2": {"host": "10.0.0.2", "device_type": "cisco_ios"},
        "SW3": {"host": "10.0.0.3", "device_type": "cisco_ios"},
    }
    connections, errors = vlan_tool.connect_all(inventory, "admin", "wrong")
    assert list(connections) == ["SW1"]
    assert "login failed" in errors["SW2"]
    assert "cannot reach 10.0.0.3" in errors["SW3"]


def test_connect_all_refuses_user_mode_prompt(monkeypatch):
    fake = FakeSwitch("192.168.99.11", prompt="SW1>")
    monkeypatch.setattr(vlan_tool, "ConnectHandler", lambda **kwargs: fake)
    inventory = {"SW1": {"host": "192.168.99.11", "device_type": "cisco_ios"}}
    connections, errors = vlan_tool.connect_all(inventory, "admin", "secret")
    assert connections == {}
    assert "privilege 15" in errors["SW1"]
    assert fake.disconnected


def test_unreachable_switch_stops_before_any_change(tmp_path, monkeypatch, capsys):
    """Like TC10: wrong SW3 IP stops at pre-check, nothing is read or changed."""
    monkeypatch.setattr(vlan_tool, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(vlan_tool, "ask_credentials", lambda: ("admin", PASSWORD))
    fakes = {}

    def fake_connect(host, **kwargs):
        if host == "192.168.99.13":
            raise NetmikoTimeoutException("timed out")
        fakes[host] = FakeSwitch(host)
        return fakes[host]

    monkeypatch.setattr(vlan_tool, "ConnectHandler", fake_connect)
    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(ARGS + ["--dry-run"])

    assert exit_info.value.code == 1
    assert "SW3: cannot reach 192.168.99.13" in capsys.readouterr().out
    for fake in fakes.values():
        assert fake.sent == []          # no show commands, no backup
        assert fake.config_sent == []
        assert fake.disconnected
    assert not os.path.exists(tmp_path / "backups")


# ---------- Step 3: backup ----------

def test_backup_writes_file_and_reads_ports(tmp_path, monkeypatch):
    monkeypatch.setattr(vlan_tool, "BACKUP_DIR", str(tmp_path))
    fake = FakeSwitch("192.168.99.11")
    path, ports = vlan_tool.backup_switch("SW1", fake, ["GigabitEthernet0/3"], "20261023_120000")
    assert path == os.path.join(str(tmp_path), "SW1_20261023_120000.cfg")
    with open(path) as f:
        assert "hostname fake-192.168.99.11" in f.read()
    assert ports == {"GigabitEthernet0/3": []}


# ---------- Steps 4 and 5: plan and dry-run ----------

def test_dry_run_prints_commands_and_changes_nothing(switches, tmp_path, capsys):
    vlan_tool.main(ARGS + ["--dry-run"])
    out = capsys.readouterr().out

    # All five switches are printed, in processing order SW1 to SW5.
    positions = [out.index(f"=== SW{n} ===") for n in range(1, 6)]
    assert positions == sorted(positions)
    assert "switchport trunk allowed vlan add 10,20,30,40" in out
    assert "switchport access vlan 40" in out
    assert "default interface GigabitEthernet0/2" in out
    assert "Dry run: nothing was sent" in out
    # One backup file per switch
    assert sorted(f.split("_")[0] for f in os.listdir(tmp_path / "backups")) == ["SW1", "SW2", "SW3", "SW4", "SW5"]
    for fake in switches.values():
        assert fake.config_sent == []   # no config, no write memory
        assert fake.disconnected


def test_unsafe_switch_state_stops(switches, monkeypatch, capsys):
    """If a planned trunk is not really a trunk, stop instead of guessing."""
    monkeypatch.setitem(SAMPLES, "192.168.99.12",
                        ("sw2_show_vlan_brief_baseline.txt", "sw1_show_interfaces_trunk_baseline.txt"))
    with pytest.raises(SystemExit):
        vlan_tool.main(ARGS + ["--dry-run"])
    assert "not an active trunk" in capsys.readouterr().out
    for fake in switches.values():
        assert fake.config_sent == []
        assert fake.disconnected


# ---------- Step 6: apply ----------

def test_apply_clean_output_is_ok(capsys):
    fake = FakeSwitch("192.168.99.12")
    result = vlan_tool.apply(fake, "SW2", ["vlan 10", "name Sales"])
    assert result["ok"] is True
    assert result["error_line"] == ""
    assert fake.config_sent == [["vlan 10", "name Sales"]]
    assert "[6/9] Apply SW2 ... OK" in capsys.readouterr().out


def test_apply_invalid_input_fails_with_that_line(capsys):
    fake = FakeSwitch("192.168.99.12")
    fake.config_output = INVALID_CONFIG_OUTPUT
    result = vlan_tool.apply(fake, "SW2", ["interface GigabitEthernet9/9"])
    assert result["ok"] is False
    assert result["error_line"] == "% Invalid input detected at '^' marker."
    assert "[6/9] Apply SW2 ... FAILED" in capsys.readouterr().out
    assert fake.config_sent != [] and "write memory" not in fake.config_sent


def flash_commands(fake):
    """Just the commands of each flash copy step, e.g. [copy, Enter, Enter]."""
    return [command for command, expect_string, read_timeout in fake.flash_sent]


def test_apply_answers_flash_prompts():
    fake = FakeSwitch("192.168.99.12")
    vlan_tool.apply(fake, "SW2", ["vlan 10"])
    # Copy command, then Enter for the filename, then Enter for overwrite.
    assert flash_commands(fake) == ["copy running-config flash:pre_change.cfg", "\n", "\n"]
    assert fake.timing_sent == []


def test_apply_flash_copy_new_file_needs_one_enter():
    """If pre_change.cfg does not exist yet, IOS skips the overwrite prompt."""
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["Destination filename [pre_change.cfg]? ", "2048 bytes copied in 0.3 secs\nSW#"]
    assert vlan_tool.apply(fake, "SW2", ["vlan 10"])["ok"] is True
    assert flash_commands(fake) == ["copy running-config flash:pre_change.cfg", "\n"]


def test_apply_flash_error_sends_nothing():
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["Destination filename [pre_change.cfg]? ",
                          "%Error copying flash:pre_change.cfg (No space left on device)\nSW#"]
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is False
    assert "%Error copying" in result["error_line"]
    assert fake.config_sent == []


def real_flash_lines(copy_number):
    """Lines SW2 printed for copy 1 (no file yet) or copy 2 (file exists), real capture 4 Nov.

    The real silence before "bytes copied" (13.4 s, 6.8 s on overwrite) is set
    with FakeSwitch.silent_seconds in the tests that need it.
    """
    text = read_sample("real/SW2_flash_copy_and_gi01.txt")
    output = text.split("SW2#copy running-config flash:pre_change.cfg\n")[copy_number]
    return output.split("SW2#")[0].splitlines()


def real_sw2_fake(copy_number, silent_seconds=0):
    """A FakeSwitch with prompt SW2# that answers the flash copy with SW2's real text."""
    lines = real_flash_lines(copy_number)
    fake = FakeSwitch("192.168.99.12", prompt="SW2#")
    if copy_number == 1:
        fake.flash_replies = [lines[0], lines[1] + "\nSW2#"]
    else:
        fake.flash_replies = [lines[0], lines[1] + "\n" + lines[2], lines[3] + "\nSW2#"]
    fake.silent_seconds = silent_seconds
    return fake


def test_apply_real_sw2_first_flash_copy():
    fake = real_sw2_fake(1)   # filename prompt, bytes copied
    assert vlan_tool.apply(fake, "SW2", ["vlan 10"])["ok"] is True
    assert flash_commands(fake) == ["copy running-config flash:pre_change.cfg", "\n"]


def test_apply_real_sw2_overwrite_warning_is_not_an_error():
    assert real_flash_lines(2)[1] == "%Warning:There is a file already existing with this name"
    fake = real_sw2_fake(2)   # filename prompt, %Warning, [confirm], bytes copied
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is True and result["error_line"] == ""
    assert flash_commands(fake) == ["copy running-config flash:pre_change.cfg", "\n", "\n"]


# ---------- Step 6: a slow flash copy (real SW2: silent 13.4 s, or 6.8 s on overwrite) ----------
# The first two tests FAIL on the old send_command_timing code, which stopped after 2 s of silence.

def test_apply_waits_for_silent_first_copy():
    fake = real_sw2_fake(1, silent_seconds=13.4)
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is True
    assert fake.config_sent == [["vlan 10"]]


def test_apply_waits_for_silent_overwrite_copy():
    fake = real_sw2_fake(2, silent_seconds=6.8)
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is True
    assert fake.config_sent == [["vlan 10"]]


def test_apply_flash_silent_too_long_sends_nothing(capsys):
    """No reply within 60 s: apply fails cleanly (no crash) and sends no config."""
    fake = real_sw2_fake(1, silent_seconds=90)
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is False and result["sent"] is False
    assert "no 'bytes copied' line" in result["error_line"]
    assert fake.config_sent == []
    assert "[6/9] Apply SW2 ... FAILED" in capsys.readouterr().out


def test_flash_waits_for_this_switch_prompt_only():
    fake = real_sw2_fake(2, silent_seconds=6.8)
    vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert len(fake.flash_sent) == 3
    for command, expect_string, read_timeout in fake.flash_sent:
        assert re.search(expect_string, "SW2#")
        # A bare "#" or another switch's prompt must not end the wait.
        assert not re.search(expect_string, "#")
        assert not re.search(expect_string, "SW3#")
    # The copy runs after each Enter, so those two steps wait up to 60 s.
    assert [read_timeout for command, expect_string, read_timeout in fake.flash_sent[1:]] == [60, 60]


def test_save_waits_for_silent_ok():
    """Guard (passes on old and new code): Netmiko's save_config() waits for the
    prompt for up to 100 s, so a quiet "Building configuration..." still ends in [OK]."""
    fake = FakeSwitch("192.168.99.12")
    fake.silent_seconds = 13.4
    result = vlan_tool.save_or_rollback(fake, "SW2", True, [])
    assert result["ok"] is True


def test_apply_flash_without_bytes_copied_sends_nothing():
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["SW#"]
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is False
    assert "bytes copied" in result["error_line"]
    assert fake.config_sent == []


@pytest.mark.parametrize("error", ["% Invalid input detected at '^' marker.",
                                   "% Incomplete command.",
                                   "% Ambiguous command:  \"sw\""])
def test_find_ios_error_finds_each_error(error):
    output = "SW(config)#something\n" + error + "\nSW(config)#"
    assert vlan_tool.find_ios_error(output, vlan_tool.IOS_ERRORS) == error


def test_main_stops_after_first_failed_switch(switches, monkeypatch, capsys):
    """SW1 rejects a command: SW1 is rolled back, SW2 to SW5 get no config,
    and nothing is saved."""
    original_connect = vlan_tool.ConnectHandler

    def connect_with_bad_sw1(host, **kwargs):
        fake = original_connect(host, **kwargs)
        if host == "192.168.99.11":
            fake.config_output = INVALID_CONFIG_OUTPUT
        return fake

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_with_bad_sw1)
    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(ARGS)

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "[6/9] Apply SW1 ... FAILED" in out
    assert "[8/9] Rollback SW1 ... OK" in out
    assert "Summary: saved none | rolled back SW1 | untouched SW2, SW3, SW4, SW5" in out
    sw1_config = switches["192.168.99.11"].config_sent
    assert len(sw1_config) == 2                        # the commands, then the undo list
    assert sw1_config[1][-1] == "no vlan 10"           # undo runs in reverse order
    for host in ["192.168.99.12", "192.168.99.13", "192.168.99.14", "192.168.99.15"]:
        assert switches[host].config_sent == []
        assert switches[host].flash_sent == []     # not even the flash copy
    for fake in switches.values():
        assert "write memory" not in fake.config_sent
        assert fake.disconnected


# ---------- Step 7: verify ----------

PLAN_VLANS = [{"id": 10, "name": "Sales"}, {"id": 20, "name": "IT"},
              {"id": 30, "name": "Guest"}, {"id": 40, "name": "Finance"}]
SW1_PLAN = {"trunks": {"GigabitEthernet0/1": [10, 20, 30, 40], "GigabitEthernet0/2": [10, 20, 30, 40],
                       "GigabitEthernet0/3": [10, 20, 30, 40], "GigabitEthernet1/1": [10, 20, 30, 40]}}
SW2_PLAN = {"access": {"GigabitEthernet0/1": 10, "GigabitEthernet0/2": 20},
            "trunks": {"GigabitEthernet0/0": [10, 20, 30, 40]}}


VERIFY_COMMANDS = ["show vlan brief", "show interfaces trunk", "show interfaces switchport"]
# Frozen samples of the real lab runs (never regenerated by the tests).
APPLY_LOG = "run_20261105_235328.log"   # real apply run: SW1 to SW5 SAVED
TC09_LOG = "run_20261106_014041.log"    # real TC09 run: SW3 ROLLED_BACK


def fake_from_files(folder, prefix, suffix=""):
    """A FakeSwitch whose verify show commands come from files such as
    tests/samples/<folder>/<prefix>_show_vlan_brief<suffix>.txt."""
    fake = FakeSwitch("unused")
    for command in VERIFY_COMMANDS:
        filename = "%s/%s_%s%s.txt" % (folder, prefix, command.replace(" ", "_"), suffix)
        fake.outputs[command] = read_sample(filename)
    return fake


def fake_from_log(log_file, marker):
    """A FakeSwitch whose verify show commands answer with the REAL SSH output
    that the tool logged after marker (see conftest.log_block)."""
    fake = FakeSwitch("unused")
    for command in VERIFY_COMMANDS:
        fake.outputs[command] = log_block(log_file, marker, command)
    return fake


def real_after(switch):
    """REAL post-apply output: what verify() read over SSH in the apply run (5 Nov).
    The spanning tree section there still shows only VLAN 99 (it lags after
    apply), so verify passing on it proves verify reads the configured allowed list."""
    return fake_from_log(APPLY_LOG, "[6/9] Apply %s ..." % switch)


def test_verify_real_baseline_sw1_fails(capsys):
    """REAL IOSvL2 data: before the tool runs, VLANs 10-40 are missing everywhere."""
    result = vlan_tool.verify(fake_from_files("real", "SW1"), "SW1", SW1_PLAN, PLAN_VLANS)
    assert result["ok"] is False
    for vlan_id in [10, 20, 30, 40]:
        assert "VLAN %d missing" % vlan_id in result["mismatches"]
    assert "GigabitEthernet1/1 allowed list is missing 10, 20, 30, 40" in result["mismatches"]
    # The real Gi1/0 is correct, so the protected port check must stay quiet.
    assert not any("protected" in m for m in result["mismatches"])
    assert "[7/9] Verify SW1 ... FAILED (8 mismatches)" in capsys.readouterr().out


def test_verify_real_baseline_sw2_fails_on_pc_ports():
    """REAL IOSvL2 data: PC ports are still dynamic auto in VLAN 1."""
    result = vlan_tool.verify(fake_from_files("real", "SW2"), "SW2", SW2_PLAN, PLAN_VLANS)
    assert result["ok"] is False
    assert "GigabitEthernet0/1 mode is dynamic auto, planned static access" in result["mismatches"]
    assert "GigabitEthernet0/2 access VLAN is 1, planned 20" in result["mismatches"]
    assert "GigabitEthernet0/0 allowed list is missing 10, 20, 30, 40" in result["mismatches"]


@pytest.mark.parametrize("switch", ["SW1", "SW2", "SW3", "SW4", "SW5"])
def test_verify_real_after_apply_passes(switch, capsys):
    """REAL SSH output after the apply run, checked against the real plan.yaml."""
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
    result = vlan_tool.verify(real_after(switch), switch, plan["switches"][switch] or {}, plan["vlans"])
    assert result == {"ok": True, "mismatches": []}
    assert "[7/9] Verify %s ... OK" % switch in capsys.readouterr().out


@pytest.mark.parametrize("switch", ["SW1", "SW2"])
def test_verify_real_console_after_passes(switch):
    """REAL console captures (CRLF, wrapped vlan brief) taken minutes after the run."""
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
    fake = fake_from_files("real", switch, suffix="_after")
    result = vlan_tool.verify(fake, switch, plan["switches"][switch] or {}, plan["vlans"])
    assert result == {"ok": True, "mismatches": []}


def test_check_vlans_pass():
    vlans = {10: {"name": "Sales"}, 20: {"name": "IT"}, 30: {"name": "Guest"},
             40: {"name": "Finance"}, 99: {"name": "Management"}}
    assert vlan_tool.check_vlans(PLAN_VLANS, vlans) == []


def test_check_vlans_fail():
    vlans = {10: {"name": "Sales"}, 20: {"name": "IT"}, 30: {"name": "VLAN0030"}}
    assert vlan_tool.check_vlans(PLAN_VLANS, vlans) == [
        "VLAN 30 name is VLAN0030, planned Guest",
        "VLAN 40 missing",
        "management VLAN 99 missing",
    ]


def test_check_trunks_pass():
    trunks = {"GigabitEthernet0/0": [10, 20, 30, 40, 99]}
    assert vlan_tool.check_trunks(SW2_PLAN["trunks"], trunks) == []


def test_check_trunks_fail():
    plan = {"GigabitEthernet0/0": [10, 20, 30, 40], "GigabitEthernet0/1": [10]}
    trunks = {"GigabitEthernet0/0": [10, 20, 30]}   # 40 and 99 missing, Gi0/1 not trunking
    assert vlan_tool.check_trunks(plan, trunks) == [
        "GigabitEthernet0/0 allowed list is missing 40, 99",
        "GigabitEthernet0/1 is not trunking",
    ]


def test_check_access_ports_pass():
    switchports = {
        "GigabitEthernet0/1": {"admin_mode": "static access", "oper_mode": "down", "access_vlan": 10},
        "GigabitEthernet0/2": {"admin_mode": "static access", "oper_mode": "static access", "access_vlan": 20},
    }
    assert vlan_tool.check_access_ports(SW2_PLAN["access"], switchports) == []


def test_check_access_ports_fail():
    switchports = {
        "GigabitEthernet0/1": {"admin_mode": "dynamic auto", "oper_mode": "static access", "access_vlan": 10},
    }
    assert vlan_tool.check_access_ports(SW2_PLAN["access"], switchports) == [
        "GigabitEthernet0/1 mode is dynamic auto, planned static access",
        "GigabitEthernet0/2 not found in show interfaces switchport",
    ]


def test_check_protected_ports_pass():
    switchports = {"GigabitEthernet1/0": {"admin_mode": "static access", "oper_mode": "static access",
                                          "access_vlan": 99}}
    assert vlan_tool.check_protected_ports("SW1", switchports) == []
    assert vlan_tool.check_protected_ports("SW2", {}) == []   # SW2 has no protected ports


def test_check_protected_ports_fail():
    switchports = {"GigabitEthernet1/0": {"admin_mode": "static access", "oper_mode": "static access",
                                          "access_vlan": 1}}
    assert vlan_tool.check_protected_ports("SW1", switchports) == [
        "protected port GigabitEthernet1/0 access VLAN is 1, planned 99",
    ]


def test_main_stops_when_verify_fails(switches, capsys):
    """Apply works on SW1, but the switch still shows the baseline, so verify
    fails: SW1 is rolled back, SW2 to SW5 get nothing, nothing is saved."""
    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(ARGS)

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "[6/9] Apply SW1 ... OK" in out
    assert "[7/9] Verify SW1 ... FAILED" in out
    assert "[8/9] Rollback SW1 ... OK" in out
    assert "untouched SW2, SW3, SW4, SW5" in out
    assert len(switches["192.168.99.11"].config_sent) == 2   # commands, then undo
    for host in ["192.168.99.12", "192.168.99.13", "192.168.99.14", "192.168.99.15"]:
        assert switches[host].config_sent == []
        assert switches[host].flash_sent == []
    for fake in switches.values():
        assert "write memory" not in fake.config_sent
        assert fake.disconnected


# ---------- Step 8: save or roll back ----------

def real_undo_list(switch):
    """Build the undo list from the REAL plan.yaml and REAL baseline captures.

    Each PC port's old lines are cut out of the real 'show running-config'
    (no real 'show running-config interface' capture yet).
    """
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
    running_config = read_sample("real/%s_show_running_config.txt" % switch).splitlines()
    port_configs = {}
    for port in (plan["switches"][switch] or {}).get("access") or {}:
        start = running_config.index("interface " + port)
        end = running_config.index("!", start)
        port_configs[port] = parse_interface_config("\n".join(running_config[start:end + 1]))
    vlans = parse_vlan_brief(read_sample("real/%s_show_vlan_brief.txt" % switch))
    trunks = parse_trunk_allowed(read_sample("real/%s_show_interfaces_trunk.txt" % switch))
    return build_changes(switch, plan, vlans, trunks, port_configs)[1]


@pytest.mark.parametrize("switch", ["SW1", "SW2", "SW3", "SW4", "SW5"])
def test_real_undo_list_never_touches_vlan_99_or_protected_ports(switch):
    """Safety rule, proven on the real plan.yaml and real baseline output."""
    undo = real_undo_list(switch)
    assert undo != []
    for line in undo:
        assert line not in ["no vlan %d" % MANAGEMENT_VLAN, "vlan %d" % MANAGEMENT_VLAN]
        if line.startswith("switchport trunk allowed vlan remove "):
            assert MANAGEMENT_VLAN not in vlan_tool.expand_vlan_list(line.split()[-1])
        for port in PROTECTED_PORTS.get(switch, []):
            assert not line.endswith("interface " + port)
    assert vlan_tool.unsafe_undo_lines(switch, undo) == []


def test_unsafe_undo_lines_catches_vlan_99_and_gi1_0():
    undo = ["interface GigabitEthernet1/0", "default interface GigabitEthernet1/0",
            "switchport trunk allowed vlan remove 10,99", "no vlan 99", "no vlan 10"]
    assert vlan_tool.unsafe_undo_lines("SW1", undo) == undo[:4]
    # Gi1/0 is only protected on SW1.
    assert vlan_tool.unsafe_undo_lines("SW2", ["default interface GigabitEthernet1/0"]) == []


def test_undo_targets_reads_back_the_tool_changes():
    vlan_ids, trunk_vlans, ports = vlan_tool.undo_targets(real_undo_list("SW2"))
    assert vlan_ids == [40, 30, 20, 10]
    assert trunk_vlans == {"GigabitEthernet0/0": [10, 20, 30, 40]}
    assert ports == ["GigabitEthernet0/2", "GigabitEthernet0/1"]


def test_undo_targets_port_that_was_already_static_access():
    undo = ["default interface GigabitEthernet0/1", "interface GigabitEthernet0/1",
            "switchport mode access", "switchport access vlan 30"]
    assert vlan_tool.undo_targets(undo)[2] == []


def test_save_sends_write_memory(capsys):
    fake = FakeSwitch("192.168.99.12")
    result = vlan_tool.save_or_rollback(fake, "SW2", True, ["no vlan 10"])
    assert result["ok"] is True
    assert fake.config_sent == ["write memory"]        # no undo sent
    assert "[8/9] Save SW2 ... OK" in capsys.readouterr().out


def test_save_without_ok_line_fails(capsys):
    fake = FakeSwitch("192.168.99.12")
    fake.save_output = "write memory\nBuilding configuration...\n%Error writing nvram\nSW#"
    result = vlan_tool.save_or_rollback(fake, "SW2", True, ["no vlan 10"])
    assert result["ok"] is False
    assert "[8/9] Save SW2 ... FAILED" in capsys.readouterr().out


def test_save_real_write_memory_output_is_ok(capsys):
    """REAL SW1 write memory through Netmiko (5 Nov). '[OK]' is glued to the end
    of the 'Compressed configuration ...' line, not on a line of its own."""
    fake = FakeSwitch("192.168.99.11")
    fake.save_output = log_block(APPLY_LOG, "[7/9] Verify SW1 ... OK", "write memory")
    assert "Compressed configuration from 4091 bytes to 2223 bytes[OK]" in fake.save_output.splitlines()
    result = vlan_tool.save_or_rollback(fake, "SW1", True, ["no vlan 10"])
    assert result["ok"] is True
    assert "[8/9] Save SW1 ... OK" in capsys.readouterr().out


def test_verify_fail_sends_undo_and_no_write_memory(capsys):
    """Fake shows the baseline after the undo, so the rollback check passes."""
    fake = FakeSwitch("192.168.99.12")
    undo = real_undo_list("SW2")
    result = vlan_tool.save_or_rollback(fake, "SW2", False, undo)
    assert result["ok"] is True
    assert fake.config_sent == [undo]
    assert "write memory" not in fake.config_sent
    assert "[8/9] Rollback SW2 ... OK" in capsys.readouterr().out


def test_rollback_runs_all_lines_and_collects_percent_lines(capsys):
    fake = FakeSwitch("192.168.99.12")
    fake.config_output = "SW(config)#no vlan 40\n% Vlan 40 not found\nSW(config)#no vlan 30\nSW(config)#end\nSW#"
    result = vlan_tool.save_or_rollback(fake, "SW2", False, ["no vlan 40", "no vlan 30"])
    assert result["ok"] is True                        # % lines alone are not a failure
    assert result["error_lines"] == ["% Vlan 40 not found"]
    assert fake.config_sent == [["no vlan 40", "no vlan 30"]]
    assert "IOS said: % Vlan 40 not found" in capsys.readouterr().out


def test_rollback_check_failing_prints_manual_message(capsys):
    """REAL post-apply output stays after the undo, as if the undo did nothing."""
    fake = real_after("SW2")
    result = vlan_tool.save_or_rollback(fake, "SW2", False, real_undo_list("SW2"))
    assert result["ok"] is False
    assert "VLAN 40 still exists" in result["mismatches"]
    assert "GigabitEthernet0/0 still allows 10, 20, 30, 40" in result["mismatches"]
    assert "GigabitEthernet0/1 is still static access" in result["mismatches"]
    out = capsys.readouterr().out
    assert "[8/9] Rollback SW2 ... INCOMPLETE" in out
    assert "ROLLBACK INCOMPLETE on SW2: restore flash:pre_change.cfg by hand" in out
    assert "write memory" not in fake.config_sent


def test_unsafe_undo_sends_nothing(capsys):
    fake = FakeSwitch("192.168.99.11")
    result = vlan_tool.save_or_rollback(fake, "SW1", False, ["no vlan 10", "no vlan 99"])
    assert result["ok"] is False
    assert fake.config_sent == []                      # not even "no vlan 10"
    assert "ROLLBACK INCOMPLETE on SW1" in capsys.readouterr().out


def test_flash_copy_failure_skips_rollback(capsys):
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["%Error opening flash:pre_change.cfg (No such device)\nSW#"]
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
    status = vlan_tool.run_switch(fake, "SW2", plan, ["vlan 10"], ["no vlan 10"], vlan_tool.new_record("SW2", "x"))
    assert status == "NOT_CHANGED"
    assert fake.config_sent == []
    assert "Rollback SW2 ... SKIPPED" in capsys.readouterr().out


def test_verified_but_not_saved(capsys):
    fake = FakeSwitch("192.168.99.12")
    fake.after_config = real_after("SW2").outputs
    fake.save_output = "write memory\nSW#"
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan.yaml"))
    status = vlan_tool.run_switch(fake, "SW2", plan, ["vlan 10"], ["no vlan 10"], vlan_tool.new_record("SW2", "x"))
    assert status == "NOT_SAVED"
    assert fake.config_sent == [["vlan 10"], "write memory"]   # no undo after a verified run
    assert "SW2 verified but NOT saved" in capsys.readouterr().out


def test_main_sw1_saved_sw2_rolled_back(switches, monkeypatch, capsys):
    """SW1 shows the REAL post-apply state once config is sent, so it
    passes and is saved. SW2 still shows the baseline, so it is rolled back.
    SW3 to SW5 are never touched."""
    original_connect = vlan_tool.ConnectHandler

    def connect_with_good_sw1(host, **kwargs):
        fake = original_connect(host, **kwargs)
        if host == "192.168.99.11":
            fake.after_config = real_after("SW1").outputs
        return fake

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_with_good_sw1)
    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(ARGS)

    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "[8/9] Save SW1 ... OK" in out
    assert "[8/9] Rollback SW2 ... OK" in out
    assert "Summary: saved SW1 | rolled back SW2 | untouched SW3, SW4, SW5" in out
    assert switches["192.168.99.11"].config_sent[-1] == "write memory"
    assert "write memory" not in switches["192.168.99.12"].config_sent
    for host in ["192.168.99.13", "192.168.99.14", "192.168.99.15"]:
        assert switches[host].config_sent == []
        assert switches[host].flash_sent == []


# ---------- Real TC09 run (6 Nov): bad port Gi0/9 on SW3 ----------

def tc09_sw3_changes():
    """SW3's commands and undo list exactly as the real TC09 run printed them
    (step 5), e.g. '  default interface GigabitEthernet0/9' under 'Undo list'."""
    block = read_sample("real/" + TC09_LOG).split("=== SW3 ===\n", 1)[1].split("\n\n", 1)[0]
    commands_part, undo_part = block.split("Undo list (runs only if verification fails):\n")
    commands = [line.strip() for line in commands_part.splitlines()[1:]]   # [1:] skips "Commands:"
    undo = [line.strip() for line in undo_part.splitlines()]
    return commands, undo


# Real send_config_set output for SW3: the apply (3 rejected lines) and the undo.
TC09_APPLY_ECHO = log_block(TC09_LOG, "[8/9] Save SW2 ... OK", "apply: send_config_set")
TC09_ROLLBACK_ECHO = log_block(TC09_LOG, "[6/9] Apply SW3 ... FAILED", "rollback: send_config_set")
TC09_INVALID = "% Invalid input detected at '^' marker."


def test_tc09_log_sw3_plan_sizes():
    commands, undo = tc09_sw3_changes()
    assert len(commands) == 19 and commands[-3] == "interface GigabitEthernet0/9"
    assert len(undo) == 13 and undo[0] == "default interface GigabitEthernet0/9"


def test_apply_real_tc09_echo_is_an_error(capsys):
    """The caret line + '% Invalid input' after 'interface GigabitEthernet0/9'.
    IOS rejects 3 lines; apply keeps only the first % line (enough to fail)."""
    assert TC09_APPLY_ECHO.count(TC09_INVALID) == 3
    fake = FakeSwitch("192.168.99.13")
    fake.config_output = TC09_APPLY_ECHO
    result = vlan_tool.apply(fake, "SW3", tc09_sw3_changes()[0])
    assert result["ok"] is False and result["sent"] is True
    assert result["error_line"] == TC09_INVALID
    assert "[6/9] Apply SW3 ... FAILED (%s)" % TC09_INVALID in capsys.readouterr().out


def test_tc09_post_rollback_state_equals_baseline():
    """What check_rollback read on SW3 after the undo = the real SW3 baseline."""
    marker = "----- rollback: send_config_set -----"
    parsers = [("show vlan brief", parse_vlan_brief), ("show interfaces trunk", parse_trunk_allowed),
               ("show interfaces switchport", parse_switchport_all)]
    for command, parse in parsers:
        after = parse(log_block(TC09_LOG, marker, command))
        baseline = parse(read_sample("real/SW3_%s.txt" % command.replace(" ", "_")))
        assert after == baseline, command


def test_rollback_real_tc09_echo_is_rolled_back(capsys):
    """One '% Invalid input' (on 'default interface GigabitEthernet0/9') does
    not stop the rollback: the switch is back at baseline, so it is OK."""
    commands, undo = tc09_sw3_changes()
    assert TC09_ROLLBACK_ECHO.count(TC09_INVALID) == 1
    fake = fake_from_log(TC09_LOG, "----- rollback: send_config_set -----")
    fake.config_output = TC09_ROLLBACK_ECHO
    result = vlan_tool.save_or_rollback(fake, "SW3", False, undo)
    assert result["ok"] is True and result["mismatches"] == []
    assert result["error_lines"] == [TC09_INVALID]
    assert fake.config_sent == [undo]                  # undo only, no write memory
    out = capsys.readouterr().out
    assert "[8/9] Rollback SW3 ... OK" in out
    assert "IOS said: " + TC09_INVALID in out


def test_run_switch_real_tc09_sw3_rolled_back(capsys):
    """Steps 6 to 8 on SW3 with the real apply echo, then the real undo echo and
    the real post-rollback show output. Pins today's report fields:
    commands_sent = apply lines then undo lines; error_lines = 1 from apply + 1 from rollback."""
    commands, undo = tc09_sw3_changes()
    fake = FakeSwitch("192.168.99.13")
    fake.config_outputs = [TC09_APPLY_ECHO, TC09_ROLLBACK_ECHO]
    fake.after_config = fake_from_log(TC09_LOG, "----- rollback: send_config_set -----").outputs
    plan = load_yaml(os.path.join(PROJECT_DIR, "plan_tc09_bad_port.yaml"))
    record = vlan_tool.new_record("SW3", "192.168.99.13")
    status = vlan_tool.run_switch(fake, "SW3", plan, commands, undo, record)
    assert status == "ROLLED_BACK"
    assert fake.config_sent == [commands, undo]
    assert "write memory" not in fake.config_sent
    assert record["commands_sent"] == commands + undo
    assert record["error_lines"] == [TC09_INVALID, TC09_INVALID]
    assert record["mismatches"] == []


# ---------- Step 9: report and log ----------

def read_report(tmp_path):
    """Load the one report written by the run."""
    files = os.listdir(tmp_path / "reports")
    assert len(files) == 1
    with open(tmp_path / "reports" / files[0]) as f:
        return json.load(f)


def read_log(tmp_path):
    files = os.listdir(tmp_path / "logs")
    assert len(files) == 1
    with open(tmp_path / "logs" / files[0]) as f:
        return f.read()


def statuses_in(report):
    return [(entry["name"], entry["status"]) for entry in report["switches"]]


def sw1_passes(monkeypatch):
    """SW1 shows the REAL post-apply state once config is sent; the
    other switches keep showing the baseline (so SW2 fails verify)."""
    original_connect = vlan_tool.ConnectHandler

    def connect_with_good_sw1(host, **kwargs):
        fake = original_connect(host, **kwargs)
        if host == "192.168.99.11":
            fake.after_config = real_after("SW1").outputs
        return fake

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_with_good_sw1)


def test_report_precheck_failure(switches, monkeypatch, tmp_path):
    original_connect = vlan_tool.ConnectHandler

    def connect_sw3_down(host, **kwargs):
        if host == "192.168.99.13":
            raise NetmikoTimeoutException("timed out")
        return original_connect(host, **kwargs)

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_sw3_down)
    with pytest.raises(SystemExit):
        vlan_tool.main(ARGS)

    report = read_report(tmp_path)
    assert statuses_in(report) == [("SW1", "NOT_RUN"), ("SW2", "NOT_RUN"), ("SW3", "UNREACHABLE"),
                                   ("SW4", "NOT_RUN"), ("SW5", "NOT_RUN")]
    assert "cannot reach 192.168.99.13" in report["switches"][2]["error_lines"][0]
    assert report["stop_reason"] == "pre-check failed"
    assert report["dry_run"] is False


def test_report_unsafe_switch_state(switches, monkeypatch, tmp_path, capsys):
    """SW2's uplink is not a trunk: step 4 stops in apply mode with exit 1,
    nothing is sent to any switch, and the report says why."""
    monkeypatch.setitem(SAMPLES, "192.168.99.12",
                        ("sw2_show_vlan_brief_baseline.txt", "sw1_show_interfaces_trunk_baseline.txt"))
    with pytest.raises(SystemExit) as exit_info:
        vlan_tool.main(ARGS)

    assert exit_info.value.code == 1
    assert "Stopped, nothing was changed: SW2 GigabitEthernet0/0 is not an active trunk" in capsys.readouterr().out
    for fake in switches.values():
        assert fake.config_sent == [] and fake.flash_sent == []
        assert fake.disconnected
    report = read_report(tmp_path)
    assert report["stop_reason"] == ("unsafe switch state: SW2 GigabitEthernet0/0 is not an active trunk;"
                                     " the tool does not change trunk mode")
    assert statuses_in(report) == [("SW%d" % n, "NOT_RUN") for n in range(1, 6)]
    sw1, sw2, sw3 = report["switches"][0], report["switches"][1], report["switches"][2]
    assert sw1["planned_commands"] != [] and sw1["backup_file"].endswith(".cfg")
    # SW2 was backed up, then planning stopped, so it has no plan; SW3 was never read.
    assert sw2["backup_file"].endswith(".cfg")
    assert sw2["planned_commands"] == [] and sw2["undo_list"] == []
    assert sw3["backup_file"] is None


def test_report_dry_run(switches, tmp_path, capsys):
    vlan_tool.main(ARGS + ["--dry-run"])
    report = read_report(tmp_path)

    assert report["dry_run"] is True
    assert report["stop_reason"] == "dry run"
    assert report["plan_file"] == "plan.yaml"
    assert statuses_in(report) == [("SW%d" % n, "NOT_CHANGED") for n in range(1, 6)]
    sw1 = report["switches"][0]
    assert sw1["ip"] == "192.168.99.11"
    assert sw1["commands_sent"] == []                  # nothing was sent
    assert "switchport trunk allowed vlan add 10,20,30,40" in sw1["planned_commands"]
    assert sw1["undo_list"][-1] == "no vlan 10"
    assert sw1["backup_file"].endswith(".cfg")
    assert "[9/9] Report saved: " in capsys.readouterr().out


def test_report_rollback_stop(switches, monkeypatch, tmp_path):
    sw1_passes(monkeypatch)
    with pytest.raises(SystemExit):
        vlan_tool.main(ARGS)

    report = read_report(tmp_path)
    assert statuses_in(report) == [("SW1", "SAVED"), ("SW2", "ROLLED_BACK"), ("SW3", "NOT_RUN"),
                                   ("SW4", "NOT_RUN"), ("SW5", "NOT_RUN")]
    assert report["stop_reason"] == "stopped at SW2 (ROLLED_BACK)"
    sw1, sw2 = report["switches"][0], report["switches"][1]
    assert sw1["mismatches"] == []
    assert "VLAN 10 missing" in sw2["mismatches"]      # why verify failed
    # Apply commands first, then the undo list that rolled them back.
    assert sw2["commands_sent"] == sw2["planned_commands"] + sw2["undo_list"]
    assert report["switches"][2]["commands_sent"] == []


def test_report_full_pass(switches, monkeypatch, tmp_path):
    """All five switches pass with the real plan.yaml: once config is sent,
    each switch answers with its OWN real post-apply output from the apply run."""
    inventory = load_yaml(ARGS[3])
    switch_of_host = {details["host"]: switch for switch, details in inventory.items()}
    original_connect = vlan_tool.ConnectHandler

    def connect_all_pass(host, **kwargs):
        fake = original_connect(host, **kwargs)
        fake.after_config = real_after(switch_of_host[host]).outputs
        return fake

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_all_pass)
    vlan_tool.main(ARGS)   # no SystemExit

    report = read_report(tmp_path)
    assert statuses_in(report) == [("SW%d" % n, "SAVED") for n in range(1, 6)]
    assert report["stop_reason"] == "completed"
    for entry in report["switches"]:
        assert entry["mismatches"] == [] and entry["error_lines"] == []
        assert entry["commands_sent"] == entry["planned_commands"]


def test_report_unexpected_error(switches, monkeypatch, tmp_path):
    """SSH drops in the middle of apply: the report is still written."""
    original_connect = vlan_tool.ConnectHandler

    def connect_sw1_drops(host, **kwargs):
        fake = original_connect(host, **kwargs)
        if host == "192.168.99.11":
            def drop(commands):
                raise OSError("Socket is closed")
            fake.send_config_set = drop
        return fake

    monkeypatch.setattr(vlan_tool, "ConnectHandler", connect_sw1_drops)
    with pytest.raises(OSError):
        vlan_tool.main(ARGS)
    report = read_report(tmp_path)
    assert report["stop_reason"] == "unexpected error: Socket is closed"
    # SW1 may be half-changed, so the report must not call it NOT_RUN.
    assert statuses_in(report) == [("SW1", "IN_PROGRESS"), ("SW2", "NOT_RUN"), ("SW3", "NOT_RUN"),
                                   ("SW4", "NOT_RUN"), ("SW5", "NOT_RUN")]


def test_password_never_in_report_or_log(switches, monkeypatch, tmp_path):
    sw1_passes(monkeypatch)
    with pytest.raises(SystemExit):
        vlan_tool.main(ARGS)

    with open(os.path.join(tmp_path, "reports", os.listdir(tmp_path / "reports")[0])) as f:
        report_text = f.read()
    log_text = read_log(tmp_path)
    assert PASSWORD not in report_text
    assert PASSWORD not in log_text
    # The running config (password hash) stays in backups/ only, never in the log.
    assert "hostname fake-" not in log_text
    # The log holds the progress lines AND the raw device output (TC08).
    assert "[6/9] Apply SW1 ... OK" in log_text
    assert "----- show interfaces switchport -----" in log_text
    assert "----- rollback: send_config_set -----" in log_text
    assert "[9/9] Report saved: " in log_text


def test_two_runs_never_overwrite(switches, tmp_path):
    vlan_tool.main(ARGS + ["--dry-run"])
    vlan_tool.main(ARGS + ["--dry-run"])     # very likely the same second
    assert len(os.listdir(tmp_path / "reports")) == 2
    assert len(os.listdir(tmp_path / "logs")) == 2


def test_password_is_asked_with_getpass():
    with mock.patch("builtins.input", return_value="admin"), \
         mock.patch("getpass.getpass", return_value="secret") as fake_getpass:
        assert vlan_tool.ask_credentials() == ("admin", "secret")
    fake_getpass.assert_called_once()
