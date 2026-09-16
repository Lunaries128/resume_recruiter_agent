import copy

import pandas as pd
import streamlit as st

from ui_common import (
    api,
    request,
    ep,
)


FIELDS = [
    "学历",
    "专业",
    "工作年限",
    "技能",
    "项目",
    "竞赛证书",
]

KINDS = [
    "硬性条件",
    "必备技能",
    "优先加分",
]

DIMENSIONS = [
    "学历",
    "专业",
    "项目经验",
    "竞赛证书",
    "技术栈",
]

DIMENSION_OF = dict(
    zip(
        FIELDS,
        [
            "学历",
            "专业",
            "项目经验",
            "技术栈",
            "项目经验",
            "竞赛证书",
        ],
    )
)


def blank():
    return {
        "name": "",
        "raw_jd": "",
        "conditions": [],
        "weights": {
            dimension: 0
            for dimension in DIMENSIONS
        },
        "warnings": [],
        "duties": [],
        "review_items": [],
        "review_note": "",
        "weight_note": "",
    }


def chat_panel(sid):
    with st.expander("招聘助理聊天"):
        messages = api(
            "GET",
            ep(sid, "/messages"),
        )["messages"]

        for message in messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

                if message.get("trace"):
                    with st.expander(
                        "工具执行记录"
                    ):
                        st.json(message["trace"])

        with st.form(
            "chat_" + sid,
            clear_on_submit=True,
        ):
            text = st.text_area(
                "输入筛选、比较或追问要求"
            )

            send = st.form_submit_button(
                "发送"
            )

        if send and text.strip():
            with st.spinner(
                "助理正在处理"
            ):
                api(
                    "POST",
                    ep(sid, "/chat"),
                    json={
                        "message": text,
                        "hr_id": st.session_state.hr_id,
                    },
                    timeout=180,
                )

            st.rerun()

        reports = api(
            "GET",
            ep(sid, "/reports"),
        )["reports"]

        for filename in reports:
            content = request(
                "GET",
                ep(
                    sid,
                    "/reports/" + filename,
                ),
            ).content

            st.download_button(
                "下载历史聊天报告 " + filename[:8],
                content,
                file_name=filename,
                mime="text/markdown",
                key="old_report_" + filename,
            )


def score_preview(sid):
    data = api(
        "GET",
        ep(sid, "/jobs"),
    )

    current = next(
        (
            item
            for item in data["templates"]
            if item["id"] == data["active_id"]
        ),
        None,
    )

    if not current:
        return

    with st.expander(
        "当前岗位匹配分",
        expanded=True,
    ):
        st.caption(
            "以下使用已保存模板："
            + current["name"]
            + "，不是上方未保存的草稿。"
        )

        if st.button(
            "开始匹配全部候选人"
        ):
            api(
                "POST",
                ep(
                    sid,
                    "/jobs/"
                    + current["id"]
                    + "/activate",
                ),
            )

            st.success(
                "匹配结果已更新。"
            )

        items = api(
            "GET",
            ep(sid, "/candidates"),
        )["candidates"]

        if items:
            rows = [
                {
                    "姓名": item.get("masked_name"),
                    "匹配分": item.get("match_score"),
                    "硬性条件": item.get("hard_status"),
                }
                for item in items
            ]

            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                use_container_width=True,
            )

        else:
            st.info(
                "本会话尚无成功入库的候选人。"
            )


def job_page(session):
    sid = session["id"]

    data = api(
        "GET",
        ep(sid, "/jobs"),
    )

    mapping = {
        item["id"]: item
        for item in data["templates"]
    }

    choice_key = "job_choice_" + sid

    next_choice = st.session_state.pop(
        "next_job_" + sid,
        None,
    )

    if next_choice:
        st.session_state[choice_key] = next_choice

    if st.session_state.get(
        choice_key
    ) not in ["__new__", *mapping]:
        st.session_state[choice_key] = "__new__"

    selected = st.selectbox(
        "编辑岗位模板",
        ["__new__", *mapping],
        key=choice_key,
        format_func=lambda value: (
            "新建岗位模板"
            if value == "__new__"
            else mapping[value]["name"]
        ),
    )

    draft_key = "jd_v2_draft_" + sid

    if (
        draft_key not in st.session_state
        or st.session_state[draft_key]["editing"]
        != selected
    ):
        payload = (
            mapping[selected]["payload"]
            if selected in mapping
            else {}
        )

        st.session_state[draft_key] = dict(
            blank(),
            **copy.deepcopy(payload),
            editing=selected,
            revision=0,
        )

    draft = st.session_state[draft_key]

    widget_id = (
        sid
        + "_"
        + selected
        + "_"
        + str(draft["revision"])
    )

    mode = st.radio(
        "填写方式",
        [
            "粘贴JD自动解析",
            "手动填写模板",
        ],
        horizontal=True,
        key="jd_mode_" + sid,
    )

    st.caption(
        "切换模板或填写方式前，请先暂存草稿；"
        "重新解析会替换当前条件，请先确认。"
    )

    if draft["duties"]:
        with st.expander(
            "识别到的岗位职责",
            expanded=True,
        ):
            for text in draft["duties"]:
                st.write(text)

    for warning in draft["warnings"]:
        st.warning(warning)

    if draft["review_items"]:
        st.info(
            f"有{len(draft['review_items'])}项"
            "待确认内容，目前不参与评分。"
        )

        review_frame = pd.DataFrame(
            draft["review_items"]
        ).rename(
            columns={
                "text": "要求",
                "source": "原文依据",
                "reason": "待确认原因",
            }
        )

        st.dataframe(
            review_frame,
            hide_index=True,
            use_container_width=True,
        )

    with st.form(
        "jd_form_" + widget_id
    ):
        raw_jd = draft["raw_jd"]

        parse_clicked = False

        if mode == "粘贴JD自动解析":
            raw_jd = st.text_area(
                "原始岗位描述",
                value=raw_jd,
                height=280,
                placeholder=(
                    "直接粘贴完整JD，保留换行即可。"
                    "支持岗位职责、任职要求、"
                    "加分项及简短口语描述。"
                ),
                key="jd_raw_" + widget_id,
            )

            parse_clicked = st.form_submit_button(
                "解析岗位要求"
            )

        st.caption(
            "以下为最终评分条件，可增删修改。"
            "不明确的事项请先核实，再手动加入；"
            "不要直接猜测门槛。"
        )

        name = st.text_input(
            "岗位模板名称",
            value=draft["name"],
        )

        conditions = st.data_editor(
            pd.DataFrame(
                draft["conditions"],
                columns=[
                    "kind",
                    "field",
                    "rule",
                    "value",
                    "source",
                ],
            ),
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key="jd_conditions_" + widget_id,
            column_config={
                "kind": (
                    st.column_config.SelectboxColumn(
                        "条件类型",
                        options=KINDS,
                        required=True,
                    )
                ),
                "field": (
                    st.column_config.SelectboxColumn(
                        "字段",
                        options=FIELDS,
                        required=True,
                    )
                ),
                "rule": (
                    st.column_config.SelectboxColumn(
                        "规则",
                        options=[
                            "任一",
                            "全部",
                            "最低",
                        ],
                        required=True,
                    )
                ),
                "value": (
                    st.column_config.TextColumn(
                        "要求",
                        required=True,
                    )
                ),
                "source": (
                    st.column_config.TextColumn(
                        "原句／人工依据"
                    )
                ),
            },
        )

        st.caption(
            "多个值用分号分隔；"
            "最低仅用于学历和总工作年限。"
            "现有算法不能直接核验某项技术使用了几年。"
        )

        st.caption(
            draft.get("weight_note")
            or "权重合计100%，没有条件的维度必须为0。"
        )

        weights = st.data_editor(
            pd.DataFrame(
                [
                    {
                        "维度": dimension,
                        "权重": float(
                            draft["weights"].get(
                                dimension,
                                0,
                            )
                        ),
                    }
                    for dimension in DIMENSIONS
                ]
            ),
            disabled=["维度"],
            hide_index=True,
            use_container_width=True,
            key="jd_weights_" + widget_id,
            column_config={
                "权重": (
                    st.column_config.NumberColumn(
                        "权重（%）",
                        min_value=0,
                        max_value=100,
                        step=1,
                    )
                )
            },
        )

        review_note = st.text_area(
            "待确认事项的处理说明",
            value=draft["review_note"],
            help=(
                "说明哪些已核实并加入条件表，"
                "哪些保留给面试核实。"
                "这里的说明本身不参与评分。"
            ),
        )

        save_column, draft_column, weight_column = (
            st.columns(3)
        )

        saved = save_column.form_submit_button(
            "保存岗位模板并更新匹配分",
            type="primary",
            use_container_width=True,
        )

        staged = draft_column.form_submit_button(
            "暂存草稿",
            use_container_width=True,
        )

        balanced = weight_column.form_submit_button(
            "按现有条件建议等权",
            use_container_width=True,
        )

    payload = {
        "name": name,
        "raw_jd": raw_jd,
        "conditions": (
            conditions
            .fillna("")
            .to_dict("records")
        ),
        "weights": dict(
            zip(
                weights["维度"],
                weights["权重"]
                .fillna(0)
                .astype(float),
            )
        ),
        "duties": draft["duties"],
        "review_items": draft["review_items"],
        "review_note": review_note,
        "warnings": draft["warnings"],
        "weight_note": draft["weight_note"],
    }

    if parse_clicked or staged or balanced or saved:
        st.session_state[draft_key] = dict(
            payload,
            editing=selected,
            revision=draft["revision"],
        )

    if parse_clicked:
        if not raw_jd.strip():
            st.error(
                "请先粘贴岗位描述。"
            )

        else:
            try:
                with st.spinner(
                    "正在提取岗位要求并核对原文"
                ):
                    result = api(
                        "POST",
                        ep(sid, "/jobs/parse"),
                        json={"jd": raw_jd},
                        timeout=120,
                    )

                result["name"] = (
                    result.get("name")
                    or name
                )

                st.session_state[draft_key] = dict(
                    blank(),
                    **result,
                    editing=selected,
                    revision=draft["revision"] + 1,
                )

                st.rerun()

            except RuntimeError as exc:
                st.error(str(exc))

                st.info(
                    "原文和已填写内容仍保留，"
                    "可稍后重试或手动填写。"
                )

    if balanced:
        active_dimensions = {
            DIMENSION_OF[item["field"]]
            for item in payload["conditions"]
            if item.get("field") in DIMENSION_OF
        }

        values = {
            dimension: (
                round(
                    100 / len(active_dimensions),
                    2,
                )
                if dimension in active_dimensions
                else 0
            )
            for dimension in DIMENSIONS
        }

        if active_dimensions:
            last = [
                dimension
                for dimension in DIMENSIONS
                if dimension in active_dimensions
            ][-1]

            values[last] = round(
                values[last]
                + 100
                - sum(values.values()),
                2,
            )

        st.session_state[draft_key].update(
            weights=values,
            revision=draft["revision"] + 1,
            weight_note=(
                "这是按有效维度等权分配的建议，"
                "不是模型推断的岗位重要性。"
            ),
        )

        st.rerun()

    if staged:
        st.success(
            "草稿已暂存于当前浏览器会话；"
            "正式入库请点击保存岗位模板。"
        )

    if saved:
        try:
            path = ep(sid, "/jobs")

            if selected != "__new__":
                path += "/" + selected

            method = (
                "POST"
                if selected == "__new__"
                else "PUT"
            )

            item = api(
                method,
                path,
                json=payload,
            )

            st.session_state[
                "next_job_" + sid
            ] = item["id"]

            st.session_state[
                "rank_job_" + sid
            ] = item["id"]

            st.session_state.pop(
                draft_key,
                None,
            )

            st.session_state.pop(
                "pdf_snapshot",
                None,
            )

            st.session_state.notice = (
                "岗位已保存。招聘要求与候选人信息页面"
                "使用同一套匹配结果。"
            )

            st.rerun()

        except RuntimeError as exc:
            st.error(str(exc))

    if selected in mapping:
        with st.expander(
            "删除岗位模板"
        ):
            accepted = st.checkbox(
                "确认删除模板及对应评分，保留简历",
                key="delete_job_check_" + selected,
            )

            if st.button(
                "确认删除岗位模板",
                disabled=not accepted,
            ):
                api(
                    "DELETE",
                    ep(
                        sid,
                        "/jobs/" + selected,
                    ),
                )

                st.session_state[
                    "next_job_" + sid
                ] = "__new__"

                st.session_state.pop(
                    "pdf_snapshot",
                    None,
                )

                st.rerun()

    score_preview(sid)
    chat_panel(sid)