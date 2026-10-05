# -*- coding: utf-8 -*-
"""Code 128 바코드를 SVG 로 그린다.

발주번호를 종이에 찍어 창고에서 스캔하려면 바코드가 필요하다.
외부 라이브러리(python-barcode, Pillow)를 쓰면 의존성이 둘 늘고 PNG 를 만들려면
폰트까지 따라온다. Code 128 은 표 하나와 체크섬 한 줄이면 끝이라 직접 그린다.

왜 Code 128 인가
  · 영문 대문자 + 숫자를 그대로 담는다. 발주번호가 `PO` + 12자리라 딱 맞는다
  · 거의 모든 1D 스캐너가 기본으로 읽는다 (설정 없이)
  · 자릿수 제한이 없다

왜 SVG 인가
  · 해상도에 상관없이 인쇄된다. 바코드는 선 굵기가 생명이라 PNG 확대는 못 쓴다
  · 파이썬 기본만으로 만들 수 있다
"""

# Code 128 패턴표 — 값 0~106. 각 패턴은 '바-공백-바-공백-바-공백' 폭(모듈 수).
# 106(정지 문자)만 7자리다.
_PATTERNS = (
    "212222222122222221121223121322131222122213122312132212221213"
    "221312231212112232122132122231113222123122123221223211221132"
    "221231213212223112312131311222321122321221312212322112322211"
    "212123212321232121111323131123131321112313132113132311211313"
    "231113231311112133112331132131113123113321133121313121211331"
    "231131213113213311213131311123311321331121312113312311332111"
    "314111221411431111111224111422121124121421141122141221112214"
    "112412122114122411142112142211241211221114413111241112134111"
    "111242121142121241114212124112124211411212421112421211212141"
    "214121412121111143111341131141114113114311411113411311113141"
    "114131311141411131211412211214211232"
)
STOP = "2331112"

START_B = 104          # 영문 대소문자 + 숫자 + 기호
_QUIET = 10            # 좌우 여백(모듈). 없으면 스캐너가 시작을 못 잡는다


def _pattern(v):
    return STOP if v == 106 else _PATTERNS[v * 6:v * 6 + 6]


def encode(text):
    """문자열 → 모듈 폭 목록. 홀수 번째가 바, 짝수 번째가 공백이다."""
    vals = [START_B]
    for ch in text:
        o = ord(ch)
        if not (32 <= o <= 126):
            raise ValueError("Code 128 로 담을 수 없는 글자입니다: %r" % ch)
        vals.append(o - 32)
    # 체크섬 — 시작값 + (자리번호 x 값) 의 합을 103 으로 나눈 나머지
    chk = vals[0]
    for i, v in enumerate(vals[1:], start=1):
        chk += i * v
    vals.append(chk % 103)
    vals.append(106)                      # 정지
    out = []
    for v in vals:
        out.extend(int(c) for c in _pattern(v))
    return out


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def svg(text, height=52, module=2, show_text=True, quiet=_QUIET):
    """바코드 SVG 한 장.

    module  모듈(가장 가는 바) 하나의 폭(px). 2 이하면 싸구려 스캐너가 놓친다
    height  바 높이. 글자를 넣으면 그 아래 12px 가 더 붙는다
    """
    text = str(text or "").strip().upper()
    if not text:
        raise ValueError("바코드로 만들 값이 없습니다.")
    if len(text) > 48:
        raise ValueError("값이 너무 깁니다(48자 이내).")

    widths = encode(text)
    bar_w = sum(widths)
    total_w = (bar_w + quiet * 2) * module
    cap = 13 if show_text else 0
    total_h = height + cap

    bars, x, dark = [], quiet, True
    for w in widths:
        if dark and w:
            bars.append('<rect x="%g" y="0" width="%g" height="%g"/>'
                        % (x * module, w * module, height))
        x += w
        dark = not dark

    label = ""
    if show_text:
        label = ('<text x="%g" y="%g" text-anchor="middle" fill="#1A1917"'
                 ' font-family="ui-monospace,SFMono-Regular,Menlo,monospace"'
                 ' font-size="11" letter-spacing="1.5">%s</text>'
                 % (total_w / 2.0, height + 10, _esc(text)))

    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g"'
            ' viewBox="0 0 %g %g" role="img" aria-label="%s">'
            '<rect width="100%%" height="100%%" fill="#fff"/>'
            '<g fill="#1A1917">%s</g>%s</svg>'
            % (total_w, total_h, total_w, total_h, _esc(text),
               "".join(bars), label))
