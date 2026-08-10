"""Orivellum A-01 Mail Steward — governed Outlook.com workspace.

Imports are lazy to prevent circular-import problems at startup.
"""

from __future__ import annotations

from orivellum.capabilities.mail.models import MailStewardError

__all__ = ["MailStewardError"]


def sync_mail(db, cfg):
    from orivellum.capabilities.mail.steward import sync_mail as _f

    return _f(db, cfg)


def assess_message(db, cfg, record_id):
    from orivellum.capabilities.mail.steward import assess_message as _f

    return _f(db, cfg, record_id)
