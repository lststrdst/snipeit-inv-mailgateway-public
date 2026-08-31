class GatewayError(Exception):
    """Base expected gateway error."""


class ValidationError(GatewayError):
    pass


class AuthenticationError(GatewayError):
    pass


class PermanentProcessingError(GatewayError):
    pass


class TemporaryProcessingError(GatewayError):
    pass
