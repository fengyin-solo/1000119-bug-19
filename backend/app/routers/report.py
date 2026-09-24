"""报表导出接口：维护报表任务，覆盖生成报表、重试任务、单份下载与打包下载。

状态流转（生成、重试）走 actions 接口；下载是只读动作，走独立的下载接口，
返回带正确 Content-Disposition 的文件，不会把任务状态改成已完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from app.schemas import ActionResult, EntryPayload, PageResult, ReportPackagePayload
from app.services.report import content_disposition
from app.services.report import ReportService

router = APIRouter(prefix="/api/report", tags=["报表导出"])

service = ReportService()

LIST_FIELDS = ["报表名称", "统计范围", "统计周期", "导出格式", "任务状态", "生成时间"]
STATUSES = ["排队中", "生成中", "已完成", "已失败"]


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


@router.get("/{entry_id}/download")
def download_entry(entry_id: int) -> Response:
    """下载单份报表文件。任务未完成或生成失败时说明原因，且不改动任务状态与记录。"""
    payload, message = service.download_entry(entry_id)
    if payload is None:
        raise HTTPException(status_code=409, detail=message)
    return Response(
        content=payload["data"],
        media_type=payload["media_type"],
        headers={"Content-Disposition": content_disposition(payload["filename"])},
    )


@router.post("/package")
def package_entries(payload: ReportPackagePayload) -> Response:
    """把勾选的多张已完成报表打成一个 zip；任一张不可下载都会逐项说明原因。"""
    result, message = service.package_entries(payload.ids)
    if result is None:
        raise HTTPException(status_code=409, detail=message)
    return Response(
        content=result["data"],
        media_type=result["media_type"],
        headers={"Content-Disposition": content_disposition(result["filename"])},
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条报表任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"报表任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条报表任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="报表任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条报表任务执行生成报表、重试任务；下载请走下载接口，不应改变任务状态。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
