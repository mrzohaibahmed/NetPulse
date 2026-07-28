"""Domain exceptions for the Storm Protection eligibility engine."""


class StormEligibilityError(Exception):
    """Base error for eligibility evaluation failures."""


class MissingInterfaceError(StormEligibilityError):
    """Raised when the interface payload is missing or empty."""


class InvalidInterfaceDataError(StormEligibilityError):
    """Raised when interface metadata cannot be normalised."""


class EligibilityDisabledError(StormEligibilityError):
    """Raised when eligibility evaluation is disabled in configuration."""
