"""Tests for vlan_tool.py steps 1 to 5 (pre-check, backup, plan, dry-run).

No real switches: Netmiko's ConnectHandler is replaced with FakeSwitch,
which answers show commands with the hand-written sample files.
NOT YET VERIFIED ON IOSvL2: still needs a real run in GNS3 (TC01, TC02, TC10).
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

# Which sample files each fake switch answers with (SW3 and SW4 look like SW2).
ACCESS = ("sw2_show_vlan_brief_baseline.txt", "sw2_show_interfaces_trunk_baseline.txt")
SAMPLES = {
    "192.168.99.11": ("sw1_show_vlan_brief_baseline.txt", "sw1_show_interfaces_trunk_baseline.txt"),
    "192.168.99.12": ACCESS,
    "192.168.99.13": ACCESS,
    "192.168.99.14": ACCESS,
}


class FakeSwitch:
    """Stands in for a Netmiko connection to a switch in its baseline state."""

    def __init__(self, host, prompt="SW#"):
        self.host = host
        self.prompt = prompt
        self.sent = []          # every show command we received
        self.config_sent = []   # anything sent as config (must stay empty)
        self.disconnected = False

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

    def send_config_set(self, commands):
        self.config_sent.append(commands)

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

    for switch in ["SW1", "SW2", "SW3", "SW4"]:
        assert f"=== {switch} ===" in out
    assert "SW5" not in out  # commented out in inventory.yaml and plan.yaml
    assert "switchport trunk allowed vlan add 10,20,30,40" in out
    assert "switchport access vlan 40" in out
    assert "default interface GigabitEthernet0/2" in out
    assert "Dry run: nothing was sent" in out
    # One backup file per switch
    assert sorted(f.split("_")[0] for f in os.listdir(tmp_path / "backups")) == ["SW1", "SW2", "SW3", "SW4"]
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


def test_password_is_asked_with_getpass():
    with mock.patch("builtins.input", return_value="admin"), \
         mock.patch("getpass.getpass", return_value="secret") as fake_getpass:
        assert vlan_tool.ask_credentials() == ("admin", "secret")
    fake_getpass.assert_called_once()
