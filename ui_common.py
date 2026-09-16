import base64
import re

from io import BytesIO
from pathlib import Path

import requests
import streamlit as st

from PIL import Image

from config import API_URL


ROOT = Path(__file__).resolve().parent

LOGO = next(
    (
        path
        for path in (
            ROOT / "asserts/logo.svg",
            ROOT / "assets/logo.svg",
        )
        if path.exists()
    ),
    None,
)


def setup():
    icon = str(LOGO) if LOGO else "💼"

    if LOGO:
        svg_text = LOGO.read_text(
            encoding="utf-8"
        )

        match = re.search(
            r'data:image/png;base64,([^\"\x27]+)',
            svg_text,
        )

        if match:
            icon = Image.open(
                BytesIO(
                    base64.b64decode(
                        match.group(1)
                    )
                )
            )

    st.set_page_config(
        page_title="智能简历筛选与招聘助理",
        page_icon=icon,
        layout="wide",
    )

    # 按钮文字允许换行，不使用省略号截断。
    # 同时由各页面给操作按钮预留独立区域。
    st.markdown(
        """
        <style>
        .stButton button,
        .stDownloadButton button,
        .stFormSubmitButton button {
            height: auto !important;
            min-height: 2.6rem;
            padding: 0.55rem 0.8rem !important;
            white-space: normal !important;
            overflow: visible !important;
        }

        .stButton button *,
        .stDownloadButton button *,
        .stFormSubmitButton button * {
            white-space: normal !important;
            text-overflow: clip !important;
            overflow: visible !important;
            overflow-wrap: anywhere !important;
            -webkit-line-clamp: unset !important;
        }

        button[data-baseweb="tab"] {
            white-space: normal !important;
            height: auto;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if LOGO:
        icon_uri = (
            "data:image/svg+xml;base64,"
            + base64.b64encode(
                LOGO.read_bytes()
            ).decode("ascii")
        )

        st.markdown(
            f"""
            <style>
            button[data-baseweb="tab"]::before {{
                content: "";
                display: inline-block;
                flex-shrink: 0;
                width: 18px;
                height: 18px;
                margin-right: 6px;
                background:
                    url("{icon_uri}")
                    center / contain
                    no-repeat;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )


def request(method, path, **kwargs):
    timeout = kwargs.pop("timeout", 30)

    try:
        response = requests.request(
            method,
            API_URL.rstrip("/") + path,
            timeout=timeout,
            **kwargs,
        )

    except requests.Timeout as exc:
        raise RuntimeError(
            "请求超时，请刷新查看已保存状态，"
            "不要重复上传。"
        ) from exc

    except requests.RequestException as exc:
        raise RuntimeError(
            "无法连接后端，请使用 "
            "uvicorn server:app --reload --port 8000 "
            "启动后端。"
        ) from exc

    if not response.ok:
        try:
            detail = response.json().get(
                "detail",
                response.text,
            )

        except ValueError:
            detail = response.text

        if isinstance(detail, list):
            detail = "；".join(
                str(item.get("msg", item))
                if isinstance(item, dict)
                else str(item)
                for item in detail
            )

        raise RuntimeError(str(detail))

    return response


def api(method, path, **kwargs):
    return request(
        method,
        path,
        **kwargs,
    ).json()


def ep(sid, tail=""):
    return f"/sessions/{sid}{tail}"


def open_session(sid):
    st.session_state.update(
        active_session=sid,
        page="workspace",
        detail=None,
        pending_delete=None,
    )

    st.rerun()


def ask_delete(
    sid,
    kind,
    targets=None,
    location="sidebar",
):
    result = api(
        "POST",
        ep(sid, "/delete/request"),
        json={
            "kind": kind,
            "targets": targets or [],
        },
    )

    st.session_state.pending_delete = dict(
        result,
        sid=sid,
        location=location,
    )

    st.rerun()


def delete_confirmation(location):
    pending = st.session_state.get(
        "pending_delete"
    )

    if not pending:
        return

    if pending["location"] != location:
        return

    if location in ("uploads", "candidates"):
        if (
            pending["sid"]
            != st.session_state.active_session
        ):
            return

    if pending["kind"] == "session":
        label = "整个会话及全部关联数据"

    else:
        label = (
            f"所选的{pending['count']}条记录"
            "及原文件、候选人和评分"
        )

    # 删除提示文字、背景色、边框色在这里修改。
    st.markdown(
        f"""
        <div style="
            background: #EEF3FA;
            border: 1px solid #82A5D9;
            border-left: 5px solid #82A5D9;
            border-radius: 8px;
            padding: 14px;
            color: #26364D;
        ">
            删除确认：即将删除{label}。
            此操作不可恢复。
        </div>
        """,
        unsafe_allow_html=True,
    )

    accepted = st.checkbox(
        "我确认删除",
        key="confirm_" + pending["token"],
    )

    if location == "sidebar":
        action_area = st.container()

    else:
        action_area = st.columns([4, 6])[0]

    with action_area:
        yes, no = st.columns(2)

        if yes.button(
            "确认删除",
            disabled=not accepted,
            key="delete_yes",
            use_container_width=True,
        ):
            result = api(
                "POST",
                ep(
                    pending["sid"],
                    "/delete/confirm",
                ),
                json={
                    "token": pending["token"],
                    "confirmed": True,
                },
            )

            if (
                result["kind"] == "session"
                and pending["sid"]
                == st.session_state.active_session
            ):
                st.session_state.active_session = None
                st.session_state.page = "workspace"

            # 避免删除后继续下载浏览器里保留的旧报告。
            st.session_state.pop(
                "pdf_snapshot",
                None,
            )

            st.session_state.pending_delete = None
            st.session_state.detail = None

            st.session_state.notice = (
                "删除完成。"
                + " ".join(
                    result.get("warnings", [])
                )
            )

            st.rerun()

        if no.button(
            "取消",
            key="delete_no",
            use_container_width=True,
        ):
            st.session_state.pending_delete = None

            st.rerun()