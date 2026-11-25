"""Captured, exact-match allowlists for the existing loopback delivery client."""
import hashlib
import json
import os
import stat


MAX_POLICY_BYTES = 16384


def authorize_destination(path, destination):
    from .delivery import DeliveryError, endpoint

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DeliveryError("Duplicate destination policy field")
            result[key] = value
        return result

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DeliveryError("Destination policy must be a regular file")
            chunks = []
            remaining = MAX_POLICY_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
    except (OSError, ValueError, TypeError) as error:
        raise DeliveryError("Destination policy could not be read") from error
    if len(raw) > MAX_POLICY_BYTES:
        raise DeliveryError("Destination policy exceeds 16384 bytes")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as error:
        raise DeliveryError("Invalid destination policy JSON") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "destinations"} \
            or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise DeliveryError("Use destination policy schema version 1 with only destinations")
    destinations = document["destinations"]
    if not isinstance(destinations, list) or not 1 <= len(destinations) <= 16:
        raise DeliveryError("Destination policy needs 1..16 distinct loopback URLs")
    for value in destinations:
        endpoint(value)
    if len(set(destinations)) != len(destinations):
        raise DeliveryError("Destination policy URLs must be distinct")
    if destination not in destinations:
        raise DeliveryError("Destination is not approved by the policy")
    return hashlib.sha256(raw).hexdigest()
