import base64
import hashlib
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui_common import (
    api,
    ep,
    ask_delete,
    delete_confirmation,
)

from report_service import (
    facts,
    safe_name,
)


def education(candidate):
    levels = {
        "博士": 5,
        "硕士": 4,
        "研究生": 4,
        "本科": 3,
        "学士": 3,
        "大专": 2,
        "专科": 2,
        "高中": 1,
    }

    def level(item):
        degree = item.get("degree", "")

        matched = [
            value
            for key, value in levels.items()
            if key in degree
        ]

        return max(matched or [0])

    return max(
        candidate.get("education", []),
        key=level,
        default={},
    )


def experience(candidate):
    items = (
        candidate.get("experiences", [])
        + candidate.get("projects", [])
    )

    values = []

    if candidate.get("work_years"):
        values.append(
            f"{candidate['work_years']}年工作经验"
        )

    if items:
        first = items[0]

        values.append(
            first.get("role")
            or first.get("name")
            or first.get("organization")
            or ""
        )

        values += (
            first.get("actions", [])
            + first.get("results", [])
        )[:2]

    return (
        "；".join(
            value
            for value in values
            if value
        )
        or "未提供"
    )


def candidate_page(sid):
    data = api(
        "GET",
        ep(sid, "/jobs"),
    )

    mapping = {
        item["id"]: item
        for item in data["templates"]
    }

    if mapping:
        key = "rank_job_" + sid

        if st.session_state.get(key) not in mapping:
            active_id = data["active_id"]

            st.session_state[key] = (
                active_id
                if active_id in mapping
                else next(iter(mapping))
            )

        jid = st.selectbox(
            "匹配岗位",
            list(mapping),
            format_func=lambda value: (
                mapping[value]["name"]
            ),
            key=key,
        )

        if jid != data["active_id"]:
            api(
                "POST",
                ep(
                    sid,
                    "/jobs/" + jid + "/activate",
                ),
            )

            st.session_state.pop(
                "pdf_snapshot",
                None,
            )

    else:
        jid = None

        st.info(
            "请先在招聘要求页面保存岗位模板。"
        )

    candidates = api(
        "GET",
        ep(sid, "/candidates"),
    )["candidates"]

    if not candidates:
        st.info(
            "尚无成功入库的候选人，请先上传简历。"
        )
        return

    selected = []

    for index, candidate in enumerate(
        candidates,
        1,
    ):
        code = candidate["candidate_code"]

        edu = education(candidate)

        with st.container(border=True):
            name = safe_name(
                candidate.get("masked_name")
            )

            if st.checkbox(
                "选择 " + name,
                key="candidate_" + code,
            ):
                selected.append(
                    candidate["upload_id"]
                )

            rank = (
                index
                if candidate.get("match_score") is not None
                else "未评分"
            )

            row = {
                "排名": rank,
                "姓名": name,
                "匹配分": candidate.get(
                    "match_score"
                ),
                "硬性条件": candidate.get(
                    "hard_status"
                ),
                "学校专业": (
                    edu.get("school", "")
                    + " / "
                    + edu.get("major", "")
                ),
                "最高学历": edu.get(
                    "degree",
                    "未提供",
                ),
                "经历": experience(candidate),
            }

            st.dataframe(
                pd.DataFrame([row]),
                hide_index=True,
                use_container_width=True,
            )

            detail_column, report_column, _ = (
                st.columns([2, 2, 6])
            )

            if detail_column.button(
                "查看详情",
                key="detail_" + code,
                use_container_width=True,
            ):
                st.session_state.detail = (
                    "candidate",
                    code,
                )

                st.rerun()

            if report_column.button(
                "预览并下载报告",
                disabled=not jid,
                key="report_" + code,
                use_container_width=True,
            ):
                st.session_state.detail = (
                    "report",
                    code,
                    jid,
                )

                st.rerun()

    if st.button(
        "删除所选候选人及原文件",
        disabled=not selected,
    ):
        ask_delete(
            sid,
            "uploads",
            selected,
            "candidates",
        )

    delete_confirmation("candidates")


def candidate_detail(sid, code):
    if st.button("返回候选人信息"):
        st.session_state.detail = None

        st.rerun()

    candidates = api(
        "GET",
        ep(sid, "/candidates"),
    )["candidates"]

    candidate = next(
        (
            item
            for item in candidates
            if item["candidate_code"] == code
        ),
        None,
    )

    if not candidate:
        st.info("候选人已删除。")
        return

    left, right = st.columns([6, 4])

    with left:
        st.subheader(
            safe_name(
                candidate.get("masked_name")
            )
        )

        st.write(
            "电话："
            + (
                candidate.get("masked_phone")
                or "未提供"
            )
        )

        st.write(
            "邮箱："
            + (
                candidate.get("email")
                or "未提供"
            )
        )

        if st.button("查看原文件"):
            st.session_state.detail = (
                "original",
                candidate["upload_id"],
            )

            st.rerun()

        for section in facts(candidate):
            st.subheader(section["title"])

            for line in section["lines"]:
                st.write(line)

    with right:
        detail = (
            candidate.get("score_detail")
            or {}
        )

        if not detail:
            st.info(
                "请选择并保存岗位模板后评分。"
            )
            return

        st.metric(
            "综合匹配分",
            detail["total_score"],
        )

        st.write(
            "硬性条件："
            + detail["hard_status"]
        )

        dimensions = detail["dimensions"]

        kind = st.radio(
            "图表类型",
            [
                "雷达图",
                "柱状图",
            ],
            horizontal=True,
        )

        names = [
            item["name"]
            for item in dimensions
        ]

        values = [
            item["score"]
            for item in dimensions
        ]

        if kind == "雷达图":
            figure = go.Figure(
                go.Scatterpolar(
                    r=values + values[:1],
                    theta=names + names[:1],
                    fill="toself",
                    line_color="#82A5D9",
                )
            )

            figure.update_layout(
                polar={
                    "radialaxis": {
                        "range": [0, 100]
                    }
                }
            )

        else:
            figure = go.Figure(
                go.Bar(
                    x=names,
                    y=values,
                    marker_color="#82A5D9",
                )
            )

            figure.update_yaxes(
                range=[0, 100]
            )

        figure.update_layout(
            margin={
                "l": 30,
                "r": 30,
                "t": 20,
                "b": 20,
            },
            showlegend=False,
        )

        st.plotly_chart(
            figure,
            use_container_width=True,
        )

        groups = [
            (
                "满足项",
                ["满足"],
            ),
            (
                "部分满足与待核实",
                ["部分满足", "待核实"],
            ),
            (
                "未匹配项",
                ["未匹配"],
            ),
        ]

        for title, states in groups:
            st.markdown(
                "**" + title + "**"
            )

            for row in detail["conditions"]:
                if row["state"] in states:
                    st.write(
                        f"{row['field']}："
                        f"{row['value']}"
                        f"（{row['state']}）"
                    )

                    st.caption(
                        "证据：" + row["evidence"]
                    )

        with st.expander(
            "查看完整评分审计记录"
        ):
            for line in detail["audit_log"]:
                st.write(line)

        jid = api(
            "GET",
            ep(sid, "/jobs"),
        )["active_id"]

        if st.button(
            "预览并下载PDF报告",
            disabled=not jid,
        ):
            st.session_state.detail = (
                "report",
                code,
                jid,
            )

            st.rerun()


def report_page(sid, code, jid):
    if st.button("返回候选人信息"):
        st.session_state.detail = None

        st.rerun()

    jobs = api(
        "GET",
        ep(sid, "/jobs"),
    )["templates"]

    job = next(
        (
            item
            for item in jobs
            if item["id"] == jid
        ),
        None,
    )

    candidates = api(
        "GET",
        ep(sid, "/candidates"),
    )["candidates"]

    candidate = next(
        (
            item
            for item in candidates
            if item["candidate_code"] == code
        ),
        None,
    )

    if not job or not candidate:
        st.session_state.pop(
            "pdf_snapshot",
            None,
        )

        st.info(
            "模板或候选人已删除，"
            "不能生成或下载报告。"
        )

        return

    stamp_source = json.dumps(
        [
            sid,
            jid,
            job["version"],
            candidate,
        ],
        sort_keys=True,
        ensure_ascii=False,
    )

    stamp = hashlib.sha256(
        stamp_source.encode("utf-8")
    ).hexdigest()

    snapshot = st.session_state.get(
        "pdf_snapshot"
    )

    if (
        not snapshot
        or snapshot["stamp"] != stamp
    ):
        with st.spinner(
            "正在生成脱敏报告"
        ):
            result = api(
                "POST",
                ep(
                    sid,
                    f"/jobs/{jid}"
                    f"/candidates/{code}"
                    "/pdf-report",
                ),
                timeout=120,
            )

        snapshot = {
            "stamp": stamp,
            **result,
        }

        st.session_state.pdf_snapshot = snapshot

    report = snapshot["report"]

    st.title("招聘筛选评估报告")

    st.caption(
        "以下预览与PDF使用同一份数据。"
        "对外发送前请人工复核脱敏结果与事实。"
    )

    st.download_button(
        "下载PDF报告",
        base64.b64decode(
            snapshot["pdf_base64"]
        ),
        file_name=snapshot["filename"],
        mime="application/pdf",
        type="primary",
    )

    st.write(
        f"岗位：{report['job_name']} · "
        f"模板版本：{report['job_version']} · "
        f"候选人：{report['candidate_name']}"
    )

    st.caption(
        f"报告编号：{report['report_id']} · "
        f"生成时间：{report['generated_at']}"
    )

    st.caption(
        "数据快照摘要："
        + report["snapshot_hash"][:16]
    )

    for section in report["sections"]:
        st.subheader(section["title"])

        for line in section["lines"]:
            st.write(line)