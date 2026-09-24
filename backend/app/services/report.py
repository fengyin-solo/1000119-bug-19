"""报表导出业务规则：状态流转、文件生成与筛选口径都收在这里。"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["报表名称", "统计范围", "统计周期"]
STATUS_ORDER = ["排队中", "生成中", "已完成", "已失败"]
QUEUED_STATUS, GENERATING_STATUS, DONE_STATUS, FAILED_STATUS = STATUS_ORDER

# 导出格式（小写）→ (扩展名, MIME)；导出格式字段在这里真正生效
FORMAT_RULES = {
    "csv": ("csv", "text/csv"),
    "excel": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}
DEFAULT_FORMAT = "CSV"
FILE_COLUMNS = ["报表名称", "统计范围", "统计周期", "导出格式", "任务状态", "生成时间"]

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_stem(name: Any, entry_id: int) -> str:
    """净化报表名称作为文件名；名称为空时用任务 id 兜底，保证文件名永远非空。"""
    stem = _INVALID_NAME_CHARS.sub("_", str(name or "").strip()).strip(". ")
    return stem or f"报表-{entry_id}"


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    """用标准库拼一个最小 xlsx（inlineStr 写法），避免引入第三方依赖。"""

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(ord("A") + c)}{r}" t="inlineStr"><is><t>{esc(str(value))}</t></is></c>'
            for c, value in enumerate(row)
        )
        body.append(f'<row r="{r}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="报表" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr("[Content_Types].xml", content_types)
        pack.writestr("_rels/.rels", root_rels)
        pack.writestr("xl/workbook.xml", workbook)
        pack.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        pack.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class ReportService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("报表名称", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["导出格式"] = str(values.get("导出格式") or "").strip() or DEFAULT_FORMAT
        entry["status"] = QUEUED_STATUS
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = ""
        rows.append(entry)
        return self._present(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报表任务 {entry_id} 不存在或已归档", False
        if action == "生成报表":
            return self._generate(entry)
        if action == "重试任务":
            return self._retry(entry)
        if action == "下载报表":
            return None, "下载报表不再改动任务状态，请使用下载接口获取文件", False
        return None, f"动作「{action}」不属于报表导出可执行范围", False

    def build_file(self, entry_id: int) -> tuple[tuple[str, bytes, str] | None, str]:
        """给下载接口用：只有已完成的任务才能取文件，内容从当前记录现场渲染。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报表任务 {entry_id} 不存在或已归档"
        if entry.get("status") != DONE_STATUS:
            return None, f"报表任务尚未生成完成，当前状态：{entry.get('status')}，请先生成报表"
        rendered, reason = self._render(entry)
        if rendered is None:
            return None, f"报表文件生成失败：{reason}"
        return rendered, ""

    def build_bundle(self, entry_ids: list[int]) -> tuple[list[tuple[str, bytes]], list[str]]:
        """打包下载：已完成任务逐个渲染成文件，被跳过的任务逐条说明原因。"""
        files: list[tuple[str, bytes]] = []
        skipped: list[str] = []
        seen_names: set[str] = set()
        for entry_id in entry_ids:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                skipped.append(f"任务 {entry_id}：不存在或已归档")
                continue
            if entry.get("status") != DONE_STATUS:
                skipped.append(f"任务 {entry_id}（{entry.get('报表名称') or '未命名'}）：状态为{entry.get('status')}，未生成完成")
                continue
            rendered, reason = self._render(entry)
            if rendered is None:
                skipped.append(f"任务 {entry_id}（{entry.get('报表名称') or '未命名'}）：{reason}")
                continue
            filename, content, _mime = rendered
            if filename in seen_names:
                stem, dot, ext = filename.rpartition(".")
                filename = f"{stem}-{entry_id}{dot}{ext}" if dot else f"{filename}-{entry_id}"
            seen_names.add(filename)
            files.append((filename, content))
        return files, skipped

    def _generate(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        """生成报表：失败时置为已失败并写明原因，原有记录（含历史文件信息）保留。"""
        reason = self._validate(entry)
        if reason:
            entry["status"] = FAILED_STATUS
            entry["pending"] = False
            entry["abnormal"] = True
            entry["失败原因"] = reason
            return self._present(entry), f"报表生成失败：{reason}", False
        entry["status"] = DONE_STATUS
        entry["pending"] = False
        entry["abnormal"] = False
        entry["失败原因"] = ""
        entry["生成时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rendered, _ = self._render(entry)
        assert rendered is not None  # 校验已通过，渲染必然成功
        entry["文件名称"] = rendered[0]
        return self._present(entry), f"报表已生成：{rendered[0]}", True

    def _retry(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """重试任务：仅已失败的任务可重试，重置回排队中并清掉失败原因。"""
        if entry.get("status") != FAILED_STATUS:
            return None, f"仅已失败的任务支持重试，当前状态：{entry.get('status')}", False
        entry["status"] = QUEUED_STATUS
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = ""
        return self._present(entry), "任务已重新排队，请再次生成报表", True

    def _validate(self, entry: dict[str, Any]) -> str | None:
        missing = [field for field in REQUIRED_FIELDS if not str(entry.get(field) or "").strip()]
        if missing:
            return f"缺少必填字段：{'、'.join(missing)}"
        if self._format_of(entry) is None:
            return f"导出格式「{entry.get('导出格式')}」暂不支持，可选 CSV / Excel"
        return None

    def _format_of(self, entry: dict[str, Any]) -> tuple[str, str] | None:
        raw = str(entry.get("导出格式") or "").strip() or DEFAULT_FORMAT
        return FORMAT_RULES.get(raw.lower())

    def _render(self, entry: dict[str, Any]) -> tuple[tuple[str, bytes, str] | None, str | None]:
        """把任务记录渲染成文件；文件与列表、详情读的是同一份数据，内容自然一致。"""
        reason = self._validate(entry)
        rule = self._format_of(entry)
        if reason or rule is None:
            return None, reason or "导出格式暂不支持，可选 CSV / Excel"
        ext, mime = rule
        row = [str(entry.get(field) or "") for field in FILE_COLUMNS]
        row[FILE_COLUMNS.index("任务状态")] = str(entry.get("status") or "")
        rows = [FILE_COLUMNS, row]
        if ext == "csv":
            buf = io.StringIO()
            csv.writer(buf).writerows(rows)
            content = buf.getvalue().encode("utf-8-sig")
        else:
            content = _xlsx_bytes(rows)
        filename = f"{_safe_stem(entry.get('报表名称'), int(entry.get('id', 0)))}.{ext}"
        return (filename, content, mime), None

    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """对外输出统一注入实时任务状态，列表、详情、下载入口看到同一份结果。"""
        data = dict(row)
        data["任务状态"] = row.get("status")
        return data
