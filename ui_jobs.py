import copy

import pandas as pd
import streamlit as st

from ui_common import (
    api,
    request,
    ep,
)

from jd_parser import (
    FIELDS,
    KINDS,
    DIMENSIONS,
)


def blank():
    return {
        "name": "",
        "raw_jd": "",
        "conditions": [],
        "weights": dict.fromkeys(
            DIMENSIONS,
            0,
        ),
        "duties": [],
        "review_items": [],
        "warnings": [],
        "review_note": "",
        "weight_note": "",
    }


def matching_panel(
    sid,
    key="jobs",
):
    data = api(
        "GET",
        ep(sid, "/jobs"),
    )

    jid = data["active_id"]

    if not jid:
        return

    name = next(
        item["name"]
        for item in data["templates"]
        if item["id"] == jid
    )

    candidates = api(
        "GET",
        ep(sid, "/candidates"),
    )["candidates"]

    with st.expander(
        "当前岗位匹配：" + name,
        expanded=True,
    ):
        st.caption(
            "点击后逐份匹配；"
            "已完成且条件未变化的候选人复用缓存。"
            "失败者可再次点击重试。"
        )

        clicked = st.button(
            "开始能力匹配／重试未完成项",
            key=key + "_match_" + sid,
            disabled=not candidates,
        )

        if clicked:
            progress = st.progress(0)
            errors = []

            for index, candidate in enumerate(
                candidates
            ):
                code = candidate[
                    "candidate_code"
                ]

                try:
                    api(
                        "POST",
                        ep(
                            sid,
                            f"/jobs/{jid}"
                            f"/candidates/{code}"
                            "/assess",
                        ),
                        timeout=75,
                    )

                except RuntimeError as exc:
                    errors.append(
                        str(exc)
                    )

                progress.progress(
                    (index + 1)
                    / len(candidates)
                )

            st.session_state.pop(
                "pdf_snapshot",
                None,
            )

            st.session_state.notice = (
                f"本轮已处理{len(candidates)}份；"
                f"失败{len(errors)}份，"
                "失败项没有计为0分。"
            )

            st.rerun()

        if not candidates:
            st.info(
                "本会话暂无成功入库的简历。"
            )
            return

        rows = [
            {
                "姓名": item.get(
                    "masked_name"
                ),
                "匹配分": item.get(
                    "match_score"
                ),
                "状态": item.get(
                    "hard_status"
                ),
                "待核实项数": item.get(
                    "score_detail",
                    {},
                ).get(
                    "pending_count"
                ),
                "错误提示": item.get(
                    "matching_error",
                    "",
                ),
            }
            for item in candidates
        ]

        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            use_container_width=True,
        )

        st.caption(
            "这是简历证据匹配分，"
            "不是实际能力分；"
            "待核实项应结合原简历和面试确认，"
            "不自动淘汰。"
        )


def chat_panel(sid):
    with st.expander(
        "招聘助理聊天"
    ):
        messages = api(
            "GET",
            ep(sid, "/messages"),
        )["messages"]

        for message in messages:
            with st.chat_message(
                message["role"]
            ):
                st.markdown(
                    message["content"]
                )

                if message.get("trace"):
                    with st.expander(
                        "工具执行记录"
                    ):
                        st.json(
                            message["trace"]
                        )

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
                        "hr_id": (
                            st.session_state.hr_id
                        ),
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
                "下载聊天报告 "
                + filename[:8],
                content,
                file_name=filename,
                mime="text/markdown",
                key="chat_report_" + filename,
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
        st.session_state[
            choice_key
        ] = next_choice

    if st.session_state.get(
        choice_key
    ) not in ["__new__", *mapping]:
        st.session_state[
            choice_key
        ] = "__new__"

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

    draft_key = (
        "capability_draft_" + sid
    )

    if (
        draft_key not in st.session_state
        or st.session_state[
            draft_key
        ]["editing"] != selected
    ):
        draft = blank()

        if selected in mapping:
            draft.update(
                copy.deepcopy(
                    mapping[selected][
                        "payload"
                    ]
                )
            )

        draft.update(
            editing=selected,
            revision=0,
            preference="",
        )

        st.session_state[
            draft_key
        ] = draft

    draft = st.session_state[
        draft_key
    ]

    widget_id = (
        sid
        + selected
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
        "切换模式或模板前请暂存草稿；"
        "重新解析会替换条件。"
        "能力等级：1了解、2参与、"
        "3独立负责、4带领统筹。"
    )

    for warning in draft.get(
        "warnings",
        [],
    ):
        st.warning(warning)

    if draft.get("duties"):
        with st.expander(
            "识别到的岗位职责"
        ):
            for duty in draft["duties"]:
                st.write(duty)

    if draft.get("review_items"):
        with st.expander(
            "待人工确认事项",
            expanded=True,
        ):
            st.dataframe(
                pd.DataFrame(
                    draft["review_items"]
                ),
                hide_index=True,
                use_container_width=True,
            )

    with st.form(
        "job_form_" + widget_id
    ):
        raw = st.text_area(
            "完整JD或口语化岗位需求",
            value=draft.get(
                "raw_jd",
                "",
            ),
            height=220,
        )

        parse = st.form_submit_button(
            "解析岗位要求",
            disabled=(
                mode == "手动填写模板"
            ),
        )

        name = st.text_input(
            "岗位模板名称",
            value=draft["name"],
        )

        columns = [
            "kind",
            "field",
            "rule",
            "value",
            "scope",
            "target_level",
            "source",
        ]

        initial = [
            {
                "scope": "",
                "target_level": 2,
                **item,
            }
            for item in draft["conditions"]
        ]

        conditions = st.data_editor(
            pd.DataFrame(
                initial,
                columns=columns,
            ),
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            key="conditions_" + widget_id,
            column_config={
                "kind": (
                    st.column_config.SelectboxColumn(
                        "类型",
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
                        "要求／能力",
                        required=True,
                    )
                ),
                "scope": (
                    st.column_config.TextColumn(
                        "专项年限领域（总年限留空）"
                    )
                ),
                "target_level": (
                    st.column_config.NumberColumn(
                        "要求能力等级",
                        min_value=1,
                        max_value=4,
                        step=1,
                        default=2,
                    )
                ),
                "source": (
                    st.column_config.TextColumn(
                        "JD原句／人工依据"
                    )
                ),
            },
        )

        st.caption(
            "业务能力放在“项目”字段；"
            "多个能力用分号分隔，"
            "任一取最高匹配比例，"
            "全部取各项平均。"
            "工作年限填写数字、规则选最低。"
            "学历、专业、证书不使用能力等级。"
        )

        preference = st.text_area(
            "权重偏好（选填）",
            value=draft.get(
                "preference",
                "",
            ),
            placeholder=(
                "例如：更看重学历而不是工作经验。"
                "这里只调整权重，"
                "不新增学历门槛。"
            ),
        )

        recommend = st.form_submit_button(
            "根据偏好建议权重"
        )

        weight_rows = [
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

        weights = st.data_editor(
            pd.DataFrame(weight_rows),
            disabled=["维度"],
            hide_index=True,
            use_container_width=True,
            key="weights_" + widget_id,
            column_config={
                "权重": (
                    st.column_config.NumberColumn(
                        "权重（%）",
                        min_value=0,
                        max_value=100,
                        step=1,
                    )
                ),
            },
        )

        st.caption(
            draft.get("weight_note")
            or "合计100%；没有条件的维度为0。"
        )

        review_note = st.text_area(
            "人工确认说明",
            value=draft.get(
                "review_note",
                "",
            ),
        )

        save = st.form_submit_button(
            "保存岗位模板",
            type="primary",
        )

        stage = st.form_submit_button(
            "暂存草稿"
        )

    if (
        parse
        or recommend
        or save
        or stage
    ):
        records = (
            conditions
            .fillna("")
            .to_dict("records")
        )

        for record in records:
            record["target_level"] = int(
                record.get("target_level")
                or 2
            )

        payload = {
            key: copy.deepcopy(
                draft[key]
            )
            for key in blank()
        }

        payload.update(
            name=name,
            raw_jd=raw,
            conditions=records,
            weights=dict(
                zip(
                    weights["维度"],
                    weights["权重"]
                    .fillna(0)
                    .astype(float),
                )
            ),
            review_note=review_note,
        )

        st.session_state[
            draft_key
        ] = {
            **payload,
            "editing": selected,
            "revision": draft["revision"],
            "preference": preference,
        }

        try:
            if parse:
                if not raw.strip():
                    raise RuntimeError(
                        "请先填写岗位需求。"
                    )

                with st.spinner(
                    "正在提取条件、能力与权重偏好"
                ):
                    result = api(
                        "POST",
                        ep(
                            sid,
                            "/jobs/parse",
                        ),
                        json={"jd": raw},
                        timeout=120,
                    )

                st.session_state[
                    draft_key
                ].update(
                    result,
                    revision=(
                        draft["revision"] + 1
                    ),
                )

                st.rerun()

            if recommend:
                with st.spinner(
                    "正在解析权重偏好"
                ):
                    result = api(
                        "POST",
                        ep(
                            sid,
                            "/jobs/suggest-weights",
                        ),
                        json={
                            "conditions": records,
                            "preference": preference,
                        },
                        timeout=75,
                    )

                st.session_state[
                    draft_key
                ].update(
                    result,
                    revision=(
                        draft["revision"] + 1
                    ),
                )

                st.rerun()

            if save:
                path = ep(
                    sid,
                    "/jobs",
                )

                if selected in mapping:
                    path += "/" + selected

                result = api(
                    (
                        "PUT"
                        if selected in mapping
                        else "POST"
                    ),
                    path,
                    json=payload,
                )

                st.session_state[
                    "next_job_" + sid
                ] = result["id"]

                st.session_state[
                    "rank_job_" + sid
                ] = result["id"]

                st.session_state.pop(
                    draft_key,
                    None,
                )

                st.session_state.pop(
                    "pdf_snapshot",
                    None,
                )

                st.session_state.notice = (
                    "岗位已保存。"
                    "请在下方或候选人信息页"
                    "开始能力匹配。"
                )

                st.rerun()

            if stage:
                st.success(
                    "草稿已暂存；"
                    "正式保存请点击保存岗位模板。"
                )

        except RuntimeError as exc:
            st.error(str(exc))

    if selected in mapping:
        with st.expander(
            "删除岗位模板"
        ):
            confirmed = st.checkbox(
                "确认删除模板及其评分，保留简历",
                key="del_job_" + selected,
            )

            if st.button(
                "确认删除岗位模板",
                disabled=not confirmed,
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

    matching_panel(sid)
    chat_panel(sid)