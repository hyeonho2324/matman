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

  var SEL = '.listwrap,.tblwrap,.slist,.sheet,.po-body,.fglist,.left,.mlist';
  function watch() {
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
  // 목록은 다시 그려지므로 내용이 바뀔 때마다 다시 본다
  if (window.MutationObserver) {
    new MutationObserver(watch).observe(document.documentElement,
      { childList: true, subtree: true });
  }
})();
