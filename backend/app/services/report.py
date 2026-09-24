"""报表导出业务规则：状态流转、字段校验、文件生成与筛选口径都收在这里。

任务状态机：排队中 → 生成中 → 已完成 / 已失败。
- 生成报表：仅「排队中」可触发，同步生成文件产物。
- 重试任务：仅「已失败」可触发，沿用原任务记录重新生成。
- 下载报表：不是状态流转，只读取已生成的文件，任何情况下都不改状态。
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date
from typing import Any
from urllib.parse import quote

from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["报表名称", "统计范围", "统计周期"]
STATUS_QUEUED = "排队中"
STATUS_RUNNING = "生成中"
STATUS_DONE = "已完成"
STATUS_FAILED = "已失败"
STATUS_ORDER = [STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_FAILED]
TERMINAL_STATUSES = {STATUS_DONE, STATUS_FAILED}

# 导出格式 → (扩展名, MIME 类型)，文件格式字段真正在这里生效
SUPPORTED_FORMATS: dict[str, tuple[str, str]] = {
    "CSV": ("csv", "text/csv; charset=utf-8"),
    "JSON": ("json", "application/json; charset=utf-8"),
}
DEFAULT_FORMAT = "CSV"

CONTENT_FIELDS = ["报表名称", "统计范围", "统计周期", "导出格式", "任务状态", "生成时间"]


class ReportGenerationError(Exception):
    """文件生成失败：message 会写进任务的「失败原因」并原样返回给调用方。"""


class ReportService:
    def __init__(self) -> None:
        # entry_id -> (文件名, MIME 类型, 文件字节)；内存仓库重启后可按元数据惰性重建
        self._files: dict[int, tuple[str, str, bytes]] = {}
        for row in store.rows(MODULE):
            self._sync_entry(row)

    # ---------- 列表与明细 ----------

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
        page_rows = rows[start:start + size]
        return [self._sync_entry(row) for row in page_rows], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._sync_entry(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        fmt = str(values.get("导出格式") or "").strip()
        entry["导出格式"] = (fmt or DEFAULT_FORMAT).upper()
        entry["status"] = STATUS_QUEUED
        entry["pending"] = True
        entry["abnormal"] = False
        entry["生成时间"] = None
        entry["文件名"] = None
        entry["文件大小"] = None
        entry["失败原因"] = None
        rows.append(entry)
        return self._sync_entry(entry), []

    # ---------- 状态流转 ----------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报表任务 {entry_id} 不存在或已归档"

        if action == "下载报表":
            # 下载是只读动作，绝不改变任务状态；真正取文件走 download_entry。
            return None, "下载报表不会改变任务状态，请通过下载入口获取已生成的文件"

        if action == "生成报表":
            if entry["status"] == STATUS_DONE:
                return None, "报表已生成完成，请直接下载，无需重复生成"
            if entry["status"] == STATUS_RUNNING:
                return None, "报表正在生成中，请勿重复触发"
            if entry["status"] == STATUS_FAILED:
                return None, "该任务已失败，请使用「重试任务」重新生成"
            message = self._generate(entry)
            if entry["status"] == STATUS_FAILED:
                return None, message
            return self._sync_entry(entry), message

        if action == "重试任务":
            if entry["status"] != STATUS_FAILED:
                return None, f"仅已失败的任务可以重试，当前状态为「{entry['status']}」"
            message = self._generate(entry)
            if entry["status"] == STATUS_FAILED:
                return None, message
            return self._sync_entry(entry), message

        return None, f"动作「{action}」不属于报表导出可执行范围"

    # ---------- 文件下载 ----------

    def download_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """取单份报表文件。成功返回 (文件描述, "")，失败返回 (None, 原因)；失败不改任务记录。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"报表任务 {entry_id} 不存在或已归档"
        self._sync_entry(entry)

        if entry["status"] == STATUS_FAILED:
            reason = entry.get("失败原因") or "文件生成失败"
            return None, f"报表生成失败，暂无文件可下载：{reason}"
        if entry["status"] != STATUS_DONE:
            return None, f"任务当前为「{entry['status']}」，需生成完成后才能下载"

        payload = self._materialize(entry)
        if payload is None:
            return None, entry.get("失败原因") or "文件缺失且无法按原有配置重建"
        return payload, ""

    def package_entries(self, entry_ids: list[int]) -> tuple[dict[str, Any] | None, str]:
        """把多份已完成报表打成 zip；任何一份不可下载都说明原因且不丢原有记录。"""
        if not entry_ids:
            return None, "请先勾选要打包下载的报表"

        members: list[tuple[str, bytes]] = []
        used_names: set[str] = set()
        problems: list[str] = []
        for entry_id in entry_ids:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                problems.append(f"任务 {entry_id} 不存在或已归档")
                continue
            self._sync_entry(entry)
            if entry["status"] != STATUS_DONE:
                state = f"已失败（{entry.get('失败原因')}）" if entry["status"] == STATUS_FAILED else entry["status"]
                problems.append(f"「{entry.get('报表名称', entry_id)}」当前为{state}，不可打包")
                continue
            payload = self._materialize(entry)
            if payload is None:
                problems.append(f"「{entry.get('报表名称', entry_id)}」文件缺失：{entry.get('失败原因') or '无法重建'}")
                continue
            filename = self._unique_name(payload["filename"], entry_id, used_names)
            used_names.add(filename)
            members.append((filename, payload["data"]))

        if problems:
            return None, "以下报表无法打包：" + "；".join(problems)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename, data in members:
                archive.writestr(filename, data)
        data = buffer.getvalue()
        return {
            "filename": "报表任务打包.zip",
            "media_type": "application/zip",
            "data": data,
        }, ""

    # ---------- 内部实现 ----------

    def _generate(self, entry: dict[str, Any]) -> str:
        """排队中/已失败 → 生成中 → 已完成/已失败。失败时保留原有记录，只补失败原因。"""
        entry["status"] = STATUS_RUNNING
        entry["pending"] = True
        entry["abnormal"] = False
        entry["失败原因"] = None
        try:
            # 只做格式校验，文件内容在状态落定为「已完成」后才渲染，保证内容与状态一致
            fmt = self._validate_format(entry)
        except ReportGenerationError as exc:
            entry["status"] = STATUS_FAILED
            entry["pending"] = False
            entry["abnormal"] = True
            entry["失败原因"] = str(exc)
            entry["文件名"] = None
            entry["文件大小"] = None
            self._sync_entry(entry)
            return f"报表生成失败：{exc}"

        entry["status"] = STATUS_DONE
        entry["pending"] = False
        entry["abnormal"] = False
        entry["失败原因"] = None
        entry["生成时间"] = entry.get("生成时间") or date.today().isoformat()
        payload = self._build_payload(entry, fmt)
        entry["文件名"] = payload["filename"]
        entry["文件大小"] = len(payload["data"])
        self._sync_entry(entry)
        self._files[int(entry["id"])] = (payload["filename"], payload["media_type"], payload["data"])
        return "报表已生成完成"

    def _materialize(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """取已缓存文件；缓存不在（如重启后）就按任务原有配置惰性重建，状态保持不动。"""
        entry_id = int(entry["id"])
        cached = self._files.get(entry_id)
        if cached is not None:
            filename, media_type, data = cached
            return {"filename": filename, "media_type": media_type, "data": data}
        try:
            fmt = self._validate_format(entry)
            return self._build_payload(entry, fmt)
        except ReportGenerationError:
            return None

    def _validate_format(self, entry: dict[str, Any]) -> str:
        raw_format = str(entry.get("导出格式") or "").strip().lstrip(".").upper()
        if raw_format not in SUPPORTED_FORMATS:
            supported = "、".join(sorted(SUPPORTED_FORMATS))
            raise ReportGenerationError(
                f"导出格式「{raw_format or '未指定'}」暂不支持，当前支持：{supported}"
            )
        entry["导出格式"] = raw_format
        return raw_format

    def _build_payload(self, entry: dict[str, Any], fmt: str | None = None) -> dict[str, Any]:
        fmt = fmt or self._validate_format(entry)
        ext, media_type = SUPPORTED_FORMATS[fmt]
        base_name = self._safe_base_name(str(entry.get("报表名称") or f"report-{entry['id']}"))
        filename = f"{base_name}.{ext}"
        # 用快照渲染，文件里看到的状态、时间就是落定后的最终结果
        snapshot = dict(entry)
        snapshot["任务状态"] = entry.get("status") if entry.get("status") in TERMINAL_STATUSES else STATUS_DONE
        if ext == "csv":
            data = self._render_csv(snapshot)
        else:
            data = self._render_json(snapshot)
        return {"filename": filename, "media_type": media_type, "data": data}

    def _render_csv(self, snapshot: dict[str, Any]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CONTENT_FIELDS)
        writer.writerow([snapshot.get(field, "") for field in CONTENT_FIELDS])
        # 带 BOM，Excel 打开中文不乱码
        return buffer.getvalue().encode("utf-8-sig")

    def _render_json(self, snapshot: dict[str, Any]) -> bytes:
        payload = {
            "module": MODULE,
            "item": {field: snapshot.get(field) for field in CONTENT_FIELDS},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    def _sync_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表页、详情页、下载入口共用同一份视图：任务状态列始终跟 status 对齐。"""
        entry.setdefault("失败原因", None)
        entry.setdefault("文件名", None)
        entry.setdefault("文件大小", None)
        if entry.get("status") not in STATUS_ORDER:
            entry["status"] = STATUS_QUEUED
        entry["任务状态"] = entry["status"]
        return entry

    @staticmethod
    def _safe_base_name(name: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name).strip(" ._")
        return cleaned or "report"

    @staticmethod
    def _unique_name(filename: str, entry_id: int, used: set[str]) -> str:
        if filename not in used:
            return filename
        stem, dot, ext = filename.rpartition(".")
        candidate = f"{stem or 'report'}-{entry_id}.{ext}" if dot else f"{filename}-{entry_id}"
        return candidate


def content_disposition(filename: str) -> str:
    """统一用 RFC 5987 的 UTF-8 文件名，中文与特殊字符都不会丢，避免拿到空文件名。"""
    return f"attachment; filename*=UTF-8''{quote(filename)}"
