from langchain_core.tools import tool
# ══════════════════════════════════════════════════════════════════
#  TOOLS
# ══════════════════════════════════════════════════════════════════

@tool
def make_call(caller_number: str, callee_number: str, duration_seconds: int) -> dict:
    """Simulate a GSM CAMEL call toward SCP."""
    # return telco_engine.simulate_call(caller_number, callee_number, duration_seconds)
    return {"call_id": "c_mock_001", "status": "completed",
            "actual_duration": duration_seconds, "charged": True}

@tool
def get_balance(msisdn: str) -> dict:
    """Query subscriber balance from billing system."""
    # return telco_engine.check_balance(msisdn)
    return {"balance": 67.50, "currency": "AZN", "package_active": False}

@tool
def topup_balance(msisdn: str, amount_azn: float) -> dict:
    """Top up subscriber balance."""
    # return telco_engine.topup(msisdn, amount_azn)
    return {"success": True, "new_balance": 7.50 + amount_azn}

@tool
def send_sms(sender_number: str, recipient_number: str, message_body: str) -> dict:
    """Send SMS via SMSGW."""
    # return telco_engine.send_sms(sender_number, recipient_number, message_body)
    return {"message_id": "sms_mock_001", "status": "delivered"}

@tool
def activate_package(msisdn: str, package_code: str) -> dict:
    """Activate a tariff package for a subscriber."""
    # return telco_engine.activate_package(msisdn, package_code)
    return {"success": True, "package_id": "pkg_mock_001",
            "valid_until": "2026-12-31T23:59:59"}

TOOL_MAP = {
    "make_call":        make_call,
    "get_balance":      get_balance,
    "topup_balance":    topup_balance,
    "send_sms":         send_sms,
    "activate_package": activate_package,
}