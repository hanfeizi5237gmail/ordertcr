"""Contract checks for the persistent, versioned price catalog."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from openpyxl import Workbook


CORE_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(CORE_DIR))
from order_mapping_core.catalog import CatalogStore, catalog_lock  # noqa: E402


TMP_ROOT = Path(__file__).resolve().parents[3] / "evaluation" / "tmp"


class CatalogStoreTests(unittest.TestCase):
    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.store = CatalogStore(self.runtime)

    def workbook(self, filename="price.xlsx", *, price="9.90", code="800001", name="土豆"):
        path = self.root / filename
        book = Workbook()
        sheet = book.active
        sheet.title = "Sheet0"
        sheet.append(["食堂食材基准价价格表", None, None, None, None])
        sheet.append(["类别名称", "材料编号", "材料名称", "挂牌价", "规格"])
        sheet.append(["蔬菜类", code, name, price, "500g"])
        book.save(path)
        return path

    def test_register_persists_snapshot_and_no_new_table_reuses_active_version(self):
        with self.assertRaises(ValueError):
            self.store.current()
        source = self.workbook()
        registered = self.store.register(source)
        self.assertEqual(registered["source_name"], "price.xlsx")
        self.assertEqual(registered["records"], [{
            "code": "800001", "name": "土豆", "category": "蔬菜类",
            "spec": "500g", "price": "9.90", "sheet": "Sheet0", "row": 3,
        }])
        self.assertEqual(CatalogStore(self.runtime).current()["version_id"], registered["version_id"])
        self.assertEqual(CatalogStore(self.runtime).current()["sha256"], registered["sha256"])
        snapshot = self.runtime / "catalogs" / registered["version_id"] / "source.xlsx"
        self.assertEqual(snapshot.read_bytes(), source.read_bytes())
        self.store.assert_current(registered["version_id"], registered["sha256"])
        with self.assertRaises(ValueError):
            self.store.assert_current("old-version", registered["sha256"])

    def test_valid_catalog_without_optional_worksheet_dimensions(self):
        source = self.workbook()
        dimensionless = self.root / 'dimensionless.xlsx'
        with zipfile.ZipFile(source) as reader, zipfile.ZipFile(dimensionless, 'w') as writer:
            for info in reader.infolist():
                data = reader.read(info.filename)
                if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml'):
                    data = re.sub(rb'<dimension\b[^>]*/>', b'', data)
                writer.writestr(info, data)
        registered = self.store.register(dimensionless)
        self.assertEqual(registered['records'][0]['name'], '土豆')
        self.assertEqual(registered['records'][0]['row'], 3)
        self.assertEqual(registered['records'][0]['spec'], '500g')
        self.assertEqual(registered['records'][0]['price'], '9.90')
        self.assertEqual(CatalogStore(self.runtime).current()['version_id'], registered['version_id'])

    def test_same_filename_changed_contents_replaces_current_without_overwriting_history(self):
        source = self.workbook(price="9.90")
        first = self.store.register(source)
        previous_snapshot = (self.runtime / "catalogs" / first["version_id"] / "source.xlsx").read_bytes()
        self.workbook(price="11.20")
        second = self.store.register(source)
        self.assertNotEqual(second["version_id"], first["version_id"])
        self.assertEqual(second["records"][0]["price"], "11.20")
        self.assertEqual((self.runtime / "catalogs" / first["version_id"] / "source.xlsx").read_bytes(), previous_snapshot)
        with self.assertRaises(ValueError):
            self.store.assert_current(first["version_id"], first["sha256"])

    def test_repeated_identical_content_records_receipt_without_new_version(self):
        source = self.workbook()
        first = self.store.register(source)
        duplicate = self.store.register(source)
        self.assertEqual(duplicate["version_id"], first["version_id"])
        self.assertEqual(len(list((self.runtime / "catalogs").iterdir())), 1)
        registry = json.loads((self.runtime / "catalog-registry.json").read_text(encoding="utf-8"))
        self.assertEqual(len(registry["receipts"]), 2)

    def test_reference_is_saved_but_never_activates(self):
        first = self.store.register(self.workbook(price="9.90"))
        reference = self.store.register(self.workbook("history.xlsx", price="12.00"), reference=True)
        self.assertNotEqual(first["version_id"], reference["version_id"])
        self.assertEqual(self.store.current()["version_id"], first["version_id"])

    def test_bad_update_blocks_old_price_until_explicit_withdrawal(self):
        first = self.store.register(self.workbook())
        invalid = self.workbook("new.xlsx", price="not-a-price")
        with self.assertRaises(ValueError):
            self.store.register(invalid)
        with self.assertRaises(ValueError):
            CatalogStore(self.runtime).current()
        with self.assertRaises(ValueError):
            self.store.assert_current(first["version_id"], first["sha256"])
        with self.assertRaises(ValueError):
            self.store.withdraw_pending(" ")
        result = self.store.withdraw_pending("用户撤回错误更新")
        self.assertEqual(result["version_id"], first["version_id"])
        self.assertEqual(self.store.current()["records"][0]["price"], "9.90")

    def test_valid_update_resolves_pending_and_removed_sku_is_not_inherited(self):
        self.store.register(self.workbook(code="800001"))
        with self.assertRaises(ValueError):
            self.store.register(self.workbook("new.xlsx", price="bad"))
        replacement = self.store.register(self.workbook("new.xlsx", code="800002", name="白菜"))
        self.assertEqual([record["code"] for record in replacement["records"]], ["800002"])
        self.assertEqual(self.store.current()["version_id"], replacement["version_id"])

    def test_withdrawing_first_failed_update_leaves_store_uninitialized(self):
        with self.assertRaises(ValueError):
            self.store.register(self.workbook(price="invalid"))
        withdrawn = self.store.withdraw_pending("取消本次上传")
        self.assertIsNone(withdrawn["active"])
        with self.assertRaises(ValueError):
            self.store.current()

    def test_corrupted_snapshot_blocks_current_instead_of_using_source_file(self):
        registered = self.store.register(self.workbook())
        snapshot = self.runtime / "catalogs" / registered["version_id"] / "source.xlsx"
        snapshot.write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            self.store.current()

    def test_corrupted_active_pointer_is_a_catalog_error(self):
        self.store.register(self.workbook())
        registry_path = self.runtime / "catalog-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        del registry["active"]["source_name"]
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.current()

    def test_cross_process_lock_has_bounded_wait(self):
        script = (
            "import sys,time; "
            "sys.path.insert(0,sys.argv[2]); "
            "from order_mapping_core.catalog import catalog_lock; "
            "lock=catalog_lock(sys.argv[1]); "
            "lock.__enter__(); print('locked',flush=True); time.sleep(1); lock.__exit__(None,None,None)"
        )
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.runtime), str(CORE_DIR)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked", child.stderr.read() if child.poll() is not None else "")
            with self.assertRaises(TimeoutError):
                with catalog_lock(self.runtime, timeout=0.1):
                    self.fail("the competing process still owns the lock")
        finally:
            child.terminate()
            child.communicate(timeout=5)
        with catalog_lock(self.runtime, timeout=0.5):
            pass


if __name__ == "__main__":
    unittest.main()
