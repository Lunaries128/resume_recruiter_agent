import hashlib
import json
import math
import uuid
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field

import database as db
from guardrails import validate_filter_request
from report_service import clean


CATEGORIES = (
    '技能',
    '经验',
    '学历',
    '项目',
    '加分项（优先条件）',
    '需确认',
)
DIMENSIONS = CATEGORIES[:5]
DEFAULT_WEIGHTS = dict(
    zip(DIMENSIONS, (50.0, 25.0, 15.0, 10.0, 0.0))
)
SCORING_VERSION = 'dimension-relative-v1'


class JobRequirement(BaseModel):
    name: str = Field(min_length=1)
    category: Literal[
        '技能',
        '经验',
        '学历',
        '项目',
        '加分项（优先条件）',
        '需确认',
    ]
    quote: str = Field(min_length=1)


class JobDraft(BaseModel):
    title: str = '待确认岗位'
    requirements: list[JobRequirement]


def number(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是数字。') from None

    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(label + '必须是0到100之间的有限数字。')

    return value


def dimension_weights(values):
    if not isinstance(values, dict) or set(values) != set(DIMENSIONS):
        raise ValueError('请完整填写五个维度的权重。')

    result = {
        key: number(values[key], key + '维度权重')
        for key in DIMENSIONS
    }

    if not math.isclose(
        sum(result.values()),
        100.0,
        abs_tol=0.001,
    ):
        raise ValueError('五个维度的权重之和必须为100%。')

    return result


def effective_weights(requirements, dimensions):
    totals = {
        key: sum(
            item['weight']
            for item in requirements
            if item['category'] == key
        )
        for key in DIMENSIONS
    }

    active = {
        key: dimensions[key]
        for key in DIMENSIONS
        if dimensions[key] > 0 and totals[key] > 0
    }

    denominator = sum(active.values())

    if denominator <= 0:
        raise ValueError(
            '至少一个非零维度中需要有相对权重大于0的岗位要求。'
        )

    actual = {
        key: active.get(key, 0) / denominator
        for key in DIMENSIONS
    }

    items = {
        item['id']: (
            actual.get(item['category'], 0)
            * item['weight']
            / totals[item['category']]
            if item['category'] in totals
            and totals[item['category']] > 0
            else 0.0
        )
        for item in requirements
    }

    return actual, items


def init():
    with db.get_connection() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS hr_preferences_v2(
          id TEXT PRIMARY KEY,
          hr_id TEXT NOT NULL,
          scope TEXT NOT NULL,
          content TEXT NOT NULL,
          enabled INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hr_criteria_v2(
          sid TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          payload TEXT NOT NULL,
          confirmed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS hr_selection_v2(
          sid TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          codes TEXT NOT NULL
        );
        ''')


def preferences(hr_id, scope=None):
    with db.get_connection() as conn:
        rows = conn.execute(
            '''
            SELECT * FROM hr_preferences_v2
            WHERE hr_id=?
            ORDER BY created_at DESC
            ''',
            (hr_id,),
        ).fetchall()

    return [
        dict(item)
        for item in rows
        if scope is None
        or (
            item['enabled']
            and item['scope'] in ('通用', scope)
        )
    ]


def save_preference(hr_id, content, scope='通用'):
    content = content.strip()
    scope = scope.strip() or '通用'

    if not hr_id.strip() or not content or len(content) > 1000:
        raise ValueError('请填写HR编号及不超过1000字的岗位偏好。')

    validate_filter_request(content)
    identifier = uuid.uuid4().hex

    with db.get_connection() as conn:
        conn.execute(
            'INSERT INTO hr_preferences_v2 VALUES(?,?,?,?,?,?)',
            (
                identifier,
                hr_id,
                scope,
                clean(content),
                1,
                db.now(),
            ),
        )

    return {
        'id': identifier,
        'message': '偏好已持久保存。',
    }


def edit_preference(hr_id, identifier, content, scope, enabled):
    validate_filter_request(content)

    if not content.strip():
        raise ValueError('偏好不能为空。')

    with db.get_connection() as conn:
        cursor = conn.execute(
            '''
            UPDATE hr_preferences_v2
            SET content=?, scope=?, enabled=?
            WHERE id=? AND hr_id=?
            ''',
            (
                clean(content),
                scope.strip() or '通用',
                int(enabled),
                identifier,
                hr_id,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError('偏好不存在或HR编号不匹配。')


def delete_preference(hr_id, identifier, confirmed):
    if not confirmed:
        raise ValueError('删除偏好需要确认。')

    with db.get_connection() as conn:
        conn.execute(
            'DELETE FROM hr_preferences_v2 WHERE id=? AND hr_id=?',
            (identifier, hr_id),
        )


def put_criteria(sid, payload, confirmed):
    db.get_session(sid)

    with db.get_connection() as conn:
        conn.execute(
            '''
            INSERT INTO hr_criteria_v2 VALUES(?,?,?)
            ON CONFLICT(sid) DO UPDATE SET
              payload=excluded.payload,
              confirmed=excluded.confirmed
            ''',
            (
                sid,
                json.dumps(payload, ensure_ascii=False),
                int(confirmed),
            ),
        )


def get_criteria(sid, required=False):
    session = db.get_session(sid)

    with db.get_connection() as conn:
        row = conn.execute(
            'SELECT * FROM hr_criteria_v2 WHERE sid=?',
            (sid,),
        ).fetchone()

    result = json.loads(row['payload']) if row else {}

    valid = bool(
        row
        and row['confirmed']
        and result.get('jd_text') == session['jd'].strip()
        and result.get('scoring_version') == SCORING_VERSION
    )

    if required and not valid:
        raise ValueError('请先确认当前JD的要求和权重。')

    return {
        'criteria': result,
        'confirmed': valid,
    }


def prepare(sid, jd, hr_id, scope, use_memory):
    from semantic_match import ask, VERSION

    text = clean(jd)

    if not text:
        raise ValueError('JD没有可整理内容。')

    def validate(job):
        if not job.requirements:
            raise ValueError('requirements不能为空。')

        for index, item in enumerate(job.requirements):
            if item.quote not in text:
                raise ValueError(
                    f'requirements[{index}].quote必须引用JD连续原文。'
                )

    draft = ask(
        JobDraft,
        '将JD拆成可独立核验的岗位要求，每条只选择一种类型。'
        '技能：技术、工具、知识或能力；经验：工作年限、行业经历、岗位职责经验；'
        '学历：学历、学位或专业要求；项目：明确的项目经历、项目职责与交付成果；'
        '加分项（优先条件）：所有明确写了优先、加分、具备更佳的要求，'
        '即使涉及技能、经验、学历或项目也归入此类，不升级为必需；'
        '需确认：原文含糊、矛盾、不能确定含义的条件，不自行补全。'
        '不同要求尽量拆行，同一要求不重复计入多个类型；保留且/或关系、'
        '年限、范围和熟练程度。删除福利、宣传、隐私条件及性格套话。'
        'quote逐字引用输入JD；岗位名不明确则写待确认岗位。',
        {'jd': text},
        validate,
    )

    result = {
        'version': VERSION,
        'scoring_version': SCORING_VERSION,
        'jd_text': jd.strip(),
        'title': clean(draft.title),
        'draft_id': uuid.uuid4().hex,
        'scope': scope,
        'hr_id': hr_id,
        'dimension_weights': DEFAULT_WEIGHTS.copy(),
        'requirements': [],
    }

    for index, item in enumerate(draft.requirements, 1):
        result['requirements'].append({
            'id': f'R{index}',
            **item.model_dump(),
            'weight': (
                1.0 if item.category in CATEGORIES[:4] else 0.0
            ),
            'origin': 'JD',
        })

    if use_memory:
        for pref in preferences(hr_id, scope):
            result['requirements'].append({
                'id': 'P' + pref['id'],
                'name': pref['content'],
                'category': CATEGORIES[4],
                'quote': pref['content'],
                'weight': 0.0,
                'origin': '历史偏好，需HR核对与本次JD是否冲突',
            })

    db.update_session(sid, jd=jd)
    put_criteria(sid, result, False)
    return result


def confirm(sid, rows, weights=None):
    result = get_criteria(sid)['criteria']

    if (
        not result
        or result['jd_text'] != db.get_session(sid)['jd'].strip()
    ):
        raise ValueError('JD已变化，请重新整理岗位要求。')

    if result.get('scoring_version') != SCORING_VERSION:
        raise ValueError('旧版岗位要求请先重新整理，再确认六种类型。')

    dimensions = dimension_weights(
        weights if weights is not None
        else result['dimension_weights']
    )

    normalized = []

    for row in rows:
        name = str(row.get('name') or '').strip()

        if not name:
            continue

        validate_filter_request(name)
        name = clean(name)

        if not name:
            raise ValueError('岗位要求清洗后为空，请修改内容。')

        category = row.get('category')

        if category not in CATEGORIES:
            raise ValueError('岗位要求类型必须为六种类型之一。')

        value = row.get('weight')

        if value is None or value == '':
            value = 1 if category in CATEGORIES[:4] else 0

        weight = number(value, '相对权重')

        normalized.append({
            'id': f'R{len(normalized) + 1}',
            'name': name,
            'category': category,
            'weight': weight,
            'quote': clean(row.get('quote')) or 'HR确认补充',
            'origin': 'HR已确认',
        })

    effective_weights(normalized, dimensions)

    result['requirements'] = normalized
    result['dimension_weights'] = dimensions
    result.pop('revision', None)

    result['revision'] = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()

    put_criteria(sid, result, True)
    return result


def weighted(result, criteria):
    actual, weights = effective_weights(
        criteria['requirements'],
        criteria['dimension_weights'],
    )

    rows = result['reviews']
    expected = {
        item['id']: item
        for item in criteria['requirements']
    }

    if (
        len(rows) != len(expected)
        or {item['id'] for item in rows} != set(expected)
    ):
        raise ValueError('核验结果缺少岗位要求或出现重复要求，未保存。')

    low = sum(
        weights[item['id']] * item['strength'] * 25
        for item in rows
    )

    unknown = sum(
        weights[item['id']]
        for item in rows
        if item['status'] == '信息不足'
    )

    result.update(
        total_score=round(low, 1),
        score_range=[
            round(low, 1),
            round(low + unknown * 100, 1),
        ],
        coverage=round((1 - unknown) * 100, 1),
        criteria_revision=criteria['revision'],
        scoring_version=SCORING_VERSION,
        dimension_weights=criteria['dimension_weights'],
        effective_dimension_weights={
            key: value * 100
            for key, value in actual.items()
        },
    )

    # 保留原报告及核验接口分类，新类型另存。
    dimensions = []

    for row in rows:
        item = expected[row['id']]

        row['requirement_type'] = item['category']
        row['relative_weight'] = item['weight']
        row['effective_weight'] = weights[row['id']]
        row['score_contribution'] = (
            weights[row['id']] * row['strength'] * 25
        )

        if weights[row['id']] <= 0:
            continue

        dimensions.append({
            'key': row['id'],
            'name': row['name'],
            'score': row['strength'] * 25,
            'weight': weights[row['id']],
            'status': (
                'satisfied'
                if row['status'] == '有充分支持'
                else 'partial'
                if row['status'] == '有部分支持'
                else 'unsatisfied'
            ),
            'requirement': row['name'],
            'candidate_value': row['reason'],
            'evidence_source': '结构化简历',
            'evidence': [
                f"{ref['source']}：{ref['quote']}"
                for ref in row['references']
            ],
        })

    result['dimensions'] = dimensions

    result['scoring_rule'] = (
        '先分配五个维度权重，再按各维度内逐条要求的相对权重分配。'
        '无有效要求的维度不扣分，其余参与维度归一化。'
        '充分支持100，部分支持25/50/75，明确差距0；'
        '未知项单列，上界仅假设未知项满足。'
        '加分项只有维度权重和条目相对权重均大于0才计分，'
        '始终不作硬门槛；需确认不计分。'
    )

    result['audit_log'][0] = result['scoring_rule']
    result['audit_log'] += [
        '确认版本：' + criteria['revision'],
        'HR维度权重：' + json.dumps(
            criteria['dimension_weights'],
            ensure_ascii=False,
        ),
        '实际维度权重：' + json.dumps(
            result['effective_dimension_weights'],
            ensure_ascii=False,
        ),
        '实际逐条权重：' + json.dumps(
            weights,
            ensure_ascii=False,
        ),
        (
            f'证据支持分{result["total_score"]}；'
            f'覆盖率{result["coverage"]}%'
        ),
    ]

    return result


def score_one(code):
    from semantic_match import evaluate_candidate

    criteria = get_criteria(
        db.current_session(),
        True,
    )['criteria']

    candidate = db.get_candidate(code)

    if not candidate:
        raise ValueError('候选人不属于当前会话。')

    # 适配原核验器，不改变其证据核验逻辑。
    compatible = deepcopy(criteria)

    for row in compatible['requirements']:
        category = row['category']
        row['requirement_type'] = category
        row['category'] = (
            '需确认'
            if category == '需确认'
            else '优先条件'
            if category == CATEGORIES[4]
            else '必需条件'
        )

    # 原核验器需要核心条件计算中间值。
    # 仅有优先项时，核验完成后恢复优先属性，
    # 最终评分仍完全使用HR确认的两层权重。
    bonus_only = not any(
        row['category'] == '必需条件'
        for row in compatible['requirements']
    )

    if bonus_only:
        for row in compatible['requirements']:
            if row['category'] == '优先条件':
                row['category'] = '必需条件'

    result = evaluate_candidate(candidate, compatible)

    if bonus_only:
        for row in result['reviews']:
            if row.get('requirement_type') == CATEGORIES[4]:
                row['category'] = '优先条件'

        result.update(
            recommendation='建议人工核实优先条件',
            summary='当前评分依据为HR确认的优先条件，不作为必需门槛。',
            satisfied=[],
            partial=[],
            unsatisfied=[],
            counts={},
        )

    result = weighted(result, criteria)
    db.save_score(code, criteria['revision'], result)
    return result


def candidates():
    state = get_criteria(db.current_session())
    revision = (
        state['criteria'].get('revision')
        if state['confirmed']
        else None
    )

    rows = db.list_candidates()

    for item in rows:
        detail = item.get('score_detail') or {}

        if (
            not revision
            or detail.get('criteria_revision') != revision
        ):
            item.update(
                score_detail={},
                match_score=None,
            )

    rows.sort(
        key=lambda item: (
            item['match_score'] is not None,
            item['match_score'] or 0,
            item['score_detail'].get('coverage', 0),
        ),
        reverse=True,
    )

    for index, item in enumerate(rows, 1):
        item['rank'] = (
            index if item['match_score'] is not None else None
        )

    return rows


def set_selection(codes):
    sid = db.current_session()
    allowed = {
        item['candidate_code']
        for item in db.list_candidates()
    }

    if set(codes) - allowed:
        raise ValueError('候选人集合越过当前会话。')

    with db.get_connection() as conn:
        conn.execute(
            '''
            INSERT INTO hr_selection_v2 VALUES(?,?)
            ON CONFLICT(sid) DO UPDATE SET codes=excluded.codes
            ''',
            (
                sid,
                json.dumps(list(dict.fromkeys(codes))),
            ),
        )


def selection():
    with db.get_connection() as conn:
        row = conn.execute(
            'SELECT codes FROM hr_selection_v2 WHERE sid=?',
            (db.current_session(),),
        ).fetchone()

    return json.loads(row['codes']) if row else None


def selected_candidates():
    codes = selection()

    return [
        item
        for item in candidates()
        if codes is None or item['candidate_code'] in codes
    ]


def safe_candidate(item):
    keys = (
        'candidate_code',
        'masked_name',
        'education',
        'skills',
        'experiences',
        'projects',
        'awards',
        'certificates',
        'work_years',
        'rank',
        'score_detail',
    )

    def sanitize(value):
        if isinstance(value, str):
            return clean(value)

        if isinstance(value, list):
            return [sanitize(item) for item in value]

        if isinstance(value, dict):
            return {
                key: sanitize(item)
                for key, item in value.items()
            }

        return value

    return sanitize({
        key: item.get(key)
        for key in keys
    })


init()