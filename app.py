import uuid

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st


API_URL = "http://127.0.0.1:8000"


st.set_page_config(
    page_title="智能招聘助理",
    page_icon="📄",
    layout="wide",
)


def new_session():
    session_id = str(
        uuid.uuid4()
    )

    st.session_state.sessions[
        session_id
    ] = {
        "title": "新招聘会话",
        "messages": [],
        "jd": "",
    }

    st.session_state.current_session = (
        session_id
    )


def load_candidates() -> list[dict]:
    try:
        response = requests.get(
            f"{API_URL}/candidates",
            timeout=15,
        )

        response.raise_for_status()

        return response.json().get(
            "candidates",
            [],
        )

    except requests.RequestException:
        return []


def candidate_dataframe(
    candidates: list[dict],
) -> pd.DataFrame:
    rows = []

    for item in candidates:
        rows.append({
            "候选人编号": item[
                "candidate_code"
            ],
            "来源文件": item[
                "source_filename"
            ],
            "最高学历": item.get(
                "highest_education",
                "未知",
            ),
            "工作年限": item.get(
                "work_years",
                0,
            ),
            "技能": "、".join(
                item.get(
                    "skills",
                    [],
                )
            ),
            "匹配度": item.get(
                "match_score"
            ),
        })

    return pd.DataFrame(rows)


def render_radar(
    candidate: dict,
):
    detail = candidate.get(
        "score_detail"
    )

    if not detail:
        st.info(
            "请先让Agent执行匹配度评分。"
        )
        return

    categories = [
        "技能",
        "经验",
        "学历",
        "项目",
    ]

    values = [
        detail["skill_score"],
        detail["experience_score"],
        detail["education_score"],
        detail["project_score"],
    ]

    figure = go.Figure()

    figure.add_trace(
        go.Scatterpolar(
            r=values + [values[0]],
            theta=(
                categories
                + [categories[0]]
            ),
            fill="toself",
            name=candidate[
                "candidate_code"
            ],
        )
    )

    figure.update_layout(
        polar={
            "radialaxis": {
                "visible": True,
                "range": [0, 100],
            }
        },
        showlegend=False,
    )

    st.plotly_chart(
        figure,
        use_container_width=True,
    )


if "sessions" not in st.session_state:
    st.session_state.sessions = {}

if "current_session" not in (
    st.session_state
):
    st.session_state.current_session = (
        None
    )

if "hr_id" not in st.session_state:
    st.session_state.hr_id = (
        "default_hr"
    )

if not st.session_state.sessions:
    new_session()


session_id = (
    st.session_state.current_session
)

session = (
    st.session_state.sessions[
        session_id
    ]
)


with st.sidebar:
    st.title("招聘助理")

    st.session_state.hr_id = (
        st.text_input(
            "HR编号",
            value=(
                st.session_state.hr_id
            ),
        )
    )

    if st.button(
        "新建招聘会话",
        use_container_width=True,
    ):
        new_session()
        st.rerun()

    st.divider()
    st.caption("历史会话")

    for history_id, history in (
        st.session_state.sessions.items()
    ):
        if st.button(
            history["title"],
            key=history_id,
            use_container_width=True,
        ):
            (
                st.session_state
                .current_session
            ) = history_id

            st.rerun()

    st.divider()

    if st.button(
        "清空当前会话",
        use_container_width=True,
    ):
        requests.post(
            f"{API_URL}/sessions/"
            f"{session_id}/clear",
            timeout=10,
        )

        session["messages"] = []
        st.rerun()


st.title("智能简历筛选与招聘助理")

st.warning(
    "系统只提供岗位相关信息整理与评分，"
    "不得代替HR作出录用或淘汰决定。"
)


upload_tab, ranking_tab, chat_tab = (
    st.tabs([
        "简历入库",
        "候选人排名",
        "招聘助理",
    ])
)


with upload_tab:
    st.subheader("批量上传简历")

    uploaded_files = st.file_uploader(
        "支持PDF、DOCX和TXT",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
    )

    if st.button(
        "解析并入库",
        disabled=not uploaded_files,
    ):
        files = [
            (
                "files",
                (
                    file.name,
                    file.getvalue(),
                    file.type,
                ),
            )
            for file in uploaded_files
        ]

        with st.spinner(
            "正在解析和脱敏简历……"
        ):
            response = requests.post(
                f"{API_URL}/upload",
                files=files,
                timeout=300,
            )

        if response.ok:
            result = response.json()

            st.success(
                f"成功入库"
                f"{result['success_count']}"
                f"/{result['total']}份"
            )

            st.json(
                result["results"]
            )

        else:
            st.error(response.text)


with ranking_tab:
    candidates = load_candidates()

    if not candidates:
        st.info(
            "当前没有候选人，请先上传简历。"
        )

    else:
        dataframe = (
            candidate_dataframe(
                candidates
            )
        )

        st.dataframe(
            dataframe,
            use_container_width=True,
            hide_index=True,
        )

        scored = dataframe.dropna(
            subset=["匹配度"]
        )

        if not scored.empty:
            figure = px.bar(
                scored.sort_values(
                    "匹配度"
                ),
                x="匹配度",
                y="候选人编号",
                orientation="h",
                range_x=[0, 100],
            )

            st.plotly_chart(
                figure,
                use_container_width=True,
            )

        selected_code = st.selectbox(
            "查看候选人评分明细",
            [
                item["candidate_code"]
                for item in candidates
            ],
        )

        selected = next(
            item
            for item in candidates
            if (
                    item["candidate_code"]
                    == selected_code
            )
        )

        render_radar(selected)

        with st.expander(
            "查看结构化信息"
        ):
            st.json(selected)

        st.divider()
        st.subheader("删除候选人")

        if st.button(
            "申请删除当前候选人"
        ):
            response = requests.post(
                f"{API_URL}/delete/request",
                json={
                    "candidate_code": (
                        selected_code
                    ),
                },
                timeout=15,
            )

            if response.ok:
                (
                    st.session_state
                    .delete_token
                ) = response.json()[
                    "token"
                ]

                st.warning(
                    "删除需要人工确认。"
                )

        if st.session_state.get(
            "delete_token"
        ):
            confirmed = st.checkbox(
                "我确认永久删除该候选人"
            )

            if st.button(
                "确认删除",
                disabled=not confirmed,
            ):
                response = requests.post(
                    f"{API_URL}/delete/confirm",
                    json={
                        "token": (
                            st.session_state
                            .delete_token
                        ),
                        "confirmed": True,
                    },
                    timeout=15,
                )

                if response.ok:
                    st.success(
                        "候选人已删除"
                    )

                    del (
                        st.session_state[
                            "delete_token"
                        ]
                    )

                    st.rerun()


with chat_tab:
    session["jd"] = st.text_area(
        "岗位要求 JD",
        value=session["jd"],
        height=180,
        placeholder=(
            "例如：招聘Python算法工程师，"
            "要求3年以上Python经验，"
            "熟悉推荐系统……"
        ),
    )

    for message in session[
        "messages"
    ]:
        with st.chat_message(
            message["role"]
        ):
            st.markdown(
                message["content"]
            )

            if message.get("trace"):
                with st.expander(
                    "查看工具执行过程"
                ):
                    st.json(
                        message["trace"]
                    )

    user_input = st.chat_input(
        "输入筛选、比较或追问要求"
    )

    if user_input:
        if not session["messages"]:
            session["title"] = (
                user_input[:18]
            )

        session["messages"].append({
            "role": "user",
            "content": user_input,
        })

        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message(
            "assistant"
        ):
            with st.spinner(
                "Agent正在分析……"
            ):
                response = requests.post(
                    f"{API_URL}/chat",
                    json={
                        "session_id": (
                            session_id
                        ),
                        "hr_id": (
                            st.session_state
                            .hr_id
                        ),
                        "message": user_input,
                        "jd": session["jd"],
                    },
                    timeout=300,
                )

            if response.ok:
                result = response.json()
                reply = result["reply"]

                st.markdown(reply)

                if result.get("trace"):
                    with st.expander(
                        "查看工具执行过程"
                    ):
                        st.json(
                            result["trace"]
                        )

            else:
                reply = response.text
                result = {"trace": []}
                st.error(reply)

        session["messages"].append({
            "role": "assistant",
            "content": reply,
            "trace": result.get(
                "trace",
                [],
            ),
        })