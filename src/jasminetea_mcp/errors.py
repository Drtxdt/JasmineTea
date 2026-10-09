class TeaError(Exception):
    """A public error containing no credential material."""

    def __init__(self, code: str, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.uncertain = uncertain

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "result_uncertain": self.uncertain}
