"""Versioned local price catalogs with explicit activation and integrity checks."""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time

from openpyxl import load_workbook


HEADERS = ("类别名称", "材料编号", "材料名称", "挂牌价", "规格")


@contextmanager
def catalog_lock(runtime_root, *, timeout=5.0):
    """Hold the catalog's process-wide publication lock for at most timeout seconds."""
    if timeout < 0:
        raise ValueError("目录锁等待时间不能为负")
    root = Path(runtime_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "catalog.lock"
    with lock_path.open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    if handle.read(1) == b"":
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("目录正在被其他进程更新或发布，请稍后重试") from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _text(value, *, field, row):
    if value is None or not str(value).strip():
        raise ValueError(f"目录第{row}行缺少{field}")
    return str(value).strip()


def _code(value, row):
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"目录第{row}行材料编号不是整数或文本")
        return str(int(value))
    return _text(value, field="材料编号", row=row)


def _parse_catalog(data):
    try:
        book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f"新基准价表无法读取：{exc}") from exc
    records = []
    seen_codes = set()
    try:
        for sheet in book.worksheets:
            if sheet.sheet_state != "visible":
                continue
            header_index = None
            # Worksheet dimensions are optional XLSX metadata. Stream the
            # first twenty rows when an exporter omits them, without changing
            # the uploaded workbook or inferring the size of its data.
            header_limit = min(sheet.max_row, 20) if sheet.max_row is not None else 20
            for index, row in enumerate(
                sheet.iter_rows(min_row=1, max_row=header_limit, max_col=5, values_only=True), start=1,
            ):
                if tuple(str(value).strip() if value is not None else "" for value in row[:5]) == HEADERS:
                    header_index = index
                    break
            if header_index is None:
                continue
            for row_number, row in enumerate(sheet.iter_rows(min_row=header_index + 1, max_col=5, values_only=True), header_index + 1):
                fields = tuple(row[:5])
                if all(value is None or str(value).strip() == "" for value in fields):
                    continue
                category, code, name, price, spec = fields
                code = _code(code, row_number)
                if code in seen_codes:
                    raise ValueError(f"目录材料编号重复：{code}（{sheet.title}第{row_number}行）")
                seen_codes.add(code)
                try:
                    amount = Decimal(str(price).strip())
                except (InvalidOperation, ValueError, AttributeError) as exc:
                    raise ValueError(f"目录第{row_number}行挂牌价无效") from exc
                if not amount.is_finite() or amount <= 0:
                    raise ValueError(f"目录第{row_number}行挂牌价必须是正数")
                records.append({
                    "code": code,
                    "name": _text(name, field="材料名称", row=row_number),
                    "category": _text(category, field="类别名称", row=row_number),
                    "spec": _text(spec, field="规格", row=row_number),
                    "price": format(amount, "f"),
                    "sheet": sheet.title,
                    "row": row_number,
                })
        if not records:
            raise ValueError("新基准价表缺少表头或商品记录")
        return records
    finally:
        book.close()


class CatalogStore:
    """Persist user-supplied catalogs; only a successful update changes the active version."""

    def __init__(self, runtime_root):
        self.root = Path(runtime_root)
        self.registry_path = self.root / "catalog-registry.json"

    def _registry(self):
        if not self.registry_path.exists():
            return {"active": None, "pending": None, "versions": {}, "receipts": []}
        try:
            registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(registry["versions"], dict) or not isinstance(registry["receipts"], list):
                raise ValueError("invalid catalog registry")
            return registry
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("目录注册表损坏，不能选择旧价格") from exc

    def _save(self, registry):
        _atomic_write(self.registry_path, _json_bytes(registry))

    def _snapshot(self, version_id, data, records):
        folder = self.root / "catalogs" / version_id
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "source.xlsx"
        parsed = folder / "records.json"
        encoded = _json_bytes({"records": records})
        for destination, contents in ((source, data), (parsed, encoded)):
            if destination.exists():
                if _sha(destination.read_bytes()) != _sha(contents):
                    raise ValueError(f"目录版本{version_id}的快照已损坏，不能覆盖")
            else:
                _atomic_write(destination, contents)
        return _sha(encoded)

    def register(self, path, *, reference=False):
        """Store a complete catalog and activate it unless explicitly marked reference."""
        path = Path(path)
        with catalog_lock(self.root):
            registry = self._registry()
            try:
                data = path.read_bytes()
                digest = _sha(data)
                records = _parse_catalog(data)
                records_hash = self._snapshot(digest, data, records)
                previous = registry["versions"].get(digest)
                if previous is not None and previous["records_sha256"] != records_hash:
                    raise ValueError("已有目录记录与本次解析结果不一致")
            except (OSError, ValueError) as exc:
                if not reference:
                    registry["pending"] = {
                        "source_name": path.name,
                        "reason": str(exc),
                        "received_at": _timestamp(),
                    }
                    self._save(registry)
                raise ValueError(f"新基准价表更新失败：{exc}") from exc
            received_at = _timestamp()
            if digest not in registry["versions"]:
                registry["versions"][digest] = {
                    "version_id": digest,
                    "sha256": digest,
                    "source_name": path.name,
                    "received_at": received_at,
                    "records_sha256": records_hash,
                }
            registry["receipts"].append({
                "sequence": len(registry["receipts"]) + 1,
                "version_id": digest,
                "source_name": path.name,
                "received_at": received_at,
                "reference": bool(reference),
            })
            if not reference:
                registry["active"] = {
                    "version_id": digest,
                    "source_name": path.name,
                    "received_at": received_at,
                }
                registry["pending"] = None
            self._save(registry)
            return {
                "version_id": digest, "sha256": digest,
                "source_name": path.name, "received_at": received_at,
                "records": records,
            }

    def current(self):
        """Read and verify the active immutable snapshot; pending updates block use."""
        registry = self._registry()
        if registry.get("pending") is not None:
            raise ValueError(f"新基准价表更新待处理：{registry['pending']['reason']}")
        active = registry.get("active")
        if active is None:
            raise ValueError("尚未提供可用的基准价表")
        if not isinstance(active, dict) or not all(
            isinstance(active.get(field), str) and active[field]
            for field in ("version_id", "source_name", "received_at")
        ):
            raise ValueError("当前目录指针损坏")
        version_id = active.get("version_id")
        entry = registry["versions"].get(version_id)
        if entry is None or entry.get("sha256") != version_id:
            raise ValueError("当前目录版本记录损坏")
        folder = self.root / "catalogs" / version_id
        try:
            source = (folder / "source.xlsx").read_bytes()
            parsed = (folder / "records.json").read_bytes()
            if _sha(source) != version_id or _sha(parsed) != entry["records_sha256"]:
                raise ValueError("当前目录快照指纹不一致")
            records = json.loads(parsed)["records"]
            if not isinstance(records, list):
                raise ValueError("当前目录记录格式错误")
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("当前目录快照丢失或损坏") from exc
        return {
            "version_id": version_id,
            "sha256": version_id,
            "source_name": active["source_name"],
            "received_at": active["received_at"],
            "records": records,
        }

    def assert_current(self, version_id, sha256):
        active = self.current()
        if active["version_id"] != version_id or active["sha256"] != sha256:
            raise ValueError("目录版本已变化，请重新匹配、计算并发布")

    def withdraw_pending(self, reason):
        """Explicitly withdraw a bad update, preserving its audit record."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("撤回待处理目录更新必须提供原因")
        with catalog_lock(self.root):
            registry = self._registry()
            pending = registry.get("pending")
            if pending is None:
                raise ValueError("没有待处理的目录更新")
            registry.setdefault("withdrawals", []).append({
                "pending": pending,
                "reason": reason.strip(),
                "withdrawn_at": _timestamp(),
            })
            registry["pending"] = None
            self._save(registry)
            return self.current() if registry.get("active") is not None else {"active": None}
