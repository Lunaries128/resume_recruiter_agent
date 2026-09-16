from datetime import (
    datetime,
    timedelta,
    timezone,
)

import streamlit as st

from ui_common import (
    LOGO,
    api,
    ep,
    open_session,
    ask_delete,
    delete_confirmation,
)


def today(value):
    tz = timezone(timedelta(hours=8))

    created_date = (
        datetime.fromisoformat(value)
        .astimezone(tz)
        .date()
    )

    current_date = datetime.now(tz).date()

    return created_date == current_date


def sidebar(sessions):
    with st.sidebar:
        logo_column, title_column = st.columns(
            [1, 4]
        )

        if LOGO:
            logo_column.image(
                str(LOGO),
                width=40,
            )

        title_column.markdown(
            "### 智能小助理"
        )

        st.text_input(
            "HR编号",
            key="hr_id",
        )

        if st.button(
            "新建招聘会话",
            use_container_width=True,
        ):
            result = api("POST", "/sessions")

            open_session(result["id"])

        if st.button(
            "历史文件夹",
            use_container_width=True,
        ):
            st.session_state.page = "history"
            st.session_state.detail = None

            st.rerun()

        if (
            st.session_state.page == "history"
            and st.session_state.active_session
        ):
            if st.button(
                "返回当前工作页面",
                use_container_width=True,
            ):
                open_session(
                    st.session_state.active_session
                )

        groups = [
            (
                "置顶会话",
                [
                    item
                    for item in sessions
                    if item["pinned"]
                ],
            ),
            (
                "今天的会话",
                [
                    item
                    for item in sessions
                    if not item["pinned"]
                    and today(item["created_at"])
                ],
            ),
        ]

        for label, items in groups:
            st.caption(label)

            for item in items:
                if st.button(
                    item["title"],
                    key="open_" + item["id"],
                    use_container_width=True,
                ):
                    open_session(item["id"])

        sid = st.session_state.active_session

        current = next(
            (
                item
                for item in sessions
                if item["id"] == sid
            ),
            None,
        )

        if current:
            with st.expander("当前会话设置"):
                with st.form("rename_" + sid):
                    title = st.text_input(
                        "会话名称",
                        value=current["title"],
                        max_chars=80,
                    )

                    if st.form_submit_button(
                        "保存名称"
                    ):
                        api(
                            "PATCH",
                            ep(sid),
                            json={"title": title},
                        )

                        st.rerun()

                pin_label = (
                    "取消置顶"
                    if current["pinned"]
                    else "置顶当前会话"
                )

                if st.button(pin_label):
                    api(
                        "PATCH",
                        ep(sid),
                        json={
                            "pinned": not bool(
                                current["pinned"]
                            )
                        },
                    )

                    st.rerun()

                if st.button("删除当前会话"):
                    ask_delete(
                        sid,
                        "session",
                    )

            if st.button(
                "清空当前对话",
                use_container_width=True,
            ):
                api(
                    "POST",
                    ep(sid, "/clear"),
                )

                st.rerun()

        delete_confirmation("sidebar")


def history_page(sessions):
    st.title("历史文件夹")

    query = st.text_input(
        "搜索会话"
    ).strip().lower()

    for item in sessions:
        if query not in item["title"].lower():
            continue

        with st.container(border=True):
            info, actions = st.columns(
                [6, 4]
            )

            prefix = (
                "置顶 · "
                if item["pinned"]
                else ""
            )

            info.write(
                prefix + item["title"]
            )

            info.caption(
                f"{item['created_at'][:10]} · "
                f"{item['upload_count']}份上传 · "
                f"{item['candidate_count']}名候选人"
            )

            with actions:
                opened, pinned, deleted = (
                    st.columns(
                        3,
                        gap="small",
                    )
                )

                if opened.button(
                    "打开",
                    key="history_open_" + item["id"],
                    use_container_width=True,
                ):
                    open_session(item["id"])

                pin_label = (
                    "取消置顶"
                    if item["pinned"]
                    else "置顶"
                )

                if pinned.button(
                    pin_label,
                    key="pin_" + item["id"],
                    use_container_width=True,
                ):
                    api(
                        "PATCH",
                        ep(item["id"]),
                        json={
                            "pinned": not bool(
                                item["pinned"]
                            )
                        },
                    )

                    st.rerun()

                if deleted.button(
                    "删除",
                    key="del_" + item["id"],
                    use_container_width=True,
                ):
                    ask_delete(
                        item["id"],
                        "session",
                        location="history",
                    )

    delete_confirmation("history")