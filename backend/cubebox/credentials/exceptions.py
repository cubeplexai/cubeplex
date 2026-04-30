"""Credential vault domain exceptions."""


class CredentialNotFound(Exception):
    """Credential id does not exist or is in a different org."""


class CredentialKindMismatch(Exception):
    """Caller's requested kind does not match the stored credential kind."""


class CredentialInUseError(Exception):
    """Credential cannot be deleted because another row references it."""
