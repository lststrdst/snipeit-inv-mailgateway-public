from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_no_production_secret_files_are_tracked():
    forbidden = {"config.json", ".env", "id_rsa", "id_ed25519"}
    assert not any(path.name in forbidden for path in ROOT.rglob("*") if ".venv" not in path.parts)


def test_nginx_is_narrow_and_tls_on_2443():
    nginx = (ROOT / "deploy/nginx/snipeit-inventory-gateway.conf").read_text()
    assert "listen 2443 ssl" in nginx
    assert "location = /api/v1/events" in nginx
    assert "location / { return 404; }" in nginx
    assert "client_max_body_size 256k" in nginx


def test_systemd_hardening_and_version():
    for unit in (ROOT / "deploy/systemd").glob("*.service"):
        text = unit.read_text()
        assert "NoNewPrivileges=true" in text
        assert "ProtectSystem=strict" in text
    assert all("v1.0.0" in path.read_text() for path in (ROOT / "deploy/systemd").glob("*.service"))


def test_agent_has_local_retry_queue_and_no_snipe_or_ssh_secret():
    agent = (ROOT / "agent/SnipeIT.Inventory.Agent.ps1").read_text()
    assert "Save-QueuedEnvelope" in agent and "Send-LocalQueue" in agent
    assert "SnipeITToken" not in agent and "IdentityFile" not in agent


def test_agent_log_rotation_is_capped_at_five_archives():
    agent = (ROOT / "agent/SnipeIT.Inventory.Agent.ps1").read_text()
    assert '$oldestArchive = "${path}.5"' in agent
    assert "for ($archiveIndex = 4; $archiveIndex -ge 1; $archiveIndex--)" in agent
    assert 'Move-Item -LiteralPath $path -Destination "${path}.1" -Force' in agent


def test_agent_install_is_system_scheduled_and_uses_machine_dpapi():
    installer = (ROOT / "agent/install-agent.ps1").read_text()
    assert "DataProtectionScope]::LocalMachine" in installer
    assert "New-ScheduledTaskPrincipal -UserId 'SYSTEM'" in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" in installer
    assert "New-ScheduledTaskTrigger -Daily" in installer
    assert "Get-Credential" not in installer
    assert "Password =" not in installer
    assert "[string]$TaskName = 'SnipeIT Inventory Gateway'" in installer
    assert '-Description "SnipeIT Inventory Gateway v${agentVersion}"' in installer
    assert "Inventory " + "Collection" not in installer


def test_agent_uninstall_preserves_queue_by_default_and_guards_purge():
    uninstaller = (ROOT / "agent/uninstall-agent.ps1").read_text()
    assert "[switch]$PurgeData" in uninstaller
    assert "PurgeData is allowed only for the default agent directory" in uninstaller
    assert "Config, keys, queue and logs were preserved" in uninstaller
