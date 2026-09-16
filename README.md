# Speech Sumarize

맥에서 녹음하고, 받아쓰고, 요약하는 도구.

## 주의: 다른 사람 목소리를 녹음할 때

이 도구는 시스템 소리를 녹음하므로 **회의 참석자 전원의 발언이 담긴다.**

- 녹음하기 전에 참석자에게 알리고 동의를 받을 것
- 지역에 따라 상대방 동의 없는 대화 녹음이 법으로 제한된다. 본인이 대화의
  당사자인지, 어느 관할인지에 따라 다르므로 미리 확인할 것
- 녹음과 대본에는 다른 사람의 발언이 그대로 남는다. 공유·보관·삭제 기준을
  정해 두고, 필요 없어지면 지울 것 (요약 후 원본 정리 기능이 이를 돕는다)

---

## Release Note

[CHANGELOG.md](CHANGELOG.md)

## Installation

### 1. 시스템 도구

```bash
brew install ffmpeg
```

오디오 변환·녹음에 쓴다. 없으면 대부분의 기능이 동작하지 않는다.

### 2. 파이썬 패키지

```bash
/opt/anaconda3/bin/python3 -m pip install groq google-genai mlx-whisper tkinterdnd2
```

| 패키지 | 용도 | 없으면 |
|---|---|---|
| `google-genai` | Gemini 받아쓰기·요약 | 요약 불가 |
| `mlx-whisper` | Whisper(local) 받아쓰기 | Whisper(local) 엔진 비활성 (Apple Silicon 전용) |
| `groq` | Groq 받아쓰기 | Groq 엔진 불가 |
| `tkinterdnd2` | 드래그 앤 드롭 | 버튼으로만 파일 추가 |

Whisper(local) 엔진은 첫 실행 때 모델을 자동으로 내려받는다(turbo 기준 약 1.5GB,
`~/.cache/huggingface`).

### 3. BlackHole (앱에서 직접 녹음할 때만)

맥의 시스템 소리를 녹음하려면 가상 오디오 장치가 필요하다.

```bash
brew install blackhole-16ch
```

---

## API 키 설정

요약에는 Gemini 키가 반드시 필요하다. 받아쓰기를 `Whisper(local)` 로만 한다면 Groq 키는
없어도 된다.

### Gemini (요약, 그리고 Gemini 받아쓰기)

1. `aistudio.google.com` 에서 키 발급 (카드 등록 불필요)
2. 아래를 **직접 터미널에 입력**한다. 키가 셸 기록에 남지 않는다.

```bash
read -rs GEMINI_KEY && printf '%s\n' "$GEMINI_KEY" > ~/.gemini_key && chmod 600 ~/.gemini_key && unset GEMINI_KEY && echo "저장 완료"
```

엔터를 누르면 커서만 깜빡인다. 거기에 키를 붙여넣고 다시 엔터.

### Groq (선택)

`console.groq.com` 에서 발급 후 같은 방식으로 `~/.groq_key` 에 저장한다.

두 키 모두 환경변수(`GEMINI_API_KEY`, `GROQ_API_KEY`)로 줘도 된다.
파일보다 환경변수가 우선한다.

### 키 확인

```bash
python3 ~/Desktop/py/stt/src/check_key.py
```

키 값은 앞뒤 몇 글자만 가려서 보여주고, 실제로 서버에 호출해 작동 여부까지
확인한다. 길이 이상, 붙여넣기 중복, 공백 혼입 같은 사고도 잡아낸다.

---

## 실행

폴더 내의 **SpeechSumarize.command** 를 더블클릭하거나:

```bash
python3 ~/Desktop/py/stt/src/stt_gui.py
```

### 화면 구성

**1. 녹음 파일** — 파일을 끌어다 놓거나 버튼으로 추가한다. 폴더를 놓으면
하위 폴더까지 뒤져서 오디오를 전부 찾는다. 이미 받아쓴 대본(`.txt`)도 받는다.

맨 위 **● 시스템 소리 녹음** 버튼으로 직접 녹음할 수도 있다. 녹음이 끝나면
파일이 목록에 자동으로 들어간다.

**2. 저장 위치** — 폴더를 고르고, 아래 칸에 이름을 적는다. 결과는
**`<저장 위치>/<이름>/`** 폴더 하나에 모인다.

```
~/Desktop/회의A/
├── 회의A_rec.ogg          앱에서 직접 녹음한 파일
├── 회의A_transcript.txt   대본
└── 회의A_summary.md       요약
```

폴더를 안 고르면 `~/Documents/녹음/` 아래에 만든다. 이름을 비우면
`2026-09-15_14-30-02` 같은 날짜를 이름으로 쓴다.

녹음·받아쓰기·요약은 한 번 정한 폴더를 계속 쓴다. 이름이나 저장 위치를
바꾸지 않는 한 다시 묻지 않는다.

**3. 받아쓰기 생성** — 엔진(`Gemini` / `Whisper` / `Whisper(local)`), 언어,
용어 힌트를 정하고 **받아쓰기 시작**. `이름_transcript.txt` 가 만들어진다.

**4. 요약 생성** — 추가 요청을 적고(선택) **요약 생성**.
`이름_summary.md` 또는 `.txt` 가 만들어진다.

아래쪽에 진행바와 로그가 있다. **중지**는 파일 경계에서 멈춘다.

### 이름이 겹칠 때

받아쓸 파일이 **여러 개**면 이름 뒤에 번호가 붙는다.

```
~/Desktop/세미나/
├── 세미나_1_transcript.txt
├── 세미나_1_summary.md
├── 세미나_2_transcript.txt
├── 세미나_2_summary.md
├── 세미나_3_transcript.txt
└── 세미나_3_summary.md
```

**같은 이름의 폴더가 이미 있으면** 물어본다.

- **예** — 그 폴더에 이어서 넣는다. 같은 이름의 파일이 있으면
  `회의A_2_transcript.txt` 처럼 번호를 붙여 피한다. 덮어쓰지 않는다
- **아니오** — `회의A_2` 처럼 새 폴더를 만든다
- **취소** — 아무것도 하지 않는다

같은 폴더에 **다시 녹음**하면 `회의A_2_rec.ogg`, `회의A_3_rec.ogg` 로 쌓인다.

목록에 직접 넣은 대본(`.txt`)을 요약하면 그 대본 이름을 따라
`대본이름_summary.md` 가 된다.

---

받아쓰기 엔진은 셋 중에 고른다. 요약은 Gemini 로 한다.

| 엔진 | 비용 | 속도 | 인터넷 | 비고 |
|---|---|---|---|---|
| **Whisper(local)** (mlx-whisper) | 무료·무제한 | 실시간의 약 28배 | 불필요 | 기본으로 권장 |
| **Gemini** | 무료 한도 내 | 매우 빠름 | 필요 | 전용 어휘 지원 |
| **Whisper** (Groq API) | 무료 한도 내 | 가장 빠름 | 필요 | 학교망에서 차단될 수 있음 |

`Whisper(local)` 과 Gemini 는 같은 음성에서 **글자 단위로 동일한 결과**를 냈다. 토큰도 안
들고 네트워크와 무관하므로 평소에는 `Whisper(local)` 을 쓰면 된다.

---

## 용어 힌트 — 가장 중요한 설정

**3. 받아쓰기 생성**의 용어 힌트 칸에 자주 나오는 영어 용어를 쉼표로 적는다.

```
RLPD, offline reinforcement learning, coverage
```

| | 결과 |
|---|---|
| 힌트 없음 | 오프라인 **리인포스먼트 러닝** 기반의 **커버리지**를 |
| 힌트 있음 | offline **reinforcement learning** 기반의 **coverage**를 |

엔진마다 다르게 쓰인다. Gemini 는 `custom_vocabulary` API 필드로, `Whisper(local)` 은
Whisper 의 `initial_prompt` 로 넘어간다. 요약할 때 고유명사 표기 기준으로도
쓰인다.

문장이 아니라 **용어만** 적는다. "한국어 녹음입니다" 같은 문장은 효과가 없다.

---

## 요약

### 고정 규칙

모든 요약에 아래가 항상 적용된다. 프롬프트를 바꾸려면
[summarize.py](src/summarize.py) 의 `COMMON_RULES` 를 고친다.

- 영어 전문 용어는 변형하지 않고 그대로 쓴다
- 대본에 한글로 음차된 영어 용어는 영어 표기로 되돌린다
  (리인포스먼트 러닝 → reinforcement learning)
- 분량은 공백 포함 1000~2000자
- 대본에 없는 내용을 지어내지 않는다

### 추가 요청

**4. 요약 생성**의 입력칸에 원하는 바를 적으면 기본 지시 뒤에 붙는다.

```
할 일만 체크리스트로 뽑아줘
발표용으로 5개 항목만
영어로 써줘
```

### 저장 형식

- **.md** — 마크다운. 제목·표·굵게가 살아 있다.
- **.txt** — 평문. 마크다운 문법 없이 생성하고, 남은 표기도 코드로 걷어낸다.

---

## 앱에서 직접 녹음하기

시스템 소리(회의 앱, 영상, 강의 녹화)를 BlackHole 로 잡는다. 마이크는 녹음하지
않으므로 **본인 목소리는 들어가지 않는다.**

### 순서

1. 메뉴막대 소리 아이콘 → 출력을 **BlackHole 16ch** 로 변경
2. **● 시스템 소리 녹음** 클릭
3. 회의·영상 재생
4. **■ 녹음 종료** → 파일이 목록에 자동 추가
5. **출력을 스피커로 되돌리기**

### 소리가 안 들리는 게 정상이다

출력을 BlackHole 로 돌리면 스피커로는 아무 소리도 안 난다. 들으면서 녹음하려면
**오디오 MIDI 설정**에서 "BlackHole + 스피커"를 묶은 다중 출력 장치를 만들어
그것을 출력으로 선택한다.

### 무음 방지

출력 전환을 잊는 사고가 잦아서 세 군데서 막는다.

- **녹음 시작 전** — 출력이 BlackHole 이 아니면 경고하고 진행 여부를 묻는다
- **녹음 중** — 10초마다 평균 볼륨을 로그에 적는다. 1분이 지난 시점에 전체
  평균이 -80dB 아래면 경고창을 띄운다. 녹음은 멈추지 않으므로 그 자리에서
  출력을 바꾸면 그 뒤부터는 소리가 잡힌다
- **녹음 종료 후** — 다시 볼륨을 재서 -80dB 아래면 무음으로 판정하고,
  **그 파일을 휴지통으로 보낼지 묻는다**(기본 **예**). 완전 삭제가 아니라
  되돌릴 수 있다

녹음 중에 찍히는 숫자와 끝나고 나오는 숫자는 재는 방식이 달라서 조금 어긋난다
(앞은 RMS, 뒤는 평균 진폭). 무음인지 아닌지를 가리는 데는 지장이 없다.

녹음은 **Ogg/Opus** 로 저장한다. 스트리밍 형식이라 앱이 강제 종료되거나 맥이
꺼져도 그 시점까지 그대로 남는다. 시간당 약 11MB.

녹음 파일은 **2. 저장 위치**에서 정한 `<저장 위치>/<이름>/` 폴더에
`이름_rec.ogg` 로 저장한다. 폴더를 안 골랐으면 `~/Documents/녹음/` 아래다.

---

## 요약 후 원본 정리

요약이 끝나면 대본과 녹음 파일을 정리할지 물어본다. **예**를 누르면 요약본만
남기고 나머지를 **휴지통으로** 보낸다. 완전 삭제가 아니라 되돌릴 수 있다.

- 요약에 **성공한** 파일만 대상이다
- 지울 파일을 이름과 총 용량까지 보여준다
- 기본 선택은 **아니오**

---

## 터미널에서 쓰기

GUI 없이 각 단계를 따로 돌릴 수 있다.

```bash
# Whisper(local) 받아쓰기
python3 ~/Desktop/py/stt/src/local_stt.py 회의.m4a --hint "RLPD, coverage"

# Gemini 받아쓰기
python3 ~/Desktop/py/stt/src/gemini_stt.py 회의.m4a --hint "RLPD, coverage"

# Groq 받아쓰기 (+ 이어서 요약)
python3 ~/Desktop/py/stt/src/transcribe.py 회의.m4a --summarize --format txt

# 이미 있는 대본 요약
python3 ~/Desktop/py/stt/src/summarize.py 대본.txt --extra "할 일만 체크리스트로"

# 폴더 통째로
python3 ~/Desktop/py/stt/src/local_stt.py ~/Desktop/recordings

# 녹음 장치 상태 확인 (3초 녹음 후 볼륨 측정)
python3 ~/Desktop/py/stt/src/recorder.py 3

# 쓸 수 있는 Gemini 모델 목록
python3 ~/Desktop/py/stt/src/summarize.py --list-models .
```

주요 옵션: `--lang`(기본 ko) · `--hint` · `--out` · `--format {md,txt}` ·
`--size {turbo,large,medium,small}`(Whisper(local))

터미널에서는 폴더를 만들지 않는다. `회의.txt`, `회의_요약.md` 처럼 원본 옆에
그대로 쌓는다. `<이름>/이름_transcript.txt` 구조는 GUI 에서만 쓴다.

---

## 문제 해결

### `403 Access denied` (Groq)

**Groq 가 이쪽 IP 를 차단한 것이다.** 대학 네트워크는
수많은 사용자가 공인 IP 하나를 공유해서 Groq 의 자동 차단에 걸리기 쉽다.
`groq.com` 은 열리는데 `api.groq.com` 과 `console.groq.com` 만 403 이면 이 경우다.

학교 IT 에 요청해도 해결되지 않는다. **엔진을 `Whisper(local)` 이나 `Gemini` 로 바꾸면 된다.**
꼭 Groq 를 써야 하면 핫스팟을 쓰거나, `support@groq.com` 에 공인 IP
(`curl ifconfig.me`)를 알려 차단 해제를 요청한다.

### `429 RESOURCE_EXHAUSTED` / "혼잡" 로그가 반복

Gemini 무료 한도에 걸렸거나 서버가 붐빈다. 자동으로 재시도한 뒤 다음 모델로
넘어가므로 대개 알아서 성공한다. 자주 겪으면 **받아쓰기를 `Whisper(local)` 로 돌리면**
Gemini 사용량이 요약분만 남아 크게 줄어든다.

### 녹음이 전부 무음이다

맥의 출력이 BlackHole 로 되어 있지 않다. 메뉴막대에서 바꾸고 다시 녹음한다.

녹음 중 로그에 10초마다 찍히는 평균이 계속 **무음**이면 이 경우다. 1분이
지나면 경고창도 뜨는데, 그때 출력을 바꾸면 **녹음을 멈추지 않고도** 그 뒤부터
소리가 잡힌다. 장치 상태만 따로 확인하려면:

```bash
python3 ~/Desktop/py/stt/src/recorder.py 3
```

### 영어 용어가 자꾸 한글로 나온다

용어 힌트 칸에 **정확한 영어 표기**를 쉼표로 적는다. 대본 단계에서 놓쳐도
요약 단계에서 한 번 더 영어로 복원되지만, 힌트를 주는 편이 확실하다.

### 드래그 앤 드롭이 안 된다

`pip install tkinterdnd2`. 없어도 버튼으로는 추가할 수 있다.

### Whisper(local) 엔진이 안 보인다 / 첫 실행이 멎은 것 같다

Apple Silicon 전용이다. 첫 실행 때 모델 1.5GB 를 내려받는 동안 진행률이 GUI 에
표시되지 않아 멈춘 것처럼 보인다. 몇 분 기다리면 된다.

---

## 파일 구성

```
stt/
├── SpeechSumarize.command   더블클릭 실행용 런처
├── README.md
├── .gitignore
└── src/                     파이썬 소스
```

| 파일 | 역할 |
|---|---|
| [stt_gui.py](src/stt_gui.py) | GUI 본체 |
| [recorder.py](src/recorder.py) | BlackHole 녹음 |
| [local_stt.py](src/local_stt.py) | Whisper(local) 받아쓰기 (mlx-whisper) |
| [gemini_stt.py](src/gemini_stt.py) | Gemini 받아쓰기 |
| [transcribe.py](src/transcribe.py) | Groq 받아쓰기 + 오디오 변환 공용 함수 |
| [summarize.py](src/summarize.py) | Gemini 요약, 프롬프트 규칙 |
| [check_key.py](src/check_key.py) | API 키 점검 |


