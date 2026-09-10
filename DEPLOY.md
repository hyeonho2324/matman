# MatMan 배포 가이드

> 이 문서만 따라 하면 외부에서 접속 가능한 링크가 생깁니다.
> 코딩 지식 없이도 되도록 클릭 순서 그대로 적었습니다.

**배포에 필요한 것은 이미 다 준비돼 있습니다.** 계정 가입만 직접 하시면 됩니다.

| 준비된 파일 | 역할 |
|---|---|
| `requirements.txt` | 설치할 라이브러리 목록 (flask, gunicorn) |
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
pip3 install --user flask
```

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
