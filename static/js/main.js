// ── 사이드바 토글 ─────────────────────────────────────────
const sidebar   = document.getElementById('sidebar');
const mainWrap  = document.getElementById('main-wrap');
const toggleBtn = document.getElementById('sidebar-toggle');
const mobileBtn = document.getElementById('mobile-toggle');

if (toggleBtn) {
  toggleBtn.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    const icon = toggleBtn.querySelector('i');
    if (sidebar.classList.contains('collapsed')) {
      icon.className = 'ti ti-layout-sidebar-left-expand';
    } else {
      icon.className = 'ti ti-layout-sidebar-left-collapse';
    }
    localStorage.setItem('sb-collapsed', sidebar.classList.contains('collapsed'));
  });
  // 저장된 상태 복원
  if (localStorage.getItem('sb-collapsed') === 'true') {
    sidebar.classList.add('collapsed');
    toggleBtn.querySelector('i').className = 'ti ti-layout-sidebar-left-expand';
  }
}

// 햄버거로 연 메뉴는 **항목을 누르지 않고도** 닫을 수 있어야 한다.
// 뒤 막을 탭하거나, X 를 누르거나, Esc 를 치면 닫힌다.
const backdrop = document.getElementById('sb-backdrop');
const closeBtn = document.getElementById('sidebar-close');

function setMobileMenu(open) {
  if (!sidebar) return;
  sidebar.classList.toggle('mobile-open', open);
  if (backdrop) backdrop.classList.toggle('on', open);
  // 메뉴가 떠 있는 동안 뒤 본문이 따라 스크롤되지 않게
  document.body.style.overflow = open ? 'hidden' : '';
}

if (mobileBtn) {
  mobileBtn.addEventListener('click', () => {
    setMobileMenu(!sidebar.classList.contains('mobile-open'));
  });
}
if (backdrop) backdrop.addEventListener('click', () => setMobileMenu(false));
if (closeBtn) closeBtn.addEventListener('click', () => setMobileMenu(false));
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && sidebar && sidebar.classList.contains('mobile-open')) {
    setMobileMenu(false);
  }
});
// 폰에서 가로로 돌리거나 창이 넓어지면 열린 채로 남지 않게
window.addEventListener('resize', () => {
  if (window.innerWidth > 900) setMobileMenu(false);
});

// ── 탭 전환 (공통) ───────────────────────────────────────
document.querySelectorAll('[data-tabs]').forEach(container => {
  const tabBtns     = container.querySelectorAll('.tab');
  const tabContents = container.querySelectorAll('.tab-content');
  tabBtns.forEach((btn, i) => {
    btn.addEventListener('click', () => {
      tabBtns.forEach(b => b.classList.remove('active'));
      tabContents.forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      if (tabContents[i]) tabContents[i].classList.add('active');
    });
  });
});

// ── 전역 검색 ───────────────────────────────────────────
const searchInp = document.querySelector('.topbar-search-inp');
if (searchInp) {
  searchInp.addEventListener('keydown', e => {
    if (e.key === 'Enter' && searchInp.value.trim()) {
      const q = searchInp.value.trim();
      // LOT_ID 패턴
      if (/^LOT/i.test(q)) {
        window.location.href = `/lot?id=${encodeURIComponent(q)}`;
      }
      // 품번 패턴
      else if (/^[ESVUT]\d{8}/.test(q)) {
        window.location.href = `/products/${encodeURIComponent(q)}`;
      }
      // 발주번호 패턴
      else if (/^PO/i.test(q)) {
        window.location.href = `/purchase?id=${encodeURIComponent(q)}`;
      }
    }
  });
}

// ── 알림 ────────────────────────────────────────────────
const notifBtn = document.getElementById('notif-btn');
const notifPop = document.getElementById('notif-pop');

if (notifBtn && notifPop) {
  notifBtn.addEventListener('click', e => {
    e.stopPropagation();
    const open = notifPop.hidden;
    notifPop.hidden = !open;
    notifBtn.setAttribute('aria-expanded', String(open));
  });
  // 바깥을 누르거나 Esc 로 닫는다
  document.addEventListener('click', e => {
    if (!notifPop.hidden && !notifPop.contains(e.target)) closeNotif();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeNotif(); });
}
function closeNotif() {
  if (!notifPop || notifPop.hidden) return;
  notifPop.hidden = true;
  notifBtn.setAttribute('aria-expanded', 'false');
}

// 대기 건수는 어느 화면에 있든 같아야 한다.
// 목록 HTML 은 서버가 그려 보낸다 — 같은 조각을 JS 로 또 짜면 둘이 갈라진다.
let alertsBusy = false;
async function refreshAlerts() {
  if (alertsBusy) return;     // iframe load 와 focus 가 겹쳐 두 번 부르는 경우
  alertsBusy = true;
  let d;
  try {
    const r = await fetch('/api/alerts', { headers: { 'Accept': 'application/json' } });
    if (!r.ok) return;
    d = await r.json();
  } catch (e) { return; }          // 통신이 끊겨도 화면은 그대로 둔다
  finally { alertsBusy = false; }

  const badge = document.getElementById('notif-badge');
  if (badge) { badge.textContent = d.txt; badge.hidden = !d.total; }
  const cnt = document.getElementById('np-n');
  if (cnt) cnt.textContent = d.total ? `처리 대기 ${d.total}건` : '대기 없음';
  const body = document.getElementById('np-body');
  if (body) body.innerHTML = d.html;

  document.querySelectorAll('[data-badge]').forEach(el => {
    const m = d.menu[el.dataset.badge];
    el.hidden = !m;
    if (!m) { el.textContent = ''; el.removeAttribute('title'); return; }
    el.textContent = m.txt;
    el.title = m.title;
    el.className = 'sb-badge sb-' + m.tone;
  });
}

// 승인·불출·입고는 전부 iframe 안에서 일어나고, 끝나면 그 iframe 이 스스로
// 다시 뜬다. 바깥 레이아웃은 그대로라 배지가 옛 숫자로 남는다 — 그 load 를
// 신호로 쓴다. 첫 로드에도 한 번 더 도는데 조회가 4ms 라 그냥 둔다.
document.querySelectorAll('iframe').forEach(f => {
  f.addEventListener('load', () => setTimeout(refreshAlerts, 200));
});
// 다른 탭에서 처리하고 돌아온 경우
window.addEventListener('focus', refreshAlerts);

// ── 유틸 함수 ────────────────────────────────────────────
const MatMan = {
  // 날짜 포맷
  fmtDate(d) {
    if (!d) return '—';
    const dt = new Date(d);
    return `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
  },
  // 숫자 포맷
  fmtNum(n) { return Number(n).toLocaleString(); },
  // D-Day
  dday(dateStr) {
    const today = new Date(2025, 11, 8);
    const target = new Date(dateStr);
    const diff = Math.round((target - today) / (1000*60*60*24));
    if (diff === 0) return '오늘';
    if (diff > 0)  return `D-${diff}`;
    return `${Math.abs(diff)}일 지연`;
  },
  // 배지 HTML
  badge(text, type='muted') {
    return `<span class="badge badge-${type}">${text}</span>`;
  },
  // Toast 알림
  toast(msg, type='info', duration=3000) {
    const el = document.createElement('div');
    el.style.cssText = `
      position:fixed;bottom:20px;right:20px;z-index:9999;
      padding:10px 16px;border-radius:8px;font-size:12px;font-weight:500;
      box-shadow:0 4px 16px rgba(0,0,0,.15);
      animation:slideUp .2s ease;
      background:${type==='success'?'#1D9E75':type==='danger'?'#E24B4A':type==='warning'?'#EF9F27':'#2a78d6'};
      color:#fff;
    `;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), duration);
  },
};

// slideUp 애니메이션
const style = document.createElement('style');
style.textContent = `@keyframes slideUp{from{transform:translateY(10px);opacity:0}to{transform:translateY(0);opacity:1}}`;
document.head.appendChild(style);

window.MatMan = MatMan;
