"""Tests for vlan_tool.py steps 1 to 6 (pre-check, backup, plan, dry-run, apply).

No real switches: Netmiko's ConnectHandler is replaced with FakeSwitch,
which answers show commands with the hand-written sample files.
NOT YET VERIFIED ON IOSvL2: still needs a real run in GNS3 (TC01, TC02, TC10).
The flash copy prompts and the canned config output below are guesses from
normal IOS behaviour, not captured from our switches.
"""

import os
from unittest import mock

import pytest
from netmiko import NetmikoAuthenticationException, NetmikoTimeoutException

import vlan_tool
from conftest import read_sample

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
# already exists: one reply per send_command_timing call.
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
        self.sent = []          # every show command we received
        self.config_sent = []   # anything sent as config (must stay empty)
        self.disconnected = False
        self.timing_sent = []   # flash copy command and the Enter answers
        self.flash_replies = list(FLASH_PROMPTS)
        self.config_output = CLEAN_CONFIG_OUTPUT

    def find_prompt(self):
        return self.prompt

    def send_command(self, command):
        self.sent.append(command)
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
        self.timing_sent.append(command)
        return self.flash_replies.pop(0)

    def send_config_set(self, commands):
        self.config_sent.append(commands)
        return self.config_output

    def save_config(self):
        self.config_sent.append("write memory")

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def switches(tmp_path, monkeypatch):
    """Patch Netmiko and the login prompt. Returns {host: FakeSwitch}."""
    monkeypatch.setattr(vlan_tool, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(vlan_tool, "ask_credentials", lambda: ("admin", "secret"))
    fakes = {}

    def fake_connect(host, **kwargs):
        fakes[host] = FakeSwitch(host)
        return fakes[host]

    monkeypatch.setattr(vlan_tool, "ConnectHandler", fake_connect)
    return fakes


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
    monkeypatch.setattr(vlan_tool, "ask_credentials", lambda: ("admin", "secret"))
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


def test_apply_answers_flash_prompts():
    fake = FakeSwitch("192.168.99.12")
    vlan_tool.apply(fake, "SW2", ["vlan 10"])
    # Copy command, then Enter for the filename, then Enter for overwrite.
    assert fake.timing_sent == ["copy running-config flash:pre_change.cfg", "\n", "\n"]


def test_apply_flash_copy_new_file_needs_one_enter():
    """If pre_change.cfg does not exist yet, IOS skips the overwrite prompt."""
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["Destination filename [pre_change.cfg]? ", "2048 bytes copied in 0.3 secs\nSW#"]
    assert vlan_tool.apply(fake, "SW2", ["vlan 10"])["ok"] is True
    assert fake.timing_sent == ["copy running-config flash:pre_change.cfg", "\n"]


def test_apply_flash_error_sends_nothing():
    fake = FakeSwitch("192.168.99.12")
    fake.flash_replies = ["Destination filename [pre_change.cfg]? ",
                          "%Error copying flash:pre_change.cfg (No space left on device)\nSW#"]
    result = vlan_tool.apply(fake, "SW2", ["vlan 10"])
    assert result["ok"] is False
    assert "%Error copying" in result["error_line"]
    assert fake.config_sent == []


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
    """SW1 rejects a command: SW2 to SW5 get no config and nothing is saved."""
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
    assert "Stopped at SW1" in capsys.readouterr().out
    assert len(switches["192.168.99.11"].config_sent) == 1
    for host in ["192.168.99.12", "192.168.99.13", "192.168.99.14", "192.168.99.15"]:
        assert switches[host].config_sent == []
        assert switches[host].timing_sent == []     # not even the flash copy
    for fake in switches.values():
        assert "write memory" not in fake.config_sent
        assert fake.disconnected


def test_password_is_asked_with_getpass():
    with mock.patch("builtins.input", return_value="admin"), \
         mock.patch("getpass.getpass", return_value="secret") as fake_getpass:
        assert vlan_tool.ask_credentials() == ("admin", "secret")
    fake_getpass.assert_called_once()
