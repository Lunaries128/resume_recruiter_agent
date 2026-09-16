import base64
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from PIL import Image

from config import API_URL

ROOT = Path(__file__).resolve().parent
LOGO = next((p for p in (ROOT/'assets/logo.svg', ROOT/'asserts/logo.svg') if p.exists()), None)


def favicon():
    if LOGO:
        match = re.search(r'data:image/png;base64,([^\"\x27]+)', LOGO.read_text(encoding='utf-8'))
        if match:
            return Image.open(BytesIO(base64.b64decode(match.group(1))))
        return str(LOGO)
    return '💼'


st.set_page_config(page_title='智能简历筛选与招聘助理', page_icon=favicon(), layout='wide')
st.markdown('''<style>
[data-testid="stMetric"] {background:white;border:1px solid #D9E4F2;border-radius:10px;padding:12px;}
</style>''', unsafe_allow_html=True)
if LOGO:
    tab_icon = favicon()
    if isinstance(tab_icon, Image.Image):
        tab_icon = tab_icon.copy()
        tab_icon.thumbnail((32, 32))
        buffer = BytesIO()
        tab_icon.save(buffer, format='PNG')
        icon_uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    else:
        icon_uri = 'data:image/svg+xml;base64,' + base64.b64encode(LOGO.read_bytes()).decode('ascii')
    st.markdown(f'''<style>
    button[data-baseweb="tab"]::before {{content:"";display:inline-block;
      width:18px;height:18px;margin-right:7px;background:url("{icon_uri}") center/contain no-repeat;}}
    </style>''', unsafe_allow_html=True)


def request(method, path, **kwargs):
    timeout = kwargs.pop('timeout', 20)
    try:
        response = requests.request(method, API_URL.rstrip('/') + path, timeout=timeout, **kwargs)
    except requests.Timeout as exc:
        raise RuntimeError('请求超时。请刷新查看服务器已保存的状态，再决定是否重试。') from exc
    except requests.RequestException as exc:
        raise RuntimeError('无法连接后端，请检查后端是否已启动。') from exc
    if not response.ok:
        try:
            detail = response.json().get('detail', response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(str(detail))
    return response


def api(method, path, **kwargs):
    return request(method, path, **kwargs).json()


def endpoint(sid, tail=''):
    return f'/sessions/{sid}{tail}'


def open_session(sid):
    st.session_state.active_session = sid
    st.session_state.page = 'workspace'
    st.session_state.detail = None
    st.rerun()


def confirm_request(sid, kind, targets=None):
    try:
        result = api('POST', endpoint(sid, '/delete/request'),
                     json={'kind': kind, 'targets': targets or []})
        st.session_state.pending_delete = {'sid': sid, **result}
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))


def delete_confirmation():
    pending = st.session_state.get('pending_delete')
    if not pending:
        return
    with st.container(border=True):
        label = '整个会话及其全部原文件、候选人、评分和聊天记录' if pending['kind'] == 'session' else f"所选的 {pending['count']} 条上传记录及其原文件和候选人评分"
        st.warning('确认删除' + label + '？此操作不可恢复。')
        accepted = st.checkbox('我确认删除', key='confirm_' + pending['token'])
        left, right = st.columns(2)
        if left.button('确认删除', disabled=not accepted, key='delete_yes'):
            try:
                result = api('POST', endpoint(pending['sid'], '/delete/confirm'),
                             json={'token': pending['token'], 'confirmed': True})
                if result['kind'] == 'session' and st.session_state.active_session == pending['sid']:
                    st.session_state.active_session = None
                    st.session_state.page = 'history'
                # 清理这一个会话的页面草稿，删除其他会话不会影响当前编辑。
                if result['kind'] == 'session':
                    sid = pending['sid']
                    for key in list(st.session_state):
                        if key in (f'jd_{sid}', f'match_{sid}', f'upload_version_{sid}'):
                            st.session_state.pop(key, None)
                st.session_state.pending_delete = None
                st.session_state.detail = None
                st.session_state.notice = '删除完成。' + ' '.join(result.get('warnings', []))
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
        if right.button('取消', key='delete_no'):
            st.session_state.pending_delete = None
            st.rerun()


def is_today(value):
    local = timezone(timedelta(hours=8))
    return datetime.fromisoformat(value).astimezone(local).date() == datetime.now(local).date()


def sidebar(sessions):
    with st.sidebar:
        logo_col, text_col = st.columns([1, 4], vertical_alignment='center')
        if LOGO:
            logo_col.image(str(LOGO), width=40)
        text_col.markdown('### 智能小助理')
        st.text_input('HR编号', key='hr_id')
        if st.button('＋ 新建招聘会话', use_container_width=True):
            try:
                open_session(api('POST', '/sessions')['id'])
            except RuntimeError as exc:
                st.error(str(exc))
        if st.button('历史文件夹', use_container_width=True):
            st.session_state.page = 'history'
            st.session_state.detail = None
            st.rerun()
        st.divider()
        for title, items in (
            ('置顶会话', [s for s in sessions if s['pinned']]),
            ('今天的会话', [s for s in sessions if not s['pinned'] and is_today(s['created_at'])]),
        ):
            st.caption(title)
            if not items:
                st.caption('暂无')
            for item in items:
                if st.button(item['title'], key='open_'+item['id'], use_container_width=True):
                    open_session(item['id'])
        sid = st.session_state.active_session
        current = next((s for s in sessions if s['id'] == sid), None)
        if current:
            st.divider()
            with st.expander('当前会话设置'):
                st.caption(current['title'])
                with st.form('rename_'+sid):
                    name = st.text_input('会话名称', value=current['title'], max_chars=80)
                    if st.form_submit_button('保存名称'):
                        try:
                            api('PATCH', endpoint(sid), json={'title': name})
                            st.rerun()
                        except RuntimeError as exc:
                            st.error(str(exc))
                if st.button('取消置顶' if current['pinned'] else '置顶当前会话'):
                    api('PATCH', endpoint(sid), json={'pinned': not bool(current['pinned'])})
                    st.rerun()
                if st.button('删除当前会话'):
                    confirm_request(sid, 'session')
            if st.button('清空当前对话', use_container_width=True):
                api('POST', endpoint(sid, '/clear'))
                st.rerun()


def history_page(sessions):
    st.title('历史文件夹')
    st.caption('所有会话均保存在本机。打开历史会话可继续查看原文件、候选人和匹配结果。')
    query = st.text_input('搜索会话名称').strip().lower()
    items = [s for s in sessions if query in s['title'].lower()]
    if not items:
        st.info('暂无历史会话，请在侧边栏新建会话。')
    for item in items:
        with st.container(border=True):
            info, opened, pinned, deleted = st.columns([5, 1, 1, 1])
            info.markdown(('📌 ' if item['pinned'] else '') + item['title'])
            info.caption(f"创建于 {datetime.fromisoformat(item['created_at']).astimezone(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M} · {item['upload_count']} 份上传 · {item['candidate_count']} 名候选人")
            if opened.button('打开', key='history_open_'+item['id']):
                open_session(item['id'])
            if pinned.button('取消置顶' if item['pinned'] else '置顶', key='pin_'+item['id']):
                api('PATCH', endpoint(item['id']), json={'pinned': not bool(item['pinned'])})
                st.rerun()
            if deleted.button('删除', key='del_'+item['id']):
                confirm_request(item['id'], 'session')


def original_page(sid, uid):
    if st.button('← 返回会话'):
        st.session_state.detail = None
        st.rerun()
    records = api('GET', endpoint(sid, '/uploads'))['uploads']
    record = next((x for x in records if x['id'] == uid), None)
    if not record:
        st.info('该上传记录已被删除。')
        return
    st.subheader(record['filename'])
    st.caption('状态：' + record['status'])
    if record['error']:
        st.warning(record['error'])
    response = request('GET', endpoint(sid, f'/uploads/{uid}/original'))
    content = response.content
    st.download_button('下载原文件', content, file_name=record['filename'],
                       mime=response.headers.get('content-type', 'application/octet-stream'))
    suffix = Path(record['filename']).suffix.lower()
    if suffix == '.pdf':
        import fitz
        # 浏览器不支持内嵌PDF时也能按页查看；渲染原PDF而非模型重新生成。
        with fitz.open(stream=content, filetype='pdf') as document:
            if not len(document):
                st.info('PDF没有页面。')
                return
            page = st.number_input('页码', 1, len(document), 1, key='pdf_page_'+uid)
            pix = document[int(page)-1].get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            st.image(pix.tobytes('png'), use_container_width=True)
    elif suffix == '.txt':
        from charset_normalizer import from_bytes
        decoded = from_bytes(content).best()
        st.text(str(decoded) if decoded else '编码无法识别，请下载原文件查看。')
    elif suffix == '.docx':
        from docx import Document
        document = Document(BytesIO(content))
        st.caption('下方为原DOCX的文本和表格预览；要查看完整排版、图片和文本框，请下载原文件。')
        for paragraph in document.paragraphs:
            if paragraph.text:
                st.text(paragraph.text)
        for table in document.tables:
            st.dataframe(pd.DataFrame([[cell.text for cell in row.cells] for row in table.rows]),
                         hide_index=True, use_container_width=True)
    else:
        st.info('此格式无法在线预览，可以下载原文件。')


def uploads_page(sid):
    version_key = 'upload_version_'+sid
    version = st.session_state.setdefault(version_key, 0)
    files = st.file_uploader('上传简历（PDF、DOCX、TXT，每份不超过10MB）',
                             type=['pdf','docx','txt'], accept_multiple_files=True,
                             key=f'files_{sid}_{version}')
    if st.button('解析并入库', disabled=not files, type='primary'):
        errors = []
        progress = st.progress(0)
        for index, file in enumerate(files):
            with st.spinner('正在处理：' + file.name):
                try:
                    api('POST', endpoint(sid, '/uploads'),
                        files={'file': (file.name, file.getvalue(), file.type)}, timeout=75)
                except RuntimeError as exc:
                    errors.append(file.name + '：' + str(exc))
            progress.progress((index+1)/len(files))
        st.session_state[version_key] += 1
        st.session_state.notice = '\n'.join(errors) if errors else '本批次处理完成，详情请查看上传记录。'
        st.rerun()
    records = api('GET', endpoint(sid, '/uploads'))['uploads']
    if not records:
        st.info('尚未上传简历。上传后会在这里显示解析状态，并可查看原文件。')
        return
    st.caption('解析置信度为文本质量估计，不代表已验证的准确率。解析失败或超时的原文件仍然保留。')
    widths = [0.5, 2.5, 1.3, 0.8, 2, 1, 1.2]
    for col, title in zip(st.columns(widths), ['选择', '上传文件名', '入库状态', '置信度', '提示', '重试', '查看详情']):
        col.markdown('**'+title+'**')
    selected = []
    for item in records:
        cols = st.columns(widths, vertical_alignment='center')
        if cols[0].checkbox('选择', key='upload_select_'+item['id'], label_visibility='collapsed'):
            selected.append(item['id'])
        cols[1].write(item['filename'])
        cols[2].write(item['status'])
        cols[3].write(f"{item['confidence']:.1f}%")
        cols[4].write(item['error'] or '—')
        if item['status'] not in ('成功入库', '解析中'):
            if cols[5].button('重试', key='retry_'+item['id']):
                with st.spinner('重新解析中…'):
                    api('POST', endpoint(sid, f"/uploads/{item['id']}/retry"), timeout=75)
                st.rerun()
        if cols[6].button('查看原文件', key='original_'+item['id']):
            st.session_state.detail = ('original', item['id'])
            st.rerun()
        st.divider()
    if st.button('删除所选上传记录', disabled=not selected):
        confirm_request(sid, 'uploads', selected)


def job_page(session):
    sid = session['id']
    jd = st.text_area('招聘要求 JD', value=session['jd'], height=190, key='jd_'+sid)
    save, score = st.columns(2)
    if save.button('保存招聘要求'):
        api('PATCH', endpoint(sid), json={'jd': jd})
        st.session_state.notice = '招聘要求已保存。'
        st.rerun()
    if score.button('开始匹配本会话候选人', disabled=not jd.strip(), type='primary'):
        st.session_state.pop('match_'+sid, None)
        try:
            with st.spinner('正在解析招聘要求并评分…'):
                result = api('POST', endpoint(sid, '/score'), json={'jd': jd}, timeout=180)
            st.session_state['match_'+sid] = {'jd': jd, **result}
        except RuntimeError as exc:
            st.session_state['match_'+sid] = {'jd': jd, 'error': str(exc)}
    result = st.session_state.get('match_'+sid)
    if result:
        if result['jd'] != jd:
            st.info('下方是上一次JD的匹配反馈，请保存并重新匹配。')
        if result.get('error'):
            st.error(result['error'])
        else:
            rows = result['results']
            ok = sum(bool(x['success']) for x in rows)
            (st.success if ok == len(rows) and ok else st.warning)(f'成功评分 {ok}/{len(rows)} 名候选人。')
            for row in rows:
                if not row['success']:
                    st.error(row['candidate_code'] + '：' + row['error'])
            with st.expander('查看本次提取的条件'):
                st.json(result['requirements'])
    st.divider()
    with st.expander('招聘助理聊天', expanded=True):
        for message in api('GET', endpoint(sid, '/messages'))['messages']:
            with st.chat_message(message['role']):
                st.markdown(message['content'])
                if message.get('trace'):
                    with st.expander('工具执行记录'):
                        st.json(message['trace'])
        with st.form('chat_'+sid, clear_on_submit=True):
            message = st.text_input('输入筛选、比较或追问要求')
            send = st.form_submit_button('发送')
        if send and message.strip():
            # 将页面当前JD先保存，聊天和评分读取同一个版本。
            api('PATCH', endpoint(sid), json={'jd': jd})
            with st.spinner('助理正在处理…'):
                api('POST', endpoint(sid, '/chat'),
                    json={'message': message, 'hr_id': st.session_state.hr_id}, timeout=180)
            st.rerun()
        reports = api('GET', endpoint(sid, '/reports'))['reports']
        if reports:
            with st.expander('本会话生成的报告'):
                for filename in reports:
                    content = request('GET', endpoint(sid, '/reports/'+filename)).content
                    st.download_button('下载报告 '+filename[:8], content, file_name=filename,
                                       mime='text/markdown', key='report_'+filename)


def education(candidate):
    levels = {'博士':5,'硕士':4,'研究生':4,'本科':3,'学士':3,'大专':2,'专科':2,'高中':1}
    items = candidate.get('education', [])
    def level(item):
        return max([value for key, value in levels.items() if key in item.get('degree','')] or [0])
    best = max(items, key=level) if items else {}
    return best


def experience(candidate):
    facts = []
    if candidate.get('work_years'):
        facts.append(f"{candidate['work_years']}年工作经验")
    items = candidate.get('experiences', []) + candidate.get('projects', [])
    if items:
        item = items[0]
        facts += [item.get('role') or item.get('name') or item.get('organization') or '']
        facts += (item.get('actions', []) + item.get('results', []))[:2]
    return '；'.join(x for x in facts if x) or '信息缺失'


def candidate_page(sid):
    candidates = api('GET', endpoint(sid, '/candidates'))['candidates']
    if not candidates:
        st.info('本会话尚无成功入库的简历，请先前往“简历入库”上传。')
        return
    widths = [0.5, 0.6, 1, 0.8, 1.7, 0.9, 2.5, 1.2]
    for col, label in zip(st.columns(widths), ['选择','排名','姓名','匹配分','学校专业','最高学历','经历','操作']):
        col.markdown('**'+label+'**')
    selected = []
    rank = 0
    for candidate in candidates:
        code = candidate['candidate_code']
        cols = st.columns(widths, vertical_alignment='center')
        if cols[0].checkbox('选择', key='candidate_select_'+code, label_visibility='collapsed'):
            selected.append(candidate['upload_id'])
        value = candidate['match_score']
        if value is not None:
            rank += 1
        cols[1].write(rank if value is not None else '—')
        cols[2].write(candidate.get('masked_name') or '姓名未识别')
        cols[3].write(f'{value:.1f}' if value is not None else '未评分')
        edu = education(candidate)
        cols[4].write(' / '.join(x for x in (edu.get('school'), edu.get('major')) if x) or '信息缺失')
        cols[5].write(edu.get('degree') or '未知')
        cols[6].write(experience(candidate))
        if cols[7].button('查看详情', key='candidate_detail_'+code):
            st.session_state.detail = ('candidate', code)
            st.rerun()
        st.divider()
    if st.button('删除所选候选人及原文件', disabled=not selected):
        confirm_request(sid, 'uploads', selected)


def candidate_detail(sid, code):
    if st.button('← 返回候选人信息'):
        st.session_state.detail = None
        st.rerun()
    candidates = api('GET', endpoint(sid, '/candidates'))['candidates']
    candidate = next((x for x in candidates if x['candidate_code'] == code), None)
    if not candidate:
        st.info('当前会话中不存在该候选人。')
        return
    left, right = st.columns([6,4], gap='large')
    with left:
        st.subheader(candidate.get('masked_name') or '姓名未识别')
        st.write('电话：' + (candidate.get('masked_phone') or '未提供'))
        st.write('邮箱：' + (candidate.get('email') or '未提供'))
        if st.button('查看原文件'):
            st.session_state.detail = ('original', candidate['upload_id'])
            st.rerun()
        for title, key in [('教育经历','education'),('工作与实习','experiences'),('项目经历','projects'),('竞赛获奖','awards')]:
            st.subheader(title)
            items = candidate.get(key, [])
            if not items:
                st.caption('未提供')
            for item in items:
                header = ' · '.join(str(item.get(k,'')) for k in ('school','major','degree','organization','role','name','level') if item.get(k))
                st.write(header)
                dates = ' — '.join(str(item.get(k,'')) for k in ('start_date','end_date','date') if item.get(k))
                if dates:
                    st.caption(dates)
                for fact in item.get('actions', []) + item.get('results', []):
                    st.write('• ' + fact)
                if item.get('technologies'):
                    st.caption('技术：'+'、'.join(item['technologies']))
        st.subheader('技能与证书')
        st.write('、'.join(candidate.get('skills', []) + candidate.get('certificates', [])) or '未提供')
    with right:
        detail = candidate.get('score_detail', {})
        if not detail:
            st.info('当前JD尚未评分。')
            return
        st.metric('综合匹配分', detail['total_score'])
        dimensions = detail.get('dimensions', [])
        kind = st.radio('图表类型', ['雷达图', '柱状图'], horizontal=True)
        if dimensions:
            names = [x['name'] for x in dimensions]
            values = [x['score'] for x in dimensions]
            if kind == '雷达图':
                fig = go.Figure(go.Scatterpolar(r=values+[values[0]], theta=names+[names[0]],
                                               fill='toself', line_color='#82A5D9'))
                fig.update_layout(polar={'radialaxis': {'range':[0,100]}})
            else:
                fig = go.Figure(go.Bar(x=names, y=values, marker_color='#82A5D9'))
                fig.update_yaxes(range=[0,100])
            fig.update_layout(showlegend=False, margin=dict(l=35,r=35,t=20,b=20))
            st.plotly_chart(fig, use_container_width=True)
        for title, key in [('✅ 满足项','satisfied'),('⚠ 部分满足','partial'),('❌ 未匹配／待核实','unsatisfied')]:
            st.markdown('**'+title+'**')
            for value in detail.get(key, []) or ['无']:
                st.write(value)
        with st.expander('查看完整评分审计记录'):
            for line in detail.get('audit_log', []):
                st.write(line)


def main():
    for key, value in {'active_session':None, 'page':'workspace', 'detail':None, 'hr_id':'default_hr'}.items():
        st.session_state.setdefault(key, value)
    sessions = api('GET', '/sessions')['sessions']
    # 新浏览器先进入历史页；新建会话由用户明确点击，不悄悄加载旧候选人。
    if st.session_state.active_session not in [s['id'] for s in sessions]:
        st.session_state.active_session = None
        st.session_state.page = 'history'
    sidebar(sessions)
    delete_confirmation()
    notice = st.session_state.pop('notice', None)
    if notice:
        st.info(notice)
    if st.session_state.page == 'history' or not st.session_state.active_session:
        history_page(sessions)
        return
    sid = st.session_state.active_session
    session = api('GET', endpoint(sid))
    detail = st.session_state.get('detail')
    if detail:
        (original_page if detail[0] == 'original' else candidate_detail)(sid, detail[1])
        return
    st.title('智能简历筛选与招聘助理')
    st.caption('当前会话：' + session['title'])
    tabs = st.tabs(['简历入库','招聘要求','候选人信息'])
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
    st.error(f'页面处理失败：{type(exc).__name__}: {exc}')
