/* 기간 선택 공통 모듈
 *
 * 화면마다 '전체 누적'만 보여주면 데이터가 쌓일수록 최근 상황이 묻힌다.
 * (작년에 잘했으면 이번 달이 엉망이어도 지표가 좋아 보인다)
 * 그래서 기간을 잘라 볼 수 있게 한다.
 *
 * ⚠️ 기준일은 '오늘'이 아니라 데이터의 마지막 날짜다.
 *    더미데이터의 시간축(2025-07 ~ 2026-02)과 실제 오늘이 달라
 *    오늘 기준으로 자르면 전 구간이 0건이 된다.
 *
 * 두 가지 방식을 지원한다.
 *   화면 계산 : 거래 원본이 이미 화면에 있는 경우 (입출고 이력, 생산 실적)
 *   서버 계산 : 서버에서 집계해 내려오는 경우 (협력사, 사용자, ABC)
 *               → ?period=m3 을 붙여 다시 불러온다
 */
(function (global) {
  'use strict';

  // [개월수, 표시명] — 화면마다 같은 구분을 쓴다
  var PERIODS = [
    [12, '최근 1년'],
    [6, '최근 6개월'],
    [3, '최근 3개월 (분기)'],
    [1, '최근 1개월']
  ];

  function pad(n) { return String(n).length < 2 ? '0' + n : String(n); }

  /* 기준일에서 N개월 전.
     toISOString() 은 UTC 로 바꿔 한국 시간대에서 하루 밀리므로 쓰지 않는다. */
  function monthsBefore(base, n) {
    var d = new Date(base + 'T00:00:00');
    var day = d.getDate();
    d.setDate(1);
    d.setMonth(d.getMonth() - n);
    // 말일 보정 — 3/31 의 1개월 전은 2/28 이어야지 3/3 이 되면 안 된다
    var last = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
    d.setDate(Math.min(day, last));
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  /* 선택값(v)이 가리키는 시작일. 전체/특정월이면 null */
  function from(v, base) {
    if (!v || v.charAt(0) !== 'm') return null;
    return monthsBefore(base, parseInt(v.slice(1), 10));
  }

  /* 날짜가 선택 기간에 드는가.
     v = ''(전체) | 'm3'(최근 3개월) | '2025-09'(특정 월) */
  function has(date, v, base) {
    if (!v) return true;
    date = date || '';
    if (v.charAt(0) === 'm') {
      var f = from(v, base);
      return date >= f && date <= base;
    }
    return date.slice(0, 7) === v;
  }

  /* 지금 보고 있는 구간을 사람이 읽는 문장으로 */
  function label(v, base) {
    if (!v) return '전체 기간';
    if (v.charAt(0) === 'm') {
      var n = parseInt(v.slice(1), 10), name = v;
      for (var i = 0; i < PERIODS.length; i++) {
        if (PERIODS[i][0] === n) { name = PERIODS[i][1]; break; }
      }
      return name + ' (' + from(v, base) + ' ~ ' + base + ')';
    }
    return v + ' 한 달';
  }

  /* <select> 채우기.
     months 를 주면 '특정 월' 목록을 구분선 뒤에 붙인다. */
  function fill(el, months, selected) {
    var h = '<option value="">전체 기간</option>';
    PERIODS.forEach(function (p) {
      h += '<option value="m' + p[0] + '">' + p[1] + '</option>';
    });
    if (months && months.length) {
      h += '<option disabled>──── 특정 월 ────</option>';
      months.slice().reverse().forEach(function (m) {
        var ym = typeof m === 'string' ? m : m.ym;
        h += '<option value="' + ym + '">' + ym + '</option>';
      });
    }
    el.innerHTML = h;
    if (selected) el.value = selected;
  }

  /* 서버 계산 방식 — 주소에 ?period= 를 붙여 다시 불러온다 */
  function reloadWith(v) {
    var u = new URL(global.location.href);
    if (v) u.searchParams.set('period', v);
    else u.searchParams.delete('period');
    global.location.href = u.toString();
  }
  function current() {
    return new URL(global.location.href).searchParams.get('period') || '';
  }

  /* 백분율 — 서버(db._pct)와 같은 0.5 올림을 쓴다.
     파이썬 round() 는 은행가 반올림이라 96.25 가 96.2 가 되어 값이 갈린다. */
  function pct(part, whole, nd) {
    if (!whole) return null;
    var f = Math.pow(10, nd == null ? 1 : nd);
    return Math.floor(part / whole * 100 * f + 0.5) / f;
  }

  global.Period = {
    PERIODS: PERIODS,
    monthsBefore: monthsBefore,
    from: from,
    has: has,
    label: label,
    fill: fill,
    reloadWith: reloadWith,
    current: current,
    pct: pct
  };
})(window);
