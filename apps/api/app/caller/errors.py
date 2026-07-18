class CallerError(Exception):
    """Base error that is safe to translate at the API boundary."""


class NotFoundError(CallerError):
    pass


class ConflictError(CallerError):
    pass


class ValidationError(CallerError):
    pass


class InvalidTransitionError(ConflictError):
    pass


class ImmutableSpecificationError(ConflictError):
    pass


class ExternalServiceError(CallerError):
    pass
