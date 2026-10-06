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
    /* ⚠️ A4 발주서·실사표는 **일부러** 210mm 를 지켜 가로로 넘겨 본다
       (화면에서 줄이면 칸이 눌려 실제 종이와 달라 보인다). 카드로 바꾸면 안 된다. */
    if (t.closest('.shv, #poBody')) return;
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


  /* ④ 상세 패널을 아래에서 올라오는 시트로 (폰 전용)

     좌우 2단 화면 17개가 폰에서는 위아래로 쌓여 높이를 다퉜다. 시트로
     띄우면 목록은 목록대로 다 쓰고, 상세는 86vh 까지 올라와 제 내용을
     스스로 스크롤한다.

     ⚠️ 닫기 버튼을 시트 **안에** 넣으면 안 된다. 화면들이 상세를
        innerHTML 로 통째로 다시 그리기 때문에 매번 지워진다. 밖에 둔다. */
  var bd, xbtn;

  /* 상세 패널은 **`.main` 의 둘째(마지막) 칸** 이다.
     클래스 이름으로는 못 가린다 — `.side` 가 재고 실사·피킹에서는
     **목록**이고 다른 화면에서는 상세다. 2단 화면은 예외 없이
     [목록, 상세] 둘이라 순서가 가장 믿을 만하다.
     `.panel`(발주 시뮬 조작)·`.hist`(스캐너 이력)는 눌러서 여는 상세가
     아니라 늘 보여야 하는 칸이라 뺀다. */
  function sheetEl() {
    var main = document.querySelector('.main');
    if (!main || main.children.length < 2) return null;
    var last = main.children[main.children.length - 1];
    return last.matches('.panel, .hist') ? null : last;
  }
  function inSheet(el) {
    var sh = sheetEl();
    return !!sh && !!el && (el === sh || sh.contains(el));
  }

  /* 화면 위에 뜨는 창들(LOT 이력·단가 이력·발주서·실사표·확인 패널…).
     전부 position:fixed + inset:0 이고 클래스가 다섯 가지뿐이다
     (reg14 가 embed 전수를 훑어 이 목록이 빠짐없는지 검사한다). */
  var MODAL_SEL = '.ovl, .hv, .po-ovl, .pvl, .shv, .camov';

  function modalOpen() {
    var els = document.querySelectorAll(MODAL_SEL);
    for (var i = 0; i < els.length; i++) {
      var cs = getComputedStyle(els[i]);
      if (cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0
          && els[i].getBoundingClientRect().height > 40) return true;
    }
    return false;
  }

  /* 고를 것을 아직 안 골랐으면 화면이 '.empty' 안내를 그려 둔다 */
  function sheetHasContent(el) {
    return !!el && !el.querySelector('.empty') && el.textContent.trim().length > 0;
  }

  function sheetSetup() {
    if (bd) return;
    bd = document.createElement('div');
    bd.className = 'sheet-bd';
    bd.addEventListener('click', function () { sheetClose(); });
    document.body.appendChild(bd);

    xbtn = document.createElement('button');
    xbtn.className = 'sheet-x';
    xbtn.type = 'button';
    xbtn.innerHTML = '<i class="ti ti-chevron-down"></i>닫기';
    xbtn.addEventListener('click', function (e) { e.stopPropagation(); sheetClose(); });
    document.body.appendChild(xbtn);
  }

  function sheetOpen() {
    var el = sheetEl();
    if (!el || window.innerWidth > 760 || !sheetHasContent(el)) return;
    /* ⚠️ 모달이 떠 있으면 올리지 않는다. 품번(파란 글자)을 누르면 LOT 이력
       창이 뜨면서 **상세 패널도 같이 갱신**되는데, 그 내용 변화를 보고
       시트가 모달 뒤에서 함께 올라왔다. 모달 안에서 LOT 을 눌러도 같다. */
    if (modalOpen()) return;
    sheetSetup();
    el.classList.add('sheet-on');
    bd.classList.add('on');
    xbtn.classList.add('on');
    document.body.classList.add('sheet-open');
    // 닫기 버튼을 시트 머리 위에 올린다
    var top = el.getBoundingClientRect().top;
    xbtn.style.top = Math.max(8, Math.round(top) - 34) + 'px';
    el.scrollTop = 0;
  }

  /* 칸 머리를 누르면 그 칸만 펴진다. 상세는 고를 때마다 통째로 다시
     그려지므로 **접힌 상태가 기본값**이 된다 — 따로 되돌릴 일이 없다. */
  document.addEventListener('click', function (e) {
    if (window.innerWidth > 760) return;
    var head = e.target.closest && e.target.closest('.pnh');
    if (!head || !inSheet(head)) return;
    var card = head.parentElement;
    if (!card || !card.classList.contains('pn')) return;
    card.classList.toggle('fold-on');
  });

  function sheetClose() {
    var el = sheetEl();
    if (el) el.classList.remove('sheet-on');
    if (bd) bd.classList.remove('on');
    if (xbtn) xbtn.classList.remove('on');
    document.body.classList.remove('sheet-open');
  }

  /* 언제 올리나.

     처음에는 '목록 안을 눌렀으면' 으로 판정했는데 두 번 걸렸다.

     ① 줄의 onclick="pick(...)" 이 목록을 통째로 다시 그려서, 버블링까지
        기다리면 e.target 이 이미 DOM 에서 떨어져 나가 closest() 가 아무것도
        못 찾는다. → **캡처 단계(true)** 에서 봐야 한다.
     ② 발주 캘린더는 '목록' 이 아니라 **날짜 격자**(.weeks)다. 고르는 자리의
        이름을 하나씩 열거하는 방식은 화면이 늘 때마다 빠뜨린다.

     그래서 **어디를 눌렀는지는 보지 않는다.** 누른 적이 있고(최근 1.2초)
     상세에 내용이 들어오면 올린다. 화면 구조와 무관해진다. */
  var lastTap = 0;
  document.addEventListener('click', function (e) {
    if (window.innerWidth > 760) return;
    if (inSheet(e.target)) return;
    if (!e.target.closest) return;
    if (e.target.closest('.sheet-x, .sheet-bd')) return;
    if (e.target.closest(MODAL_SEL)) return;      // 모달 안 조작은 시트와 무관하다
    lastTap = Date.now();
    setTimeout(sheetOpen, 80);        // 같은 줄을 다시 눌러 내용이 안 바뀌는 경우
  }, true);

  function sheetMaybeOpen() {
    if (Date.now() - lastTap < 1200) sheetOpen();
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') sheetClose();
  });
  window.addEventListener('resize', function () {
    if (window.innerWidth > 760) sheetClose();
  });

  var SEL = '.listwrap,.tblwrap,.slist,.sheet,.po-body,.fglist,.left,.mlist';
  function watch() {
    sheetVsModal();
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
  /* 시트가 떠 있는 동안 모달이 열리면 둘이 겹친다 — 시트를 내린다 */
  function sheetVsModal() {
    var el = sheetEl();
    if (el && el.classList.contains('sheet-on') && modalOpen()) sheetClose();
  }

  function watchSheet() {
    var el = sheetEl();
    if (!el || el.dataset.sheetWatched) return;
    el.dataset.sheetWatched = '1';
    new MutationObserver(function () {
      if (window.innerWidth > 760) return;
      if (!sheetHasContent(el)) { sheetClose(); return; }
      if (el.classList.contains('sheet-on')) el.scrollTop = 0;
      else sheetMaybeOpen();          // 누른 직후 내용이 들어왔다 → 올린다
    }).observe(el, { childList: true, subtree: true });
  }

  document.addEventListener('DOMContentLoaded', function () { watch(); watchSheet(); });
  // 폰을 가로로 돌리면 들어가던 표가 안 들어가기도 하고 그 반대도 된다
  window.addEventListener('resize', function () {
    document.querySelectorAll('table[data-card-fits]').forEach(function (t) {
      delete t.dataset.cardFits;
    });
    watch();
  });
  // 목록은 다시 그려지므로 내용이 바뀔 때마다 다시 본다
  if (window.MutationObserver) {
    new MutationObserver(function () { watch(); watchSheet(); }).observe(document.documentElement,
      { childList: true, subtree: true });
  }
})();
