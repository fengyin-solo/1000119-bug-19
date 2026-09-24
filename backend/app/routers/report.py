"""报表导出接口：维护报表任务，覆盖生成报表、重试任务、下载报表等动作。"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["报表导出"])

service = ReportService()

LIST_FIELDS = ["报表名称", "统计范围", "统计周期", "导出格式", "任务状态", "生成时间", "失败原因"]
STATUSES = ["排队中", "生成中", "已完成", "已失败"]


def _content_disposition(filename: str, fallback: str) -> str:
    """中文文件名走 RFC 5987 编码，避免下载或上传共享目录时文件名丢失。"""
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按报表名称检索"),
    status: str | None = Query(default=None, description="排队中、生成中、已完成、已失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按报表名称与状态过滤报表导出列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出报表导出清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "report", "total": total, "items": items}


@router.get("/bundle")
def download_bundle(ids: str = Query(default="", description="逗号分隔的报表任务 id")) -> StreamingResponse:
    """打包下载选中的报表：已完成任务逐个进 zip，未纳入的任务在包内说明文件里写明原因。"""
    try:
        entry_ids = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids 只能是逗号分隔的数字")
    if not entry_ids:
        raise HTTPException(status_code=400, detail="请先勾选要打包下载的报表任务")
    files, skipped = service.build_bundle(entry_ids)
    if not files:
        raise HTTPException(status_code=400, detail=f"所选任务都无法下载：{'；'.join(skipped)}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as bundle:
        for filename, content in files:
            bundle.writestr(filename, content)
        if skipped:
            bundle.writestr("未纳入报表说明.txt", "\n".join(skipped) + "\n")
    buf.seek(0)
    zip_name = f"报表打包-{datetime.now():%Y%m%d%H%M%S}.zip"
    headers = {"Content-Disposition": _content_disposition(zip_name, "report-bundle.zip")}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条报表任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"报表任务 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/download")
def download_entry(entry_id: int) -> StreamingResponse:
    """下载报表文件：只读不改状态；任务未完成或文件渲染失败时说明原因。"""
    rendered, reason = service.build_file(entry_id)
    if rendered is None:
        status_code = 404 if "不存在" in reason else 409
        raise HTTPException(status_code=status_code, detail=reason)
    filename, content, mime = rendered
    ext = filename.rpartition(".")[2] or "csv"
    headers = {"Content-Disposition": _content_disposition(filename, f"report-{entry_id}.{ext}")}
    return StreamingResponse(io.BytesIO(content), media_type=mime, headers=headers)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条报表任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="报表任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条报表任务执行生成报表、重试任务；失败时保留记录并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message, ok = service.run_action(entry_id, action)
    return ActionResult(ok=ok, message=message, entry=entry)
