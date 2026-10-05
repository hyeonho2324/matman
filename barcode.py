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
START_C = 105          # 숫자 두 자리를 한 글자에 담는다
TO_C = 99              # B → C 전환
TO_B = 100             # C → B 전환
_QUIET = 10            # 좌우 여백(모듈). 없으면 스캐너가 시작을 못 잡는다


def _pattern(v):
    return STOP if v == 106 else _PATTERNS[v * 6:v * 6 + 6]


def _digits(text, i):
    """i 부터 이어지는 숫자 개수."""
    n = 0
    while i + n < len(text) and text[i + n].isdigit():
        n += 1
    return n


def symbols(text):
    """문자열 → Code 128 심벌 값 목록 (시작·전환·체크섬·정지 포함).

    ⚠️ 숫자가 이어지면 Code C 로 넘어간다 — **두 자리를 한 글자에** 담는다.
    발주번호(`PO` + 12자리)는 이것만으로 189 → 134 모듈로 줄어,
    같은 폭에 넣으면 바가 **40% 굵어진다.** 바가 굵을수록 잘 읽힌다.
    """
    for ch in text:
        if not (32 <= ord(ch) <= 126):
            raise ValueError("Code 128 로 담을 수 없는 글자입니다: %r" % ch)

    # 처음부터 숫자가 길게 이어지면 C 로 시작한다 (전환 문자 한 칸을 아낀다)
    run0 = _digits(text, 0)
    mode = "C" if (run0 >= 4 and run0 % 2 == 0) else "B"
    vals = [START_C if mode == "C" else START_B]

    i = 0
    while i < len(text):
        run = _digits(text, i)
        if mode == "C":
            if run >= 2:
                vals.append(int(text[i:i + 2]))
                i += 2
            else:
                vals.append(TO_B)
                mode = "B"
        else:
            # 짝수 자리만 C 로 보낼 수 있다. 홀수면 한 자리를 B 로 먼저 털어낸다
            usable = run - (run % 2)
            if usable >= 6 or (usable >= 4 and i + run == len(text)):
                if run % 2:
                    vals.append(ord(text[i]) - 32)
                    i += 1
                vals.append(TO_C)
                mode = "C"
                continue
            vals.append(ord(text[i]) - 32)
            i += 1

    # 체크섬 — 시작값 + (자리번호 x 값) 의 합을 103 으로 나눈 나머지
    chk = vals[0]
    for n, v in enumerate(vals[1:], start=1):
        chk += n * v
    vals.append(chk % 103)
    vals.append(106)                      # 정지
    return vals


def encode(text):
    """문자열 → 모듈 폭 목록. 홀수 번째가 바, 짝수 번째가 공백이다."""
    out = []
    for v in symbols(text):
        out.extend(int(c) for c in _pattern(v))
    return out


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# 좁은 바 최소 폭(mm). 이보다 얇으면 보통 스캐너가 놓친다.
# GS1 일반 유통 권고가 0.25mm, 절대 하한이 0.19mm 쯤이다.
MIN_X = 0.19
WARN_X = 0.25


def svg(text, mm=60, bar_mm=12, show_text=True, quiet=_QUIET):
    """바코드 SVG 한 장. **크기를 mm 로 지정한다.**

    mm      바코드 전체 가로 폭(여백 포함). 인쇄하면 정확히 이 크기로 나온다
    bar_mm  바 높이

    ⚠️ px 로 지정하면 인쇄 크기를 알 수 없다 — 브라우저가 96dpi 로 환산하는데
       그건 화면 기준이라 종이에서 몇 cm 가 될지 계산해야 한다.
       실제로 module=2px 가 **11cm** 로 인쇄돼 한 번에 안 찍히는 문제가 있었다.
       SVG 의 width 를 mm 로 주면 종이에서 그 크기 그대로 나온다.
    """
    text = str(text or "").strip().upper()
    if not text:
        raise ValueError("바코드로 만들 값이 없습니다.")
    if len(text) > 48:
        raise ValueError("값이 너무 깁니다(48자 이내).")
    mm = float(mm)
    bar_mm = float(bar_mm)

    widths = encode(text)
    units = sum(widths) + quiet * 2          # 전체 모듈 수
    x_dim = mm / units                        # 모듈 하나가 몇 mm 인가
    if x_dim < MIN_X:
        raise ValueError(
            "폭 %.0fmm 로는 바가 너무 얇아 못 읽습니다(%.3fmm). "
            "%.0fmm 이상으로 하세요." % (mm, x_dim, units * MIN_X))

    # 좌표는 모듈 단위로 두고, 바깥 크기만 mm 로 준다 — 비율이 정확히 유지된다
    cap_u = (4.0 / x_dim) if show_text else 0      # 글자 영역 4mm
    bar_u = bar_mm / x_dim
    total_u = bar_u + cap_u
    total_mm = bar_mm + (4.0 if show_text else 0)

    bars, x, dark = [], quiet, True
    for w in widths:
        if dark and w:
            bars.append('<rect x="%.2f" y="0" width="%.2f" height="%.2f"/>'
                        % (x, w, bar_u))
        x += w
        dark = not dark

    label = ""
    if show_text:
        label = ('<text x="%.2f" y="%.2f" text-anchor="middle" fill="#1A1917"'
                 ' font-family="ui-monospace,SFMono-Regular,Menlo,monospace"'
                 ' font-size="%.2f" letter-spacing="%.2f">%s</text>'
                 % (units / 2.0, bar_u + cap_u * 0.78,
                    cap_u * 0.62, cap_u * 0.09, _esc(text)))

    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%.2fmm" height="%.2fmm"'
            ' viewBox="0 0 %.2f %.2f" role="img" aria-label="%s">'
            '<rect width="100%%" height="100%%" fill="#fff"/>'
            '<g fill="#1A1917">%s</g>%s</svg>'
            % (mm, total_mm, units, total_u, _esc(text),
               "".join(bars), label))


def spec(text, mm=60, quiet=_QUIET):
    """이 값을 이 폭으로 그리면 좁은 바가 몇 mm 가 되는지. 크기를 정할 때 쓴다."""
    units = sum(encode(text)) + quiet * 2
    x = float(mm) / units
    return {"units": units, "x_dim": round(x, 4), "mm": float(mm),
            "ok": x >= MIN_X, "comfortable": x >= WARN_X,
            "min_mm": round(units * MIN_X, 1)}
