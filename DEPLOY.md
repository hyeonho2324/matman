# MatMan 배포 가이드

> 이 문서만 따라 하면 외부에서 접속 가능한 링크가 생깁니다.
> 코딩 지식 없이도 되도록 클릭 순서 그대로 적었습니다.

**배포에 필요한 것은 이미 다 준비돼 있습니다.** 계정 가입만 직접 하시면 됩니다.

| 준비된 파일 | 역할 |
|---|---|
| `requirements.txt` | 설치할 라이브러리 목록 (flask, gunicorn, waitress, openpyxl) |
| `Procfile` · `render.yaml` | Render 자동 배포 설정 |
| `wsgi.py` | PythonAnywhere 진입점 |
| `data/erp.db` | 데이터베이스 파일 (1.4MB) — 이것만 있으면 DB 서버가 따로 필요 없음 |

---

# 방법 1 — PythonAnywhere (면접용 메인 링크 추천)

**장점**: 서버가 잠들지 않아 면접관이 언제 눌러도 즉시 열립니다.
**단점**: 무료 계정은 3개월마다 연장 버튼을 한 번 눌러야 합니다.

### 1단계 · 가입
1. https://www.pythonanywhere.com 접속
2. **Pricing & signup** → **Create a Beginner account** (무료)
3. 사용자명을 정합니다. **이 이름이 주소가 됩니다** → `사용자명.pythonanywhere.com`
   - 포트폴리오 링크로 쓸 이름이니 신중히 정하세요 (예: `matman-demo`)

### 2단계 · 파일 올리기
1. 상단 **Files** 탭 클릭
2. `matman` 폴더를 새로 만듭니다 (New directory 입력칸에 `matman` 입력)
3. 그 안에 아래 파일과 폴더를 업로드합니다

```
matman/
├── app.py
├── db.py
├── wsgi.py
├── requirements.txt
├── data/erp.db            ← 폴더 만들고 그 안에 업로드
├── static/css/main.css
├── static/js/main.js
└── templates/             ← 폴더 전체 (하위 embed/ 포함)
```

> 파일이 많아 번거로우면 **방법 2(GitHub)** 를 먼저 하고,
> PythonAnywhere의 **Consoles → Bash** 에서 `git clone` 한 줄로 가져오는 게 훨씬 빠릅니다.
> (아래 "GitHub에서 바로 가져오기" 참고)

### 3단계 · 웹앱 만들기
1. 상단 **Web** 탭 → **Add a new web app**
2. Domain: 기본값 그대로 → **Next**
3. **Manual configuration** 선택 (Flask 아님, 주의)
4. Python 버전: **3.10** 이상 선택 → **Next**

### 4단계 · 설정 3가지

**(1) Source code / Working directory**
Web 탭의 해당 칸에 입력:
```
/home/사용자명/matman
```

**(2) WSGI configuration file**
링크를 클릭해 파일을 열고, **내용을 전부 지운 뒤** 아래로 교체:
```python
import sys
sys.path.insert(0, '/home/사용자명/matman')
from app import app as application
```
`사용자명`을 본인 것으로 바꾸세요. 저장(**Save**).

**(3) 라이브러리 설치**
**Consoles → Bash** 에서:
```bash
pip3 install --user flask openpyxl
```

> ⚠️ **`openpyxl` 을 빼먹으면 엑셀 업로드만 안 됩니다.** 나머지 화면은 그대로 돌아가고,
> 자재 CSV 일괄 등록에서 `.xlsx` 를 올릴 때만 "이 서버에 엑셀 읽기 모듈이 없습니다" 가
> 뜹니다(CSV 는 모듈 없이도 됩니다). 이미 배포해 둔 사이트라면 이 한 줄만 더 실행하고
> **Reload** 하면 됩니다.

### 5단계 · 실행
**Web** 탭 상단의 초록색 **Reload** 버튼 클릭 → 주소 접속

```
https://사용자명.pythonanywhere.com
```

### 안 될 때
Web 탭 아래 **Error log** 를 여세요. 마지막 줄에 원인이 나옵니다.
가장 흔한 원인은 경로 오타(`/home/사용자명/matman`)입니다.

---

# 방법 2 — GitHub + Render (코드 공개 + 자동 배포)

**장점**: 코드를 고치면 자동으로 다시 배포됩니다. GitHub 링크 자체가 포트폴리오가 됩니다.
**단점**: 15분간 아무도 안 들어오면 서버가 잠들어, 첫 접속 시 30초쯤 흰 화면이 뜹니다.

### 1단계 · GitHub 저장소 만들기
1. https://github.com 가입 후 로그인
2. 우측 상단 **+** → **New repository**
3. Repository name: `matman` (원하는 이름)
4. **Public** 선택 (포트폴리오로 보여줄 거라면)
5. 나머지는 건드리지 말고 **Create repository**
   - ⚠️ "Add a README file" 체크하지 마세요. 이미 있습니다.

### 2단계 · 코드 올리기
생성된 페이지에 나오는 주소를 복사한 뒤, 아래를 실행합니다.
`본인계정`과 `저장소이름`만 바꾸세요.

```bash
cd "C:\claude code accept\matman"
git remote add origin https://github.com/본인계정/저장소이름.git
git branch -M main
git push -u origin main
```

로그인 창이 뜨면 GitHub 계정으로 로그인하세요.

### 3단계 · Render 연결
1. https://render.com → **Get Started** → **GitHub 계정으로 로그인**
2. **New +** → **Web Service**
3. 방금 만든 저장소 선택 → **Connect**
4. 설정은 `render.yaml` 을 자동으로 읽습니다. 확인만 하세요:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`
   - Instance Type: **Free**
5. **Create Web Service** 클릭

3~5분 뒤 상단에 주소가 나옵니다:
```
https://저장소이름.onrender.com
```

### 이후 코드를 고쳤을 때
```bash
cd "C:\claude code accept\matman"
git add -A
git commit -m "수정 내용 한 줄"
git push
```
푸시하면 Render가 자동으로 다시 배포합니다.

---

## 이미 올려 둔 사이트를 최신으로 (갱신)

> 지금 돌아가는 곳: https://hyeonhomatman1.pythonanywhere.com
> 저장소: https://github.com/hyeonho2324/matman

**Consoles → Bash** 에서 세 줄이면 됩니다.

```bash
cd ~/matman
git pull origin main
touch /var/www/hyeonhomatman1_pythonanywhere_com_wsgi.py
```

마지막 줄이 **Reload** 와 같은 일을 합니다. Web 탭의 초록색 **Reload** 버튼을
눌러도 똑같습니다.

### ⚠️ `git pull` 이 거부될 때

```
error: Your local changes to the following files would be overwritten by merge:
        data/erp.db
```

**정상입니다.** 배포된 사이트에서 발주·불출을 눌러 봤다면 그 서버의 `data/erp.db`
가 바뀌어 있습니다. 데모 데이터라 버려도 되므로 **서버 쪽 변경을 버리고** 받습니다.

```bash
cd ~/matman
git checkout -- data/erp.db
git pull origin main
touch /var/www/hyeonhomatman1_pythonanywhere_com_wsgi.py
```

> 서버에서 만든 데이터를 남기고 싶다면 먼저 `cp data/erp.db ~/erp.backup.db` 로
> 빼 두세요. 다만 **갱신마다 테이블이 늘어나는 일이 잦습니다**(재고 실사 · 현장
> 반납 · 단가 이력 · 발주 제안 · 발주 정책 · 운영 설정). 옛 DB 를 그대로 쓰면
> 새 화면이 "no such table" 로 터지므로 **받은 `data/erp.db` 를 쓰는 게 맞습니다.**

### git 으로 안 올렸다면 (파일을 손으로 올린 경우)

`~/matman` 에서 `git status` 가 "not a git repository" 라고 하면 처음에 파일을
직접 올린 것입니다. 한 번만 git 으로 바꿔 두면 다음부터 위 세 줄로 끝납니다.

```bash
cd ~
mv matman matman.old
git clone https://github.com/hyeonho2324/matman.git matman
touch /var/www/hyeonhomatman1_pythonanywhere_com_wsgi.py
```

Web 탭의 **Source code** · **Working directory** 가 `/home/hyeonhomatman1/matman`
그대로면 설정은 손댈 게 없습니다. 열어 보고 멀쩡하면 `rm -rf ~/matman.old` 로
옛 폴더를 지웁니다.

### 갱신됐는지 확인 (2026-10-08 기준)

갱신 전에는 **404** 가 뜨던 주소가 열리면 된 것입니다.

| 주소 | 화면 | 무엇이 보이면 성공인가 |
|---|---|---|
| [/order-plan](https://hyeonhomatman1.pythonanywhere.com/order-plan) | **발주 제안** | 대기 3건 · 자동 발주 1 · 자동 멈춤 1 · 품목 예외 2 |
| [/calendar](https://hyeonhomatman1.pythonanywhere.com/calendar) | 발주 캘린더 | **2026년 10월**로 열리고, 칸에 `✓n 부족 n 대기 n` |
| [/return](https://hyeonhomatman1.pythonanywhere.com/return) | 현장 반납 | 현장 보유 LOT 197건 |
| [/stock-count](https://hyeonhomatman1.pythonanywhere.com/stock-count) | 재고 실사 | 15차수 |
| [/products](https://hyeonhomatman1.pythonanywhere.com/products) | 자재 목록 | 상세에 **현장 보유** 줄, LOT 이력에 `현장 / 투입` 열 |

왼쪽 메뉴 **구매/협력사**에 `발주 제안` 이 보이고, 대시보드 '오늘 할 일' 에
`발주 제안 3` 칸이 생기면 된 것입니다.

> 새로 설치할 라이브러리는 없습니다. 이번 변경은 `flask` · `openpyxl` 밖에
> 안 씁니다.

### 그래도 안 열릴 때

Web 탭 아래 **Error log** 의 마지막 줄을 봅니다.

| 마지막 줄 | 원인 | 할 일 |
|---|---|---|
| `no such table: Site_Return_tb` | 옛 `data/erp.db` 가 남음 | `git checkout -- data/erp.db` 후 다시 pull |
| `attempt to write a readonly database` | DB 파일 권한 | `chmod 644 ~/matman/data/erp.db` |
| `No module named 'app'` | 경로 오타 | Web 탭 Source code 가 `/home/hyeonhomatman1/matman` 인지 |

---

## GitHub에서 바로 가져오기 (PythonAnywhere 업로드 대체)

방법 2를 먼저 끝냈다면, PythonAnywhere **Consoles → Bash** 에서:

```bash
git clone https://github.com/본인계정/저장소이름.git matman
```

파일을 하나씩 올릴 필요가 없어집니다. 이후 3~5단계는 동일합니다.
나중에 코드가 바뀌면 같은 Bash 창에서:

```bash
cd ~/matman && git pull
```
그리고 Web 탭에서 **Reload**.

---

## 배포 전 확인 사항

| 항목 | 상태 |
|---|---|
| `pyodbc` 런타임 의존성 | ✅ 없음 — `db.py` 는 파이썬 내장 `sqlite3` 만 사용. `build_db.py`(윈도우 전용)는 개발 PC에서만 실행 |
| `debug` 모드 | ✅ 기본 꺼짐. 켜려면 로컬에서 `FLASK_DEBUG=1 py app.py` |
| 비밀번호 노출 | ✅ `User_tb.PS`(평문 비밀번호)는 `erp.db` 생성 시 제외됨 |
| `ERP.accdb` | ✅ 저장소에 미포함 (평문 비밀번호 포함 원본이라 의도적으로 제외) |
| 개인정보 마스킹 | ✅ `User_tb` 의 전화번호·생년월일 마스킹 처리됨<br>`01032181960` → `010-****-1960` · `19980508` → `1998` |
| 용량 | ✅ 약 1.9MB — 무료 티어에 충분 |

마스킹은 `build_db.py` 의 `mask_phone()` / `mask_birth()` 에서 처리하므로,
DB를 다시 만들어도 원본 값이 되살아나지 않습니다.

---

## 데이터베이스를 다시 만들어야 할 때

원본 데이터(`DB_csv/`, `ERP.accdb`)가 바뀐 경우에만 필요합니다.
**개발 PC(윈도우)에서만** 실행됩니다.

```bash
cd "C:\claude code accept\matman"
py build_db.py
```

새로 생긴 `data/erp.db` 를 배포처에 다시 올리면 반영됩니다.
