class EstimatorError(Exception):
    """Base Estimator error safe to expose at the API boundary."""


class EstimatorNotFoundError(EstimatorError):
    pass


class EstimatorConflictError(EstimatorError):
    pass


class EstimatorValidationError(EstimatorError):
    pass
