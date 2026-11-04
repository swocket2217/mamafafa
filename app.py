"""성적 페이스메이커 | 실행: streamlit run app.py
GitHub 저장소에 app.py와 requirements.txt를 올린 뒤 Streamlit Community Cloud에서
app.py를 진입 파일로 배포합니다. GitHub 자체는 Python 서버를 실행하지 않습니다.

중요: 학기 평균만으로 실제 9등급 석차등급을 복원할 수 없습니다.
이 앱의 9등급 참고 지표는 2*g-1이라는 임의의 선형 척도입니다.
공식 환산/백분위 추정/대학별 입시 산출 결과가 아닙니다.
목표는 이 참고 지표의 5개 학기 단순평균 기준입니다.
과목별 학점 가중치와 학기별 반영 비중, 대학별 환산은 반영하지 않습니다.
성적은 외부 DB나 파일에 저장하지 않으며 현재 Streamlit 세션에서만 유지됩니다.
"""
import math
import streamlit as st

TERMS = ['1학년 1학기', '1학년 2학기', '2학년 1학기', '2학년 2학기', '3학년 1학기']
MODES = [
    ('균등 페이스', '남은 학기를 같은 목표로', 0),
    ('꾸준히 상승', '시간이 갈수록 더 좋은 등급', -1),
    ('초반 스퍼트', '앞에서 집중하고 뒤는 여유롭게', 1),
]


def to_nine(grade):
    """실제 석차등급 환산이 아닌 선형 참고 지표."""
    return 2.0 * grade - 1.0


def roadmap(required_mean, count, direction):
    """각 목표를 [1,5]에 제한하면서 합계를 맞춥니다.
    5등급 척도 기준 최대 초/말 간격은 0.8입니다.
    이 간격은 학습효과 예측이 아니라 로드맵을 나누는 설계 규칙입니다.
    소수 둘째 자리 내림으로 표시된 목표를 달성해도 최종 목표보다 나빠지지 않게 합니다.
    """
    if count <= 0 or not 1.0 <= required_mean <= 5.0:
        raise ValueError('유효한 남은 학기와 평균이 필요합니다.')
    if count == 1:
        offsets = [0.0]
    else:
        offsets = [direction * 0.8 * (i / (count - 1) - 0.5) for i in range(count)]
    lo, hi = -10.0, 10.0
    for _ in range(100):
        center = (lo + hi) / 2
        values = [min(5.0, max(1.0, center + d)) for d in offsets]
        if sum(values) < required_mean * count:
            lo = center
        else:
            hi = center
    values = [min(5.0, max(1.0, (lo + hi) / 2 + d)) for d in offsets]
    return [max(1.0, min(5.0, math.floor((x + 1e-9) * 100) / 100)) for x in values]


def analyze(grades, target_nine):
    n = len(grades)
    if not 1 <= n <= 5 or any(not math.isfinite(g) or not 1 <= g <= 5 for g in grades):
        raise ValueError('학기 성적은 1.00~5.00 범위여야 합니다.')
    if not math.isfinite(target_nine) or not 1 <= target_nine <= 9:
        raise ValueError('목표는 1.00~9.00 범위여야 합니다.')
    remaining = 5 - n
    total = sum(grades)
    current = to_nine(total / n)
    if not remaining:
        return dict(current=current, remaining=0, status='finished', achieved=current <= target_nine + 1e-8)
    best = to_nine((total + remaining) / 5)
    worst = to_nine((total + 5 * remaining) / 5)
    needed = (5 * ((target_nine + 1) / 2) - total) / remaining
    if needed < 1 - 1e-8:
        return dict(current=current, remaining=remaining, status='impossible', best=best)
    # 이미 충분히 여유가 있으면 최저 성과(5.00)로도 목표 달성 가능합니다.
    needed = min(5.0, max(1.0, needed))
    plans = []
    for title, subtitle, direction in MODES:
        values = roadmap(needed, remaining, direction)
        final = to_nine((total + sum(values)) / 5)
        plans.append(dict(title=title, subtitle=subtitle, values=values, final=final))
    return dict(current=current, remaining=remaining, status='ok', plans=plans,
                needed=needed, best=best, relaxed=worst <= target_nine + 1e-8)


def main():
    st.set_page_config(page_title='성적 페이스메이커', page_icon='🎯', layout='centered', initial_sidebar_state='collapsed')
    st.markdown('''<style>
    .block-container{max-width:560px;padding:.5rem .65rem 1rem;}
    header[data-testid="stHeader"]{height:0;min-height:0;}
    [data-testid="stVerticalBlock"]{gap:.4rem;}
    [data-testid="stHorizontalBlock"]{gap:.4rem;}
    [data-testid="stNumberInput"] input{font-size:.92rem;}
    .hero{position:fixed;top:0;left:50%;transform:translateX(-50%);width:min(100%,560px);
      z-index:999;background:#102a43;color:#fff;padding:12px 16px;
      border-radius:0 0 18px 18px;box-sizing:border-box;box-shadow:0 3px 12px #0002;}
    .hero-title{font-size:12px;color:#c7d9ea;}
    .hero-value{font-size:25px;font-weight:800;line-height:1.3;}
    .hero-note{font-size:10px;color:#c7d9ea;}
    .spacer{height:94px;}
    .plan{border:1px solid #cbd5e1;border-radius:13px;padding:9px 11px;margin-bottom:6px;}
    .plan-head{display:flex;justify-content:space-between;gap:6px;font-weight:700;font-size:14px;}
    .plan-sub{font-size:10px;color:#64748b;margin:2px 0 5px;}
    .term-row{display:flex;gap:5px;}
    .term{flex:1;background:#eaf2fb;border-radius:8px;padding:5px 2px;text-align:center;color:#102a43;}
    .term-label{font-size:10px;}
    .term-grade{font-size:17px;font-weight:800;}
    .term-small{font-size:9px;}
    [data-testid="stCaptionContainer"] p{font-size:11px;line-height:1.35;}
    @media(prefers-color-scheme:dark){.plan{border-color:#64748b;}.plan-sub{color:#94a3b8;}}
    </style>''', unsafe_allow_html=True)

    # 미니 폼: 마지막 학기를 선택하면 그 학기까지의 입력 칸만 표시합니다.
    st.markdown('<div class="spacer"></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1.2, 1])
    with c1:
        last = st.selectbox('성적이 나온 마지막 학기', range(5), index=1, format_func=lambda i: TERMS[i])
    with c2:
        target = st.number_input('최종 목표 · 9등급 참고', min_value=1.0, max_value=9.0, value=3.0, step=.1, format='%.2f')
    grades = []
    # 휴대폰에서도 입력을 1~2줄로 압축합니다. 미입력 학기는 평균에 포함하지 않습니다.
    for start in range(0, last + 1, 3):
        cols = st.columns(min(3, last + 1 - start))
        for col, index in zip(cols, range(start, min(last + 1, start + 3))):
            with col:
                value = st.number_input(TERMS[index].replace('학년 ', '-').replace('학기', ''),
                                        min_value=1.0, max_value=5.0, value=None,
                                        step=.01, format='%.2f', placeholder='5등급 평균', key=f'grade_{index}')
                grades.append(value)
    st.caption('입력: 5등급제 학기 평균 · 목표: 9등급 참고 지표 · 낮을수록 좋은 성적')
    complete = all(g is not None for g in grades)
    if complete:
        result = analyze(grades, float(target))
        headline = f"{result['current']:.2f} <span style='font-size:13px'>/ 9 참고</span>"
        detail = f"현재 {sum(grades)/len(grades):.2f} / 5 · {len(grades)}개 학기 평균 · 목표 {target:.2f}"
    else:
        headline = '성적을 입력해 주세요'
        detail = '선택한 학기까지 모두 입력하면 즉시 계산합니다.'
    st.markdown(f'''<div class="hero"><div class="hero-title">🎯 성적 페이스메이커 · 현재 성적</div>
    <div class="hero-value">{headline}</div><div class="hero-note">{detail} · 공식 환산 아님</div></div>''', unsafe_allow_html=True)

    if not complete:
        st.info('빈 성적 칸을 채우면 3가지 로드맵이 여기에 표시됩니다.')
    elif result['status'] == 'finished':
        if result['achieved']:
            st.success(f"5개 학기가 모두 입력되었습니다. 최종 참고 지표 {result['current']:.2f} — 목표 달성!")
        else:
            st.warning(f"5개 학기가 모두 입력되었습니다. 최종 참고 지표 {result['current']:.2f} — 목표 미달. 남은 학기가 없습니다.")
    elif result['status'] == 'impossible':
        st.warning(f"현재 입력 기준으로 목표 달성이 어렵습니다. 남은 학기 모두 1.00 / 5를 받아도 최종 {result['best']:.2f} / 9(참고)입니다.")
    else:
        if result['relaxed']:
            st.caption('✓ 남은 학기를 모두 5.00으로 받아도 목표 이내입니다. 아래는 유지 가능한 상한선입니다.')
        else:
            st.caption(f"남은 {result['remaining']}학기 평균 {result['needed']:.2f} / 5 이하가 필요합니다. 아래 굵은 숫자는 5등급 기준 목표입니다.")
        future = TERMS[len(grades):]
        for plan in result['plans']:
            cells = ''.join(
                f'<div class="term"><div class="term-label">{term}</div>'
                f'<div class="term-grade">{value:.2f}</div>'
                f'<div class="term-small">9 참고 {to_nine(value):.2f}</div></div>'
                for term, value in zip(future, plan['values']))
            st.markdown(f'''<div class="plan"><div class="plan-head"><span>{plan['title']}</span>
            <span>최종 {plan['final']:.2f} / 9</span></div><div class="plan-sub">{plan['subtitle']}</div>
            <div class="term-row">{cells}</div></div>''', unsafe_allow_html=True)
        if result['remaining'] == 1 or result['needed'] <= 1.000001 or result['needed'] >= 4.999999:
            st.caption('※ 남은 학기가 1개이거나 등급 한계에 도달하면 모드별 목표가 같아질 수 있습니다.')
    with st.expander('계산 기준 · 꼭 확인하세요'):
        st.write('학기 평균만으로 개인의 석차·백분위 또는 실제 9등급을 알 수 없습니다. 이 앱은 입시용 환산기가 아니라 목표 관리 도구입니다.')
        st.code('9등급 참고 지표 = 2 × 5등급 평균 − 1\n남은 학기 필요 평균 = (목표의 5등급 환산값 × 5 − 완료 학기 성적 합계) ÷ 남은 학기 수', language=None)
        st.write('1→1, 2→3, 3→5, 4→7, 5→9의 선형 대응은 앱 설계상 가정이며, 등급별 인원 비율을 반영하지 않습니다. 대학이 인정하는 공식 환산표가 아닙니다.')
        st.write('1학년 1학기부터 3학년 1학기까지 총 5학기를 동일 비중으로 계산합니다. 성적이 나온 학기까지 빠짐없이 입력하세요. 과목별 이수학점·대학별 반영비율은 고려하지 않습니다.')
        st.write('상승·스퍼트 모드는 남은 학기 초·말 목표 차이를 최대 0.80(5등급 기준)으로 설정합니다. 목표를 1~5에 제한하고 합계가 맞도록 재조정합니다. 표시 목표는 0.01 단위 내림으로, 계산 목표보다 조금 엄격할 수 있습니다. 모든 목표는 해당 값 이하를 뜻합니다.')
        st.caption('단일 페이지·상단 고정 요약입니다. 작은 화면, 확대 글꼴, 키보드 표시 상태에서는 스크롤이 필요할 수 있습니다. 입력값은 영구 저장되지 않습니다.')


if __name__ == '__main__':
    main()
