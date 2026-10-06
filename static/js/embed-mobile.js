/* embed 화면 공용 — 폰에서만 쓰는 작은 동작 두 가지.
   화면별 스크립트와 겹치지 않도록 전역 이름을 만들지 않는다. */
(function () {
  'use strict';

  /* ① 설명 상자(.ro) 펼치기
     폰에서 설명이 150px 를 먹어 목록이 밀려났다. CSS 가 두 줄만 남기고
     '더보기' 를 붙이므로, 여기서는 탭하면 펼쳐 주기만 하면 된다.
     화면이 넓으면 CSS 가 애초에 자르지 않으니 아무 일도 하지 않는다. */
  document.addEventListener('click', function (e) {
    var ro = e.target.closest && e.target.closest('.ro');
    if (!ro) return;
    if (window.innerWidth > 760) return;
    // 설명 안의 링크·버튼을 누른 것이면 그쪽이 먼저다
    if (e.target.closest('a, button, input, select')) return;
    ro.classList.toggle('open');
  });

  /* ② 스크롤되는 목록에 '아직 더 있다' 는 표시
     고정 패널과 목록이 붙어 있어 어디가 움직이는 곳인지 몰랐다.
     끝에 닿지 않은 동안만 아래쪽 그림자를 진하게 둔다. */
  function mark(el) {
    var more = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
    el.classList.toggle('has-more', more);
  }
  /* 지표 칸은 폰에서 가로로 민다 — 오른쪽에 더 있으면 끝을 흐린다 */
  function markX(el) {
    el.classList.toggle('has-more-x',
      el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
  }

  /* ③ 긴 목록 표를 카드로 바꿀 수 있게 열 이름을 심는다
     CSS 가 `td::before{content:var(--cN)}` 로 꺼내 쓴다. 변수는 <table> 에
     한 번만 심으면 되고, 목록이 tbody 만 다시 그려도 살아남는다. */
  function labelTable(t) {
    // 상세·모달 표는 클래스를 달고 있다. 머리(thead)가 있는 목록 표만 바꾼다
    if (t.className) return;
    var ths = t.querySelectorAll('thead th');
    if (!ths.length || ths.length > 14) return;

    var sig = '';
    for (var i = 0; i < ths.length; i++) sig += ths[i].textContent + '|';
    if (t.dataset.cardSig !== sig) {               // 머리가 그대로면 다시 안 심는다
      for (var j = 0; j < ths.length; j++) {
        var txt = (ths[j].textContent || '').replace(/\s+/g, ' ').trim();
        t.style.setProperty('--c' + (j + 1), JSON.stringify(txt));
      }
      t.dataset.cardSig = sig;
    }

    /* ⚠️ 들어가는 표까지 카드로 만들면 안 된다. 5열짜리 순위표는 375px 에
       이미 들어가는데, 카드로 바꾸면 **되레 세 배 길어진다.**
       그래서 '담는 칸을 넘는가' 를 직접 재서 넘치는 표만 바꾼다.
       한 번 바꾸면 폭이 줄어 다시 잴 수 없으므로 판정은 한 번만 한다. */
    if (window.innerWidth > 760) return;
    if (t.dataset.cardify || t.dataset.cardFits) return;
    var box = t.parentElement;
    if (t.scrollWidth > box.clientWidth + 4) t.dataset.cardify = '1';
    else t.dataset.cardFits = '1';
  }

  var SEL = '.listwrap,.tblwrap,.slist,.sheet,.po-body,.fglist,.left,.mlist';
  function watch() {
    document.querySelectorAll('table').forEach(labelTable);
    document.querySelectorAll('.kpi-row').forEach(function (el) {
      if (!el.dataset.xWatched) {
        el.dataset.xWatched = '1';
        el.addEventListener('scroll', function () { markX(el); }, { passive: true });
      }
      markX(el);
    });
    document.querySelectorAll(SEL).forEach(function (el) {
      if (el.dataset.scrollWatched) { mark(el); return; }
      el.dataset.scrollWatched = '1';
      el.addEventListener('scroll', function () { mark(el); }, { passive: true });
      mark(el);
    });
  }
  document.addEventListener('DOMContentLoaded', watch);
  // 폰을 가로로 돌리면 들어가던 표가 안 들어가기도 하고 그 반대도 된다
  window.addEventListener('resize', function () {
    document.querySelectorAll('table[data-card-fits]').forEach(function (t) {
      delete t.dataset.cardFits;
    });
    watch();
  });
  // 목록은 다시 그려지므로 내용이 바뀔 때마다 다시 본다
  if (window.MutationObserver) {
    new MutationObserver(watch).observe(document.documentElement,
      { childList: true, subtree: true });
  }
})();
