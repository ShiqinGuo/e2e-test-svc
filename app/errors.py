class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found():
    return APIError(404, "NOT_FOUND", "Resource not found")
