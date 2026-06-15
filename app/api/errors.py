class ApiError(Exception):
    def __init__(self, status, code, message, job_id=None, retryable=False):
        self.status, self.code, self.message = status, code, message
        self.job_id, self.retryable = job_id, retryable

class InvalidUpload(ApiError):
    def __init__(self, message): 
        super().__init__(422, "INVALID_UPLOAD", message)

class UnsupportedMedia(ApiError):
    def __init__(self, message):
        super().__init__(415, "UNSUPPORTED_MEDIA", message)

class UploadTooLarge(ApiError):
    def __init__(self): 
        super().__init__(413, "UPLOAD_TOO_LARGE", "file exceeds the size limit")