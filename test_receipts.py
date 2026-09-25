"""Receipts tests: RFC 8032 vectors, tamper detection, chain integrity."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from edward.receipts import (ReceiptChain, ensure_key, ed25519_publickey,
                             ed25519_sign, ed25519_verify, record_hash, verify_chain)
from edward.audit import AuditLog


# RFC 8032 §7.1 test vectors (seed, pub, msg, sig)
VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


class TestEd25519RFC8032(unittest.TestCase):
    def test_vectors(self):
        for seed_hex, pub_hex, msg_hex, sig_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            msg = bytes.fromhex(msg_hex)
            self.assertEqual(ed25519_publickey(seed).hex(), pub_hex)
            self.assertEqual(ed25519_sign(seed, msg).hex(), sig_hex)
            self.assertTrue(ed25519_verify(bytes.fromhex(pub_hex), msg,
                                           bytes.fromhex(sig_hex)))

    def test_rejects_tampered(self):
        seed = bytes.fromhex(VECTORS[0][0])
        pub = ed25519_publickey(seed)
        sig = ed25519_sign(seed, b"hello")
        self.assertFalse(ed25519_verify(pub, b"hellO", sig))
        bad = bytearray(sig)
        bad[10] ^= 1
        self.assertFalse(ed25519_verify(pub, b"hello", bytes(bad)))
        self.assertFalse(ed25519_verify(pub, b"hello", sig[:63]))


class TestReceiptChain(unittest.TestCase):
    def test_chain_roundtrip_and_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            seed, pub = ensure_key(os.path.join(td, "signing_key"))
            audit = os.path.join(td, "audit.jsonl")
            receipts = os.path.join(td, "receipts.jsonl")
            chain = ReceiptChain(receipts, seed, pub)
            log = AuditLog(audit)
            for i in range(3):
                rec = {"type": "intervention", "n": i, "action": "PAUSE"}
                log.emit(rec.pop("type"), **rec)
                with open(audit, encoding="utf-8") as fh:
                    lines = [json.loads(l) for l in fh if l.strip()]
                chain.append(lines[-1])

            result = verify_chain(audit, receipts)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["records"], 3)

            # tamper with an audit record -> MODIFIED
            with open(audit, encoding="utf-8") as fh:
                lines = fh.readlines()
            obj = json.loads(lines[1])
            obj["action"] = "CONTINUE"
            lines[1] = json.dumps(obj) + "\n"
            open(audit, "w").writelines(lines)
            result = verify_chain(audit, receipts)
            self.assertFalse(result["ok"])
            self.assertTrue(any("no receipt" in e for e in result["errors"]),
                            result["errors"])

    def test_detects_dropped_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            seed, pub = ensure_key(os.path.join(td, "signing_key"))
            audit, receipts = os.path.join(td, "a.jsonl"), os.path.join(td, "r.jsonl")
            chain = ReceiptChain(receipts, seed, pub)
            log = AuditLog(audit)
            for i in range(2):
                log.emit("x", session="s", n=i)
                with open(audit, encoding="utf-8") as fh:
                    chain.append([json.loads(l) for l in fh if l.strip()][-1])
            self.assertTrue(verify_chain(audit, receipts)["ok"])
            with open(receipts, encoding="utf-8") as fh:
                kept = fh.readlines()
            open(receipts, "w").writelines(kept[:1])  # drop receipt #2
            result = verify_chain(audit, receipts)
            self.assertFalse(result["ok"])
            self.assertTrue(any("no receipt" in e for e in result["errors"]),
                            result["errors"])

    def test_ensure_key_stable_and_private(self):
        with tempfile.TemporaryDirectory() as td:
            kp = os.path.join(td, "signing_key")
            seed1, pub1 = ensure_key(kp)
            seed2, pub2 = ensure_key(kp)
            self.assertEqual(seed1, seed2)
            self.assertEqual(pub1, pub2)
            self.assertEqual(len(pub1), 44)  # base64 of 32 bytes
            mode = os.stat(kp).st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRotationAwareVerify(unittest.TestCase):
    def _emit_and_sign(self, audit, chain, i):
        log = AuditLog(audit)
        log.emit("intervention", n=i, action="PAUSE")
        with open(audit, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        chain.append(lines[-1])

    def test_records_in_rotated_generation_verify(self):
        with tempfile.TemporaryDirectory() as td:
            seed, pub = ensure_key(os.path.join(td, "key"))
            audit = os.path.join(td, "audit.jsonl")
            receipts = os.path.join(td, "receipts.jsonl")
            chain = ReceiptChain(receipts, seed, pub)
            self._emit_and_sign(audit, chain, 1)
            self._emit_and_sign(audit, chain, 2)
            os.replace(audit, audit + ".1")  # rotation
            self._emit_and_sign(audit, chain, 3)
            self._emit_and_sign(audit, chain, 4)
            result = verify_chain(audit, receipts)
            self.assertTrue(result["ok"], result["errors"])
            self.assertEqual(result["records"], 4, "must scan .1 + main")
            self.assertFalse(any("absent" in w for w in result["warnings"]),
                             result["warnings"])

    def test_truly_missing_record_still_warns(self):
        with tempfile.TemporaryDirectory() as td:
            seed, pub = ensure_key(os.path.join(td, "key"))
            audit = os.path.join(td, "audit.jsonl")
            receipts = os.path.join(td, "receipts.jsonl")
            chain = ReceiptChain(receipts, seed, pub)
            self._emit_and_sign(audit, chain, 1)
            # simulate deletion of the rotated generation: receipt exists,
            # but its audit record survives in no generation
            os.replace(audit, audit + ".gone")
            self._emit_and_sign(audit, chain, 2)
            result = verify_chain(audit, receipts)
            self.assertTrue(any("absent" in w for w in result["warnings"]),
                             result["warnings"])
