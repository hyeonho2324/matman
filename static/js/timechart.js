/* 시계열 차트 공통 모듈
 *
 * 월별 막대를 '데이터 전 구간'에 대해 그리면 연차가 쌓일수록 못 읽는다.
 * 네 화면(입출고 이력·구매 발주·생산 실적·수요 예측)이 전부 같은 코드였다.
 *
 *     폭 1000px 고정 · gw = 1000 / n · 모든 구간에 라벨
 *       8개월(현재)  막대 44px   정상
 *      36개월(3년)   막대 11px   라벨 '25-07'(약 29px)이 겹치기 시작
 *      60개월(5년)   막대  7px   라벨 뭉갬
 *     120개월(10년)  막대  3px   실 수준
 *
 * 그래서 구간이 늘면 '보는 단위'를 올린다 — 월 → 분기 → 연.
 * 막대 수를 12~36 사이로 유지한다. 사람이 한눈에 비교할 수 있는 범위다.
 *
 * ⚠️ 합산은 단순 덧셈이다. 월별 '고유 담당자 수' 처럼 DISTINCT 로 센 값은
 *    분기로 합치면 중복 계산된다. 차트가 그리는 값(수량·건수·금액)은 전부
 *    더해도 되는 값이라 괜찮지만, 새 필드를 그릴 때는 확인할 것.
 */
(function (global) {
  'use strict';

  // 구간 수가 이 이하이면 그 단위를 쓴다. 넘으면 한 단계 올린다
  var UNITS = [
    { key: 'month',   upto: 36,       label: '월별',   short: '월' },
    { key: 'quarter', upto: 96,       label: '분기별', short: '분기' },
    { key: 'year',    upto: Infinity, label: '연별',   short: '연' }
  ];

  // 라벨 솎는 간격은 '보기 좋은 수' 로 맞춘다.
  // 월을 5칸마다 찍으면 분기·반기와 어긋나 읽기 나쁘다
  var NICE = { month: [1, 2, 3, 6, 12], quarter: [1, 2, 4, 8], year: [1, 2, 5, 10] };

  function autoUnit(n) {
    for (var i = 0; i < UNITS.length; i++) if (n <= UNITS[i].upto) return UNITS[i].key;
    return 'year';
  }

  function unitOf(key) {
    for (var i = 0; i < UNITS.length; i++) if (UNITS[i].key === key) return UNITS[i];
    return UNITS[0];
  }

  // '2025-07' → 월 '2025-07' · 분기 '2025-Q3' · 연 '2025'
  function keyOf(ym, unit) {
    var y = ym.slice(0, 4), m = parseInt(ym.slice(5, 7), 10);
    if (unit === 'year') return y;
    if (unit === 'quarter') return y + '-Q' + (Math.floor((m - 1) / 3) + 1);
    return ym;
  }

  // 축에 적는 짧은 라벨
  function labelOf(key, unit) {
    if (unit === 'year') return key;                       // 2025
    if (unit === 'quarter') return key.slice(2);           // 25-Q3
    return key.slice(2);                                   // 25-07
  }

  // 마우스를 올렸을 때 보여줄 전체 이름
  function fullOf(key, unit) {
    if (unit === 'year') return key + '년';
    if (unit === 'quarter') return key.slice(0, 4) + '년 ' + key.slice(6) + '분기';
    return key.slice(0, 4) + '년 ' + parseInt(key.slice(5, 7), 10) + '월';
  }

  function yearOf(key) { return key.slice(0, 4); }

  // 한 해의 첫 구간인가 — 연 구분선을 그을 자리
  function isYearHead(key, unit) {
    if (unit === 'year') return true;
    if (unit === 'quarter') return key.slice(6) === '1';
    return key.slice(5, 7) === '01';
  }

  /* 월별 행을 단위에 맞춰 합친다.
   * rows: [{ym:'2025-07', 수치필드…}, …]  (ym 오름차순이 아니어도 된다) */
  function bucket(rows, unit) {
    var order = [], byKey = {};
    (rows || []).slice().sort(function (a, b) {
      return a.ym < b.ym ? -1 : (a.ym > b.ym ? 1 : 0);
    }).forEach(function (r) {
      var k = keyOf(r.ym, unit), t = byKey[k];
      if (!t) {
        t = byKey[k] = { key: k, label: labelOf(k, unit), full: fullOf(k, unit),
                         year: yearOf(k), yearHead: isYearHead(k, unit), months: 0 };
        order.push(t);
      }
      t.months += 1;
      for (var f in r) {
        if (f === 'ym') continue;
        if (typeof r[f] === 'number') t[f] = (t[f] || 0) + r[f];
      }
    });
    return order;
  }

  /* 라벨을 몇 칸마다 그릴지. 한 칸에 글자가 안 들어가면 솎는다. */
  function labelStep(n, width, unit, charPx) {
    if (!n) return 1;
    var gw = width / n;
    var need = (unit === 'year' ? 4 : 5) * (charPx || 5.6) + 8;   // 글자폭 + 여백
    var raw = Math.ceil(need / gw);
    var nice = NICE[unit] || [1];
    for (var i = 0; i < nice.length; i++) if (nice[i] >= raw) return nice[i];
    return nice[nice.length - 1] * Math.ceil(raw / nice[nice.length - 1]);
  }

  /* 차트가 쓸 것을 한 번에 돌려준다.
   *   prep(월별행, {width: 1000, unit: '자동이 아니면 month|quarter|year'}) */
  function prep(rows, opt) {
    opt = opt || {};
    var months = (rows || []).length;
    var unit = opt.unit && opt.unit !== 'auto' ? opt.unit : autoUnit(months);
    var out = bucket(rows, unit);
    var width = opt.width || 1000;
    return {
      unit: unit,
      unitLabel: unitOf(unit).label,
      auto: !opt.unit || opt.unit === 'auto',
      rows: out,
      n: out.length,
      months: months,
      step: labelStep(out.length, width, unit, opt.charPx),
      // 데이터가 짧으면 더 큰 단위는 의미가 없다 (8개월 → 연 1칸)
      choices: UNITS.filter(function (u) {
        return u.key === 'month' || bucket(rows, u.key).length >= 3;
      }).map(function (u) { return { key: u.key, label: u.label, short: u.short }; })
    };
  }

  /* 단위 전환 버튼. 고를 게 하나뿐이면 아무것도 그리지 않는다 */
  function picker(info, cur, onPick) {
    if (!info.choices || info.choices.length < 2) return '';
    return '<span class="tc-unit">' + info.choices.map(function (c) {
      var on = (cur === c.key) || (!cur || cur === 'auto') && c.key === info.unit;
      return '<button type="button" class="tc-u' + (on ? ' on' : '') + '"'
           + ' onclick="' + onPick + '(\'' + c.key + '\')">' + c.short + '</button>';
    }).join('') + '</span>';
  }

  /* 연이 바뀌는 자리의 옅은 세로선 + 연도. 여러 해가 보일 때만 */
  function yearMarks(info, width, top, bottom) {
    var years = {}, out = '';
    info.rows.forEach(function (r) { years[r.year] = 1; });
    if (Object.keys(years).length < 2 || info.unit === 'year') return '';
    var gw = width / info.n;
    info.rows.forEach(function (r, i) {
      if (!r.yearHead || i === 0) return;
      var x = (gw * i).toFixed(1);
      out += '<line x1="' + x + '" y1="' + top + '" x2="' + x + '" y2="' + bottom + '"'
           + ' stroke="#D4D3CE" stroke-width="1" stroke-dasharray="2 2" opacity=".7"/>'
           + '<text x="' + (gw * i + 3).toFixed(1) + '" y="' + (top + 9) + '" font-size="8.5"'
           + ' fill="#B8B7B2">' + r.year + '</text>';
    });
    return out;
  }

  global.TimeChart = {
    prep: prep, bucket: bucket, picker: picker, yearMarks: yearMarks,
    labelStep: labelStep, autoUnit: autoUnit, keyOf: keyOf
  };
})(window);
