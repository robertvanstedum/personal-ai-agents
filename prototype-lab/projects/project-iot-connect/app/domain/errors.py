class DomainError(Exception):
    """Base error for a known business outcome."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class AuthorizationError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class IntegrationError(DomainError):
    """A required external system could not be reached or returned bad data."""

