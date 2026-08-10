import uuid


def success_envelope(data: dict) -> dict:
    return {
        "success": True,
        "data": data,
        "error": None,
        "meta": {"request_id": str(uuid.uuid4())},
    }


def error_envelope(code: str, message: str) -> dict:
    return {
        "success": False,
        "data": None,
        "error": {"code": code, "message": message},
        "meta": {"request_id": str(uuid.uuid4())},
    }
