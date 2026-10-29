"""Compatibility imports for the shared payment-text scrubber.

Keep policy in utils.payment_scrub so portal and shared consumers cannot drift.
The private Luhn helper remains available for existing characterization tests.
"""
from utils.payment_scrub import REMOVED, _luhn_ok, scrub, scrubbed

__all__ = ["REMOVED", "scrub", "scrubbed"]
