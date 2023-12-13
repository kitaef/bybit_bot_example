import hashlib
import hmac
from config import RECV_WINDOW


def gen_signature(api_key, secret_key, payload, time_stamp):
    """The function generates the sha256 HMAC"""
    param_str = str(time_stamp) + api_key + RECV_WINDOW + payload
    hash_ = hmac.new(bytes(secret_key, "utf-8"), param_str.encode("utf-8"), hashlib.sha256)
    signature = hash_.hexdigest()
    return signature
