def public_run(body):
    return {k: v for k, v in body.items() if k != "encryptedEnvironment"}


def public_recording(body):
    return {k: v for k, v in body.items() if k not in {"encryptedEnvironment", "target", "website", "role"}}


def envelope(items, total, limit, offset):
    return {"items": items, "total": total, "limit": limit, "offset": offset}
