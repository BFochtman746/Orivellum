"""Email validator — has deliberate double-dot defect for T03 eval."""
import re

# Deliberately permissive pattern — does not reject consecutive dots
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def is_valid_email(address: str) -> bool:
    """Return True if address looks like a valid email address.
    
    Known defect: accepts consecutive dots in the local part.
    """
    if not address or "@" not in address:
        return False
    return bool(_EMAIL_RE.match(address))
