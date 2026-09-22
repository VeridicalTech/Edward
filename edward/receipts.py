"""Signed evidence receipts for the audit log.

Pure-stdlib Ed25519 (RFC 8032) — no crypto dependencies, verified against
the RFC test vectors in the test suite. Each audit record is hashed with
SHA-256 over its canonical JSON; receipts form a hash chain (each receipt
covers the previous receipt's hash), so any edit to the audit file or to
past receipts breaks verification.

Receipt file format (JSONL), one receipt per audit record:
    {"seq": N, "ts": ..., "audit_sha256": ..., "prev": ..., "sig": ..., "pub": ...}

Verification (offline, key never needed):
    edward verify [audit.jsonl] [receipts.jsonl]
"""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path


# ---------------------------------------------------------------- Ed25519
# RFC 8032 reference implementation (Ed25519ph not needed; pure Ed25519).

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x):
    return pow(x, _P - 2, _P)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_By = (4 * _inv(5)) % _P
_Bx = _xrecover(_By)
_B = (_Bx % _P, _By % _P, 1, (_Bx * _By) % _P)
_IDENT = (0, 1, 1, 0)


def _edwards_add(P1, P2):
    x1, y1, z1, t1 = P1
    x2, y2, z2, t2 = P2
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _scalarmult(P, e):
    if e == 0:
        return _IDENT
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q


def _point_compress(P):
    x, y, z, _ = P
    zi = _inv(z)
    x = x * zi % _P
    y = y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return None
    x = _xrecover(y)
    if x & 1 != sign:
        x = _P - x
    P = (x, y, 1, x * y % _P)
    if not _oncurve(P):
        return None
    return P


def _oncurve(P):
    x, y, z, t = P
    return (z % _P != 0 and
            (x * y - z * t) % _P == 0 and
            (y * y - x * x - z * z - _D * t * t) % _P == 0)


def _extend_point(P):
    x, y, z = P
    return (x % _P, y % _P, z % _P, x * y % _P)


def ed25519_publickey(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    A = _scalarmult(_B, a)
    return _point_compress(A)


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a_bytes, prefix = h[:32], h[32:]
    a = int.from_bytes(a_bytes, "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    A = _point_compress(_scalarmult(_B, a))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    R = _point_compress(_scalarmult(_B, r))
    k = int.from_bytes(hashlib.sha512(R + A + msg).digest(), "little") % _L
    s = (r + k * a) % _L
    return R + int.to_bytes(s, 32, "little")


def ed25519_verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    if len(sig) != 64 or len(pub) != 32:
        return False
    A = _point_decompress(pub)
    R = _point_decompress(sig[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(sig[:32] + pub + msg).digest(), "little") % _L
    left = _scalarmult(_B, s)
    right = _edwards_add(R, _scalarmult(A, k))
    return _point_equal(left, right)


def _point_equal(P, Q):
    x1, y1, z1 = P[:3]
    x2, y2, z2 = Q[:3]
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


# ---------------------------------------------------------------- receipts

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def canonical(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def record_hash(record: dict) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def ensure_key(key_path) -> tuple:
    """Load or create the signing seed; returns (seed, pubkey_b64)."""
    key_path = Path(key_path)
    if key_path.exists():
        seed = key_path.read_bytes()
        if len(seed) != 32:
            raise ValueError(f"signing key at {key_path} is corrupt (len {len(seed)})")
    else:
        seed = secrets.token_bytes(32)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(seed)
    pub = ed25519_publickey(seed)
    pub_path = Path(str(key_path) + ".pub")
    pub_path.write_text(_b64(pub) + "\n")
    return seed, _b64(pub)


class ReceiptChain:
    """Append receipts for audit records; hash-chained and Ed25519-signed."""

    def __init__(self, receipts_path, seed: bytes, pubkey_b64: str):
        self.path = Path(receipts_path)
        self.seed = seed
        self.pubkey_b64 = pubkey_b64
        self.seq = 0
        self.prev = "GENESIS"
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        self.seq = max(self.seq, int(rec.get("seq", 0)))
                        self.prev = rec.get("audit_sha256", self.prev)
                    except json.JSONDecodeError:
                        continue

    def append(self, record: dict) -> None:
        self.seq += 1
        audit_sha = record_hash(record)
        body = {
            "seq": self.seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "audit_sha256": audit_sha,
            "prev": self.prev,
        }
        sig = ed25519_sign(self.seed, canonical(body))
        body["sig"] = _b64(sig)
        body["pub"] = self.pubkey_b64
        self.prev = audit_sha
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, ensure_ascii=False) + "\n")


def verify_chain(audit_path, receipts_path) -> dict:
    """Offline verification. Matching is by record hash, not position:
    audit rotation yields warnings, while unsigned present records, chain
    breaks, or bad signatures fail verification."""
    audit_path, receipts_path = Path(audit_path), Path(receipts_path)
    errors, warnings = [], []
    records = []
    if audit_path.exists():
        with open(audit_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        errors.append(f"audit: unparseable line: {exc}")
    receipts = []
    if receipts_path.exists():
        with open(receipts_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        receipts.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        errors.append(f"receipts: unparseable line: {exc}")

    audit_hashes = [record_hash(r) for r in records]
    covered = set()

    prev = "GENESIS"
    for i, rec in enumerate(receipts):
        if rec.get("seq") != i + 1:
            errors.append(f"receipt {i}: seq break")
        if rec.get("prev") != prev:
            errors.append(f"receipt {i}: chain break")
        pub = _unb64(rec.get("pub", ""))
        body = {k: v for k, v in rec.items() if k not in ("sig", "pub")}
        msg = canonical(body)
        try:
            sig_ok = ed25519_verify(pub, msg, _unb64(rec.get("sig", "")))
        except Exception as exc:
            sig_ok = False
            errors.append(f"receipt {i}: signature decode failed: {exc}")
        if not sig_ok:
            errors.append(f"receipt {i}: INVALID SIGNATURE")
        sha = rec.get("audit_sha256")
        if sha in audit_hashes:
            covered.add(sha)
        else:
            warnings.append(f"receipt {i}: record absent from audit file (rotated or deleted)")
        prev = sha if sha else prev

    uncovered = [h for h in audit_hashes if h not in covered]
    if uncovered:
        errors.append(f"{len(uncovered)} audit record(s) have no receipt (unsigned records present)")

    return {"ok": not errors, "records": len(records), "receipts": len(receipts),
            "errors": errors, "warnings": warnings}
