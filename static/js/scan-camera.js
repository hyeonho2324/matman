/* 휴대폰 카메라로 바코드를 읽는다. 쓰는 화면: 스캐너 · 재고 실사.
   전역은 `ScanCam` 하나만 만든다. 창 모양은 static/css/scan-camera.css.

   ── 왜 두 갈래인가 ────────────────────────────────────────
   ① BarcodeDetector — 브라우저가 OS 의 바코드 엔진을 그대로 내준다.
      추가 용량 0 바이트, 속도도 가장 빠르다. 안드로이드 크롬·삼성
      인터넷에 있다.
   ② 없으면 ZXing(328KB)을 **그때 한 번만** 내려받는다. 아이폰 사파리는
      BarcodeDetector 가 기본으로 꺼져 있어 늘 이 길로 온다. 안드로이드는
      이 파일을 아예 받지 않는다.

   ⚠️ 카메라는 **보안 컨텍스트** 에서만 열린다 — https 또는 127.0.0.1.
      사내망 IP(http://192.168.x.x:5000)로 들어오면 브라우저가
      navigator.mediaDevices 를 아예 주지 않는다. 눌러 봐야 알 수 없으니
      버튼을 미리 막는다(usable()). 배포본은 https 라 문제없다.

   ── 쓰는 법 ──────────────────────────────────────────────
     ScanCam.open(onHit)                 한 번 읽고 닫는다 (스캐너)
     ScanCam.open(onHit, {hold: true})   읽으면 멈춰만 선다 (재고 실사)
       → ScanCam.slot() 에 확인 카드를 그리고, 끝나면 ScanCam.resume()
   */
(function () {
  'use strict';

  /* 우리가 찍는 건 Code 128(barcode.py)이다. 나머지는 외부 라벨용. */
  var WANT = ['code_128', 'code_39', 'code_93', 'qr_code',
              'ean_13', 'ean_8', 'itf', 'codabar', 'data_matrix'];
  var ZXING_URL = '/static/vendor/zxing.min.js';

  var el, video, canvas, ctx, stream, track, timer;
  var detector = null, zreader = null, zhints = null;
  var busy = false, flip = 0, onHit = null, lastVal = '', lastAt = 0, ac = null;
  var opts = {};

  function usable() {
    return !!(window.isSecureContext && navigator.mediaDevices &&
              navigator.mediaDevices.getUserMedia);
  }

  function q(sel) { return el ? el.querySelector(sel) : null; }
  function say(m) {
    var b = q('.cammsg');
    if (b) { b.textContent = m || ''; b.classList.toggle('on', !!m); }
  }

  /* ── 창을 만든다 ───────────────────────────────────────
     화면마다 같은 15줄을 붙여 두면 한쪽만 고쳐져 갈라진다. 여기서 한 번만
     만든다. ⚠️ .cammsg 는 .camframe **앞** 이어야 한다 — CSS 가 뒤 형제
     선택자(`~`)로 틀을 끄기 때문이다. */
  function build() {
    el = document.getElementById('camov');
    if (el) return el;
    el = document.createElement('div');
    el.className = 'camov';
    el.id = 'camov';
    el.innerHTML =
      /* ⚠️ playsinline 이 없으면 아이폰이 전체화면 재생기로 가로채 간다.
             muted 가 없으면 자동 재생 자체가 거부된다. */
      '<video class="camvid" id="camvid" playsinline autoplay muted></video>'
      + '<div class="cammsg"></div>'
      + '<div class="camframe">'
      +   '<i class="camc"></i><i class="camc"></i><i class="camc"></i><i class="camc"></i>'
      +   '<i class="camline"></i></div>'
      + '<div class="camtop">'
      +   '<span class="camtt"><i class="ti ti-barcode"></i><span class="camtxt"></span></span>'
      +   '<button type="button" class="cambtn camtorch" title="손전등"><i class="ti ti-bulb"></i></button>'
      +   '<button type="button" class="cambtn camx" title="닫기"><i class="ti ti-x"></i></button>'
      + '</div>'
      + '<div class="camfoot"><span class="camftx"></span> · 해독 <span class="cameng"></span></div>'
      + '<div class="camslot"></div>';
    document.body.appendChild(el);
    q('.camx').addEventListener('click', close);
    q('.camtorch').addEventListener('click', torch);
    return el;
  }

  /* ── 디코더 고르기 ─────────────────────────────────────
     ⚠️ `'BarcodeDetector' in window` 로는 판정할 수 없다. 윈도 크롬은
        생성자는 있는데 읽을 수 있는 형식이 하나도 없다. 그래서 **읽을
        형식 목록**을 물어보고 code_128 이 있는지로 가른다. */
  function pickDecoder() {
    if (detector) return Promise.resolve('native');
    if (zreader) return Promise.resolve('zxing');
    if (!window.BarcodeDetector) return loadZxing();
    return window.BarcodeDetector.getSupportedFormats().then(function (fmts) {
      var use = WANT.filter(function (f) { return fmts.indexOf(f) >= 0; });
      if (use.indexOf('code_128') < 0) return loadZxing();
      detector = new window.BarcodeDetector({ formats: use });
      return 'native';
    }).catch(function () { return loadZxing(); });
  }

  function loadZxing() {
    if (zreader) return Promise.resolve('zxing');
    return new Promise(function (res, rej) {
      if (window.ZXing) { res(); return; }
      var s = document.createElement('script');
      s.src = ZXING_URL;
      s.onload = function () { res(); };
      s.onerror = function () { rej(new Error('no-decoder')); };
      document.head.appendChild(s);
    }).then(function () {
      var Z = window.ZXing;
      if (!Z) throw new Error('no-decoder');
      zhints = new Map();
      zhints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
        Z.BarcodeFormat.CODE_128, Z.BarcodeFormat.CODE_39, Z.BarcodeFormat.CODE_93,
        Z.BarcodeFormat.QR_CODE, Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8,
        Z.BarcodeFormat.ITF, Z.BarcodeFormat.CODABAR, Z.BarcodeFormat.DATA_MATRIX]);
      zhints.set(Z.DecodeHintType.TRY_HARDER, true);
      zreader = new Z.MultiFormatReader();
      return 'zxing';
    });
  }

  /* ── 안내 틀 안쪽만 잘라서 넘긴다 ───────────────────────
     화면 전체를 넘기면 글자·배경까지 뒤져 느려지고 엉뚱한 걸 집는다.
     틀을 그려 둔 것은 장식이 아니라 **실제 판독 영역**이다.

     ⚠️ <video> 는 object-fit:cover 라 원본의 긴 쪽이 잘려 보인다. 그래서
        화면 좌표를 그대로 쓸 수 없다. 틀과 영상이 둘 다 가운데 정렬이라,
        틀의 크기를 cover 배율로 되나누면 그게 원본 사각형이 된다. */
  function band() {
    var fr = q('.camframe');
    if (!video || !fr) return null;
    var vw = video.videoWidth, vh = video.videoHeight;
    var ew = video.clientWidth, eh = video.clientHeight;
    if (!vw || !vh || !ew || !eh) return null;
    var scale = Math.max(ew / vw, eh / vh) || 1;
    var r = fr.getBoundingClientRect();
    var sw = Math.min(vw, r.width / scale), sh = Math.min(vh, r.height / scale);
    return { x: (vw - sw) / 2, y: (vh - sh) / 2, w: sw, h: sh };
  }

  function grab() {
    var b = band();
    if (!b || b.w < 8 || b.h < 8) return null;
    /* 720px 보다 키워도 더 잘 읽히지 않고 프레임만 느려진다 */
    var k = Math.min(1, 720 / b.w);
    canvas.width = Math.max(1, Math.round(b.w * k));
    canvas.height = Math.max(1, Math.round(b.h * k));
    ctx.drawImage(video, b.x, b.y, b.w, b.h, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function read() {
    var c = grab();
    if (!c) return Promise.resolve('');
    if (detector) {
      return detector.detect(c).then(function (list) {
        return list && list.length ? String(list[0].rawValue || '') : '';
      });
    }
    /* ⚠️ 1D 와 2D 가 좋아하는 이진화가 다르다. Hybrid 는 QR 에 강하고
       GlobalHistogram 은 가늘고 긴 바코드에 강하다. 한 프레임에 둘 다
       돌리면 절반으로 느려지므로 **프레임마다 번갈아** 쓴다. */
    var Z = window.ZXing;
    if (!zreader || !Z) return Promise.resolve('');
    try {
      var src = new Z.HTMLCanvasElementLuminanceSource(c);
      var bin = (flip++ & 1) ? new Z.GlobalHistogramBinarizer(src)
                             : new Z.HybridBinarizer(src);
      var r = zreader.decode(new Z.BinaryBitmap(bin), zhints);
      return Promise.resolve(r ? String(r.getText() || '') : '');
    } catch (e) {
      return Promise.resolve('');       // NotFoundException — 이 프레임엔 없다
    }
  }

  function tick() {
    if (busy || !stream) return;
    busy = true;
    read().then(function (v) { if (v) hit(v); })
          .catch(function () {})
          .then(function () { busy = false; });
  }

  function hit(v) {
    v = String(v).trim();
    if (!v) return;
    /* 같은 라벨을 1초 사이에 두 번 읽는 건 한 번 찍은 것이다 */
    if (v === lastVal && Date.now() - lastAt < 1200) return;
    lastVal = v; lastAt = Date.now();
    beep();
    /* 아직 사용자가 한 번도 안 눌렀으면 크롬이 진동을 막고 콘솔에 오류를
       남긴다. 막힐 걸 알면서 부르지 않는다. */
    var act = navigator.userActivation;
    if (navigator.vibrate && (!act || act.hasBeenActive)) {
      try { navigator.vibrate(40); } catch (e) {}
    }
    /* hold 면 닫지 않고 **멈춰 선다** — 재고 실사는 라벨을 줄줄이 찍는다.
       닫았다 다시 열면 카메라를 매번 다시 잡느라 0.5초씩 끊긴다. */
    if (opts.hold) pause(); else close();
    if (onHit) onHit(v);
  }

  /* 멈춤·재개 — 스트림은 그대로 두고 판독만 쉰다 */
  function pause() {
    if (timer) { clearInterval(timer); timer = null; }
    busy = false;
    if (el) el.classList.add('hold');
  }
  function resume() {
    if (!stream || !el) return;
    el.classList.remove('hold');
    slot().innerHTML = '';
    lastVal = '';                       // 같은 라벨을 다시 찍을 수 있어야 한다
    if (!timer) timer = setInterval(tick, detector ? 120 : 230);
  }
  function slot() { build(); return q('.camslot'); }

  /* 창고에서는 화면을 안 보고 찍는다 — 읽혔다는 걸 소리로 알린다.
     음원 파일을 두지 않고 짧은 사각파를 그 자리에서 만든다.
     ⚠️ 아이폰은 사용자 제스처 안에서만 오디오가 깨어난다. 그래서 버튼을
        누른 그 순간(open)에 미리 한 번 열어 둔다 — 읽힌 뒤에 열면 늦다. */
  function wake() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      ac = ac || new AC();
      if (ac.state === 'suspended') ac.resume();
    } catch (e) {}
  }
  function beep() {
    try {
      if (!ac) return;
      var o = ac.createOscillator(), g = ac.createGain();
      o.type = 'square'; o.frequency.value = 1760;
      g.gain.value = 0.05;
      o.connect(g); g.connect(ac.destination);
      o.start(); o.stop(ac.currentTime + 0.08);
    } catch (e) {}
  }

  function why(e) {
    var n = (e && (e.name || e.message)) || '';
    if (n === 'NotAllowedError' || n === 'SecurityError')
      return '카메라 사용이 거부됐습니다. 주소창의 자물쇠 → 사이트 설정에서 카메라를 허용하고 다시 누르세요.';
    if (n === 'NotFoundError' || n === 'OverconstrainedError')
      return '쓸 수 있는 카메라가 없습니다. 번호를 직접 입력해 주세요.';
    if (n === 'NotReadableError' || n === 'TrackStartError')
      return '다른 앱이 카메라를 쓰고 있습니다. 그 앱을 닫고 다시 누르세요.';
    if (n === 'no-decoder')
      return '바코드 해독기를 불러오지 못했습니다. 연결을 확인하고 다시 누르세요.';
    return '카메라를 열지 못했습니다. (' + (n || '알 수 없는 오류') + ')';
  }

  /* 손전등은 기기가 내줄 때만 보여 준다 (아이폰 사파리는 못 준다) */
  function showTorch() {
    var b = q('.camtorch');
    if (!b) return;
    var cap = (track && track.getCapabilities) ? (track.getCapabilities() || {}) : {};
    b.style.display = ('torch' in cap) ? '' : 'none';
    b.classList.remove('on');
  }
  function torch() {
    var b = q('.camtorch');
    if (!track || !b) return;
    var want = !b.classList.contains('on');
    track.applyConstraints({ advanced: [{ torch: want }] }).then(function () {
      b.classList.toggle('on', want);
    }).catch(function () { b.style.display = 'none'; });
  }

  function open(cb, o) {
    build();
    video = document.getElementById('camvid');
    if (!canvas) { canvas = document.createElement('canvas'); ctx = canvas.getContext('2d'); }
    opts = o || {};
    onHit = cb || null;
    lastVal = '';
    el.classList.add('on');
    el.classList.remove('hold');
    el.removeAttribute('data-engine');
    q('.camslot').innerHTML = '';
    q('.camtxt').textContent = opts.title || '바코드를 틀 안에 맞추세요';
    q('.camftx').textContent = opts.foot || '읽으면 소리와 함께 자동으로 조회합니다';
    say('카메라를 준비합니다…');
    wake();                            // 제스처 안에서 소리를 깨워 둔다

    navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: 'environment' },
               width: { ideal: 1280 }, height: { ideal: 720 } }
    }).then(function (s) {
      stream = s;
      track = s.getVideoTracks()[0];
      video.srcObject = s;
      return video.play();
    }).then(function () {
      showTorch();
      return pickDecoder();
    }).then(function (kind) {
      say('');
      el.dataset.engine = kind;
      var eng = q('.cameng');
      if (eng) eng.textContent = (kind === 'native' ? '브라우저 내장' : 'ZXing');
      /* 내장 엔진은 가볍다 — 더 자주 본다. ZXing 은 한 프레임이 비싸다 */
      timer = setInterval(tick, kind === 'native' ? 120 : 230);
    }).catch(function (e) { say(why(e)); });
  }

  function close() {
    if (timer) { clearInterval(timer); timer = null; }
    busy = false;
    if (stream) {
      stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} });
      stream = null;
    }
    track = null;
    if (video) { try { video.pause(); } catch (e) {} video.srcObject = null; }
    if (el) {
      el.classList.remove('on', 'hold');
      el.removeAttribute('data-engine');
      q('.camslot').innerHTML = '';
    }
    say('');
    if (opts.onClose) { var f = opts.onClose; opts = {}; f(); }
  }

  /* ⚠️ 화면을 떠나면 카메라를 **반드시** 끈다. 안 끄면 뒤에서 계속 켜져
     있어 배터리를 먹고 상단에 녹화 표시가 남는다. */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden && stream) close();
  });
  window.addEventListener('pagehide', close);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && stream) close();
  });

  window.ScanCam = { open: open, close: close, torch: torch, usable: usable,
                     pause: pause, resume: resume, slot: slot };
})();
