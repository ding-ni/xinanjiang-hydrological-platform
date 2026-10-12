"""Runtime input promotion preserves identity, defaults and dependency hashes."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_products import InputProducts, DEFAULT_BUNDLE_VERSION_ID, file_sha256
from input_transfer import promote_input_bundle
from mdb_import import import_mdb_bundle
from test_input_service import InputFixture
from test_mdb_import import records


class InputTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = InputFixture(self.root / "model")
        self.source_runtime = self.root / "source-runtime"
        self.target_runtime = self.root / "target-runtime"
        self.products = InputProducts(self.model.root, self.source_runtime)
        self.mdb = self.root / "TEST.mdb"
        self.mdb.write_bytes(b"TEST_ONLY_MDB")
        self.imported = import_mdb_bundle(self.mdb, products=self.products, records=records())
        self.identifier = self.imported["bundleVersionId"]

    def tearDown(self):
        self.temp.cleanup()

    def promote(self, **kwargs):
        return promote_input_bundle(self.identifier, self.source_runtime, self.target_runtime,
                                    model_root=self.model.root, **kwargs)

    def test_registered_closure_keeps_identity_default_and_idempotently_reverifies(self):
        source_before = {path.relative_to(self.source_runtime).as_posix(): file_sha256(path)
                         for path in self.source_runtime.rglob("*") if path.is_file()}
        plan = self.promote(dry_run=True)
        self.assertEqual(plan["status"], "VALIDATED_DRY_RUN")
        target = InputProducts(self.model.root, self.target_runtime)
        with self.assertRaises(KeyError):
            target.resolve(self.identifier)
        completed = self.promote()
        self.assertEqual(completed["status"], "PROMOTED_AND_REVERIFIED")
        self.assertEqual(target.resolve(self.identifier)["manifestSHA256"], self.imported["manifestSHA256"])
        self.assertEqual(target.resolve()["bundleVersionId"], DEFAULT_BUNDLE_VERSION_ID)
        self.assertEqual(len(target.list_sources()["sources"]), 1)
        self.assertEqual(self.promote()["newFileCount"], 0)
        source_after = {path.relative_to(self.source_runtime).as_posix(): file_sha256(path)
                        for path in self.source_runtime.rglob("*") if path.is_file()}
        self.assertEqual(source_before, source_after)
        self.assertTrue(all((self.target_runtime / row["path"]).is_file() for row in completed["files"]))

    def test_conflict_rejected_before_copying_other_dependencies(self):
        target_flow = self.target_runtime / "inputs/products" / self.identifier / "observed-flow.csv"
        target_flow.parent.mkdir(parents=True)
        target_flow.write_bytes(b"PREEXISTING_USER_CONTENT")
        with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
            self.promote()
        self.assertEqual(target_flow.read_bytes(), b"PREEXISTING_USER_CONTENT")
        self.assertFalse((self.target_runtime / "inputs/sources").exists())
        self.assertFalse((self.target_runtime / "inputs/registry" / (self.identifier + ".json")).exists())

    def test_invalid_or_same_runtime_and_baseline_copy_are_rejected(self):
        for identifier in (DEFAULT_BUNDLE_VERSION_ID, "../outside"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                promote_input_bundle(identifier, self.source_runtime, self.target_runtime, model_root=self.model.root)
        with self.assertRaises(ValueError):
            promote_input_bundle(self.identifier, self.source_runtime, self.source_runtime, model_root=self.model.root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
