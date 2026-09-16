import streamlit as st

from ui_common import setup, api, ep
from ui_sessions import sidebar, history_page
from ui_uploads import uploads_page, original_page
from ui_jobs import job_page
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
        st.session_state.setdefault(key, value)

    sessions = api("GET", "/sessions")["sessions"]

    session_ids = [item["id"] for item in sessions]

    if st.session_state.active_session not in session_ids:
        st.session_state.active_session = None

    if (
        not st.session_state.active_session
        and st.session_state.page == "workspace"
    ):
        result = api("POST", "/sessions")

        st.session_state.active_session = result["id"]

        sessions = api("GET", "/sessions")["sessions"]

    sidebar(sessions)

    notice = st.session_state.pop("notice", None)

    if notice:
        st.info(notice)

    if st.session_state.page == "history":
        history_page(sessions)
        return

    sid = st.session_state.active_session

    session = api("GET", ep(sid))

    detail = st.session_state.detail

    if detail:
        if detail[0] == "original":
            original_page(sid, detail[1])

        elif detail[0] == "candidate":
            candidate_detail(sid, detail[1])

        elif detail[0] == "report":
            report_page(
                sid,
                detail[1],
                detail[2],
            )

        return

    st.title("智能简历筛选与招聘助理")

    st.caption("当前会话：" + session["title"])

    tabs = st.tabs(
        [
            "简历入库",
            "招聘要求",
            "候选人信息",
        ]
    )

    with tabs[0]:
        uploads_page(sid)

    with tabs[1]:
        job_page(session)

    with tabs[2]:
        candidate_page(sid)


try:
    main()

except RuntimeError as exc:
    st.error(str(exc))

except Exception as exc:
    st.error(
        f"页面处理失败：{type(exc).__name__}: {exc}"
    )