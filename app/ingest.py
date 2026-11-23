"""Strict, provenance-preserving normalized CSV/XLSX ingestion.

Never guesses marketplace accounting semantics. Raw marketplace export schemas must
first be mapped into the documented normalized contract.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from zipfile import ZipFile, BadZipFile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from openpyxl import load_workbook

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 15000
CATEGORIES = {"sale", "return", "commission", "logistics", "advertising", "storage", "adjustment", "other"}
CHARGE_CATEGORIES = {"commission", "logistics", "advertising", "storage"}
REQUIRED = {"date", "sku", "category", "amount"}
OPTIONAL = {"quantity", "operation_id", "order_id", "description"}
ALL_COLUMNS = REQUIRED | OPTIONAL
HEADERS = {
    "дата": "date", "date": "date", "дата операции": "date",
    "артикул": "sku", "артикул продавца": "sku", "sku": "sku", "товар": "sku",
    "тип операции": "category", "категория": "category", "category": "category",
    "сумма": "amount", "сумма операции": "amount", "amount": "amount", "signed_amount": "amount",
    "количество": "quantity", "qty": "quantity", "quantity": "quantity",
    "id операции": "operation_id", "operation_id": "operation_id", "идентификатор операции": "operation_id",
    "заказ": "order_id", "order_id": "order_id", "номер заказа": "order_id",
    "описание": "description", "description": "description",
}

class IngestError(ValueError):
    pass

@dataclass(frozen=True)
class ParsedFile:
    rows: list[dict[str, Any]]
    sha256: str
    warnings: list[str]
    sum_kopeks: int


def parse_money(v: Any) -> int:
    if v is None or isinstance(v, bool):
        raise IngestError("Пустая или некорректная сумма")
    raw = str(v).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace("₽", "")
    if "," in raw and "." in raw:
        # Thousands separator is the symbol leftmost of last decimal separator.
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    else:
        raw = raw.replace(",", ".")
    try:
        d = Decimal(raw)
        if not d.is_finite():
            raise ValueError()
        # Marketplace exports may contain 3+ decimals: ROUND_HALF_UP to kopeks.
        # A rounding disclosure is attached by the importer when applicable.
        kopeks = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, OverflowError) as ex:
        raise IngestError(f"Некорректная сумма: {v!r}") from ex
    if abs(kopeks) > 10**14:
        raise IngestError("Сумма за пределами допустимого диапазона")
    return kopeks


def parse_date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise IngestError(f"Дата {s!r}: ожидается YYYY-MM-DD или ДД.ММ.ГГГГ")


def parse_quantity(v: Any) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        d = Decimal(str(v).replace(",", "."))
        if d != int(d) or abs(d) > 1_000_000:
            raise ValueError()
        return int(d)
    except (ValueError, InvalidOperation) as ex:
        raise IngestError(f"Количество {v!r} должно быть целым") from ex


def _read_rows(data: bytes, filename: str) -> list[list[Any]]:
    if len(data) > MAX_FILE_BYTES:
        raise IngestError("Файл превышает лимит 5 МБ")
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "csv":
        if b"\x00" in data[:1024]:
            raise IngestError("Файл не похож на текстовый CSV")
        decoded = None
        for codec in ("utf-8-sig", "cp1251"):
            try:
                decoded = data.decode(codec)
                break
            except UnicodeDecodeError:
                pass
        if decoded is None:
            raise IngestError("Неподдерживаемая кодировка CSV")
        probe = decoded[:4096]
        delimiter = ";" if probe.count(";") > probe.count(",") else ","
        rows = list(csv.reader(io.StringIO(decoded), delimiter=delimiter))
    elif ext == "xlsx":
        if not data.startswith(b"PK"):
            raise IngestError("Файл не похож на XLSX")
        try:
            # Reject highly-compressed or oversize workbook payloads before XML parsing.
            with ZipFile(io.BytesIO(data)) as archive:
                infos=archive.infolist()
                if len(infos)>200 or sum(x.file_size for x in infos)>32*1024*1024:
                    raise IngestError('XLSX is too complex or expands beyond 32 MB')
                if any(x.file_size>20*1024*1024 for x in infos):
                    raise IngestError('XLSX has an oversized internal part')
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = book.active
            rows = []
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i > MAX_ROWS:
                    raise IngestError(f"В одном импорте допускается не более {MAX_ROWS} строк")
                rows.append(list(row))
            book.close()
        except IngestError:
            raise
        except Exception as ex:
            raise IngestError("Не удалось прочитать XLSX") from ex
    else:
        raise IngestError("Поддерживаются CSV и XLSX. Форматы маркетплейсов нужно предварительно сопоставить с шаблоном.")
    if len(rows) < 2:
        raise IngestError("Файл пуст или не содержит операций")
    if len(rows)-1 > MAX_ROWS:
        raise IngestError(f"В одном импорте допускается не более {MAX_ROWS} строк")
    return rows


def parse_ledger(data: bytes, filename: str) -> ParsedFile:
    rows = _read_rows(data, filename)
    keys = [HEADERS.get(str(v or "").strip().lower(), "") for v in rows[0]]
    missing = REQUIRED - set(keys)
    if missing:
        raise IngestError("Не найдены обязательные колонки: " + ", ".join(sorted(missing)))
    if len([k for k in keys if k]) != len(set(k for k in keys if k)):
        raise IngestError("Одинаковые колонки указаны несколько раз")
    output: list[dict[str,Any]] = []
    warnings: list[str] = []
    for index, row in enumerate(rows[1:], start=2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        item = {key: row[i] if i < len(row) else None for i, key in enumerate(keys) if key}
        try:
            category = str(item["category"] or "").strip().lower()
            if category not in CATEGORIES:
                raise IngestError("category: допустимы " + ", ".join(sorted(CATEGORIES)))
            amount = parse_money(item["amount"])
            try:
                raw_amount = str(item["amount"]).replace(' ', '').replace('\u00a0', '').replace('\u202f', '').replace(',', '.')
                precise = Decimal(raw_amount)
                if precise.is_finite() and precise != precise.quantize(Decimal('0.01')):
                    if len(warnings)<20:
                        warnings.append(f"Строка {index}: исходная сумма округлена до копейки")
            except (InvalidOperation, ValueError):
                pass
            quantity = parse_quantity(item.get("quantity"))
            sku = str(item["sku"] or "").strip()
            if len(sku) > 120:
                raise IngestError("Слишком длинный SKU")
            if category == "sale" and amount <= 0:
                raise IngestError("sale должна быть положительной")
            if category == "return" and amount >= 0:
                raise IngestError("return должна быть отрицательной")
            if category in CHARGE_CATEGORIES and amount > 0:
                raise IngestError(f"{category} должна быть отрицательной (расход)")
            if category == "sale" and quantity is not None and quantity <= 0:
                raise IngestError("sale quantity должна быть положительной")
            if category == "return" and quantity is not None and quantity >= 0:
                raise IngestError("return quantity должна быть отрицательной")
            if category not in ("sale", "return") and quantity not in (None, 0):
                raise IngestError("Для комиссий/расходов поле quantity оставьте пустым")
            if category in ("sale", "return") and not sku:
                raise IngestError("Для продаж/возвратов необходим SKU")
            out = {
                "source_row": index,
                "date": parse_date(item["date"]),
                "sku": sku,
                "category": category,
                "amount_kopeks": amount,
                "quantity": quantity,
                "operation_id": str(item.get("operation_id") or "").strip()[:150],
                "order_id": str(item.get("order_id") or "").strip()[:150],
                "description": str(item.get("description") or "").strip()[:500],
            }
            if category == "sale" and quantity is None:
                warnings.append(f"Строка {index}: отсутствует количество проданных единиц")
            output.append(out)
        except IngestError as ex:
            raise IngestError(f"Строка {index}: {ex}") from ex
    if not output:
        raise IngestError("Нет операций для загрузки")
    return ParsedFile(rows=output, sha256=hashlib.sha256(data).hexdigest(),
                      warnings=warnings[:20], sum_kopeks=sum(r["amount_kopeks"] for r in output))
