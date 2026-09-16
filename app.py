import inspect

import streamlit as st

from ui_common import (
    setup,
    api,
    ep,
)

from ui_sessions import (
    sidebar,
    history_page,
)

from ui_uploads import (
    uploads_page,
    original_page,
)

from ui_jobs import (
    job_page,
    matching_panel,
)

from ui_candidates import (
    candidate_page,
    candidate_detail,
    report_page,
)


setup()


def main():
    defaults = {
        "active_session": None,
        "page": "workspace",
        "detail": None,
        "hr_id": "default_hr",
    }

    for key, value in defaults.items():
        st.session_state.setdefault(
            key,
            value,
        )

    sessions = api(
        "GET",
        "/sessions",
    )["sessions"]

    session_ids = [
        item["id"]
        for item in sessions
    ]

    if (
        st.session_state.active_session
        not in session_ids
    ):
        st.session_state.active_session = (
            None
        )

    if (
        not st.session_state.active_session
        and st.session_state.page
        == "workspace"
    ):
        result = api(
            "POST",
            "/sessions",
        )

        st.session_state.active_session = (
            result["id"]
        )

        sessions = api(
            "GET",
            "/sessions",
        )["sessions"]

    sidebar(sessions)

    notice = st.session_state.pop(
        "notice",
        None,
    )

    if notice:
        st.info(notice)

    if (
        st.session_state.page
        == "history"
    ):
        history_page(sessions)
        return

    sid = (
        st.session_state.active_session
    )

    session = api(
        "GET",
        ep(sid),
    )

    detail = st.session_state.detail

    if detail:
        if detail[0] == "original":
            original_page(
                sid,
                detail[1],
            )

        elif detail[0] == "candidate":
            candidate_detail(
                sid,
                detail[1],
            )

        elif detail[0] == "report":
            report_page(
                sid,
                detail[1],
                detail[2],
            )

        return

    st.title(
        "智能简历筛选与招聘助理"
    )

    st.caption(
        "当前会话：" + session["title"]
    )

    labels = [
        "简历入库",
        "招聘要求",
        "候选人信息",
    ]

    memory_key = (
        "active_module_" + sid
    )

    widget_key = (
        "module_widget_" + sid
    )

    st.session_state.setdefault(
        memory_key,
        labels[0],
    )

    if widget_key not in st.session_state:
        st.session_state[
            widget_key
        ] = st.session_state[
            memory_key
        ]

    def remember():
        st.session_state[
            memory_key
        ] = st.session_state[
            widget_key
        ]

    def render(label):
        if label == labels[0]:
            uploads_page(sid)

        elif label == labels[1]:
            job_page(session)

        else:
            candidate_page(sid)

            # 保持原候选人列表与详情页面不变。
            # 匹配控制面板放在列表下方。
            matching_panel(
                sid,
                "candidates",
            )

    parameters = inspect.signature(
        st.tabs
    ).parameters

    if (
        "key" in parameters
        and "on_change" in parameters
    ):
        tabs = st.tabs(
            labels,
            key=widget_key,
            on_change=remember,
        )

        for label, tab in zip(
            labels,
            tabs,
        ):
            if tab.open:
                with tab:
                    render(label)

    else:
        # 兼容旧版Streamlit。
        # 横向选择栏可以保存当前模块，
        # 避免rerun后回到第一个页面。
        label = st.radio(
            "功能模块",
            labels,
            key=widget_key,
            horizontal=True,
            label_visibility="collapsed",
            on_change=remember,
        )

        render(label)


try:
    main()

except Exception as exc:
    st.error(
        "页面处理失败："
        f"{type(exc).__name__}: {exc}"
    )