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

if (mobileBtn) {
  mobileBtn.addEventListener('click', () => {
    sidebar.classList.toggle('mobile-open');
  });
}

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
